#!/usr/bin/env bash
# Offline build only; never connects to or programs the physical board.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
release=$PWD
work="$release/artifacts/hardware-batch/deploy/optimized-batch32"
mkdir -p "$(dirname "$work")"
mkdir "$work"
cp artifacts/resetfix-20260926/bundle/optimized32/kernel.cpp "$work/kernel.cpp"
cd "$work"
trap 'result=$?; printf "exit_code=%s\nfinished=%s\n" "$result" "$(date -Is)" > "$work/build.status"' EXIT
printf 'running\n' > build.status
g++ -std=c++14 -O1 -g -Wno-unknown-pragmas -fsanitize=address,undefined \
    -fno-sanitize-recover=all -DAUTOHLS_NATIVE_NULL_CHECKS kernel.cpp \
    "$release/hardware/hls/matmul_batch_axi.cpp" "$release/hardware/hls/matmul_batch_axi_tb.cpp" -o native-check
./native-check | tee native-check.log
if bash "$release/scripts/cloud-xilinx.sh" vivado_hls -f "$release/hardware/hls/build_batch_axi.tcl" > hls.log 2>&1; then
    printf 'HLS and IP export succeeded.\n'
else
    grep -q 'C/RTL co-simulation finished: PASS' hls.log
    grep -q 'set_property core_revision' hls.log
    /mnt/datadisk0/autohls/venv/bin/python "$release/hardware/package_ip.py" axi_project/solution1/impl/ip
fi
bash "$release/scripts/cloud-xilinx.sh" vivado -mode batch -notrace \
    -source "$release/hardware/build_overlay.tcl" > vivado-build.log 2>&1
test -s bundle/autohls_matmul.bit
test -s bundle/autohls_matmul.hwh
cd "$release"
/mnt/datadisk0/autohls/venv/bin/python -m hardware.package_batch \
    "$work" "$release/artifacts/hardware-batch/distribution"
printf 'HARDWARE_BATCH_OFFLINE_BUILD_COMPLETE; board_verified=false\n'
