# Scripts

- **`fetch-qrz.py`** — downloads the full logbook from the QRZ Logbook API as ADIF
  (`QRZ_API_KEY=xxxx python3 scripts/fetch-qrz.py -o log.adi`). Server-side only —
  the key is a subscription credential and the API has no CORS. Pages through the log
  with `AFTERLOGID`; writes a temp `.adi` that `import-adif.py` / `build-qso-map.py`
  turn into the sanitized site data. See the repo README's *Logbook source* section.
- **`import-adif.py`** — ADIF → `data/log.yaml` (scrubs emails/URLs/phones).
- **`build-qso-map.py`** — ADIF → `data/qso_map.json` (positions + great-circle paths).
- **`aprs-report.py`** — aprs.fi activity → `static/aprs.json` for the APRS badge.
- **`on-air-light.py`** — PSKReporter spots → `on-air.json` for the ON AIR badge (below).

# On-Air Light (PSKReporter)

`on-air-light.py` polls the [PSKReporter](https://pskreporter.info/) retrieval
API for stations that have **heard your callsign** on a digital mode, and writes
`on-air.json`. The site header reads that file and lights an **ON AIR** badge
when you've been spotted recently.

Because PSKReporter only records a spot when you transmit a digimode (FT8, FT4,
JS8, PSK31, …) and someone decodes you, a fresh spot is a solid proxy for
"currently transmitting."

## Run it

```bash
# one-shot (exit code 0 = on air, 1 = off air)
python3 scripts/on-air-light.py --once -c N0YEP

# poll forever, writing into the live site so the badge updates itself
python3 scripts/on-air-light.py --watch -c N0YEP \
    --appcontact you@example.com \
    -o /path/to/published/site/on-air.json
```

For local dev with `hugo server`, the default output `static/on-air.json` is
watched and live-reloaded. In production, point `-o` at the **published** site
directory (e.g. `public/on-air.json`) so the badge updates without a rebuild.

## Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `-c, --callsign` | `N0YEP` | Your callsign (the sender to watch). |
| `-o, --output` | `static/on-air.json` | Where to write the status file. |
| `--fresh` | `12` | A spot newer than N minutes ⇒ **ON AIR**. |
| `--window` | `30` | Minutes of history to request. |
| `--interval` | `300` | Seconds between polls in `--watch` (min 300). |
| `--appcontact` | – | Your email (PSKReporter API etiquette). |
| `--once` / `--watch` | – | Poll once, or poll on a loop. |

Env vars `ONAIR_CALLSIGN`, `ONAIR_OUTPUT`, `ONAIR_CONTACT` are also honored.

> **Rate limit:** PSKReporter asks for **no more than one query every 5 minutes**
> per IP, and will return HTTP 429/503 (`too many queries`) if you exceed it. The
> script clamps `--interval` to 300 s for this reason — don't lower it.

## API reference

- Endpoint: `https://retrieve.pskreporter.info/query`
- Key params: `senderCallsign`, `flowStartSeconds` (negative window),
  `rronly=1`, `appcontact`
- Response: XML with `<receptionReport>` elements
  (`receiverCallsign`, `receiverLocator`, `frequency`, `flowStartSeconds`,
  `mode`, `sNR`)
- Docs: <https://pskreporter.info/pskdev.html>

## Driving a physical light

The one-shot exit code makes hardware easy to wire up:

```bash
# e.g. toggle a smart plug / GPIO relay from the exit code
python3 scripts/on-air-light.py --once -c N0YEP && plug on || plug off
```
