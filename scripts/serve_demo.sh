#!/usr/bin/env bash
# Serves frontend/out the way GitHub Pages will: under /revise-recovery-os/ . Usage: scripts/serve_demo.sh [port]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-4173}"
[ -d "$ROOT/frontend/out" ] || { echo "frontend/out is missing. Run: scripts/build_pages.sh" >&2; exit 1; }
rm -rf "$ROOT/.demo-site" && mkdir -p "$ROOT/.demo-site"
ln -s "$ROOT/frontend/out" "$ROOT/.demo-site/revise-recovery-os"
echo "Demo at http://localhost:$PORT/revise-recovery-os/"
exec python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$ROOT/.demo-site"
