# Filament FSR1 移动版单遍超分

平台任务模板位于 `examples/applications/filament_fsr1_single_pass`，从 Filament v1.75.0 的原版 EASU shader 开始，只开放 `FsrEasuL` 函数体。候选由官方 `matc` 编译。评估程序在 Android Mali 的 Vulkan 单遍渲染路径上，只替换移动版 EASU 材质。 [官方发布版](https://github.com/google/filament/releases/tag/v1.75.0) · [原版 shader](https://github.com/google/filament/blob/v1.75.0/filament/src/materials/fsr/ffx_fsr1_mobile.fs)

安装匹配版本的 `llm4ad-next` 后，可以查看示例源码、容器部署文件和任务模板的路径。

```bash
llm4ad evaluator-gateway source --adapter filament
```

- **场景**　DamagedHelmet 固定轨道绕拍、Fox Walk 固定镜头。两者使用相同环境光，输入比例固定为 2/3，输出 1920×1080。
- **性能**　每个场景交替运行 4 组基线和候选。每次预热 30 帧，再记录 120 帧 `Renderer::FrameInfo.gpuFrameDuration`。每组按 `100 × (基线 P50 − 候选 P50) / 基线 P50` 计算改善率。两个场景各自改善率中位数的平均值以 `improvement_pct` 提交。相同 Vulkan shader 的改善率记 0，原始测量仍可查阅。
- **画质**　两个场景各取 12 个固定帧号。原版和候选各自与同一帧的原生 1080p 参考图计算 NVIDIA LDR-FLIP，固定 67 px/deg。任一场景的平均误差或最差帧误差上升时，按最大的误差增量返回 `−100 × (1 + 误差增量)`，并保留评分原因。画质合格时按性能改善率排序，负改善率单调映射至 `(−100, 0)`。
- **附件**　FLIP 模式只回传一张 `FLIP-comparison.jpg`。拼图合并两个场景的第 60 帧，每组并排显示原生参考、官方基线和候选。三列标记并放大同一区域，基线和候选同时展示与参考图的 FLIP 热图。逐帧分数在 `flip.json`，像素差异和配对计时在 `imagediff.json`，原始 GPU 计时另附。

[DamagedHelmet 模型](https://github.com/google/filament/blob/v1.75.0/third_party/models/DamagedHelmet/README.md) · [Fox 模型](https://github.com/google/filament/blob/v1.75.0/third_party/models/Fox/README.md) · [ImageDiff](https://github.com/google/filament/tree/v1.75.0/tools/diffimg)

通过 `evaluator.parameters.quality_mode` 选择规则。模板使用 `flip`。选 `pixel_exact` 时，若有像素差异，按不一致帧对的比例给予低分。未填写参数的旧任务默认采用 `pixel_exact`。

同一个网关还注册 **Relaxed Quality** 评估器（`filament-fsr1-android-performance`）。它共用手机、场景和计时流程。每个场景的平均 FLIP 和最差帧 FLIP 都允许比基线增加最多 **0.10**。任一采样帧的 R、G、B 各通道在整帧内均只变化 0 或 1 个灰阶时，视为近似纯色。超限或出现近似纯色帧的候选记 `−100 × (1 + 最大超限量)`，保留实测性能、画质报告和一张对比图。合格候选仍按 GPU 帧耗时改善率排序。

## 构建

需要 Linux x86-64（官方宿主编译器需要 glibc 2.38 或更高）、Python 3.12+、
Bubblewrap、CMake、Ninja、Android NDK 和已授权 ADB 的 Android arm64 Mali 真机。
以下构建步骤共用同一个工作目录，可以用于原生或 Docker 启动。

```bash
demo="$(llm4ad evaluator-gateway source --adapter filament | python -c 'import json,sys; print(json.load(sys.stdin)["example"])')"
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

构建时通过 `FILAMENT_FLIP_INCLUDE` 启用手机侧画质比较，启动网关时使用构建好的评估程序。[FLIP 1.7 源包](https://pypi.org/project/flip-evaluator/1.7/#files)

## 原生启动

在运行网关的 Python 环境安装拼图依赖。

```bash
python -m pip install -r "$demo/requirements.txt"
```

```bash
export FILAMENT_RUNNER="$work/build-android/filament_fsr_baseline"
export FILAMENT_STOCK_MATERIAL="$work/build-android/stock-easu.filamat"
export FILAMENT_HOST_TOOLS="$work/sdk/host/bin"
export FILAMENT_FSR_SOURCE="$work/filament/filament/src/materials/fsr"
export FILAMENT_SCENE="$work/filament/third_party/models/DamagedHelmet/DamagedHelmet.glb"
export FILAMENT_FOX_SCENE="$work/filament/third_party/models/Fox/Fox.glb"
export FILAMENT_IBL="$work/sdk/host/bin/assets/ibl/lightroom_14b/lightroom_14b_ibl.ktx"
export FILAMENT_DEVICE='<authorized device serial or wireless host:port>'

llm4ad evaluator-gateway check --adapter filament --state-dir "$work/check" --timeout 420

llm4ad evaluator-gateway start --adapter filament --no-ui --name filament-fsr1 \
  --allow-user YOUR_USER_UUID --state-dir "$work/gateway"
```

`FILAMENT_ADB` 和 `FILAMENT_ADB_PORT` 默认为 `adb` 和 `5037`。专用测试手机若会因息屏进入休眠，可设置 `FILAMENT_WAKE_SCREEN=1`，让每次渲染前唤醒屏幕。首次启动会在手机缓存固定模型和渲染程序，任务结束后清理候选及临时结果。网关上线前会对原版着色器运行 LDR-FLIP 自检。

在平台账号设置中复制「我的用户 ID」。替换 `YOUR_USER_UUID`，或设置 `LLM4AD_ALLOWED_USER_IDS`。
更换平台时可加 `--server`，默认连接官网。要使用管理面板，安装
`llm4ad-next[gateway-ui]`，启动时去掉 `--no-ui`。

## Docker 启动

先在 Linux 宿主机完成上述构建和 ADB 授权。容器复用宿主机的 ADB 服务和只读评估资源。
将部署模板复制到可写目录，避免在 SDK 安装目录里保存本地配置。

```bash
mkdir -p ./filament-gateway
cp "$demo/requirements.txt" ./filament-gateway/
cp -R "$demo/deployment" ./filament-gateway/
cd ./filament-gateway/deployment
cp env.example .env
mkdir -p state
```

| `.env` 项 | 设置 |
| --- | --- |
| `FILAMENT_WORK_DIR` | 构建步骤中的绝对工作路径，包含 `build-android`、`sdk` 和 `filament` |
| `FILAMENT_DEVICE` / `FILAMENT_ADB_PORT` | 已授权的设备序列号或无线 ADB 地址，宿主机 ADB 服务端口 |
| `FILAMENT_GATEWAY_UID` / `FILAMENT_GATEWAY_GID` | 对 `state` 有写权限的非 root 用户与组，可用 `id -u` 和 `id -g` 查到 |
| `LLM4AD_ALLOWED_USER_IDS` | 允许使用网关的平台用户 UUID |
| `LLM4AD_SERVER_URL` | 默认官网，可指定测试或私有站点 |

容器中的 Bubblewrap 需要 Linux 宿主机允许嵌套用户命名空间。网关以非 root 用户运行并移除
容器 capabilities，候选编译在关闭网络的 Bubblewrap 沙箱内执行。同一台手机只启动一套网关。
启用了 AppArmor 用户命名空间限制的宿主机（如 Ubuntu 24.04）需要先加载此示例的配置。

```bash
sudo apparmor_parser -r apparmor.profile
```

然后在 `.env` 设 `FILAMENT_APPARMOR_PROFILE=llm4ad-filament-gateway`。已经加载了允许
`userns` 的适用配置时，也可以设置为该配置的名字。

SDK 尚未发布时，创建 `deployment/wheels/` 并放入一个匹配的 wheel。也可以通过
`LLM4AD_PACKAGE` 指定已发布的兼容版本。随后构建并实测。

```bash
docker compose build gateway
docker compose run --rm --no-deps gateway check --adapter filament --state-dir /state/check --timeout 420
docker compose up -d gateway
docker compose logs -f gateway
```

管理面板默认监听宿主机的 `127.0.0.1:8768`。启动自检通过后，在平台选择
`filament_fsr1_single_pass → config.yaml`，再绑定已上线的网关评估器和模型。
