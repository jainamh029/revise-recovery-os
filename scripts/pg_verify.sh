#!/usr/bin/env bash
# Isolated PostgreSQL 16 verification. Everything lives in ./.pgdata (project-local, gitignored) and is removed on exit,
# on success or failure. No remote service, credentials or hidden steps.
#   1 initdb + start a private cluster on 127.0.0.1:$PG_PORT (socket inside .pgdata, not /tmp)
#   2 alembic upgrade head  +  alembic check (migrations match the models)
#   3 backend test suite against PostgreSQL
#   4 Chromium E2E suite against an API backed by PostgreSQL
# Prerequisite: PostgreSQL 16 server binaries (initdb, pg_ctl, createdb) on PATH or in a Homebrew keg:  brew install postgresql@16
set -euo pipefail
PART="${1:-all}"   # all = migrations + backend tests + Chromium;  e2e = Chromium only
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PGDATA="$ROOT/.pgdata"
PG_PORT="${PG_PORT:-55433}"

[ -x backend/.venv/bin/python ] || { echo "Missing backend virtualenv. Run: make setup" >&2; exit 1; }
for d in /opt/homebrew/opt/postgresql@16/bin /usr/local/opt/postgresql@16/bin /usr/lib/postgresql/16/bin; do
  [ -x "$d/initdb" ] && export PATH="$d:$PATH"
done
command -v initdb >/dev/null && command -v pg_ctl >/dev/null || {
  echo "PostgreSQL 16 not found (need initdb and pg_ctl). Install it (macOS: brew install postgresql@16) and re-run." >&2; exit 1; }
VER="$(initdb --version | grep -Eo '[0-9]+' | head -1)"
[ "$VER" = "16" ] || { echo "PostgreSQL 16 required, found major version $VER. Put postgresql@16 first on PATH." >&2; exit 1; }
"$ROOT/scripts/require_disk.sh"

cleanup() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ -f "$PGDATA/server.log" ]; then
    echo "[pg] FAILED (exit $rc). Last lines of the PostgreSQL server log:" >&2
    tail -15 "$PGDATA/server.log" >&2 || true
  fi
  pg_ctl -D "$PGDATA" stop -m immediate > /dev/null 2>&1 || true
  rm -rf "$PGDATA" "$ROOT/.e2e"
  echo "[pg] temporary cluster removed"
}
trap cleanup EXIT

export LC_ALL="${LC_ALL:-en_US.UTF-8}"
rm -rf "$PGDATA"
initdb -D "$PGDATA" -U postgres --auth=trust -E UTF8 --locale=C > /dev/null
# The project path may contain spaces, so the socket directory is quoted INSIDE the -o string.
pg_ctl -D "$PGDATA" -o "-p $PG_PORT -k '$PGDATA' -c listen_addresses=127.0.0.1" -l "$PGDATA/server.log" -w start > /dev/null
createdb -h 127.0.0.1 -p "$PG_PORT" -U postgres recovery_os
createdb -h 127.0.0.1 -p "$PG_PORT" -U postgres recovery_os_test
echo "[pg] $(postgres --version 2>/dev/null || echo PostgreSQL 16) running on 127.0.0.1:$PG_PORT"

URL="postgresql+psycopg://postgres@127.0.0.1:$PG_PORT/recovery_os"
TEST_URL="postgresql+psycopg://postgres@127.0.0.1:$PG_PORT/recovery_os_test"
(
  cd backend
  . .venv/bin/activate
  export DATABASE_URL="$URL"
  echo "[pg] alembic upgrade head"; alembic upgrade head
  echo "[pg] alembic check (models vs migrations)"; alembic check
  if [ "$PART" = "all" ]; then
    echo "[pg] backend tests on PostgreSQL"
    TEST_DATABASE_URL="$TEST_URL" python -m pytest -q
  fi
)
echo "[pg] Chromium E2E on PostgreSQL"
DATABASE_URL="$URL" "$ROOT/scripts/e2e.sh" postgres
echo "[pg] PostgreSQL verification PASSED"
