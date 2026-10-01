#!/usr/bin/env bash
# Builds the STATIC GitHub Pages demo into frontend/out. The real FastAPI backend ships to the browser (Pyodide / WebAssembly)
# together with a pre-seeded SYNTHETIC SQLite database frozen at DEMO_AS_OF. Needs the backend venv (make setup) and network
# (pip download of two small pure-Python wheels; the Pyodide runtime itself is fetched by visitors' browsers from jsDelivr).
#   PAGES_BASE_PATH  URL sub-path the site is served under (default /revise-recovery-os; use "" for a root-served site)
#   DEMO_AS_OF       the demo's frozen "today" (default: today's date)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BASE_PATH="${PAGES_BASE_PATH-/revise-recovery-os}"
AS_OF="${DEMO_AS_OF:-$(date +%F)}"
DEMO_DIR="$ROOT/frontend/public/demo"
STASH="$ROOT/.demo-stash"

[ -x backend/.venv/bin/python ] || { echo "Missing backend virtualenv. Run: make setup" >&2; exit 1; }
[ -d frontend/node_modules ] || { echo "Missing frontend dependencies. Run: make setup" >&2; exit 1; }
"$ROOT/scripts/require_disk.sh"

restore() { [ -d "$STASH/[id]" ] && mkdir -p "$ROOT/frontend/app/cohorts" && mv "$STASH/[id]" "$ROOT/frontend/app/cohorts/[id]"; rmdir "$STASH" 2>/dev/null || true; return 0; }
trap restore EXIT

echo "[pages] demo as-of date: $AS_OF   base path: '${BASE_PATH}'"
rm -rf "$DEMO_DIR/wheels" "$DEMO_DIR/app.zip" "$DEMO_DIR/demo.db" "$DEMO_DIR/manifest.json"
mkdir -p "$DEMO_DIR/wheels"

echo "[pages] 1/4 seeding the synthetic database (frozen at $AS_OF)"
( cd backend && . .venv/bin/activate && DATABASE_URL="sqlite:///../frontend/public/demo/demo.db" DEMO_TODAY="$AS_OF" python -m app.seed > /dev/null )

echo "[pages] 2/4 bundling the backend code"
python3 - "$ROOT" <<'PY'
import sys, zipfile, pathlib
root = pathlib.Path(sys.argv[1])
src = root / "backend" / "app"
out = root / "frontend" / "public" / "demo" / "app.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(src.rglob("*.py")):
        z.write(p, "app/" + p.relative_to(src).as_posix())
print(f"       {out.stat().st_size // 1024} KB")
PY

echo "[pages] 3/4 vendoring pure-Python wheels that Pyodide does not bundle"
( . backend/.venv/bin/activate && pip download pydantic-settings python-dotenv --no-deps --only-binary=:all: -q -d "$DEMO_DIR/wheels" )
WHEELS=$(ls "$DEMO_DIR/wheels" | python3 -c 'import sys,json;print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')
printf '{"as_of":"%s","built_at":"%s","wheels":%s}\n' "$AS_OF" "$(date -u +%FT%TZ)" "$WHEELS" > "$DEMO_DIR/manifest.json"
cat "$DEMO_DIR/manifest.json"

echo "[pages] 4/4 static export of the frontend"
# A static host cannot serve arbitrary /cohorts/<id> paths; the demo uses /cohort?id=<id>, so that dynamic route is set aside for this build.
mkdir -p "$STASH" && mv "$ROOT/frontend/app/cohorts/[id]" "$STASH/[id]"
( cd frontend && rm -rf out .next/types && NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH="$BASE_PATH" npx next build )
touch frontend/out/.nojekyll
echo "[pages] built frontend/out ($(du -sh frontend/out | cut -f1))"
