#!/bin/bash
# build_layer.sh — NDK 直编灰档只读层 libVkLayer_refknobs.so (arm64-v8a), 零交互。
# 依赖同 refbench: brew android-commandlinetools 的 ndk;28.2.13676358。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"          # gray/
SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
NDK="${REFBENCH_NDK:-$SDK/ndk/28.2.13676358}"
# 默认位置没有时依次找: ANDROID_NDK_HOME, Android Studio 的 SDK, 仓库外的 tools/ndk (都要求 r28c = 28.2.13676358)
if [ ! -d "$NDK" ]; then
    for c in "${ANDROID_NDK_HOME:-}" "$HOME/Library/Android/sdk/ndk/28.2.13676358" \
             "$ROOT/../../../../tools/ndk/android-ndk-r28c" "$ROOT/../../../../../tools/ndk/android-ndk-r28c"; do
        [ -n "$c" ] && [ -d "$c" ] && { NDK="$(cd "$c" && pwd)"; break; }
    done
fi
HOST_TAG=darwin-x86_64
[ -d "$NDK/toolchains/llvm/prebuilt/$HOST_TAG" ] || HOST_TAG=linux-x86_64
CXX="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin/aarch64-linux-android30-clang++"
[ -x "$CXX" ] || { echo "缺 NDK clang++: $CXX" >&2; exit 1; }

OUT="$ROOT/build/out"
mkdir -p "$OUT"
# -static-libstdc++ 是硬要求, 不是优化: 层被宿主应用的 Vulkan 加载器 dlopen, 而宿主
# 应用的 lib 目录里通常没有 libc++_shared.so。动态链 STL 会在真机上直接
#   dlopen failed: library "libc++_shared.so" not found
# (2026-09-23 refbench 实测过这个失败)。静态进去 → .so 自包含, 任意目标都能挂。
# --exclude-libs ALL 把静态进来的 STL 符号藏起来, 不污染宿主的符号表。
# copyprobe 的 1:1 拷贝算子编成 SPIR-V 并内嵌成 C 头 —— 层要自包含, 不能把 .spv
# 当运行时文件发进去 (宿主应用的沙箱里读不到我们的路径)。
GLSLC="$NDK/shader-tools/$HOST_TAG/glslc"
[ -x "$GLSLC" ] || { echo "缺 glslc: $GLSLC" >&2; exit 1; }
"$GLSLC" -O "$ROOT/layer/copyprobe_copy.comp" -o "$OUT/copyprobe_copy.spv"
"$GLSLC" -O "$ROOT/layer/upop.comp" -o "$OUT/upop.spv"
# npuop: 画面 -> NPU 输入张量 / NPU 输出张量 -> 送显分辨率 dst
"$GLSLC" -O "$ROOT/layer/npu_pack.comp" -o "$OUT/npu_pack.spv"
"$GLSLC" -O "$ROOT/layer/npu_unpack.comp" -o "$OUT/npu_unpack.spv"
python3 - "$OUT/copyprobe_copy.spv" "$OUT/upop.spv" "$OUT/npu_pack.spv" "$OUT/npu_unpack.spv" > "$OUT/copy_spv.h" <<'PYEOF'
import sys
def emit(name, path):
    d = open(path, "rb").read()
    print(f"static const unsigned char {name}[] = {{")
    for i in range(0, len(d), 16):
        print("  " + ",".join(str(b) for b in d[i:i+16]) + ",")
    print("};")
    print(f"static const unsigned int {name}Len = {len(d)};")
emit("kCopySpv", sys.argv[1])
emit("kUpopSpv", sys.argv[2])
emit("kNpuPackSpv", sys.argv[3])
emit("kNpuUnpackSpv", sys.argv[4])
PYEOF
echo "shader: copy=$(wc -c < "$OUT/copyprobe_copy.spv" | tr -d ' ')B upop=$(wc -c < "$OUT/upop.spv" | tr -d ' ')B" \
     "npu_pack=$(wc -c < "$OUT/npu_pack.spv" | tr -d ' ')B npu_unpack=$(wc -c < "$OUT/npu_unpack.spv" | tr -d ' ')B -> copy_spv.h"

# 插帧 GPU 侧 (路线 A: 缩小+光流放大采样混合; 路线 B: 块匹配+平滑+预对齐+残差合成)。先编译并内嵌成
# interp_spv.h 备用; 层代码还没调用它们 (接线是后续工作), 这里至少保证每次构建都能编过。
INTERP_SHADERS="interp_a_down interp_a_blend interp_b_luma interp_b_bm interp_b_smooth interp_b_prealign interp_b_compose"
for s in $INTERP_SHADERS; do
    "$GLSLC" --target-env=vulkan1.1 -O -I"$ROOT/layer" "$ROOT/layer/$s.comp" -o "$OUT/$s.spv"
done
python3 - "$OUT" $INTERP_SHADERS > "$OUT/interp_spv.h" <<'PYEOF'
import sys
out = sys.argv[1]
for n in sys.argv[2:]:
    d = open(f"{out}/{n}.spv", "rb").read()
    cname = "k" + "".join(p.capitalize() for p in n.split("_")) + "Spv"
    print(f"static const unsigned char {cname}[] = {{")
    for i in range(0, len(d), 16):
        print("  " + ",".join(str(b) for b in d[i:i+16]) + ",")
    print("};")
    print(f"static const unsigned int {cname}Len = {len(d)};")
PYEOF
echo "interp shaders: $(for s in $INTERP_SHADERS; do printf '%s=%sB ' $s $(wc -c < "$OUT/$s.spv" | tr -d ' '); done)-> interp_spv.h"

# selfsr: 送显前超分 (游戏小分辨率出图 → 送显分辨率), 内嵌成 selfsr_spv.h
"$GLSLC" --target-env=vulkan1.1 -O "$ROOT/layer/selfsr_up.comp" -o "$OUT/selfsr_up.spv"
"$GLSLC" --target-env=vulkan1.1 -O "$ROOT/layer/selfsr_rcas.comp" -o "$OUT/selfsr_rcas.spv"
python3 - "$OUT/selfsr_up.spv" "$OUT/selfsr_rcas.spv" > "$OUT/selfsr_spv.h" <<'PYEOF'
import sys
for name, path in (("kSelfsrUpSpv", sys.argv[1]), ("kSelfsrRcasSpv", sys.argv[2])):
    d = open(path, "rb").read()
    print(f"static const unsigned char {name}[] = {{")
    for i in range(0, len(d), 16):
        print("  " + ",".join(str(b) for b in d[i:i+16]) + ",")
    print("};")
    print(f"static const unsigned int {name}Len = {len(d)};")
PYEOF
echo "selfsr shaders: up=$(wc -c < "$OUT/selfsr_up.spv" | tr -d ' ')B rcas=$(wc -c < "$OUT/selfsr_rcas.spv" | tr -d ' ')B -> selfsr_spv.h"

# gray-artifact uses this same native source/flag list for its candidate build.
python3 - "$CXX" "$ROOT" "$OUT" <<'PYEOF'
import json, pathlib, subprocess, sys
cxx, root, out = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
config = json.loads((root / "build/layer_build.json").read_text())
subprocess.run([cxx, *config["compile_flags"], "-I", str(out),
                "-o", str(out / "libVkLayer_refknobs.so"),
                *(str(root / "layer" / name) for name in config["sources"]),
                *config["link_flags"]], check=True)
PYEOF
cp "$ROOT/layer/VkLayer_refknobs.json" "$OUT/"
echo "layer: $OUT/libVkLayer_refknobs.so ($(wc -c < "$OUT/libVkLayer_refknobs.so" | tr -d ' ') B)"
NM="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin/llvm-nm"
"$NM" -D --defined-only "$OUT/libVkLayer_refknobs.so" | grep " T " | sed 's/^/  export: /'

# 自检 1: 必需导出符号一个都不能少。
# vkEnumerateInstance{Layer,Extension}Properties 尤其关键 —— Android 的加载器靠 dlsym
# 发现层(不读 JSON manifest), 少了它们只会在真机上报
#   E vulkan: layer library '...' missing some instance enumeration functions
# 然后整个丢弃这个层。2026-09-23 实测踩过, 所以在这里挡住。
for sym in vkNegotiateLoaderLayerInterfaceVersion vkGetInstanceProcAddr vkGetDeviceProcAddr \
           vkEnumerateInstanceLayerProperties vkEnumerateInstanceExtensionProperties; do
    "$NM" -D --defined-only "$OUT/libVkLayer_refknobs.so" | grep -q " T $sym\$" \
        || { echo "自检失败: 缺导出符号 $sym" >&2; exit 1; }
done

# 自检: 任何残留的 DT_NEEDED 动态依赖都必须是宿主进程一定已加载的系统库。
# libc++_shared 出现在这里 = 上机必炸, 当场失败而不是等真机报。
NEEDED=$("$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin/llvm-readelf" -d "$OUT/libVkLayer_refknobs.so" \
    | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p')
echo "  needed: $(echo "$NEEDED" | tr '\n' ' ')"
if echo "$NEEDED" | grep -q "libc++_shared"; then
    echo "自检失败: 仍依赖 libc++_shared.so, 宿主应用 lib 目录里没有它, 上机会 dlopen 失败" >&2
    exit 1
fi
