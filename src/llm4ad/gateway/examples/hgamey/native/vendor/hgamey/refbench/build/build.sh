#!/bin/bash
# build.sh — 零交互出包: shaders → SPIR-V 头 → librefbench.so → refbench.apk (arm64-v8a)
# 不用 Gradle。依赖: android-commandlinetools (build-tools;35.0.0, ndk;28.2.13676358,
# platforms;android-35) + openjdk。所有路径可用环境变量覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
NDK="${REFBENCH_NDK:-$SDK/ndk/28.2.13676358}"
BT="${REFBENCH_BUILD_TOOLS:-$SDK/build-tools/35.0.0}"
PLATFORM_JAR="$SDK/platforms/android-35/android.jar"
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home}"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

HOST_TAG=darwin-x86_64
[ -d "$NDK/toolchains/llvm/prebuilt/$HOST_TAG" ] || HOST_TAG=linux-x86_64
TC="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin"
CC="$TC/aarch64-linux-android30-clang"
CXX="$TC/aarch64-linux-android30-clang++"
GLSLC="$NDK/shader-tools/$HOST_TAG/glslc"

for t in "$CXX" "$GLSLC" "$BT/aapt2" "$BT/zipalign" "$BT/apksigner" "$PLATFORM_JAR" "$JAVA_HOME/bin/keytool"; do
  [ -e "$t" ] || { echo "缺工具: $t" >&2; exit 1; }
done

OUT="$ROOT/build/out"
GEN="$OUT/gen"
STAGE="$OUT/stage"
rm -rf "$STAGE"
mkdir -p "$OUT" "$GEN" "$STAGE/lib/arm64-v8a"

# ── 1. shaders → SPIR-V → C 头 ──
for src in "$ROOT"/app/shaders/*.vert "$ROOT"/app/shaders/*.frag; do
  base="$(basename "$src")"
  name="${base//./_}"                       # fullscreen.vert → fullscreen_vert
  "$GLSLC" -O "$src" -o "$GEN/$name.spv"
  python3 - "$GEN/$name.spv" "$name" "$GEN/$name.h" <<'PYEOF'
import sys
data = open(sys.argv[1], "rb").read()
n = sys.argv[2]
with open(sys.argv[3], "w") as f:
    f.write("// generated — do not edit\n")
    f.write("static const unsigned char g_%s[] = {%s};\n" % (n, ",".join(str(b) for b in data)))
    f.write("static const unsigned int g_%s_len = %d;\n" % (n, len(data)))
PYEOF
done
echo "shaders ok"

# ── 2. 编译 + 链接 librefbench.so ──
CFLAGS_COMMON=(-O2 -Wall -fPIC -DVK_USE_PLATFORM_ANDROID_KHR=1
  -I"$NDK/sources/android/native_app_glue" -I"$OUT")
"$CC"  "${CFLAGS_COMMON[@]}" -c "$NDK/sources/android/native_app_glue/android_native_app_glue.c" \
      -o "$OUT/glue.o"
"$CXX" "${CFLAGS_COMMON[@]}" -std=c++17 -fno-exceptions -fno-rtti \
      -c "$ROOT/app/src/main.cpp" -o "$OUT/main.o"
"$CXX" -shared -Wl,-z,max-page-size=16384 -o "$STAGE/lib/arm64-v8a/librefbench.so" "$OUT/main.o" "$OUT/glue.o" \
      -u ANativeActivity_onCreate -static-libstdc++ -lvulkan -landroid -llog -lm
echo "librefbench.so ok ($(wc -c < "$STAGE/lib/arm64-v8a/librefbench.so" | tr -d ' ') B)"

# ── 3. aapt2 打包 + 塞 so + 对齐 + 签名 ──
"$BT/aapt2" link --manifest "$ROOT/app/AndroidManifest.xml" -I "$PLATFORM_JAR" \
    -o "$OUT/base.apk"
python3 - "$STAGE" "$OUT/base.apk" <<'PYAPK'
from pathlib import Path
import sys, zipfile
stage = Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], "a", compression=zipfile.ZIP_DEFLATED) as apk:
    for lib in sorted((stage / "lib").rglob("*.so")):
        apk.write(lib, lib.relative_to(stage).as_posix())
PYAPK
"$BT/zipalign" -P 16 -f 4 "$OUT/base.apk" "$OUT/aligned.apk"

KS="$OUT/debug.keystore"
if [ ! -f "$KS" ]; then
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KS" -alias refbench \
      -storepass refbench -keypass refbench -keyalg RSA -keysize 2048 \
      -validity 10000 -dname "CN=refbench" >/dev/null 2>&1
fi
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:refbench --key-pass pass:refbench \
    --out "$OUT/refbench.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$OUT/refbench.apk"
rm -f "$OUT/base.apk" "$OUT/aligned.apk" "$OUT/refbench.apk.idsig"

echo "APK: $OUT/refbench.apk ($(wc -c < "$OUT/refbench.apk" | tr -d ' ') B)"
