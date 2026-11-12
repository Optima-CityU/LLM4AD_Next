#!/bin/bash
# build.sh — 零交互编出 vkop_runner (arm64-v8a)。
# 依赖: android-commandlinetools 的 NDK。路径可用环境变量覆盖。
# 与 ../../../refbench/build/build.sh 同一套编译模式。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
NDK="${VKOP_NDK:-$SDK/ndk/28.2.13676358}"

HOST_TAG=darwin-x86_64
[ -d "$NDK/toolchains/llvm/prebuilt/$HOST_TAG" ] || HOST_TAG=linux-x86_64
CXX="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin/aarch64-linux-android30-clang++"

[ -e "$CXX" ] || { echo "缺工具: $CXX" >&2; exit 1; }

OUT="$HERE/android_aarch64_vkop_runner"

# -static-libstdc++: 设备上没有 libc++_shared.so, 静态链进来省掉一次推送
"$CXX" -std=c++17 -O2 -Wall -Wextra -Wno-missing-field-initializers \
  -static-libstdc++ -Wl,-z,max-page-size=16384 \
  -o "$OUT" "$HERE/main.cpp" \
  -lvulkan -lm -llog

echo "产物: $OUT"
file "$OUT" 2>/dev/null || true
ls -la "$OUT"
