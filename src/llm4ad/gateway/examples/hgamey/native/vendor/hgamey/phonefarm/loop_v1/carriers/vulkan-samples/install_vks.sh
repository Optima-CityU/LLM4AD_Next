#!/bin/bash
# install_vks.sh [apk_path] — 装 APK 并把样例要的场景资产推到设备, 零交互。
#
# 资产不进 APK: 上游 Android 端从 `ANativeActivity.externalDataPath` 读
# (components/android/src/context.cpp), 所以走 adb push 到
#   /storage/emulated/0/Android/data/com.khronos.vulkan_samples/files/
# 这个目录要等应用装完、跑过一次才会被系统建出来, 所以顺序是: 装 → 建目录 → 推。
#
# 退出码: 0=装好并推完  2=硬失败
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VKS_ROOT="${VKS_ROOT:-/Users/mac/projects/thirdparty/Vulkan-Samples}"
SERIAL="${VKS_SERIAL:-91253241019A}"
PKG=com.khronos.vulkan_samples
APPFILES="/storage/emulated/0/Android/data/$PKG/files"

APK="${1:-$(find "$VKS_ROOT/build/android_gradle/app/build/outputs/apk/release" -name '*.apk' 2>/dev/null | head -1)}"
[ -n "$APK" ] && [ -f "$APK" ] || { echo "找不到 APK, 先跑 build_vks.sh" >&2; exit 2; }

ashell() { adb -s "$SERIAL" shell "$@" </dev/null; }

echo "装 $APK ($(du -h "$APK" | cut -f1))"
adb -s "$SERIAL" install -r -g "$APK" 2>&1 | tail -3

# 应用私有外部目录: 装完就该在; 保险起见显式建一次
ashell "mkdir -p $APPFILES" >/dev/null 2>&1 || true
ashell "ls -d $APPFILES" >/dev/null || { echo "外部数据目录没建出来: $APPFILES" >&2; exit 2; }

echo "推资产与 shader (只推被保留的场景, 见 build_vks.sh 的 KEEP_SCENES)"
# shaders/ 必须推: 样例运行时从 data path 读 .spv, 不在 APK 里
for d in assets/scenes assets/textures assets/fonts shaders; do
  [ -d "$VKS_ROOT/$d" ] || continue
  echo "  $d ($(du -sh "$VKS_ROOT/$d" | cut -f1))"
  adb -s "$SERIAL" push "$VKS_ROOT/$d" "$APPFILES/" >/dev/null
done

echo "设备上:"
ashell "ls $APPFILES; du -sh $APPFILES/scenes 2>/dev/null"
