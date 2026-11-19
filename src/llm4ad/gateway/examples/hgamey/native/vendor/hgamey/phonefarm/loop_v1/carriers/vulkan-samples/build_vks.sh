#!/bin/bash
# build_vks.sh — 从零复现 Khronos Vulkan-Samples 的 Android arm64 APK, 零交互。
#
# 第三方工程不进本仓库: 克隆到 $VKS_ROOT (缺省 /Users/mac/projects/thirdparty/Vulkan-Samples)。
# 本脚本只做三件我们自己的事:
#   1. 浅克隆并**裁剪**到我们要的样例与场景 (整仓带资产 4.2GB, 裁剪后 1.6GB)
#   2. 打上 patches/ 里的两处改动 (见该目录 README 段落)
#   3. 生成 gradle 工程并出 release APK
#
# 退出码: 0=出包  2=硬失败 (缺工具/构建失败)  9=硬盘看门狗触发
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VKS_ROOT="${VKS_ROOT:-/Users/mac/projects/thirdparty/Vulkan-Samples}"
SDK="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home}"
MIN_FREE_MB="${VKS_MIN_FREE_MB:-8500}"   # 低于此值立刻停, 见 README「硬盘纪律」
export JAVA_HOME
export ANDROID_SDK_ROOT="$SDK"
export PATH="$JAVA_HOME/bin:$PATH"

# 只保留这些样例 —— 全仓 114 个样例全编会产生数 GB 中间产物, 而我们只用得上
# performance 里这四个 (三个有现成的两臂开关, hello_triangle 当冒烟)。
KEEP_SAMPLES="samples/performance/render_passes samples/performance/msaa samples/performance/subpasses samples/performance/afbc samples/api/hello_triangle"
# 只保留这些样例真正 load_scene 的场景 (assets 全量 1.0GB, 其中 bonza/vokselia/morpheus 占 766MB)
KEEP_SCENES="sponza space_module"

free_mb() { df -m /System/Volumes/Data | tail -1 | awk '{print $4}'; }
guard() {
  local a; a="$(free_mb)"
  if [ "$a" -lt "$MIN_FREE_MB" ]; then
    echo "硬盘看门狗: 剩余 ${a}MB < ${MIN_FREE_MB}MB, 停手" >&2
    exit 9
  fi
}

need() { [ -e "$1" ] || { echo "缺工具: $1" >&2; exit 2; }; }
need "$SDK/ndk/28.2.13676358"
need "$SDK/platforms/android-35/android.jar"
need "$SDK/cmake/3.22.1"          # AGP 8.7 的 externalNativeBuild 指名要它, 用 sdkmanager 装
need "$JAVA_HOME/bin/java"

guard

# ── 1. 浅克隆 ──
if [ ! -d "$VKS_ROOT" ]; then
  mkdir -p "$(dirname "$VKS_ROOT")"
  git clone --depth 1 --recurse-submodules --shallow-submodules \
      https://github.com/KhronosGroup/Vulkan-Samples.git "$VKS_ROOT"
fi
guard

# ── 2. 裁剪 (幂等) ──
cd "$VKS_ROOT"
for d in $(find samples -mindepth 2 -maxdepth 2 -type d); do
  keep=0
  for k in $KEEP_SAMPLES; do [ "$d" = "$k" ] && keep=1; done
  [ $keep -eq 0 ] && rm -rf "$d"
done
find samples -mindepth 1 -maxdepth 1 -type d -empty -delete 2>/dev/null || true
rm -rf assets/gold
for d in assets/scenes/*/; do
  n="$(basename "$d")"
  keep=0
  for k in $KEEP_SCENES; do [ "$n" = "$k" ] && keep=1; done
  # 只删大场景, 小的 (几百 KB) 留着不值得冒险
  if [ $keep -eq 0 ] && [ "$(du -sm "$d" | cut -f1)" -gt 5 ]; then rm -rf "$d"; fi
done
guard

# ── 3. 打补丁 (幂等) ──
# 3a. 不下载 Vulkan 校验层: 省约 150MB 下载与 APK 体积, 实测负载不需要它
if grep -q '^apply from: "./download_vvl.gradle"' bldsys/cmake/template/gradle/app.build.gradle.in; then
  python3 - <<'PYEOF'
p = "bldsys/cmake/template/gradle/app.build.gradle.in"
s = open(p).read()
s = s.replace('apply from: "./download_vvl.gradle"',
              '// phonefarm carrier: 不下载校验层\n//apply from: "./download_vvl.gradle"')
open(p, "w").write(s)
PYEOF
fi
# 3b. sample_config 插件: 上游只能靠 ImGui 或 batch 轮转换开关, 没有"钉死某一档"的命令行口
mkdir -p app/plugins/sample_config
cp "$HERE/patches/sample_config/sample_config.h"   app/plugins/sample_config/
cp "$HERE/patches/sample_config/sample_config.cpp" app/plugins/sample_config/
# 3c. file_logger 逐条 flush: 缺省带缓冲, 运行期日志卡在 4096 字节不落盘, 驱动面读不到
#     任何实时进度(这台设备的 logcat 是哑的, 进度信号只能走文件)。见该补丁里的注释。
if ! grep -q "flush_on" app/plugins/file_logger/file_logger.cpp; then
  python3 - <<'PYSUB'
p = "app/plugins/file_logger/file_logger.cpp"
s = open(p).read()
old = 'spdlog::default_logger()->sinks().push_back(std::make_shared<spdlog::sinks::basic_file_sink_mt>(log_file, true));'
new = (old + '\n'
       '\t\t// phonefarm carrier: 逐条 flush。缺省是带缓冲的 —— 运行期日志会一直卡在\n'
       '\t\t// 4096 字节不落盘, 外部脚本读不到任何进度, 只能等进程退出后一次性看到全部。\n'
       '\t\t// 驱动面要靠这个文件当实时信号(logcat 在这台设备上是哑的), 所以必须逐条落。\n'
       '\t\tspdlog::default_logger()->flush_on(spdlog::level::info);')
assert old in s, "file_logger 锚点没找到, 上游可能改了"
open(p, "w").write(s.replace(old, new))
PYSUB
fi
guard

# ── 4. 生成 gradle 工程 + 出包 ──
python3 scripts/generate.py android
G="$VKS_ROOT/build/android_gradle"
echo "sdk.dir=$SDK" > "$G/local.properties"
chmod +x "$G/gradlew"

( cd "$G" && ./gradlew assembleRelease --no-daemon \
    -Dorg.gradle.jvmargs="-Xmx1536m -Dfile.encoding=UTF-8" ) &
BUILD=$!
while kill -0 $BUILD 2>/dev/null; do
  a="$(free_mb)"
  if [ "$a" -lt "$MIN_FREE_MB" ]; then
    echo "硬盘看门狗: 构建中剩余 ${a}MB < ${MIN_FREE_MB}MB, 终止" >&2
    pkill -f GradleDaemon || true; pkill -f GradleWrapperMain || true
    kill $BUILD 2>/dev/null || true
    exit 9
  fi
  sleep 10
done
wait $BUILD || exit 2

APK="$(find "$G/app/build/outputs/apk/release" -name "*.apk" | head -1)"
[ -n "$APK" ] || { echo "构建结束但没找到 APK" >&2; exit 2; }
echo "APK: $APK ($(du -h "$APK" | cut -f1))"
echo "剩余硬盘: $(free_mb)MB"
