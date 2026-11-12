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
| `EvaluationUnavailableError` | 资源不可用或外部结果不确定，等待恢复 |

评估工作在独立进程中执行。本地数据和工具通过 `__file__` 或绝对路径定位；工作目录是候选目录。

以下扩展均可选，普通评估器无需实现调度、恢复或取消：

| 扩展 | 用途 |
| --- | --- |
| `before_task(job, reporter, operation)` | 操作前编译或准备资源 |
| `after_task(job, reporter, operation, *, result=None, error=None)` | 操作后的诊断和清理；强制终止不能保证执行 |
| `reporter.save_state(dict)` / `saved_state` | 持久化、读取外部执行句柄 |
| `recover(job, reporter)` | 核实外部执行，返回已有结果；无法确定时返回 `None` |
| `cancel(job, saved_state)` | 仅在确认外部执行停止后返回 `True` |
| `create_runtime()` / `runtime.local_logs(...)` | 接入持久化设备资源、自测及已有外部日志 |

`operation` 为 `evaluate`、`recover` 或 `cancel`。外部提交使用 `job.id` / `attempt_id` 做幂等标识，并在等待前保存执行句柄。

## 日志、附件与恢复

| 内容 | 查看位置 / 保留规则 |
| --- | --- |
| 状态事件 | 网关面板，支持筛选和历史查询；最终保留于 `metadata.evaluation_messages` |
| 本地执行日志 | 网关面板中的 stdout/stderr；不自动上传，按日志保留时间清理 |
| 明确声明的附件 | 平台「评估详情」，支持文本、JSON、Markdown、图片预览及原文件下载 |
| 生命周期计时 | 面板展示真实开始、结束及含前后处理的总耗时；`duration_ms` 保留评估器原值 |

本地评估器在 `cfg.artifact_dir` 写文件，并通过 `EvaluationResult.artifacts` 声明；远程评估器可使用 `reporter.artifact()` 或 `job.artifact_dir`。文件保存在 RustFS/S3，只收集明确声明的附件和返回的行为可视化数据，示例见 [evaluation_artifacts](../../../examples/evaluation_artifacts/)。

上传失败只重传文件。网关收到文件及结果确认、且外部设备恢复完成后才清理证据；本地日志独立保留。平台资料随任务保留，删除任务时清理。

| 情况 | 行为 |
| --- | --- |
| 网络中断 | 本地评估继续，恢复连接后补报；SSE 自动重连，HTTP 心跳继续同步 |
| 网关重启 | 使用原状态目录；确认旧进程停止且无外部句柄后，可从头重放未完成的本地评估 |
| 执行超时 | 本地终止进程树；外部执行仍需核实 |
| 平台停止 | 取消排队作业，并持久化已领取作业的取消请求 |
| 外部状态不确定 | 等待恢复，不自动重复实验，也不转派已领取作业 |

确认旧执行已结束后，才对不确定作业手动执行 `resolve`：

```bash
llm4ad evaluator-gateway health --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action retry --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action cancelled --state-dir ./.llm4ad-gateway
```

## HGamey 原神示例

[案例源码](../../../src/llm4ad/gateway/examples/hgamey/)包含编译、设备执行器、无线 ADB 和部署文件。网页选择 `hgamey_shader` 示例，绑定模型与网关；使用 `llm4ad evaluator-gateway source --adapter hgamey` 定位安装目录。

| 环境 | 要求 |
| --- | --- |
| 主机 | 当前验证范围为 Linux amd64，Compose 使用 host 网络；持久化目录可由 UID 1000 写入 |
| 构建 | Rust 1.99、OpenJDK 17、C++、Android NDK r27d、SDK API 35 / build-tools 35.0.0；Dockerfile 自动准备 |
| 执行工具 | phonefarm、ADB 36.0.2、GLSL/SPIR-V 工具、FFmpeg、NumPy、Pillow、Perfetto Python 0.59.1 与 Trace Processor；容器提供 |
| 手机 | 已 Root 的 Android 11+ ARM64，授权无线 ADB，Vulkan 1.1 compute、GPU 时间戳及可读写的 KGSL/GED 频率接口 |
| 材料 | 已安装并登录的原神、真实参考帧、设备序列号和本地 ADB 身份；不随 SDK 分发 |

容器从附带源码构建运行器、Vulkan 层和 refbench APK；使用 refbench 载体时需手动安装该测试 APK。游戏验证需进入可重复场景，并按设备调整 `config/game-check.json` 的分辨率、等待时间和 `enter_script`。直接系统运行需自行准备与容器一致的工具。

在案例 `deployment/` 中按 [env.example](../../../src/llm4ad/gateway/examples/hgamey/deployment/env.example) 创建本地 `.env`，填写下表配置，再执行 `docker compose up -d --build`。

| 自定义配置 | 默认值 / 用途 |
| --- | --- |
| `HGAMEY_DEVICES_JSON` | 设备数组，每项填写 `id`、`host`、`port`、`expected_serial`、`device_profile`；连接端口默认 `5555` |
| `HGAMEY_REFERENCE_PATH` | `/inputs/genshin`，真实参考帧目录 |
| `HGAMEY_DATA_DIR` / `HGAMEY_PHONEFARM_DIR` | 必填，网关/执行器状态和设备锁的宿主目录 |
| `HGAMEY_INPUTS_DIR` / `HGAMEY_CONFIG_DIR` | 必填，参考帧与游戏配置的宿主目录 |
| `ADB_IDENTITY_FILE` / `ADB_STATE_DIR` | 必填，已授权 ADB 私钥及配对状态 |
| `ADB_BIN` / `ANDROID_ADB_SERVER_PORT` | `/usr/bin/adb` / `5039`，容器 ADB 路径及 ADB 服务端口，区别于手机连接端口 |
| `HGAMEY_STARTUP_CHECK` | `true`，启动时执行一次就绪自测 |
| `HGAMEY_GAME_CHECK_CONFIG` | 可选游戏验证 JSON；部署环境示例使用 `/config/game-check.json` |
| `HGAMEY_EVAL_SECONDS` / `HGAMEY_EVAL_ROUNDS` | `5` / `8`，常规评估每臂秒数和轮数 |
| `HGAMEY_ELITE_EVAL_SECONDS` / `HGAMEY_ELITE_EVAL_ROUNDS` | 默认至少 `5` / `16`，精英复评预算 |
| `HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT` | `1`，确认改善所需的最低百分比，取值 `[1, 100)`；不改变原始得分 |
| `HGAMEY_COMPILE_TIMEOUT` / `HGAMEY_POLL_INTERVAL` | `60` / `2` 秒，编译超时和结果轮询间隔 |
| `RUST_IMAGE` / `PYTHON_IMAGE` | `rust:1.99-slim-bookworm` / `python:3.13.16-slim-bookworm`，构建基础镜像 |
| `HGAMEY_NATIVE_IMAGE` | `native-runtime`，默认从本案例源码构建；可指定已验证的原生运行时镜像 |
| `APT_MIRROR` / `CARGO_REGISTRY_MIRROR` | 可选系统包和 Rust 构建镜像 |
| `ANDROID_REPOSITORY_BASE` / `PIP_INDEX_URL` / `TRACE_PROCESSOR_URL` | 可选工具链、Python 包和 Trace Processor 下载源 |

每台设备串行评估。当前画质检查使用第一张固定参考图的中心裁剪区域，参考图至少为 1920×1080；它衡量静态重建质量，算子耗时改善不能直接代表游戏帧率提升。功耗默认关闭，不可用指标保持未测得。密钥、设备配置及真实参考帧只保存在本地。

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
| `EVALUATION_PREVIEW_MAX_BYTES` | `2097152`（2 MiB），仅限制附件预览，不影响原文件下载 |
