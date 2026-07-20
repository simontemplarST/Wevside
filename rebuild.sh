#!/usr/bin/env bash
#
# rebuild.sh — full local rebuild of the N0YEP site.
#
# Regenerates the derived data (logbook table + QSO map) from the ADIF export,
# builds the static site with Hugo, then commits the changed data to git and
# pushes to origin (which triggers the GitHub Pages deploy).
#
# Usage:
#   ./rebuild.sh                         # rebuild, commit changed data, push
#   ./rebuild.sh path/to/log.adi         # custom ADIF
#   ./rebuild.sh path/to/log.adi FN31    # custom ADIF + home grid
#   ./rebuild.sh --serve                 # rebuild (+commit/push), then `hugo server`
#   ./rebuild.sh --no-commit             # rebuild only, leave git untouched
#   ./rebuild.sh --no-push               # commit regenerated data, but don't push
#
# Env overrides:  ADIF=... HOME_GRID=... QRZ_API_KEY=... ./rebuild.sh
#
set -euo pipefail

# --- resolve repo root (this script lives at the root) ----------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- args / config ----------------------------------------------------------
SERVE=0
DO_COMMIT=1
DO_PUSH=1
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --serve) SERVE=1 ;;
    --no-commit) DO_COMMIT=0 ;;
    --no-push) DO_PUSH=0 ;;
    -h|--help) sed -n '3,18p' "$0"; exit 0 ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

ADIF="${ADIF:-${POSITIONAL[0]:-n0yep-logbook.adi}}"
HOME_GRID="${HOME_GRID:-${POSITIONAL[1]:-EN33mq}}"

# --- helpers ----------------------------------------------------------------
say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v hugo    >/dev/null 2>&1 || die "hugo not found — install Hugo extended."
command -v python3 >/dev/null 2>&1 || die "python3 not found."
hugo version | grep -q extended    || die "Hugo 'extended' is required (for SCSS/asset pipeline)."

# --- 1. logbook data --------------------------------------------------------
# Source order: a local ADIF file if present, else a live pull from QRZ when
# QRZ_API_KEY is set (same source CI uses), else reuse the committed data.
QRZ_TMP=""
# Note: keep the final `return 0` — an EXIT trap's last status becomes the
# script's exit code, and the test alone returns 1 when no temp file was used.
cleanup() { [[ -n "$QRZ_TMP" && -f "$QRZ_TMP" ]] && rm -f "$QRZ_TMP"; return 0; }
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

# --- 3. commit + push the regenerated data ----------------------------------
# Scoped to the two files rebuild.sh owns, so unrelated work-in-progress and
# anything already staged are left alone.
if [[ "$DO_COMMIT" == "1" ]]; then
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "not a git repository — skipping commit."
  else
    DATA=()
    [[ -f data/log.yaml ]]     && DATA+=(data/log.yaml)
    [[ -f data/qso_map.json ]] && DATA+=(data/qso_map.json)

    if [[ ${#DATA[@]} -eq 0 ]]; then
      warn "no data files to commit."
    elif [[ -z "$(git status --porcelain -- "${DATA[@]}")" ]]; then
      say "No data changes — nothing to commit."
    else
      QSOS="$(grep -c '  - date:' data/log.yaml 2>/dev/null || echo '?')"
      say "Committing ${DATA[*]}"
      git add -- "${DATA[@]}"
      git commit -q -m "Rebuild: refresh logbook + map (${QSOS} QSOs)" -- "${DATA[@]}"

      if [[ "$DO_PUSH" == "1" ]]; then
        BRANCH="$(git symbolic-ref --short -q HEAD || true)"
        if [[ -z "$BRANCH" ]]; then
          warn "detached HEAD — committed locally but not pushing."
        else
          say "Pushing to origin/$BRANCH"
          if ! git push origin "$BRANCH" 2>/dev/null; then
            # Someone (e.g. the QRZ refresh bot) pushed first — rebase and retry.
            warn "push rejected; syncing with origin/$BRANCH …"
            if git pull --rebase --autostash origin "$BRANCH"; then
              git push origin "$BRANCH"
            else
              git rebase --abort >/dev/null 2>&1 || true
              die "Could not auto-sync with origin (conflict). Resolve, then 'git push'."
            fi
          fi
          say "Pushed — the Pages deploy will publish shortly."
        fi
      fi
    fi
  fi
fi

# --- 4. optional preview ----------------------------------------------------
if [[ "$SERVE" == "1" ]]; then
  say "Starting hugo server (Ctrl-C to stop)"
  exec hugo server --disableFastRender
fi
