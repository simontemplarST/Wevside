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
- **Live logbook from QRZ** — a scheduled GitHub Action pulls the log straight from
  the [QRZ Logbook API](https://www.qrz.com/docs/logbook30/api) and rebuilds the
  logbook + map along with the site (see [Logbook source](#logbook-source)).
- **ADIF importer** — `scripts/import-adif.py` converts any ADIF log (QRZ, LoTW,
  WSJT-X, …) into `data/log.yaml`, scrubbing emails/URLs/phones from free text.

## Writing blog posts

`./new-post.sh` opens a local terminal editor (a curses TUI, no browser) for composing
a post — a front-matter form (title / hobby / summary / draft) plus a built-in
scrollable Markdown editor:

```bash
./new-post.sh                 # start a new post
./new-post.sh --title "…"     # pre-fill the title
./new-post.sh --resume        # resume an unsaved recovery draft
```

Keys: `Tab` moves between fields, arrows move in the body, `^E` opens the body in your
`$EDITOR`, `^P` saves a draft so a running `hugo server` previews it at
`localhost:1313/blog/<slug>/`, `^O` **finishes** — writes `content/blog/<slug>.md`,
builds with Hugo, commits to git, and offers to push (which triggers the deploy). `^X`
quits and offers to save a recovery draft.

## Logbook source

The logbook is pulled from the **QRZ Logbook API**, server-side, entirely on GitHub.
A dedicated workflow ([`.github/workflows/qrz-logbook.yml`](.github/workflows/qrz-logbook.yml))
runs **every 3 hours (and on each commit)**: it fetches the full log as ADIF with `scripts/fetch-qrz.py`,
regenerates `data/log.yaml` + `data/qso_map.json`, and commits them only when the log
actually changes — that commit triggers the normal deploy, so the logbook and map
rebuild along with the site.

The QRZ API needs a paid subscription and sends no CORS headers, so the key stays in a
GitHub **secret** and is only ever used from the Action; the raw ADIF (which carries
third-party names/emails) is fetched to a temp file and deleted — never committed. Only
the sanitized `data/log.yaml` (call / date / band / mode / RST / QTH) is.

To enable it, add in the repo's GitHub settings:

- **Secret** `QRZ_API_KEY` — from QRZ → *Logbook* → *Settings* → *API access* (needs an
  XML/logbook data subscription).
- **Variable** `HOME_GRID` — optional, your home Maidenhead grid (defaults to `EN33mq`).

Without the secret the workflow no-ops and the committed logbook is left untouched.

## Rebuild locally

`./rebuild.sh` regenerates the derived data (logbook + map), builds the site, then
**commits the changed data and pushes to origin** (which triggers the Pages deploy). It
uses a local ADIF file if present, else pulls from QRZ when `QRZ_API_KEY` is set, else
reuses the committed data:

```bash
./rebuild.sh                       # rebuild, commit changed data, push
QRZ_API_KEY=xxxx ./rebuild.sh      # force a fresh pull from the QRZ API
./rebuild.sh your-log.adi FN31     # custom ADIF + home grid
./rebuild.sh --serve               # rebuild (+commit/push), then `hugo server`
./rebuild.sh --no-commit           # rebuild only, leave git untouched
./rebuild.sh --no-push             # commit regenerated data, but don't push
```

The commit is scoped to `data/log.yaml` + `data/qso_map.json` (so unrelated
work-in-progress is left alone) and only happens when they actually change. If the push
is rejected because the QRZ refresh bot pushed first, it rebases and retries
automatically.

Or run the steps individually:

```bash
QRZ_API_KEY=xxxx python3 scripts/fetch-qrz.py -o log.adi       # pull from QRZ
python3 scripts/import-adif.py log.adi                         # logbook table
python3 scripts/build-qso-map.py log.adi --home EN33mq         # map data
hugo --gc --minify                                             # build into public/
```

Raw `.adi` exports are gitignored — they contain third-party names/emails.

## On-air light

The badge reads `on-air.json` and lights red when the callsign was spotted within
`onAirFreshMin` minutes. Everything stays on GitHub: the
[deploy workflow](.github/workflows/hugo.yml) runs **every 3 hours and on each commit**
and regenerates `on-air.json` from PSKReporter (via `scripts/on-air-light.py`) into each
published build — no external services, no commit churn.

> **Note:** because the deploy only refreshes on that 3-hour cadence, `on-air.json` is a
> snapshot from the last build, not a live feed — the badge reflects PSKReporter activity
> as of the most recent deploy. To make the light genuinely live again, give this workflow
> a tighter `schedule` (e.g. `*/15 * * * *`) or run `scripts/on-air-light.py --watch`
> locally against the published `on-air.json`.

Optional GitHub settings:

- **Variable** `ONAIR_CALLSIGN` — override the watched callsign (defaults to `N0YEP`).
- **Secret** `PSK_CONTACT` — your email, sent to PSKReporter as API etiquette.

## APRS badge

A second header badge lights when any `N0YEP-*` (base call plus SSIDs −1…−15) was
heard on [aprs.fi](https://aprs.fi/) within the last 24 hours, and links to the
station's aprs.fi track page.

A **separate** workflow ([`.github/workflows/aprs.yml`](.github/workflows/aprs.yml))
runs `scripts/aprs-report.py` **every 3 hours (and on each commit)** and commits
`static/aprs.json` only when the status changes (the next deploy publishes it). This
cadence respects aprs.fi's terms — they ask applications not to background-poll tightly
("most private web sites only get a request once per few hours"); a commit-triggered run
is occasional and user-initiated, not a loop. The API key stays in a GitHub **secret** and
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
