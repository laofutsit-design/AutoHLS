#!/usr/bin/env bash
# Build only; never connects to or programs a physical board.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
release=$PWD
variant=${1:?Specify a new output variant name}
kernel=$(realpath "${2:?Specify the selected kernel.cpp}")
[[ "$variant" =~ ^[a-z0-9-]+$ ]] || exit 2
case "$kernel" in "$release/"*) ;; *) echo 'Kernel must belong to this release' >&2; exit 2 ;; esac
work="$release/artifacts/preboard/deploy/$variant"
mkdir -p "$(dirname "$work")"
mkdir "$work"
cp "$kernel" "$work/kernel.cpp"
cd "$work"
trap 'result=$?; printf "exit_code=%s\nfinished=%s\n" "$result" "$(date -Is)" > build.status' EXIT
printf 'running\n' > build.status
g++ -std=c++14 -O0 -Wno-unknown-pragmas kernel.cpp "$release/hardware/hls/matmul_axi.cpp" \
    "$release/hardware/hls/matmul_axi_tb.cpp" -o native-check
./native-check | tee native-check.log
if bash "$release/scripts/cloud-xilinx.sh" vivado_hls -f "$release/hardware/hls/build_axi.tcl" > hls.log 2>&1; then
    printf 'HLS and IP export succeeded.\n'
else
    # Only recover the reproduced packaging metadata overflow, not HLS failures.
    grep -q 'C/RTL co-simulation finished: PASS' hls.log
    grep -q 'set_property core_revision' hls.log
    /mnt/datadisk0/autohls/venv/bin/python "$release/hardware/package_ip.py" axi_project/solution1/impl/ip
fi
bash "$release/scripts/cloud-xilinx.sh" vivado -mode batch -notrace \
    -source "$release/hardware/build_overlay.tcl" > vivado-build.log 2>&1
test -s bundle/autohls_matmul.bit
test -s bundle/autohls_matmul.hwh
printf 'Pre-board build complete: %s (board_verified=false)\n' "$work"
