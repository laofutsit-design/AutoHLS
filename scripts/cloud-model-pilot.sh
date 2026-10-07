#!/usr/bin/env bash
# Explicit bounded groups; never read historical winner metrics or access the board.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
group=${1:?Specify random, no-feedback or feedback}
case "$group" in
    random) args=(--planner random) ;;
    no-feedback) args=(--planner ollama --model autohls-qwen-coder:7b-q4km-ms9bc02b77 --no-feedback) ;;
    feedback) args=(--planner ollama --model autohls-qwen-coder:7b-q4km-ms9bc02b77) ;;
    *) echo 'Unknown pilot group' >&2; exit 2 ;;
esac
export PATH="$PWD/scripts/cloud-bin:$PATH"
mkdir -p artifacts/model-pilot
exec 9>artifacts/model-pilot/run.lock
flock -n 9 || exit 75
output="artifacts/model-pilot/$group"
[[ ! -e "$output" ]] || { echo 'Group already exists; do not overwrite or silently repeat' >&2; exit 2; }
/mnt/datadisk0/autohls/venv/bin/python -m autohls research matmul --backend hls \
    "${args[@]}" --budget 8 --goal latency --seed 0 --clock 10 \
    --limits scripts/preboard-limits.json --output-dir "$output"
