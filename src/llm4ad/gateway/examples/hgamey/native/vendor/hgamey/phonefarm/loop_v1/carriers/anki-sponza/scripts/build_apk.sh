#!/bin/bash
# build_apk.sh — macOS 侧出 arm64 Sponza APK。
#
# 前置: scripts/build_shadercompiler.sh 已经跑过一次, 着色器 bin 缓存就绪。
#       (为什么要分两步 —— 见 shadercompiler_shim.py 顶部和 ../README.md)
#
# 这一步**不需要 DXC**: 纯 NDK 交叉编译 + gradle 打包, 着色器由 shim 从缓存发货。
set -euo pipefail

ANKI_SRC="${ANKI_SRC:-/Users/mac/projects/thirdparty/anki-3d-engine}"
CACHE_DIR="${ANKI_SHADERBIN_CACHE:-/Users/mac/projects/thirdparty/anki-shaderbins}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SHIM="$HERE/shadercompiler_shim.py"

SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
NDK_VER="${ANKI_NDK_VER:-28.2.13676358}"
# JDK 必须是 17: 模板自带的 wrapper 是 Gradle 7.4.2 + AGP 7.1.3, 跑不了 JDK 21/25。
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export JAVA_HOME ANDROID_SDK_ROOT="$SDK"
export PATH="$JAVA_HOME/bin:$PATH"
export ANKI_SHADERBIN_CACHE="$CACHE_DIR"

DISK_FLOOR_GIB="${DISK_FLOOR_GIB:-8}"
avail_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
guard() {
  local a; a="$(avail_gib)"
  [ "$a" -lt "$DISK_FLOOR_GIB" ] && { echo "!! 剩余磁盘 ${a}GiB < 下限 ${DISK_FLOOR_GIB}GiB —— 中止" >&2; exit 9; }
  echo "   [disk] 剩余 ${a}GiB"
}

# ── 0. 前置检查 ───────────────────────────────────────────────
[ -d "$ANKI_SRC/AnKi" ] || { echo "找不到 AnKi 源码: $ANKI_SRC" >&2; exit 1; }
n_bin=$(ls "$CACHE_DIR"/*.ankiprogbin 2>/dev/null | wc -l | tr -d ' ')
n_prog=$(ls "$ANKI_SRC"/AnKi/Shaders/*.ankiprog 2>/dev/null | wc -l | tr -d ' ')
if [ "$n_bin" != "$n_prog" ] || [ "$n_bin" = "0" ]; then
  echo "着色器缓存不完整: 有 $n_bin 个 bin, 需要 $n_prog 个" >&2
  echo "先跑: bash $HERE/build_shadercompiler.sh" >&2
  exit 1
fi
[ -d "$SDK/ndk/$NDK_VER" ] || { echo "缺 NDK: $SDK/ndk/$NDK_VER" >&2; exit 1; }
[ -x "$JAVA_HOME/bin/java" ] || { echo "缺 JDK 17: $JAVA_HOME —— brew install openjdk@17" >&2; exit 1; }
[ -d "$SDK/platforms/android-32" ] || { echo "缺 platforms;android-32 (模板 compileSdk 32)" >&2; exit 1; }
guard

# ── 1. 生成 AndroidProject_Sponza ────────────────────────────
# 上游脚本会把模板拷成 <anki根>/AndroidProject_Sponza, 并把 shader compiler 路径
# 写进 app/build.gradle 的 ANKI_OVERRIDE_SHADER_COMPILER —— 我们指向 shim。
PROJ="$ANKI_SRC/AndroidProject_Sponza"
if [ ! -d "$PROJ" ]; then
  ( cd "$ANKI_SRC" && python3 ./Tools/Android/GenerateAndroidProject.py \
      -o . -t Sponza -a ./Samples/Sponza/Assets/ --shader-compiler "$SHIM" )
else
  echo "AndroidProject_Sponza 已存在, 跳过生成"
fi
guard

# ── 2. 只把 NDK 版本顶成本机装了的那个 ──────────────────────
# 其余版本(compileSdk 32 / AGP 7.1.3 / Gradle 7.4.2)**保持模板原样** —— 那是上游
# 实际测过的组合, 跟着走比擅自升级少踩坑。所以宁可去装 platforms;android-32,
# 也不改 compileSdk。NDK 是例外: 模板写死 26.1.10909125, 本机只有 28.2.x,
# 装一个 26.1 要另外 ~2.5GB, 先试 28.2。
GRADLE_FILE="$PROJ/app/build.gradle"
/usr/bin/sed -i '' -e "s/ndkVersion \"26.1.10909125\"/ndkVersion \"$NDK_VER\"/" "$GRADLE_FILE"
grep -E "compileSdk|targetSdk|ndkVersion" "$GRADLE_FILE"

# shim 得可执行, 而且 gradle 是直接 exec 它的 —— 要有 shebang
chmod +x "$SHIM"

# ── 3. gradle 出包 ───────────────────────────────────────────
# 用模板自带的 wrapper (Gradle 7.4.2), 不要用系统 gradle —— 系统上是 9.x,
# 和 AGP 7.1.3 不兼容。
chmod +x "$PROJ/gradlew"
( cd "$PROJ" && ./gradlew --no-daemon assembleRelease )
guard

APK="$PROJ/app/build/outputs/apk/release/app-release.apk"
[ -f "$APK" ] || APK="$(find "$PROJ/app/build/outputs/apk" -name '*.apk' | head -1)"
echo "APK: $APK ($(wc -c < "$APK" | tr -d ' ') B)"
echo "装机: adb -s \${SERIAL} install -r $APK"
