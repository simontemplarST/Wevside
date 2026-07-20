#!/usr/bin/env python3
"""
aprs-report.py — has any N0YEP-* been heard on APRS recently?

Queries the aprs.fi API for the base callsign and all SSIDs (-1..-15), finds the
most recent packet, and writes aprs.json for the site's APRS badge. Runs
server-side (GitHub Action) so the aprs.fi API key stays in a secret and never
reaches the public static site — aprs.fi's terms forbid exposing the key, and
the API sends no CORS headers anyway.

API: https://api.aprs.fi/api/get   (docs: https://aprs.fi/page/api)
aprs.fi terms: don't background-poll tightly — "most private web sites only get a
request once per few hours". This runs from .github/workflows/aprs.yml every 3 h.
Credit aprs.fi and link back (done in the site footer + APRS badge). The API key
must stay private (a GitHub secret) — never ship it to the public site.

Usage:
  APRS_API_KEY=xxxx python3 scripts/aprs-report.py -c N0YEP -o static/aprs.json
  python3 scripts/aprs-report.py -c N0YEP --window 24 --apikey xxxx
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
from datetime import datetime, timezone

API_URL = "https://api.aprs.fi/api/get"
# aprs.fi rejects requests without a descriptive User-Agent.
USER_AGENT = "n0yep-aprs-badge/1.0 (+https://github.com/simontemplarST/Wevside)"


def targets(base: str) -> str:
    """Base call + every valid APRS SSID (-1..-15), comma-separated (<=20)."""
    base = base.upper()
    return ",".join([base] + [f"{base}-{i}" for i in range(1, 16)])


def _get(params: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        f"{API_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if data.get("result") != "ok":
        raise RuntimeError(data.get("description") or data.get("code") or "aprs.fi error")
    return data


def query(base: str, apikey: str, timeout: int = 30) -> list[dict]:
    """what=loc — the last *position* aprs.fi holds for each SSID."""
    data = _get({"name": targets(base), "what": "loc",
                 "apikey": apikey, "format": "json"}, timeout)
    return data.get("entries", []) or []


def query_msg(base: str, apikey: str, timeout: int = 30) -> list[dict]:
    """what=msg — latest messages *received by* each SSID (dst-filtered, max 10
    recipients/call). Catches message/status-only stations that never beacon a
    position, so they never show up in the what=loc result."""
    names = [t.strip() for t in targets(base).split(",")]
    entries: list[dict] = []
    for i in range(0, len(names), 10):        # API caps recipients at 10 per call
        chunk = ",".join(names[i:i + 10])
        data = _get({"dst": chunk, "what": "msg",
                     "apikey": apikey, "format": "json"}, timeout)
        entries.extend(data.get("entries", []) or [])
    return entries


def build_status(base: str, entries: list[dict], window_h: int) -> dict:
    now = int(time.time())
    seen = []
    for e in entries:
        # lasttime = last time this target was heard (unix seconds, as string)
        try:
            ts = int(e.get("lasttime") or e.get("time") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts:
            seen.append((ts, e.get("name", ""), e.get("comment", "")))
    seen.sort(reverse=True)

    within = [s for s in seen if now - s[0] <= window_h * 3600]
    last_ts, last_call, last_comment = (seen[0] if seen else (0, "", ""))
    return {
        "callsign": base.upper(),
        "active": bool(within),
        "last_heard": last_ts,
        "last_heard_iso": (
            datetime.fromtimestamp(last_ts, timezone.utc).isoformat() if last_ts else None
        ),
        "last_call": last_call,
        "comment": (last_comment or "")[:80],
        "count": len(within),          # distinct SSIDs heard in the window
        "stations": [s[1] for s in within],
        "window_hours": window_h,
        "updated": now,
        "error": None,
    }


def offline(base: str, window_h: int, err: str) -> dict:
    return {
        "callsign": base.upper(), "active": False, "last_heard": 0,
        "last_heard_iso": None, "last_call": "", "comment": "", "count": 0,
        "stations": [], "window_hours": window_h, "updated": int(time.time()),
        "error": err,
    }


def write(status: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(status, fh, indent=2)
    os.replace(tmp, path)


def main() -> int:
    p = argparse.ArgumentParser(description="aprs.fi activity -> aprs.json")
    p.add_argument("-c", "--callsign",
                   default=os.environ.get("APRS_CALLSIGN", "N0YEP"))
    p.add_argument("-o", "--output",
                   default=os.environ.get("APRS_OUTPUT", "static/aprs.json"))
    p.add_argument("--window", type=int,
                   default=int(os.environ.get("APRS_WINDOW_HOURS", "24")),
                   help="hours to count as 'active' (default 24)")
    p.add_argument("--apikey", default=os.environ.get("APRS_API_KEY", ""))
    p.add_argument("-v", "--verbose", action="store_true",
                   help="log each SSID's last-heard time (goes to stderr, no key exposed)")
    args = p.parse_args()

    if not args.apikey:
        # No key configured — write an inactive state so the badge just stays dark.
        write(offline(args.callsign, args.window, "no api key"), args.output)
        print("aprs-report: no APRS_API_KEY set — wrote inactive status.",
              file=sys.stderr)
        return 0

    try:
        entries = query(args.callsign, args.apikey)
        status = build_status(args.callsign, entries, args.window)
        if args.verbose:
            now = time.time()
            print("  per-SSID last-heard (as returned by aprs.fi):", file=sys.stderr)
            if not entries:
                print("    (none — no SSID has a stored position on aprs.fi)",
                      file=sys.stderr)
            for e in sorted(entries, key=lambda x: int(x.get("lasttime") or 0),
                            reverse=True):
                ts = int(e.get("lasttime") or 0)
                age = f"{round((now - ts) / 3600, 1)}h ago" if ts else "never"
                print(f"    {e.get('name',''):<10} {age:<12} "
                      f"{(e.get('comment') or '')[:40]}", file=sys.stderr)
            # DIAGNOSTIC: dump the raw what=msg result so we can see how aprs.fi
            # reports message/status-only SSIDs (e.g. N0YEP-1 with no position).
            try:
                msgs = query_msg(args.callsign, args.apikey)
                print(f"  what=msg returned {len(msgs)} message(s):", file=sys.stderr)
                for m in msgs[:20]:
                    mt = int(m.get("time") or 0)
                    mage = f"{round((now - mt) / 3600, 1)}h ago" if mt else "?"
                    print(f"    src={m.get('srccall','')!r:14} dst={m.get('dst','')!r:14}"
                          f" {mage:<10} msg={m.get('message','')[:40]!r}",
                          file=sys.stderr)
            except Exception as mexc:  # diagnostic only — never fail the run
                print(f"  what=msg diagnostic failed: {mexc}", file=sys.stderr)
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as exc:
        status = offline(args.callsign, args.window, str(exc))

    write(status, args.output)
    flag = "ACTIVE" if status["active"] else "quiet"
    detail = status["error"] or (
        f"{status['last_call']} heard {round((time.time()-status['last_heard'])/3600,1)}h ago"
        if status["last_heard"] else "no packets found"
    )
    print(f"[{datetime.now():%H:%M:%S}] {args.callsign.upper()} APRS: {flag} — {detail}")
    return 0 if status["active"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
