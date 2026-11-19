# HGamey 原神示例

该案例提供静态超分辨率和原神实机 A/B 评估。网页选择 `hgamey_shader` 示例并配置模型。启动网关后，按通用指南选择它注册的评估器卡片。使用 `llm4ad evaluator-gateway source --adapter hgamey` 定位已安装的案例文件。[远程评估网关指南](../guides/remote-evaluation.md) · [案例源码](https://github.com/Optima-CityU/LLM4AD_Next/tree/main/src/llm4ad/gateway/examples/hgamey)

## 部署

| 环境 | 要求 |
| --- | --- |
| 主机 | 当前验证范围为 Linux amd64，Compose 使用 host 网络；持久化目录可由 UID 1000 写入 |
| 构建 | Rust 1.99、OpenJDK 17、C++、Android NDK r27d、SDK API 35 / build-tools 35.0.0；Dockerfile 自动准备 |
| 执行工具 | phonefarm、ADB 36.0.2、GLSL/SPIR-V 工具、FFmpeg、NumPy、Pillow、Perfetto Python 0.59.1 与 Trace Processor；容器提供 |
| 手机 | 已 Root 的 Android 11+ ARM64，授权无线 ADB，Vulkan 1.1 compute、GPU 时间戳及可读写的 KGSL/GED 频率接口 |
| 材料 | 静态模式需要真实参考帧；游戏模式需要已安装并登录的原神及可重复场景。两者均需设备序列号和本地 ADB 身份，私有材料不随 SDK 分发 |

容器从附带源码构建运行器、Vulkan 层和 refbench APK；使用 refbench 载体时需手动安装该测试 APK。游戏评估需进入可重复场景，并按设备调整 `config/game-check.json` 的分辨率、等待时间和 `enter_script`。直接系统运行需自行准备与容器一致的工具。

在案例 `deployment/` 中以 `env.example` 为模板创建本地 `.env`，填写平台地址、授权用户 UUID 和下表的案例配置，再执行 `docker compose up -d --build`。此案例使用 host 网络，`LLM4AD_GATEWAY_UI_HOST` 和 `LLM4AD_GATEWAY_UI_PORT` 直接控制面板监听地址和端口。[部署环境变量示例](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/src/llm4ad/gateway/examples/hgamey/deployment/env.example)

| 自定义配置 | 默认值 / 用途 |
| --- | --- |
| `HGAMEY_DEVICES_JSON` | 设备数组，每项填写 `id`、`host`、`port`、`expected_serial`、`device_profile`；连接端口默认 `5555` |
| `HGAMEY_REFERENCE_PATH` | `/inputs/genshin`，仅静态评估器需要真实参考帧 |
| `HGAMEY_STATIC_DATASET_MANIFEST` | 可选，静态评估使用的 QRISP + GameIR-SR 配对清单，例如 `/inputs/static-sr-train/manifest.json` |
| `HGAMEY_DATA_DIR` / `HGAMEY_PHONEFARM_DIR` | 必填，网关/执行器状态和设备锁的宿主目录 |
| `HGAMEY_INPUTS_DIR` / `HGAMEY_CONFIG_DIR` | 参考帧宿主目录（默认 `./inputs`，仅游戏模式时可为空）与必填的配置目录 |
| `ADB_IDENTITY_FILE` / `ADB_STATE_DIR` | 必填，已授权 ADB 私钥及配对状态 |
| `ADB_BIN` / `ANDROID_ADB_SERVER_PORT` | `/usr/bin/adb` / `5039`，容器 ADB 路径及 ADB 服务端口，区别于手机连接端口 |
| `HGAMEY_STARTUP_CHECK` | `true`，启动时执行一次就绪自测 |
| `HGAMEY_EVALUATOR_MODES` | 部署示例为 `static,game`，可选择一种或同时发布两个独立评估器 |
| `HGAMEY_GAME_CHECK_CONFIG` | `game` 必填，设备与场景配置，示例为 `/config/game-check.json` |
| `HGAMEY_GAME_SCORING_JSON` | 可选 JSON，覆盖下表权重、尺度、指标来源或 `quality_gap_score`；固定在游戏评估契约中 |
| `HGAMEY_EVAL_SECONDS` / `HGAMEY_EVAL_ROUNDS` | `5` / `8`，静态每臂秒数和 A/B 轮数；游戏使用 JSON 中的 `window` / `rounds` |
| `HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT` | `1`，确认改善所需的最低百分比，取值 `[1, 100)`；未确认改善记为 `no_advantage`，仍是有效评估，不改变原始得分 |
| `HGAMEY_COMPILE_TIMEOUT` / `HGAMEY_POLL_INTERVAL` | `60` / `2` 秒，编译超时和结果轮询间隔 |
| `RUST_IMAGE` / `PYTHON_IMAGE` | `rust:1.99-slim-bookworm` / `python:3.13.16-slim-bookworm`，构建基础镜像 |
| `HGAMEY_NATIVE_IMAGE` | `native-runtime`，默认从本案例源码构建；可指定已验证的原生运行时镜像 |
| `APT_MIRROR` / `CARGO_REGISTRY_MIRROR` | 可选系统包和 Rust 构建镜像 |
| `ANDROID_REPOSITORY_BASE` / `PIP_INDEX_URL` / `TRACE_PROCESSOR_URL` | 可选工具链、Python 包和 Trace Processor 下载源 |

## 本地设备调度

HGamey 网关根据 `HGAMEY_DEVICES_JSON` 中的启用设备自动调度。评估器、数据、评分规则和设备能力配置一致的手机共用一个注册。每次执行选择一台已就绪且空闲的兼容手机，该手机负责本次测量和恢复。静态和游戏评估共用同一台手机的执行名额，不同手机可以并行。确认恢复后，新的尝试可以使用另一台兼容手机。设备地址和序列号在网关侧配置。

正常停止网关会等待正在执行的设备实验完成恢复，并保留排队任务和身份记录。平台任务保留等待，使用原数据目录重启网关后继续；排队和正常停机的等待时间不占执行超时，已消耗的执行时间不会清零，取消请求也不会被恢复操作覆盖。强制终止时，无法确认设备恢复的实验仍需核实恢复状态，不会盲目重跑。

## 评估器与结果

| 评估器 | 指标与得分 | 节点图片 |
| --- | --- | --- |
| 静态（`hgamey-genshin-sr-static`） | 测量原生 GPU 算子耗时和画质。配置配对清单时使用 16 对原生 LR/HR，常规参考帧配置使用第一张固定参考图的 1920×1080 中心裁剪 | 配对评估每个数据集上传一张拼图；单图评估展示参考、基线和候选图 |
| 游戏（`hgamey-genshin-sr-game`） | 编译两套层，注入原神并测量成对 A/B 性能窗口；窗口结束后将采集的真实游戏帧分别交给基准和候选算子在手机 GPU 上回放，以官方 IQ 计算画质。画质未验证或退化时自动给低分，无需固定参考帧目录 | 同帧输入、基准与候选的真实 GPU 输出、逐帧 IQ 报告，以及原始游戏截图和 trace |

### 准备公开数据集的静态评估

先将 [QRISP 分卷](https://www.qualcomm.com/developer/software/qualcomm-rasterized-images-dataset/downloads)和 [GameIR-SR 归档](https://huggingface.co/datasets/LLLebin/GameIR)解压到各自目录。使用离线导入命令生成清单和配对 PNG。

```bash
uv run --with 'Pillow>=10.1' python -m llm4ad.gateway.examples.hgamey.static_dataset \
  --qrisp /data/QRISP --gameir /data/GameIR-SR \
  --qrisp-revision QRISP-2024-08-16 --gameir-revision 86dd997f3c287c419e16d9fb6c199a47a17c82fb \
  --split train --output /data/gateway-inputs/static-sr-train
```

将 `/data/gateway-inputs` 用作 `HGAMEY_INPUTS_DIR`，设置 `HGAMEY_STATIC_DATASET_MANIFEST=/inputs/static-sr-train/manifest.json`，并使用同一版源码构建网关、phonefarm 和 Vulkan runner。每个数据集固定抽取 8 对。两组数据等权汇总 GPU 相对耗时改善率。两组平均 PSNR 均达标时按改善率排序，负改善率单调映射到高于 −100 的分数。测量完整但画质未达标时，候选正常完成，得分为改善率减 200 再加最差分组的 PSNR 差值（dB）。报告保留原始改善率、逐帧 PSNR、8×8 窗口亮度 SSIM 和 GPU 耗时。节点最多展示两张 PNG，每张包含三组代表样本，差值数字按正、负、零分别显示为绿色、红色和中性色。

用 `--split test` 生成独立复评清单。它从 QRISP 官方测试场景和 GameIR Town05 各抽 8 对，使用单独的 `data_version`。每对都跑完整 A/B，设备耗时按图对数增加，可通过 `HGAMEY_EVAL_SECONDS`、`HGAMEY_EVAL_ROUNDS` 和任务执行预算配置测量用时。

### 游戏 A/B 评分

游戏加权分由案例评估器计算。各分项归一化后按下表加权汇总。

| 分项 | 默认权重 | 加权前贡献 |
| --- | --- | --- |
| FPS | 0.25 | `100 × (候选 / baseline − 1) / 10` |
| 呈现帧间隔 P95 | 0.35 | `100 × (1 − 候选 / baseline) / 10`；默认 `display_frame_p95_upper_ms` 是直方图上界 |
| 每呈现帧 GPU 活跃时间 | 0.20 | `100 × (1 − 候选 / baseline) / 10`；每帧毫秒 = `10 × gpu_uid_active_pct / display_fps`，表示应用 UID 的 GPU 活跃时间，并非单独的 shader 耗时 |
| 画质 | 0.20 | 超出官方 `iq-judge` 自噪声参考线的退化取负均值；PSNR、`1−SSIM`、detail log2 分别按 1 dB、0.001、0.15 归一化，不因单纯锐化加分 |

非负权重在配置时归一化，连同来源与尺度固定进契约，不会按候选临时重配。`quality_gap_score` 是独立的有限严格负数策略值，默认 `-100.0`，不参与权重归一化。任何有效官方画质指标超出参考线时，总分取 `min(unpenalized_score, quality_gap_score)`。画质缺失、不完整、非有限、计算溢出、状态非 `measured` 或存在 `not_computed` 诊断时，标记 `unverified`；所需性能完整即可得到 `quality_gap_score`。这些低分属于有效完成，自动流水线继续运行，无需人工审核，也不记为基础设施失败。

任何正权重性能数据缺失时仍返回 `score=None`，不会伪造设备性能。画质缺失保留不可用的分项贡献、`unpenalized_score=None` 和 `missing` 诊断，不补零、不重新分配权重。显式将画质权重设为零时，跳过 IQ 评估及自动惩罚。`quality_assessment` 提供 `status`（`within_reference`、`beyond_reference`、`unverified` 或 `not_scored`）、`penalized`、固定的 `penalty_score` 和 `reasons`，用于展示自动低分原因。参与评分的输入全部有效时，`unpenalized_score` 保留原始加权和。功耗不参与评分；更换 P95 来源需事先显式修改配置并产生新契约。

游戏 JSON 默认 `quality_samples=3`、`pan=false`。每个 A/B 对选择一张本次运行采集的真实基准帧，按现有 GPU 工具的规则中心裁剪为 1920×1080，再分别执行三次基准和三次候选计算。上传的 PNG 是这些独立 GPU 调用的实际读回结果；相同算子可以产生相同哈希。每张输入单独运行官方 `iq-judge`，保留逐帧原始指标及参考线，再平均各帧的归一化罚分。所需帧缺失时继续按原规则给低分，不挑选最好的一帧，也不混合不同输入计算自噪声。

回放在游戏性能窗口和恢复之后执行，其耗时不进入 FPS、P95 或游戏 GPU 活跃时间。报告中的 `same-input-gpu-replay@1` 表示截图输入的算子画质评估；原始 A/B 游戏截图用于查看场景和执行状态。该结果不等同于对原神实际送显链路逐帧捕获的端到端画质结论。可选 pan 仍是不计分探针。正常取消会先排空当前回放、确认恢复，再停止后续帧。

点击演化节点，「节点信息」优先展示图片对照，可切换轮次、点击放大，其余附件在下方列表中查看。图片传输失败会记录诊断，不丢弃其他有效测量。上传重试不会补拍或生成图片。可用的 trace 沿用附件列表，录屏不自动上传。设备配置、ADB 密钥和输入帧目录保留本地；静态对照 PNG 会上传，请使用不含敏感内容的测试画面。

已有固定的旧版串联任务可显式选择 `legacy` 绑定继续处理，新任务使用 `static` 和／或 `game`。
