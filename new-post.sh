#!/usr/bin/env bash
#
# new-post.sh — launch the terminal blog-post editor (scripts/new-post.py).
#
#   ./new-post.sh                 # start a new post
#   ./new-post.sh --title "…"     # pre-fill the title
#   ./new-post.sh --resume        # resume an unsaved recovery draft
#
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$ROOT/scripts/new-post.py" "$@"
