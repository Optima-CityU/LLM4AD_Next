#!/bin/bash
# run.sh — 编插帧 shader + Mac 上的核对工具 (MoltenVK), 在 I1 的三组合成样本上逐级核对并计时。
# 依赖: brew 的 molten-vk vulkan-headers vulkan-loader; NDK r28c 的 glslc; 带 TensorFlow 的 python (跑 TFLite 模型)。
# 用法: run.sh <工作目录> [names=pan32,zoom_hud,pan96]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
AGENT="${AGENT_ROOT:-$(cd "$REPO/../../.." && pwd)}"          # .../code/agent
WORK="${1:?用法: run.sh <工作目录> [names]}"; NAMES="${2:-pan32,zoom_hud,pan96}"
GLSLC="${GLSLC:-$AGENT/tools/ndk/android-ndk-r28c/shader-tools/darwin-x86_64/glslc}"
PY="${INTERP_PY:-$AGENT/tools/qairt/venv311/bin/python}"
INTERP="${INTERP_ROOT:-$AGENT/tools/interp}"
ICD="${VK_ICD_FILENAMES:-/opt/homebrew/etc/vulkan/icd.d/MoltenVK_icd.json}"
mkdir -p "$WORK/spv"
for s in "$REPO"/knobs/gray/layer/interp_*.comp; do
    n=$(basename "$s" .comp)
    "$GLSLC" --target-env=vulkan1.1 -O -I"$REPO/knobs/gray/layer" "$s" -o "$WORK/spv/$n.spv"
done
clang++ -std=c++17 -O2 -Wall -Wextra -Wno-missing-field-initializers -I/opt/homebrew/include -L/opt/homebrew/lib \
    -lvulkan -o "$WORK/harness" "$HERE/harness.cpp"
VK_ICD_FILENAMES="$ICD" TF_CPP_MIN_LOG_LEVEL=2 "$PY" -I -u "$HERE/validate.py" --interp-root "$INTERP" \
    --harness "$WORK/harness" --spv "$WORK/spv" --work "$WORK/work" --out "$WORK/validate.json" --names "$NAMES" --reps 10
