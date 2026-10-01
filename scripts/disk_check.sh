#!/usr/bin/env bash
# Reports free disk space and the size of project-local generated artifacts. Deletes NOTHING.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
AVAIL_KB=$(df -k "$ROOT" | awk 'NR==2{print $4}')
echo "Free space on the project volume: $(awk -v k="$AVAIL_KB" 'BEGIN{printf "%.2f GB (decimal) / %.2f GiB", k*1024/1e9, k/1048576}')"
echo
echo "Project-local generated artifacts (reproducible; nothing is deleted by this command):"
for p in frontend/.next frontend/.next-e2e frontend/test-results frontend/playwright-report frontend/coverage \
         backend/.pytest_tmp backend/.pytest_cache backend/.ruff_cache .pgdata .e2e; do
  if [ -e "$p" ]; then printf '  %-30s %s\n' "$p" "$(du -sh "$p" | cut -f1)"; else printf '  %-30s (absent)\n' "$p"; fi
done
echo
echo "Project-local dependencies / data (NOT generated artifacts; remove only if you want a full reinstall):"
for p in frontend/node_modules backend/.venv backend/recovery_os.db; do
  if [ -e "$p" ]; then printf '  %-30s %s\n' "$p" "$(du -sh "$p" | cut -f1)"; fi
done
echo
printf 'Project total: %s\n' "$(du -sh . | cut -f1)"
