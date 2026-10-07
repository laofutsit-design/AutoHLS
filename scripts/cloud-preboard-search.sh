#!/usr/bin/env bash
# Serial, bounded sweep: 3 reviewed kernels x 30 candidates. No board access.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/scripts/cloud-bin:$PATH"
python_bin=/mnt/datadisk0/autohls/venv/bin/python
mkdir -p artifacts/preboard
status=artifacts/preboard/search.status
exec 9>artifacts/preboard/search.lock
flock -n 9 || exit 75
trap 'result=$?; printf "exit_code=%s\nfinished=%s\n" "$result" "$(date -Is)" > "$status"' EXIT
printf 'running\nstarted=%s\n' "$(date -Is)" > "$status"
"$python_bin" -m autohls research all --backend hls --planner exhaustive \
    --budget 30 --goal latency --limits scripts/preboard-limits.json \
    --output-dir artifacts/preboard/search
