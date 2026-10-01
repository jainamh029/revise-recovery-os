#!/usr/bin/env bash
# Gate used by build/test targets: reports free space and refuses to run below MIN_FREE_GB (default 3, decimal GB).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIN_GB="${MIN_FREE_GB:-3}"
AVAIL_KB=$(df -k "$ROOT" | awk 'NR==2{print $4}')
AVAIL_GB=$(awk -v k="$AVAIL_KB" 'BEGIN{printf "%.2f", k*1024/1e9}')
echo "[disk] ${AVAIL_GB} GB free (minimum required: ${MIN_GB} GB)"
if awk -v a="$AVAIL_GB" -v m="$MIN_GB" 'BEGIN{exit !(a < m)}'; then
  echo "[disk] STOP: less than ${MIN_GB} GB free. Nothing was run and nothing was deleted." >&2
  echo "[disk] Project-local, reproducible artifacts you could remove (sizes via 'make disk-check'):" >&2
  echo "         frontend/.next  frontend/.next-e2e  frontend/test-results  frontend/playwright-report" >&2
  echo "         backend/.pytest_tmp  backend/.pytest_cache  .pgdata  .e2e" >&2
  echo "[disk] To remove exactly those paths, review them and run:  make clean-generated" >&2
  exit 2
fi
