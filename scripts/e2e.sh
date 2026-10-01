#!/usr/bin/env bash
# Chromium end-to-end suite against a SELF-CONTAINED API + database.
#   scripts/e2e.sh sqlite                      -> temporary SQLite file under ./.e2e
#   DATABASE_URL=postgresql+psycopg://... scripts/e2e.sh postgres   -> an already-running PostgreSQL (see scripts/pg_verify.sh)
# The API runs with ALLOW_RESEED=true so every test can restore the pristine synthetic dataset. Cleans up on exit, even on failure.
set -euo pipefail
MODE="${1:-sqlite}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${E2E_API_PORT:-8013}"

[ -x backend/.venv/bin/python ] || { echo "Missing backend virtualenv. Run: make setup" >&2; exit 1; }
[ -d frontend/node_modules ] || { echo "Missing frontend dependencies. Run: make setup" >&2; exit 1; }
"$ROOT/scripts/require_disk.sh"

case "$MODE" in
  sqlite)   mkdir -p .e2e; export DATABASE_URL="sqlite:///../.e2e/e2e.db" ;;   # relative to backend/ (avoids spaces in paths)
  postgres) [ -n "${DATABASE_URL:-}" ] || { echo "postgres mode needs DATABASE_URL (use 'make e2e-postgres')" >&2; exit 1; } ;;
  *) echo "usage: $0 sqlite|postgres" >&2; exit 1 ;;
esac
export ALLOW_RESEED=true

API_PID=""
cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && wait "$API_PID" 2>/dev/null || true
  [ "$MODE" = "sqlite" ] && rm -rf "$ROOT/.e2e"
  return 0
}
trap cleanup EXIT

(
  cd backend
  . .venv/bin/activate
  python -m app.seed > /dev/null
  mkdir -p "$ROOT/.e2e"
  exec uvicorn app.main:app --port "$PORT" > "$ROOT/.e2e/api.log" 2>&1
) &
API_PID=$!

for _ in $(seq 1 60); do
  curl -fs "http://127.0.0.1:$PORT/health" > /dev/null 2>&1 && break
  kill -0 "$API_PID" 2>/dev/null || { echo "API failed to start:" >&2; tail -20 "$ROOT/.e2e/api.log" >&2; exit 1; }
  sleep 1
done
curl -fs "http://127.0.0.1:$PORT/health" | grep -q '"database":"ok"' || { echo "API health check failed" >&2; exit 1; }
echo "[e2e] API healthy on :$PORT ($MODE); running Chromium suite"

cd frontend
API_URL="http://127.0.0.1:$PORT" npx playwright test
