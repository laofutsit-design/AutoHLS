#!/usr/bin/env bash
# Foreground server; launch explicitly for experiments, never at boot.
set -euo pipefail
root=/mnt/datadisk0/autohls
mkdir -p "$root/models/ollama"
export OLLAMA_MODELS="$root/models/ollama"
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_NO_CLOUD=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_KEEP_ALIVE=10m
exec "$root/ollama/v0.34.1/bin/ollama" serve
