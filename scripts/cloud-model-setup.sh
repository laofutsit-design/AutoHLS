#!/usr/bin/env bash
# Task-local, pinned Ollama runtime. No sudo, services, firewall or model download.
set -euo pipefail
root=/mnt/datadisk0/autohls
package="$root/downloads/ollama-v0.34.1-linux-amd64.tar.zst"
runtime="$root/ollama/v0.34.1"
expected=f361dc3992ec07e4ad429f4bb2d10d4663ba2c295f9a9a688c7d52f4ba650034
mkdir -p "$root/downloads" "$root/ollama"
exec 9>"$root/ollama/setup.lock"
flock -n 9 || exit 75
if [[ -e "$runtime" ]]; then
    echo "Runtime directory already exists; inspect before reinstallation: $runtime" >&2
    exit 2
fi
# The GitHub website may be unreachable while its official release API works.
source_url="https://api.github.com/repos/ollama/ollama/releases/assets/566349671?download=$(date +%s)"
if [[ "${1:-}" == --mirror ]]; then
    # Public transport fallback only: the official SHA-256 gate below is unchanged.
    source_url=https://gh-proxy.com/https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-linux-amd64.tar.zst
elif [[ $# != 0 ]]; then
    echo 'Only the optional --mirror flag is supported' >&2
    exit 2
fi
timeout 900s aria2c --continue=true --auto-file-renaming=false --file-allocation=none \
    --max-connection-per-server=16 --split=16 --min-split-size=1M \
    --connect-timeout=10 --timeout=20 --max-tries=3 --summary-interval=30 \
    --console-log-level=warn --download-result=full --enable-color=false \
    --header='Accept: application/octet-stream' \
    --dir="$(dirname "$package")" --out="$(basename "$package")" \
    "$source_url"
printf '%s  %s\n' "$expected" "$package" | sha256sum --check -
mkdir "$runtime"
tar --zstd -xf "$package" -C "$runtime"
"$runtime/bin/ollama" --version
