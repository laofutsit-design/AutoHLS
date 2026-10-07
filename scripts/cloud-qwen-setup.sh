#!/usr/bin/env bash
# Qwen publisher's pinned GGUF revision; verify before import. Cloud data disk only.
set -euo pipefail
root=/mnt/datadisk0/autohls/models/qwen2.5-coder-7b-q4km
name=qwen2.5-coder-7b-instruct-q4_k_m.gguf
revision=9bc02b77c486bb4b40e119970821bf8cfeaaf267
base="https://modelscope.cn/models/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/$revision"
mkdir -p "$root"
exec 9>"$root/download.lock"
flock -n 9 || exit 75
timeout 900s aria2c --continue=true --auto-file-renaming=false --file-allocation=none \
    --max-connection-per-server=8 --split=8 --min-split-size=4M \
    --connect-timeout=10 --timeout=20 --max-tries=3 --summary-interval=30 \
    --console-log-level=warn --enable-color=false --dir="$root" --out="$name" "$base/$name"
printf '509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c  %s\n' "$root/$name" | sha256sum --check -
curl --fail --location --connect-timeout 10 --max-time 30 "$base/LICENSE" --output "$root/LICENSE"
echo 'Verified publisher GGUF; import separately into the loopback-only Ollama server.'
