# 远程评估网关

网关在自己的工作站、集群或实验室设备上评估平台生成的算法，通过 HTTPS/SSE 主动连接平台。SDK 负责排队、并发、进度、恢复、取消和结果回传；自定义评估器负责编译、执行与评分。

## 快速开始

需要 Python 3.12+、与平台匹配的 SDK 版本及评估器依赖；未发布版本先安装本地构建的 wheel。在账号设置或任务配置中复制「我的用户 ID」。

```bash
pip install -U 'llm4ad-next[gateway-ui]'
llm4ad evaluator-gateway start
```

打开 `http://127.0.0.1:8768`，填写网关名称、允许的用户 UUID 和评估器。默认连接 `https://llm4ad-next.cn`。内置 `example` 在五组固定输入上评估 `solve(x)` 与 `x²` 的均方误差，`hgamey` 用于原神设备评估。

已有评估器时可直接无界面启动，基础安装无需 UI 依赖：

```bash
pip install -U llm4ad-next
llm4ad evaluator-gateway start --no-ui --name lab --allow-user YOUR_USER_UUID --evaluator ./evaluator.py:Evaluator
```

### Docker 启动

需要可编辑示例和 Docker 工程时，生成模板后启动：

```bash
llm4ad evaluator-gateway init ./my-gateway --name lab --allow-user YOUR_USER_UUID
cd my-gateway
llm4ad evaluator-gateway check --evaluator ./evaluator.py:Evaluator --candidate ./candidate
docker compose up -d --build
```

修改 `evaluator.py` 实现评估，额外依赖写入 `requirements.txt`。Compose 默认提供同一网关的管理面板，状态保存在 `./state`。安装包尚未发布时，可将构建的 `llm4ad_next-*.whl` 放入 `wheels/`。

## 任务设备池

新任务选择评估方法和基准后，自动使用向该用户授权的兼容设备；新设备上线即可领取后续候选，无需绑定手机编号。每次 A/B 比较在同一台设备上完成。没有可用设备、全部忙碌或离线时等待，不计为失败，也不消耗尝试次数。

| 任务评估器配置 | 默认值 | 用途 |
| --- | --- | --- |
| `selection_mode` | `auto` | 自动匹配兼容设备；`selected` 仅使用指定网关 |
| `contract` | 需选择 | 固定评估方法、数据版本及评分规则 |
| `gateway_ids` | `[]` | 仅手动选择时必填；旧配置已有编号时保留手动模式 |
| `max_attempts` | `3` | 单个候选跨设备的总尝试次数，不与网关本地次数相乘 |
| `lease_timeout` | `60` 秒 | 已领取尝试的续租期限；不是设备排队超时 |
| `batch_size` | `10` | 可同时提交的候选数；设为 `1` 会限制设备池并行利用 |

候选代码错误直接结束该评估。设备断连、租约过期或评估器故障优先换另一台兼容设备重试；旧尝试失去结果发布权限，未确认清理完成的原设备继续隔离。尝试耗尽只让当前评估失败，整轮演化继续；节点及任务进度保留失败类型和换机记录，不把基础设施问题当成代码修复建议。

自动调度需要平台及网关均支持协议 v2。升级 HGamey 后须用新生成的设备配置注册新评测契约，再为新任务选择；既有任务不会被静默切换评测口径。设备地址、序列号和安装路径仅用于本地执行绑定，不参与比较契约；实际影响结果的设备能力配置、数据和评分规则仍需兼容。

## 自定义配置

支持 CLI、`--config gateway.json` 环境键值 JSON，以及环境变量。优先级为：**CLI > 显式 JSON > 已保存的面板配置 > 环境变量 > 默认值**。自定义 JSON 环境键也会传入评估进程。

### 连接与执行

| 环境变量 | CLI 参数 | 默认值 / 用途 |
| --- | --- | --- |
| `LLM4AD_SERVER_URL` | `--server` | `https://llm4ad-next.cn`，平台地址 |
| `LLM4AD_GATEWAY_NAME` | `--name` | `local-evaluator`，显示名称 |
| `LLM4AD_ALLOWED_USER_IDS` | `--allow-user` | 必填，逗号分隔 UUID；CLI 可重复传入 |
| `LLM4AD_GATEWAY_ADAPTER` | `--adapter` | `custom`；内置可选 `example`、`hgamey` |
| `LLM4AD_EVALUATOR` | `--evaluator` | 自定义评估器必填，`FILE.py:Class` 或 `package.module:Class` |
| `LLM4AD_GATEWAY_STATE_DIR` | `--state-dir` | `.llm4ad-gateway`，身份、队列和恢复记录 |
| `LLM4AD_GATEWAY_CONCURRENCY` | `--concurrency` | `1`，并行评估数；HGamey 每台设备固定为 1 |

重启时保留完整状态目录，每个实例独占自己的目录。网关自动创建恢复密钥，无需配置平台账号密码；仅向可信用户授权，评估代码以本地账号权限运行。

### 面板与清理

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `LLM4AD_GATEWAY_UI` | 安装 UI 依赖后自动启用 | `false` 或 `--no-ui` 关闭面板 |
| `LLM4AD_GATEWAY_UI_HOST` | `127.0.0.1` | 监听地址，对应 `--ui-host` |
| `LLM4AD_GATEWAY_UI_PORT` | `8768` | 面板端口，对应 `--ui-port` |
| `LLM4AD_GATEWAY_UI_PASSWORD` | 未设置 | 独立面板密码；远程访问时必填 |
| `LLM4AD_GATEWAY_OPEN_BROWSER` | 自动检测 | `--no-open-browser` 保留面板但不打开浏览器 |
| `LLM4AD_GATEWAY_UI_LANGUAGE` | `auto` | `auto`、`zh`、`en` |
| `LLM4AD_GATEWAY_UI_THEME` | `auto` | `auto`、`dark`、`light` |
| `LLM4AD_GATEWAY_CLEANUP_ENABLED` | `true` | 清理已完成且允许删除的本地证据 |
| `LLM4AD_GATEWAY_CLEANUP_INTERVAL_SECONDS` | `30` | 清理检查间隔，秒 |
| `LLM4AD_GATEWAY_EVIDENCE_KEEP_SECONDS` | `0` | 上传及恢复确认后的额外证据保留时间，秒 |
| `LLM4AD_GATEWAY_LOG_KEEP_SECONDS` | `604800` | 本地执行日志保留时间，默认 7 天 |

名称、授权用户、并发和清理策略可在面板中更新；平台、评估器和状态目录的变更会等待当前执行、恢复及上传结束。CLI 固定的值需修改启动参数。

### Docker 配置

| 配置项 | 用途 |
| --- | --- |
| `LLM4AD_PACKAGE` | 安装与平台兼容的 SDK；本地 `wheels/` 优先 |
| `PIP_INDEX_URL` | 构建时的 Python 包源，默认 `https://pypi.org/simple` |
| `GATEWAY_BASE_IMAGE` | 内置 basic 案例基础镜像，默认 `python:3.12-slim` |
| `LLM4AD_GATEWAY_ENV_FILE` | 直接使用 basic 案例 Compose 时的环境文件绝对路径 |
| `LLM4AD_GATEWAY_DATA_DIR` | 直接使用 basic 案例 Compose 时的持久化目录绝对路径 |

直接使用 basic 案例时，在其 `deployment/` 目录复制 `env.example` 为 `.env`，填写授权用户、环境文件及持久化目录路径，再执行 `docker compose --env-file .env up -d --build`。

basic/自定义模板使用 bridge 网络，面板地址变量控制宿主端口映射；HGamey 使用 host 网络，同名变量直接控制监听地址。容器默认启用面板且不自动打开浏览器，面板密码通过运行时环境传入。

## 在平台中使用

新建任务时选择「远程评估器」，勾选授权给自己的网关。多个网关须具有相同评估契约，每个候选只分配给其中一个；离线时任务可等待网关上线。

```yaml
evaluator:
  type: remote
  gateway_ids: [YOUR_GATEWAY_UUID]
  timeout: 300
  parallel: true
  batch_size: 4
  candidate_paths: ["solution.py"]
  parameters: {}
```

| 任务配置 | 含义 |
| --- | --- |
| `timeout` | 单候选执行超时，秒；等待网关或容量不计入 |
| `parallel` / `batch_size` | 平台候选并发；实际执行仍受网关容量限制 |
| `candidate_paths` | 额外候选文件的相对 glob；默认传输源码和构建清单 |
| `parameters` | 显式传给评估器的自定义参数，对应 `job.parameters` |

候选源码及 EVOLVE 标记保留在平台，数据集和评估依赖保留在网关。默认排除 `config.yaml`、`.env*`、运行状态及数据目录。远程任务依赖平台运行环境，单独执行 `llm4ad run` 不会创建平台远程任务。

## 实现自定义评估器

继承 [GatewayEvaluator 基类](../../../src/llm4ad/gateway/sdk/contracts.py)，声明 `EvaluationContract` 并实现 `async evaluate(job, reporter)`。参见[完整基础示例](../../../src/llm4ad/gateway/examples/basic/evaluator.py)，其得分为负均方误差。平台聚合 `score` 越大越好，各指标方向由契约指定。

| 接口 | 用途 |
| --- | --- |
| `contract` | `evaluator_id` 标识实现，`version` / `data_version` 标识评估逻辑及本地数据版本，`metrics` 声明指标方向 |
| `job.project_root` / `timeout` / `evaluation_profile` | 候选目录、执行预算及 `standard` / `elite` 档位 |
| `job.parameters` | 网页任务显式传入的自定义参数；basic 案例未使用此项 |
| `reporter.progress(...)` / `message(...)` | 上报进度和状态事件 |
| `reporter.metadata(dict)` | 保存额外 JSON 信息 |
| `reporter.artifact(name, path, media_type)` | 声明需上传的报告、图片或 Trace |
| `reporter.local_log(name, description=...)` | 登记仅供网关面板查看的本地日志 |
| `EvaluationResult` | 返回 `score`、`metrics`、`monitor_metrics`、`metadata`、`evolution_feedback`、`duration_ms`、`artifacts` |
| `EvaluationError` | 已知评估失败，可携带反馈、元数据和附件 |
| `EvaluationConfigurationError` | 参数或评估器配置错误，直接失败，不进行换机重试 |
| `EvaluationUnavailableError` | 瞬时基础设施故障，在原执行预算内自动重试 |
| `EvaluationPreparationError` | 确认未下发外部任务的准备失败，直接返回失败而非无限等待恢复 |
| `TaskCheckpoints(job, directory, inputs=..., context=...)` | SDK 冻结输入并保存本地编译、预处理步骤的检查点 |

评估工作在独立进程中执行。本地数据和工具通过 `__file__` 或绝对路径定位；工作目录是候选目录。

候选代码或输出错误使用 `EvaluationError(..., metadata={"failure_category": "candidate"})`；临时设备故障使用 `EvaluationUnavailableError`。未分类的评估器异常按 `evaluator` 处理并计入换机预算，避免误判为候选代码问题。

以下扩展均可选，普通评估器无需实现调度、恢复或取消：

| 扩展 | 用途 |
| --- | --- |
| `before_task(job, reporter, operation)` | 操作前编译或准备资源 |
| `after_task(job, reporter, operation, *, result=None, error=None)` | 操作后的诊断和清理；强制终止不能保证执行 |
| `reporter.save_state(dict)` / `saved_state` | 持久化、读取外部执行句柄 |
| `recover(job, reporter)` | 保留的兼容接口；有限次数的新执行由 SDK 管理 |
| `cancel(job, saved_state)` | 仅在确认外部执行停止后返回 `True` |
| `cleanup_timeout` | 正有限数值，清理超时秒数；默认 `30`，应覆盖钩子与外部资源恢复 |
| `create_runtime()` / `runtime.local_logs(...)` | 接入持久化设备资源、自测及已有外部日志 |

`operation` 为 `evaluate` 或 `cancel`；`recover` 保留兼容。外部提交使用 `job.id` / `attempt_id` 加 `job.execution_id` 区分每次本地执行，并在等待前保存执行句柄。SDK 负责终止本地进程，`cancel` 负责恢复已修改的外部资源。

耗时的本地准备可声明步骤，由 SDK 保存和校验检查点；案例无需自行读写检查点文件。重新进入同一步骤时，SDK 复用已完成产物，保留未完成步骤的残留文件后重跑。输入和产物的哈希不匹配时明确报错。

```python
from llm4ad.gateway.sdk import TaskCheckpoints

steps = TaskCheckpoints(job, build_dir, inputs={"source.cpp": source}, context={"version": "1"})
steps.run("compile", lambda: compile_source(steps.path("source.cpp"), build_dir / "binary"),
          outputs=["binary", "compiler.log"], required=["binary"])
```

检查点仅用于可重复执行的本地准备；外部设备任务仍按原幂等标识接续，不能放入可重跑的步骤。

## 日志、附件与恢复

| 内容 | 查看位置 / 保留规则 |
| --- | --- |
| 状态事件 | 网关面板，支持筛选和历史查询；最终保留于 `metadata.evaluation_messages` |
| 本地执行日志 | 网关面板中的 stdout/stderr；不自动上传，按日志保留时间清理 |
| 明确声明的附件 | 点击演化节点，在右侧「节点信息」查看 metadata 和附件；图片缩略图可点击放大，其他文件以紧凑列表展示并支持预览、下载 |
| 生命周期计时 | 面板展示真实开始、结束及含前后处理的总耗时；`duration_ms` 保留评估器原值 |

本地评估器在 `cfg.artifact_dir` 写文件，并通过 `EvaluationResult.artifacts` 声明；远程评估器可使用 `reporter.artifact()` 或 `job.artifact_dir`。文件保存在 RustFS/S3，只收集明确声明的附件和返回的行为可视化数据，示例见 [evaluation_artifacts](../../../examples/evaluation_artifacts/)。

上传失败只重传文件。网关收到文件及结果确认、且外部设备恢复完成后才清理证据；本地日志独立保留。平台资料随任务保留，删除任务时清理。

| 情况 | 行为 |
| --- | --- |
| 网络中断 | 本地评估继续，恢复连接后补报；SSE 自动重连，HTTP 心跳继续同步 |
| 网关重启 | 使用原状态目录，自动核对旧进程归属并清理设备，然后重试未完成的执行 |
| 瞬时基础设施故障 | 协议 v2 设备池统一使用平台的 `max_attempts` 预算，每次领取只执行一次。旧协议任务最多在本地执行 3 次，计数跨重启保留，并在原有效执行预算内按 1、2 秒退避 |
| 读取失败 | 无副作用的读取可使用 `retry_read(...)`，最多调用 3 次；无效候选不重试 |
| 本地准备中断 | SDK 检查点保存准备产物；每次新执行使用独立目录 |
| 执行超时 / 平台停止 | 停止指定进程池槽，恢复外部资源并保存终态 |
| 资源清理失败 | 清理连续失败 3 次后明确返回基础设施失败；继续自动探测，仅该资源恢复前不可用，其他资源继续工作 |

控制器使用 `transitions`、`persist-queue.SQLiteAckQueue` 和公开的 `loky.ProcessPoolExecutor`。每个资源槽最多提交一个 Future；先保存结果，再确认队列收据，文件和结果重传不会重跑实机评估。不需要额外消息服务。旧的恢复记录自动迁移，日常运行无需手动 `resolve`。

```bash
llm4ad evaluator-gateway health --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
```

## HGamey 原神示例

[案例源码](../../../src/llm4ad/gateway/examples/hgamey/)包含编译、设备执行器、无线 ADB 和部署文件。网页选择 `hgamey_shader` 示例，绑定模型与网关；使用 `llm4ad evaluator-gateway source --adapter hgamey` 定位安装目录。

| 环境 | 要求 |
| --- | --- |
| 主机 | 当前验证范围为 Linux amd64，Compose 使用 host 网络；持久化目录可由 UID 1000 写入 |
| 构建 | Rust 1.99、OpenJDK 17、C++、Android NDK r27d、SDK API 35 / build-tools 35.0.0；Dockerfile 自动准备 |
| 执行工具 | phonefarm、ADB 36.0.2、GLSL/SPIR-V 工具、FFmpeg、NumPy、Pillow、Perfetto Python 0.59.1 与 Trace Processor；容器提供 |
| 手机 | 已 Root 的 Android 11+ ARM64，授权无线 ADB，Vulkan 1.1 compute、GPU 时间戳及可读写的 KGSL/GED 频率接口 |
| 材料 | 静态模式需要真实参考帧；游戏模式需要已安装并登录的原神及可重复场景。两者均需设备序列号和本地 ADB 身份，私有材料不随 SDK 分发 |

容器从附带源码构建运行器、Vulkan 层和 refbench APK；使用 refbench 载体时需手动安装该测试 APK。游戏评估需进入可重复场景，并按设备调整 `config/game-check.json` 的分辨率、等待时间和 `enter_script`。直接系统运行需自行准备与容器一致的工具。

在案例 `deployment/` 中按 [env.example](../../../src/llm4ad/gateway/examples/hgamey/deployment/env.example) 创建本地 `.env`，填写下表配置，再执行 `docker compose up -d --build`。

| 自定义配置 | 默认值 / 用途 |
| --- | --- |
| `HGAMEY_DEVICES_JSON` | 设备数组，每项填写 `id`、`host`、`port`、`expected_serial`、`device_profile`；连接端口默认 `5555` |
| `HGAMEY_REFERENCE_PATH` | `/inputs/genshin`，仅静态评估器需要真实参考帧 |
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

先在远程评估器中选择「固定图片 GPU」或「原神游戏 A/B」，再选择兼容网关。两者是独立评估器，不是前后阶段；每个候选只执行所选实验，案例关闭精英复评。同一设备的两个绑定共享执行队列和设备锁，不会同时操作手机。不同手机各有一个执行槽，可以并行；某台手机恢复未确认时，仅暂停该手机的后续评估。

正常停止网关会等待正在执行的设备实验完成恢复，并保留排队任务和身份记录。平台任务保留等待，使用原数据目录重启网关后继续；排队和正常停机的等待时间不占执行超时，已消耗的执行时间不会清零，取消请求也不会被恢复操作覆盖。强制终止时，无法确认设备恢复的实验仍需核实恢复状态，不会盲目重跑。

| 评估器 | 指标与得分 | 节点图片 |
| --- | --- | --- |
| 静态（`hgamey-genshin-sr-static`） | 测量原生 GPU 算子耗时及 PSNR，自动应用固定 reference 的 PSNR 约束，按本轮相对基线耗时改善率评分。使用第一张固定参考图的 1920×1080 中心裁剪，不启动原神 | GPU 画质测试实际使用的参考、baseline 和候选图；读回及 PNG 导出在计时之外 |
| 游戏（`hgamey-genshin-sr-game`） | 编译两套层，注入原神并测量成对 A/B 性能窗口；窗口结束后将采集的真实游戏帧分别交给基准和候选算子在手机 GPU 上回放，以官方 IQ 计算画质。画质未验证或退化时自动给低分，无需固定参考帧目录 | 同帧输入、基准与候选的真实 GPU 输出、逐帧 IQ 报告，以及原始游戏截图和 trace |

两个评估器均全自动运行。游戏加权分是平台适配方案，不是上游 HGamey 的现成总分，也不是显著性检验。各项归一化后使用下表固定权重。静态画质测量、固定 reference 的 PSNR 自动约束与评分保持不变。

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

## 平台部署配置

升级时同步更新后端、worker、task-runner、前端和网关 SDK，并应用 Alembic 迁移。标准 `docker/compose.yml` 已包含 `remote-evaluation-worker`；使用生产基础文件时保留原有覆盖配置，并添加：

```bash
cd docker
docker compose -f compose-cloud.yml -f compose.remote-evaluation.yml up -d --build
```

| 配置项 | 默认值 / 用途 |
| --- | --- |
| `LLM4AD_COMPOSE_BASE` | 标准部署为 `compose.yml`；上述生产命令需在 `.env` 设置为 `compose-cloud.yml` |
| `REMOTE_EVALUATION_CONCURRENCY` | `10`，平台远程评估 worker 并发 |
| `LLM_PROXY_ENABLE` / `LLM_PROXY_BASE_URL` | 长时间等待后继续调用模型时，设为 `True` 并填写任务容器可达的代理地址 |
| `EVALUATION_PREVIEW_MAX_BYTES` | `2097152`（2 MiB），限制服务端内联预览；图片缩略图和放大查看使用原图下载接口，原文件下载不受此限制 |
