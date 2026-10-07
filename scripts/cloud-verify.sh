#!/usr/bin/env bash
# Run from a copied project; does not install packages or require a powered board.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${AUTOHLS_PYTHON:-python3}"
mkdir -p artifacts/cloud-validation
log_dir="$(mktemp -d artifacts/cloud-validation/run-XXXXXXXX)"
{
    date -u
    uname -a
    "$python_bin" --version
    g++ --version
    df -h .
} | tee "$log_dir/environment.log"
"$python_bin" -m unittest discover -s tests -v 2>&1 | tee "$log_dir/unittest.log"
"$python_bin" -m autohls research all --backend native --budget 30 2>&1 | tee "$log_dir/native.log"
printf 'Validation logs: %s\nNative correctness only; HLS/RTL/board performance remains unverified.\n' "$log_dir"
