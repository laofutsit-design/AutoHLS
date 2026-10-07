#!/usr/bin/env bash
# One build of a frozen, audited delivery input; no board or model operations.
set -euo pipefail
release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
python=/mnt/datadisk0/autohls/venv/bin/python
cd "$release"
"$python" -m hardware.delivery verify-input "$release"
mkdir -p "$release/artifacts/delivery"
work="$release/artifacts/delivery/build"
mkdir "$work"
trap 'code=$?; printf "exit_code=%s\nfinished_utc=%s\n" "$code" "$(date -u --iso-8601=seconds)" > "$work/build.status"' EXIT
cp "$release/inputs/kernel.cpp" "$work/kernel.cpp"
cd "$work"
g++ -std=c++14 -O1 -g -Wno-unknown-pragmas -fsanitize=address,undefined \
    -fno-sanitize-recover=all -DAUTOHLS_NATIVE_NULL_CHECKS kernel.cpp \
    "$release/hardware/hls/matmul_batch_axi.cpp" "$release/hardware/hls/matmul_batch_axi_tb.cpp" -o native-check
./native-check | tee native-check.log
if ! bash "$release/scripts/cloud-xilinx.sh" vivado_hls -f "$release/hardware/hls/build_batch_axi.tcl" > hls.log 2>&1; then
    grep -q 'C/RTL co-simulation finished: PASS' hls.log
    grep -q 'set_property core_revision' hls.log
    (cd "$release" && "$python" -m hardware.package_ip "$work/axi_project/solution1/impl/ip") > repackage.log 2>&1
fi
bash "$release/scripts/cloud-xilinx.sh" vivado -mode batch -notrace -source "$release/hardware/build_overlay.tcl" > vivado-build.log 2>&1
cd "$release"
"$python" -m hardware.delivery seal "$release"
