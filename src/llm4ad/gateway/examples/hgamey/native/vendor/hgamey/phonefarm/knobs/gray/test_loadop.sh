#!/bin/bash
# Four-arm known-answer collection under one phonefarm Session.
# A/B run without the layer installed; C/D use rewrite with string extras.
# Explicit output and serial are required. No performance measurement here.
set -euo pipefail
args=(gray-layer refbench-loadop --frames "${FRAMES:-1800}" --intensity "${INTENSITY:-8}")
if [ -n "${REFBENCH_SERIAL:-}" ]; then args+=(--serial "$REFBENCH_SERIAL"); fi
if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  args+=(--out "$1")
  shift
fi
exec "${PHONEFARM_BIN:-phonefarm}" "${args[@]}" "$@"
