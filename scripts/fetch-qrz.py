#!/usr/bin/env python3
"""
fetch-qrz.py — download the full QRZ logbook as ADIF via the QRZ Logbook API.

Server-side only. The QRZ Logbook API key is a paid-subscription credential and
the API sends no CORS headers, so this runs in CI (GitHub Action) with the key
kept in a secret. It writes a local .adi (gitignored — the raw export contains
third-party names/emails); scripts/import-adif.py and scripts/build-qso-map.py
then turn that into the sanitized site data (data/log.yaml, data/qso_map.json).

API:  https://logbook.qrz.com/api   (docs: https://www.qrz.com/docs/logbook30/api)
The API returns one big url-encoded key=value response; the log is in the ADIF
field. FETCH is paged with AFTERLOGID (each record carries an APP_QRZLOG_LOGID),
so we loop until a page comes back empty.

Usage:
  QRZ_API_KEY=xxxx python3 scripts/fetch-qrz.py -o qrz-logbook.adi
  python3 scripts/fetch-qrz.py --key xxxx -o qrz-logbook.adi
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://logbook.qrz.com/api"
# QRZ likes a descriptive User-Agent; identify the app + a contact URL.
USER_AGENT = "n0yep-logbook/1.0 (+https://github.com/simontemplarST/Wevside)"

# Each QRZ record carries <APP_QRZLOG_LOGID:len>value — the numeric value is the
# paging cursor. Grab the digits after the '>' (logid is always numeric).
_LOGID_RE = re.compile(r"<APP_QRZLOG_LOGID:\d+>(\d+)", re.IGNORECASE)
_EOH_RE = re.compile(r"<eoh>", re.IGNORECASE)

PAGE = 250  # records per FETCH; well under any server cap, keeps calls modest


def _post(params: dict, timeout: int = 60) -> dict:
    """POST to the QRZ API and split the url-encoded key=value response.

    Values are percent-encoded, so the ADIF's own '&'/'=' are safe to split on.
    """
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    fields: dict[str, str] = {}
    for pair in raw.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            fields[k.strip().upper()] = urllib.parse.unquote_plus(v)
    return fields


def _records_only(adif: str) -> str:
    """Drop any ADIF header (everything up to and including <EOH>)."""
    m = _EOH_RE.search(adif)
    return adif[m.end():] if m else adif


def fetch_all(key: str) -> tuple[str, int]:
    """Return (combined ADIF record text, record count). Pages via AFTERLOGID."""
    bodies: list[str] = []
    total = 0
    after = 0
    while True:
        option = f"MAX:{PAGE}"
        if after:
            option += f",AFTERLOGID:{after}"
        fields = _post({"KEY": key, "ACTION": "FETCH", "OPTION": option})

        result = (fields.get("RESULT") or "").upper()
        if result != "OK":
            reason = (fields.get("REASON") or fields.get("STATUS") or "").strip()
            # "no records"/empty is a normal end-of-paging signal, not an error.
            if not after and reason:
                raise RuntimeError(f"QRZ FETCH failed: {reason}")
            break

        adif = fields.get("ADIF", "") or ""
        count = int(fields.get("COUNT") or 0)
        if count == 0 or not adif.strip():
            break

        bodies.append(_records_only(adif).strip())
        total += count

        logids = [int(x) for x in _LOGID_RE.findall(adif)]
        nxt = max(logids) if logids else 0
        if nxt <= after:
            # No usable cursor (or no progress) — stop rather than loop forever.
            break
        after = nxt
        if count < PAGE:  # last (partial) page
            break

    header = "ADIF export via scripts/fetch-qrz.py\n<ADIF_VER:5>3.1.4\n<EOH>\n"
    return header + "\n".join(b for b in bodies if b) + "\n", total


def main() -> int:
    p = argparse.ArgumentParser(description="Download QRZ logbook -> ADIF file.")
    p.add_argument("-o", "--output", default="qrz-logbook.adi",
                   help="destination .adi path (default: qrz-logbook.adi)")
    p.add_argument("--key", default=os.environ.get("QRZ_API_KEY", ""),
                   help="QRZ Logbook API key (or set QRZ_API_KEY)")
    args = p.parse_args()

    if not args.key:
        print("fetch-qrz: no QRZ_API_KEY / --key set — nothing to fetch.",
              file=sys.stderr)
        return 2  # distinct from a network/API failure so callers can fall back

    try:
        adif, count = fetch_all(args.key)
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as exc:
        print(f"fetch-qrz: {exc}", file=sys.stderr)
        return 1

    if count == 0:
        print("fetch-qrz: QRZ returned no records.", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(adif)
    print(f"fetch-qrz: wrote {count} QSO(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
