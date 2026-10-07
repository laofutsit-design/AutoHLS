#!/usr/bin/env bash
# Run the installed legacy tools in their tested Ubuntu 18.04 userland.
set -euo pipefail
tool=${1:?Usage: cloud-xilinx.sh vivado_hls|vivado [arguments...]}
shift
case "$tool" in vivado_hls|vivado) ;; *) echo 'Unsupported tool' >&2; exit 2 ;; esac
release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
work=$(pwd -P)
case "$work/" in "$release/"*) ;; *) echo 'Run from inside this release directory' >&2; exit 2 ;; esac
# nspawn takes an exclusive lock on this rootfs; queue concurrent callers.
exec flock /mnt/datadisk0/autohls/logs/vivado2019.run.lock \
    sudo -n systemd-nspawn --quiet --settings=no --register=no --console=pipe \
    --as-pid2 --private-network --no-new-privileges=yes --drop-capability=all \
    --link-journal=no --user=ubuntu \
    --directory=/mnt/datadisk0/autohls/environments/ubuntu18 \
    --bind="$release:$release" --chdir="$work" \
    --setenv=LANG=en_US.UTF-8 --setenv=TZ=UTC0 \
    "/opt/Xilinx/Vivado/2019.1/bin/$tool" "$@"
