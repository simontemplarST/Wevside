# N0YEP — Amateur Radio Station Site

Personal ham radio site for **N0YEP** (grid EN33), built with [Hugo](https://gohugo.io/)
and a custom dark **wireframe/blueprint** theme — graphite greys, monospace, boxed
sections, and one red accent reserved for the on-air light.

## Features

- **Live ON AIR badge** — a scheduled GitHub Action polls
  [PSKReporter](https://pskreporter.info/) and the header badge lights red when the
  callsign was spotted in the last ~12 minutes (see [On-air light](#on-air-light)).
- **APRS badge** — a second badge lights when any `N0YEP-*` was heard on
  [aprs.fi](https://aprs.fi/) in the last 24 h (see [APRS badge](#aprs-badge)).
- **Searchable, paged logbook** — full QSO log from `data/log.yaml` with client-side
  search (callsign / QTH / note / date), band/mode filters, and 50-per-page paging.
  Click any callsign for a virtual QSL card.
- **QSO map** — inline SVG world map of every worked station, with zoom/pan,
  color-by-band and great-circle-path toggles, and continent/entity breakdowns.
  Click a point for that location's QSL card.
- **ADIF importer** — `scripts/import-adif.py` converts any ADIF log (QRZ, LoTW,
  WSJT-X, …) into `data/log.yaml`, scrubbing emails/URLs/phones from free text.

## Rebuild locally

`./rebuild.sh` regenerates the derived data (logbook + map) from the ADIF and builds
the site:

```bash
./rebuild.sh                    # defaults: n0yep-logbook.adi, home grid EN33mq
./rebuild.sh your-log.adi FN31  # custom ADIF + home grid
./rebuild.sh --serve            # rebuild, then start `hugo server`
```

Or run the steps individually:

```bash
python3 scripts/import-adif.py your-log.adi                  # logbook table
python3 scripts/build-qso-map.py your-log.adi --home EN33mq  # map data
hugo --gc --minify                                           # build into public/
```

Raw `.adi` exports are gitignored — they contain third-party names/emails. Only the
sanitized `data/log.yaml` (call / date / band / mode / RST / QTH) is committed.

## On-air light

The badge reads `on-air.json` and lights red when the callsign was spotted within
`onAirFreshMin` minutes. Everything stays on GitHub: the
[deploy workflow](.github/workflows/hugo.yml) runs on a **15-minute schedule** and
regenerates `on-air.json` from PSKReporter (via `scripts/on-air-light.py`) into each
published build — no external services, no commit churn. If the schedule stalls, the
file goes stale after 20 minutes and the badge dims.

Optional GitHub settings:

- **Variable** `ONAIR_CALLSIGN` — override the watched callsign (defaults to `N0YEP`).
- **Secret** `PSK_CONTACT` — your email, sent to PSKReporter as API etiquette.

## APRS badge

A second header badge lights when any `N0YEP-*` (base call plus SSIDs −1…−15) was
heard on [aprs.fi](https://aprs.fi/) within the last 24 hours, and links to the
station's aprs.fi track page.

A **separate** workflow ([`.github/workflows/aprs.yml`](.github/workflows/aprs.yml))
runs `scripts/aprs-report.py` **every 3 hours** and commits `static/aprs.json` only
when the status changes (the commit publishes via the normal deploy). This cadence
respects aprs.fi's terms — they ask applications not to background-poll tightly
("most private web sites only get a request once per few hours"), so this is kept
off the 15-minute on-air deploy cron. The API key stays in a GitHub **secret** and
is only used server-side — aprs.fi's terms forbid exposing it (and the API sends no
CORS headers anyway). Attribution + link back to aprs.fi are in the footer and on
the badge, as their terms require.

To enable it, add in the repo's GitHub settings:

- **Secret** `APRS_API_KEY` — from aprs.fi → *My account* → *API key* (free).
- **Variable** `APRS_WINDOW_HOURS` — optional, defaults to `24`.

Without the secret the badge simply stays dark ("APRS Quiet").

You can also run the poller locally (handy for driving a physical light):

```bash
python3 scripts/on-air-light.py --watch -c N0YEP --appcontact you@example.com
```

See [scripts/README.md](scripts/README.md) for poller details and PSKReporter
rate-limit etiquette (max one query per 5 minutes).

---

73 de N0YEP
