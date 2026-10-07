#!/usr/bin/env bash
# Rebuild only the block design; reuse the unchanged, previously verified HLS IP.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
release=$PWD
old=/mnt/datadisk0/autohls/releases/autohls-20260923-preboard-v2
[[ "$release" == /mnt/datadisk0/autohls/releases/autohls-20260926-resetfix ]] || exit 2
test -d "$old/artifacts/preboard/deploy/baseline32"
mkdir -p artifacts/preboard
mkdir artifacts/preboard/reset-simulation
cd artifacts/preboard/reset-simulation
trap 'result=$?; printf "exit_code=%s\nfinished=%s\n" "$result" "$(date -Is)" > "$release/artifacts/preboard/resetfix.status"' EXIT
bash "$release/scripts/cloud-xilinx.sh" vivado -mode batch -notrace \
    -source "$release/hardware/sim/check_reset.tcl" > reset-sim.log 2>&1
grep -q 'RESET_POLARITY_REGRESSION_PASS' reset-sim.log
printf 'Vendor reset IP regression passed.\n'

for variant in baseline32 optimized32; do
    previous="$old/artifacts/preboard/deploy/$variant"
    work="$release/artifacts/preboard/deploy/$variant"
    mkdir -p "$(dirname "$work")"
    mkdir "$work"
    mkdir -p "$work/axi_project/solution1/impl" "$work/axi_project/solution1/syn/report" \
        "$work/axi_project/solution1/sim/report"
    cp -a "$previous/axi_project/solution1/impl/ip" "$work/axi_project/solution1/impl/"
    cp "$previous/axi_project/solution1/syn/report/matmul_axi_csynth.xml" \
        "$previous/axi_project/solution1/syn/report/matmul_axi_csynth.rpt" "$work/axi_project/solution1/syn/report/"
    cp "$previous/axi_project/solution1/sim/report/matmul_axi_cosim.rpt" "$work/axi_project/solution1/sim/report/"
    cp "$previous/kernel.cpp" "$previous/hls.log" "$work/"
    cd "$work"
    printf 'Reused unchanged HLS IP and its historical C/RTL reports from %s\n' "$previous" > hls-provenance.txt
    bash "$release/scripts/cloud-xilinx.sh" vivado -mode batch -notrace \
        -source "$release/hardware/build_overlay.tcl" > vivado-build.log 2>&1
    test -s bundle/autohls_matmul.bit
    test -s bundle/autohls_matmul.hwh
    printf 'Routed reset-fixed overlay completed: %s\n' "$variant"
done
cd "$release"
/mnt/datadisk0/autohls/venv/bin/python -m hardware.package_bundle \
    artifacts/preboard/deploy artifacts/preboard/distribution-resetfix
printf 'RESETFIX_OFFLINE_BUILD_COMPLETE; board_verified=false\n'
