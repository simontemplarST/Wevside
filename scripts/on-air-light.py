#!/usr/bin/env python3
"""
on-air-light.py — drive an "ON AIR" light from PSKReporter reception reports.

Polls the PSKReporter retrieval API for reception reports of your callsign
(i.e. stations that have *heard you* on a digital mode) and writes a small
JSON status file that the Hugo site reads to light up the header badge.

Because PSKReporter only logs a spot when you transmit a digimode (FT8, FT4,
JS8, PSK31, ...) and someone decodes you, a fresh spot is a reliable proxy for
"currently on the air."

API: https://retrieve.pskreporter.info/query
Docs: https://pskreporter.info/pskdev.html
Rate limit: query no more than once every 5 minutes (enforced here).

Usage:
    python3 scripts/on-air-light.py --callsign N0YEP --watch
    python3 scripts/on-air-light.py -c N0YEP -o static/on-air.json --once

Typical production setup: point --output at your *published* site directory
(e.g. public/on-air.json) and run with --watch under systemd/cron so the badge
updates without a rebuild. For `hugo server`, writing to static/on-air.json is
picked up by live-reload.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

API_URL = "https://retrieve.pskreporter.info/query"
MIN_INTERVAL = 300  # PSKReporter asks for no more than one query / 5 min

# Rough band edges (MHz) for labelling spots.
BANDS = [
    ("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("60m", 5.3, 5.4),
    ("40m", 7.0, 7.3), ("30m", 10.1, 10.15), ("20m", 14.0, 14.35),
    ("17m", 18.06, 18.17), ("15m", 21.0, 21.45), ("12m", 24.89, 24.99),
    ("10m", 28.0, 29.7), ("6m", 50.0, 54.0), ("2m", 144.0, 148.0),
]


def band_for(freq_hz: float) -> str:
    mhz = freq_hz / 1_000_000.0
    for name, lo, hi in BANDS:
        if lo <= mhz <= hi:
            return name
    return f"{mhz:.3f} MHz"


def fetch_reports(callsign: str, window_min: int, appcontact: str,
                  timeout: int = 30) -> list[dict]:
    """Query PSKReporter for stations that heard `callsign`. Returns spots."""
    params = {
        "senderCallsign": callsign.upper(),
        # negative = "look back this many seconds" (server caps the window)
        "flowStartSeconds": str(-window_min * 60),
        "rronly": "1",        # reception reports only, skip active-monitor list
        "nolocator": "1",     # we don't need grid data here
    }
    if appcontact:
        params["appcontact"] = appcontact
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        # PSKReporter blocks generic/absent user agents; identify politely.
        "User-Agent": f"on-air-light/1.0 ({appcontact or 'ham-radio-site'})",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()

    root = ET.fromstring(raw)
    spots: list[dict] = []
    # Elements are <receptionReport .../> with attributes.
    for rr in root.iter("receptionReport"):
        a = rr.attrib
        try:
            ts = int(a.get("flowStartSeconds", "0"))
        except ValueError:
            ts = 0
        try:
            freq = float(a.get("frequency", "0"))
        except ValueError:
            freq = 0.0
        snr = a.get("sNR")
        spots.append({
            "receiver": a.get("receiverCallsign", ""),
            "receiver_grid": a.get("receiverLocator", ""),
            "freq_hz": freq,
            "band": band_for(freq) if freq else "",
            "mode": a.get("mode", ""),
            "snr": int(snr) if snr not in (None, "") else None,
            "ts": ts,
        })
    return spots


def build_status(callsign: str, spots: list[dict], fresh_min: int) -> dict:
    now = int(time.time())
    if spots:
        newest = max(spots, key=lambda s: s["ts"])
        last_heard = newest["ts"]
        minutes_ago = round((now - last_heard) / 60, 1)
        on_air = (now - last_heard) <= fresh_min * 60
        # only summarise the spots inside the freshness window
        fresh = [s for s in spots if (now - s["ts"]) <= fresh_min * 60]
        window = fresh or spots
        receivers = sorted({s["receiver"] for s in window if s["receiver"]})
        bands = sorted({s["band"] for s in window if s["band"]})
        modes = sorted({s["mode"] for s in window if s["mode"]})
        snrs = [s["snr"] for s in window if s["snr"] is not None]
        best_snr = max(snrs) if snrs else None
        sample = sorted(window, key=lambda s: s["ts"], reverse=True)[:10]
        spot_out = [{
            "receiver": s["receiver"], "grid": s["receiver_grid"],
            "band": s["band"], "mode": s["mode"], "snr": s["snr"],
            "minutes_ago": round((now - s["ts"]) / 60, 1),
        } for s in sample]
    else:
        last_heard, minutes_ago, on_air = 0, None, False
        receivers, bands, modes, best_snr, spot_out = [], [], [], None, []

    return {
        "callsign": callsign.upper(),
        "on_air": on_air,
        "last_heard": last_heard,
        "last_heard_iso": (
            datetime.fromtimestamp(last_heard, timezone.utc).isoformat()
            if last_heard else None
        ),
        "minutes_ago": minutes_ago,
        "receiver_count": len(receivers),
        "receivers": receivers,
        "bands": bands,
        "modes": modes,
        "best_snr": best_snr,
        "spots": spot_out,
        "updated": now,
    }


def write_status(status: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(status, fh, indent=2)
    os.replace(tmp, path)  # atomic swap so the site never reads a half-file


def run_once(args) -> dict:
    try:
        spots = fetch_reports(args.callsign, args.window, args.appcontact)
        status = build_status(args.callsign, spots, args.fresh)
        status["error"] = None
    except (urllib.error.URLError, ET.ParseError, ValueError, OSError) as exc:
        # network hiccup: keep prior state but mark it, don't crash the loop
        status = {
            "callsign": args.callsign.upper(),
            "on_air": False, "last_heard": 0, "last_heard_iso": None,
            "minutes_ago": None, "receiver_count": 0, "receivers": [],
            "bands": [], "modes": [], "best_snr": None, "spots": [],
            "updated": int(time.time()), "error": str(exc),
        }
    write_status(status, args.output)
    flag = "ON AIR" if status["on_air"] else "off air"
    detail = status.get("error") or (
        f"heard {status['minutes_ago']} min ago by "
        f"{status['receiver_count']} station(s) "
        f"on {', '.join(status['bands']) or '—'}"
        if status["on_air"] else "no recent spots"
    )
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {args.callsign.upper()}: "
          f"{flag} — {detail}", flush=True)
    return status


def main() -> int:
    p = argparse.ArgumentParser(description="PSKReporter-driven ON AIR light.")
    p.add_argument("-c", "--callsign",
                   default=os.environ.get("ONAIR_CALLSIGN", "N0YEP"),
                   help="your callsign (the sender to watch)")
    p.add_argument("-o", "--output",
                   default=os.environ.get("ONAIR_OUTPUT", "static/on-air.json"),
                   help="path to write the status JSON")
    p.add_argument("--window", type=int, default=30,
                   help="minutes of history to request from the API")
    p.add_argument("--fresh", type=int, default=12,
                   help="a spot newer than this many minutes => ON AIR")
    p.add_argument("--interval", type=int, default=MIN_INTERVAL,
                   help="seconds between polls in --watch mode (min 300)")
    p.add_argument("--appcontact",
                   default=os.environ.get("ONAIR_CONTACT", ""),
                   help="your email, sent to PSKReporter per their API etiquette")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="poll once and exit")
    mode.add_argument("--watch", action="store_true", help="poll forever")
    args = p.parse_args()

    if args.interval < MIN_INTERVAL:
        print(f"note: clamping interval to PSKReporter's {MIN_INTERVAL}s "
              f"minimum", file=sys.stderr)
        args.interval = MIN_INTERVAL

    if not args.watch:
        status = run_once(args)
        return 0 if status["on_air"] else 1  # exit code doubles as a signal

    print(f"Watching {args.callsign.upper()} on PSKReporter "
          f"every {args.interval}s -> {args.output}. Ctrl-C to stop.")
    try:
        while True:
            run_once(args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
