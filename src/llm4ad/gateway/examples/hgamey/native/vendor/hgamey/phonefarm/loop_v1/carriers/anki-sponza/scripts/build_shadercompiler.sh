#!/bin/bash
# build_shadercompiler.sh — 一次性的「宿主着色器编译」通道 (linux/amd64 容器内)
#
# 为什么需要它: AnKi 的 Android 构建要求先有一个**宿主平台**的 ShaderCompiler,
# 用来把 63 个 AnKi/Shaders/*.ankiprog (HLSL) 编成 *.ankiprogbin。这个工具在运行期
# dlopen DXC (AnKi/ShaderCompiler/Dxc.cpp:75), 而仓库只随包了三份 DXC 预编译库:
# WinX64 / WinArm64 / LinuxX64 —— **没有 macOS 版**, 上游 DXC 也不发布 macOS 二进制。
# 所以在这台 arm64 Mac 上没法原生跑 ShaderCompiler。
#
# 但这一步是**可解耦的**: AnKi/Shaders/CMakeLists.txt 只是把 ANKI_OVERRIDE_SHADER_COMPILER
# 当成一个命令行工具调用, 产物 *.ankiprogbin 就是普通 asset。所以只要在任意
# x86_64 Linux 上跑一次, 把 63 个 bin 缓存下来, 之后 macOS 侧的 Android 构建
# 就只剩纯 NDK 交叉编译 (不需要任何 DXC)。本脚本就是那「一次」。
#
# 用法: bash build_shadercompiler.sh [anki源码路径] [输出缓存目录]
# 产物: <缓存目录>/*.ankiprogbin  +  <缓存目录>/MANIFEST.txt (sha256 + 源码 commit)
set -euo pipefail

ANKI_SRC="${1:-/Users/mac/projects/thirdparty/anki-3d-engine}"
CACHE_DIR="${2:-/Users/mac/projects/thirdparty/anki-shaderbins}"
BUILD_DIR="${ANKI_BUILD_DIR:-/Users/mac/projects/thirdparty/anki-build-linux}"
# 基础镜像必须够新: 随包的 libdxcompiler.so 要 GLIBC_2.38。
# debian:bookworm 是 2.36 —— dlopen 会以 "missing or wrong architecture" 失败
# (真实原因是 glibc 版本, 报错信息有误导性)。trixie 是 2.41, 够。
IMAGE="anki-shadercompiler:trixie-amd64"

[ -d "$ANKI_SRC/AnKi/Shaders" ] || { echo "找不到 AnKi 源码: $ANKI_SRC" >&2; exit 1; }
mkdir -p "$CACHE_DIR" "$BUILD_DIR"

# ── 磁盘下限守卫 ────────────────────────────────────────────────
# 本机与其它 agent 共用硬盘; 剩余空间**任何时候不得低于 8GiB**。
DISK_FLOOR_GIB="${DISK_FLOOR_GIB:-8}"
avail_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
guard() {
  local a; a="$(avail_gib)"
  if [ "$a" -lt "$DISK_FLOOR_GIB" ]; then
    echo "!! 剩余磁盘 ${a}GiB < 下限 ${DISK_FLOOR_GIB}GiB —— 中止" >&2
    exit 9
  fi
  echo "   [disk] 剩余 ${a}GiB"
}
guard

# ── 1. 构建镜像 (只装编译 ShaderCompiler 需要的东西) ──────────────
# 注意用空目录当 build context: 镜像里不 COPY 任何东西, 源码是后面 -v 挂进去的。
# 拿 $ANKI_SRC 当 context 会把 1.6GB 源码整个塞给 docker daemon, 纯属浪费。
CTX="$(mktemp -d)"; trap 'rm -rf "$CTX"' EXIT
docker build --platform linux/amd64 -t "$IMAGE" -f - "$CTX" <<'DOCKEREOF'
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
DOCKEREOF
guard

# ── 2. 配置 + 只编 ShaderCompiler 这一个 target ────────────────────
# 注意 ANKI_SOURCE_DIRECTORY 会被编进二进制, 用于 dlopen
# <src>/ThirdParty/Dxc/Lib/LinuxX64/libdxcompiler.so, 所以容器里的挂载点
# (/src) 必须和运行时一致 —— 也因此 ShaderCompiler 只能在容器内跑。
docker run --rm --platform linux/amd64 \
  -v "$ANKI_SRC:/src" -v "$BUILD_DIR:/build" -v "$CACHE_DIR:/out" \
  -w /build "$IMAGE" bash -euc '
    cmake /src -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DANKI_HEADLESS=ON \
      -DANKI_BUILD_SAMPLES=OFF \
      -DANKI_BUILD_TESTS=OFF \
      -DANKI_BUILD_TOOLS=ON

    # qemu 模拟 x86_64 时 gcc 会随机 cc1plus segfault (ICE), 和源码无关 ——
    # 同一个文件重试一次往往就过了。ninja 是增量的, 每轮都往前推进, 所以
    # 重试若干次即可收敛。-j 2 降低并发也能少踩一些。
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
      if ninja -j 2 ShaderCompiler; then
        echo "ninja 第 $attempt 轮通过"
        break
      fi
      echo "== 第 $attempt 轮有 ICE, 增量重试 =="
      [ "$attempt" = 10 ] && { echo "重试 10 轮仍未过" >&2; exit 1; }
    done
    ls -la /build/Binaries/ShaderCompiler
  '
guard

# ── 3. 跑编译: 63 个 .ankiprog → .ankiprogbin ─────────────────────
# 参数与 AnKi/Shaders/CMakeLists.txt 里 add_custom_command 的调用完全一致:
#   <compiler> -o <bin> -j N -I <ankiroot> -DANKI_PLATFORM_MOBILE=1 -spirv <prog>
docker run --rm --platform linux/amd64 \
  -v "$ANKI_SRC:/src" -v "$BUILD_DIR:/build" -v "$CACHE_DIR:/out" \
  -w /src "$IMAGE" bash -euc '
    n=0
    for p in /src/AnKi/Shaders/*.ankiprog; do
      b="/out/$(basename "$p")bin"
      rm -f "$b"
      /build/Binaries/ShaderCompiler -o "$b" -j 4 -I /src -DANKI_PLATFORM_MOBILE=1 -spirv "$p"
      # 不能只信退出码: 编译失败时它仍可能返回 0 (见 ShaderProgramCompilerMain)。
      # 以产物存在且非空为准 —— 缺了一个 bin 就是引擎在设备上少一个 pass, 必须硬失败。
      [ -s "$b" ] || { echo "!! 没产出: $b (源: $p)" >&2; exit 1; }
      n=$((n+1))
    done
    echo "编了 $n 个 ankiprogbin"
  '
guard

# ── 4. 记账: 产物清单 + 源码 commit, 供证据链复核 ──────────────────
{
  echo "# anki-sponza shader binary cache"
  echo "# anki commit: $(git -C "$ANKI_SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "# built:       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# args:        -j 4 -I <src> -DANKI_PLATFORM_MOBILE=1 -spirv"
  ( cd "$CACHE_DIR" && shasum -a 256 *.ankiprogbin )
} > "$CACHE_DIR/MANIFEST.txt"

echo "缓存就绪: $CACHE_DIR ($(ls "$CACHE_DIR"/*.ankiprogbin 2>/dev/null | wc -l | tr -d ' ') 个)"
echo "构建目录 $BUILD_DIR 可以删了: rm -rf $BUILD_DIR"
