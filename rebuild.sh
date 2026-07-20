#!/usr/bin/env bash
#
# rebuild.sh — full local rebuild of the N0YEP site.
#
# Regenerates the derived data (logbook table + QSO map) from the ADIF export,
# then builds the static site with Hugo.
#
# Usage:
#   ./rebuild.sh                         # use defaults below
#   ./rebuild.sh path/to/log.adi         # custom ADIF
#   ./rebuild.sh path/to/log.adi FN31    # custom ADIF + home grid
#   ./rebuild.sh --serve                 # rebuild, then `hugo server`
#
# Env overrides:  ADIF=... HOME_GRID=... ./rebuild.sh
#
set -euo pipefail

# --- resolve repo root (this script lives at the root) ----------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- args / config ----------------------------------------------------------
SERVE=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --serve) SERVE=1 ;;
    -h|--help) sed -n '3,17p' "$0"; exit 0 ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

ADIF="${ADIF:-${POSITIONAL[0]:-n0yep-logbook.adi}}"
HOME_GRID="${HOME_GRID:-${POSITIONAL[1]:-EN33mq}}"

# --- helpers ----------------------------------------------------------------
say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v hugo    >/dev/null 2>&1 || die "hugo not found — install Hugo extended."
command -v python3 >/dev/null 2>&1 || die "python3 not found."
hugo version | grep -q extended    || die "Hugo 'extended' is required (for SCSS/asset pipeline)."

# --- 1. logbook data --------------------------------------------------------
# Source order: a local ADIF file if present, else a live pull from QRZ when
# QRZ_API_KEY is set (same source CI uses), else reuse the committed data.
QRZ_TMP=""
cleanup() { [[ -n "$QRZ_TMP" && -f "$QRZ_TMP" ]] && rm -f "$QRZ_TMP"; }
trap cleanup EXIT

if [[ ! -f "$ADIF" && -n "${QRZ_API_KEY:-}" ]]; then
  say "Fetching logbook from QRZ API -> temporary ADIF"
  QRZ_TMP="$(mktemp -t qrz-logbook.XXXXXX.adi)"
  if python3 scripts/fetch-qrz.py -o "$QRZ_TMP"; then
    ADIF="$QRZ_TMP"
  else
    die "QRZ fetch failed — set a valid QRZ_API_KEY or pass a local .adi path."
  fi
fi

if [[ -f "$ADIF" ]]; then
  say "Importing logbook from $ADIF -> data/log.yaml"
  python3 scripts/import-adif.py "$ADIF" -o data/log.yaml

  say "Building QSO map from $ADIF -> data/qso_map.json (home $HOME_GRID)"
  python3 scripts/build-qso-map.py "$ADIF" --home "$HOME_GRID"
else
  printf '\033[33m! No ADIF at "%s" and no QRZ_API_KEY — reusing existing data/log.yaml and data/qso_map.json.\033[0m\n' "$ADIF"
  printf '  Pass a path (./rebuild.sh your-log.adi) or set QRZ_API_KEY to regenerate them.\n'
fi

# --- 2. static site ---------------------------------------------------------
say "Building site with Hugo"
rm -rf public
hugo --gc --minify

say "Done."
printf '  Output: %s/public\n' "$ROOT"
if [[ -f data/log.yaml ]]; then
  printf '  QSOs:   %s\n' "$(grep -c '  - date:' data/log.yaml || echo '?')"
fi
if [[ -f data/qso_map.json ]]; then
  printf '  Map:    %s locations\n' "$(python3 -c "import json;print(json.load(open('data/qso_map.json'))['stats']['locations'])" 2>/dev/null || echo '?')"
fi

# --- 3. optional preview ----------------------------------------------------
if [[ "$SERVE" == "1" ]]; then
  say "Starting hugo server (Ctrl-C to stop)"
  exec hugo server --disableFastRender
fi
