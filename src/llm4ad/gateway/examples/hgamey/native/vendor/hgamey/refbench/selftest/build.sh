#!/bin/bash
# build.sh — 重编 selftest/load.spv (已知答案负载算子)。产物入库, 平时不用跑;
# 改了 load.comp 才需要。与 build/build.sh 同一个 glslc (NDK 28.2.13676358)。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
NDK="${REFBENCH_NDK:-$SDK/ndk/28.2.13676358}"
HOST_TAG=darwin-x86_64
[ -d "$NDK/shader-tools/$HOST_TAG" ] || HOST_TAG=linux-x86_64
GLSLC="$NDK/shader-tools/$HOST_TAG/glslc"
"$GLSLC" -O --target-env=vulkan1.0 "$HERE/load.comp" -o "$HERE/load.spv"
echo "load.spv $(wc -c < "$HERE/load.spv" | tr -d ' ') B  sha256 $(shasum -a 256 "$HERE/load.spv" | cut -c1-16)"
