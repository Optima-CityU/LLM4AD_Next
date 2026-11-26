# Filament FSR1 移动版单遍超分

演化项目 [filament_fsr1_single_pass](../../applications/filament_fsr1_single_pass) 从 Filament v1.75.0 的原版 EASU shader 开始，只开放 `FsrEasuL` 函数体。候选由官方 `matc` 编译。评估程序在 Android Mali 的 Vulkan 单遍渲染路径上，只替换移动版 EASU 材质。 [官方发布版](https://github.com/google/filament/releases/tag/v1.75.0) · [原版 shader](https://github.com/google/filament/blob/v1.75.0/filament/src/materials/fsr/ffx_fsr1_mobile.fs)

- **场景**　DamagedHelmet 固定轨道绕拍、Fox Walk 固定镜头。两者使用相同环境光，输入比例固定为 2/3，输出 1920×1080。
- **性能**　每个场景交替运行 4 组基线和候选。每次预热 30 帧，再记录 120 帧 `Renderer::FrameInfo.gpuFrameDuration`。每组按 `100 × (基线 P50 − 候选 P50) / 基线 P50` 计算改善率。两个场景各自改善率中位数的平均值以 `improvement_pct` 提交。相同 Vulkan shader 的改善率记 0，原始测量仍可查阅。
- **画质**　两个场景各取 12 个固定帧号。原版和候选各自与同一帧的原生 1080p 参考图计算 NVIDIA LDR-FLIP，固定 67 px/deg。任一场景的平均误差或最差帧误差上升时，按最大的误差增量返回 `−100 × (1 + 误差增量)`，并保留评分原因。画质合格时按性能改善率排序，负改善率单调映射至 `(−100, 0)`。
- **附件**　FLIP 模式只回传一张 `FLIP-comparison.jpg`。拼图合并两个场景的第 60 帧，每组并排显示原生参考、官方基线和候选。三列标记并放大同一区域，基线和候选同时展示与参考图的 FLIP 热图。逐帧分数在 `flip.json`，像素差异和配对计时在 `imagediff.json`，原始 GPU 计时另附。

[DamagedHelmet 模型](https://github.com/google/filament/blob/v1.75.0/third_party/models/DamagedHelmet/README.md) · [Fox 模型](https://github.com/google/filament/blob/v1.75.0/third_party/models/Fox/README.md) · [ImageDiff](https://github.com/google/filament/tree/v1.75.0/tools/diffimg)

通过 `evaluator.parameters.quality_mode` 选择规则。模板使用 `flip`。选 `pixel_exact` 时，若有像素差异，按不一致帧对的比例给予低分。未填写参数的旧任务默认采用 `pixel_exact`。

同一个网关还注册 **Relaxed Quality** 评估器（`filament-fsr1-android-performance`）。它共用手机、场景和计时流程。每个场景的平均 FLIP 和最差帧 FLIP 都允许比基线增加最多 **0.10**。任一采样帧的 R、G、B 各通道在整帧内均只变化 0 或 1 个灰阶时，视为近似纯色。超限或出现近似纯色帧的候选记 `−100 × (1 + 最大超限量)`，保留实测性能、画质报告和一张对比图。合格候选仍按 GPU 帧耗时改善率排序。

## 构建

需要 Linux x86-64、Python 3.12、Bubblewrap、CMake、Ninja、Android NDK 以及已授权 ADB 的 Android arm64 Mali 真机。从仓库根目录运行。

```bash
demo=examples/remote_evaluation/filament_fsr
work=/path/to/filament-work
mkdir -p "$work/sdk/host" "$work/sdk/android"
git clone --branch v1.75.0 --depth 1 https://github.com/google/filament.git "$work/filament"

# Use the Linux and Android native archives from the v1.75.0 release.
tar -xf /path/to/filament-v1.75.0-linux.tgz -C "$work/sdk/host" --strip-components=1
tar -xf /path/to/filament-v1.75.0-android-native.tgz -C "$work/sdk/android" --strip-components=1
# Extract the FLIP 1.7 source archive from PyPI. FLIP.h is in src/cpp.
tar -xf /path/to/flip_evaluator-1.7.tar.gz -C "$work"

cmake -S "$demo" -B "$work/build-android" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24 -DANDROID_STL=c++_static \
  -DCMAKE_BUILD_TYPE=Release -DFILAMENT_SDK="$work/sdk/android" \
  -DFILAMENT_SOURCE="$work/filament" -DFILAMENT_HOST_TOOLS="$work/sdk/host/bin" \
  -DFILAMENT_FLIP_INCLUDE="$work/flip-evaluator-1.7/src/cpp"
cmake --build "$work/build-android"
```

FLIP 使用源包中的 C++ 实现，在手机端完成画质比较。启动网关需要设置 `FILAMENT_FLIP_INCLUDE`，以便两个评估器都能通过自检。[FLIP 1.7 源包](https://pypi.org/project/flip-evaluator/1.7/#files)

启用 FLIP 时，在运行网关的 Python 环境安装拼图依赖。

```bash
python -m pip install -r examples/remote_evaluation/filament_fsr/requirements.txt
```

## 运行

```bash
export FILAMENT_RUNNER="$work/build-android/filament_fsr_baseline"
export FILAMENT_STOCK_MATERIAL="$work/build-android/stock-easu.filamat"
export FILAMENT_HOST_TOOLS="$work/sdk/host/bin"
export FILAMENT_FSR_SOURCE="$work/filament/filament/src/materials/fsr"
export FILAMENT_SCENE="$work/filament/third_party/models/DamagedHelmet/DamagedHelmet.glb"
export FILAMENT_FOX_SCENE="$work/filament/third_party/models/Fox/Fox.glb"
export FILAMENT_IBL="$work/sdk/host/bin/assets/ibl/lightroom_14b/lightroom_14b_ibl.ktx"
export FILAMENT_DEVICE='<authorized device serial or wireless host:port>'

llm4ad evaluator-gateway check --evaluator "$demo/evaluator.py:Evaluator" \
  --candidate examples/applications/filament_fsr1_single_pass/algorithm \
  --state-dir "$work/check" --timeout 420

export FILAMENT_STARTUP_QUALITY_MODE=flip
llm4ad evaluator-gateway start --name "Filament FSR1 Single-Pass / Android Mali" \
  --evaluator "$demo/evaluator.py:Evaluator" --state-dir "$work/gateway" --concurrency 1
```

`FILAMENT_ADB` 和 `FILAMENT_ADB_PORT` 默认为 `adb` 和 `5037`。专用测试手机若会因息屏进入休眠，可设置 `FILAMENT_WAKE_SCREEN=1`，让每次渲染前唤醒屏幕。首次启动会在手机缓存固定模型和渲染程序，任务结束后清理候选及临时结果。设置 `FILAMENT_STARTUP_QUALITY_MODE=flip` 后，网关上线前会用原版对原版校验 LDR-FLIP 路径。

注册时按 SDK 设置 `LLM4AD_SERVER_URL` 和 `LLM4AD_ALLOWED_USER_IDS`。启动自检通过后，在平台新建任务时选择 `filament_fsr1_single_pass → config.yaml`，再选择对应网关与模型。
