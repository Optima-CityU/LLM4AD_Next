# 远程评估网关

评估网关通过 HTTPS/SSE 主动连接平台，在网关侧运行候选代码。平台把评估任务发给用户选中的网关注册项。网关 SDK 负责本地排队、并发、进度、恢复、取消和结果回传。评估器定义数据、执行方法和评分规则。

## 快速开始

需要 Python 3.12+、与平台匹配的 SDK 版本及评估器依赖；未发布版本先安装本地构建的 wheel。在账号设置或任务配置中复制「我的用户 ID」。

```bash
pip install -U 'llm4ad-next[gateway-ui]'
llm4ad evaluator-gateway start
```

网关默认启动管理面板。打开 `http://127.0.0.1:8768`，填写平台地址、网关名称和允许的用户 UUID。默认平台地址为 `https://llm4ad-next.cn`。首次试用可在面板中选择内置的 `example` 并启动连接。它对应 `basic` 案例，注册的评估器标识为 `square-error-example`，用五组固定输入计算 `solve(x)` 与 `x²` 的均方误差。

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

## 在平台中使用

打开任务配置，选择 **远程评估器**。表单提供本地网关的连接步骤和「我的用户 ID」。将该 ID 加入网关的授权列表后，网关注册成功就会出现在 **在线网关** 中。

选择一个网关卡片，再设置评估超时时间和候选并行设置。卡片的名称、评估器标识和版本均来自网关的当前注册。任务启动时固定所选注册的 UUID 和评估契约，后续候选使用这两项确认评估目标。

选择列表只展示当前在线且对你授权的注册。所选网关忙碌时等待空闲容量，离线时等待同一注册恢复连接。平台排队和离线等待不计入执行超时，连接中断不消耗评估重试次数。重跑未明确绑定唯一注册的旧任务时，需重新选择网关卡片。

```yaml
evaluator:
  type: remote
  gateway_id: YOUR_EVALUATOR_REGISTRATION_UUID
  timeout: 300
  parallel: true
  batch_size: 4
```

| 任务配置 | 含义 |
| --- | --- |
| `gateway_id` | 所选网关与评估器对应的唯一注册 UUID |
| `timeout` | 单候选执行超时，单位为秒 |
| `parallel` / `batch_size` | 控制平台提交候选的并发，实际执行受网关可用容量限制 |
| `candidate_paths` | YAML 可选项，指定额外候选文件的相对 glob。默认传输识别到的源码和构建清单 |
| `parameters` | YAML 可选项，显式传给自定义评估器的参数，对应 `job.parameters` |

平台发送候选源码和构建文件，评估数据和依赖留在网关。默认排除 `config.yaml`、`.env*`、运行状态及数据目录。远程任务由平台运行时发起。

## 自定义配置

支持 CLI、`--config gateway.json` 环境键值 JSON，以及环境变量。优先级为：**CLI > 显式 JSON > 已保存的面板配置 > 环境变量 > 默认值**。自定义 JSON 环境键也会传入评估进程。

### 连接与执行

| 环境变量 | CLI 参数 | 默认值 / 用途 |
| --- | --- | --- |
| `LLM4AD_SERVER_URL` | `--server` | `https://llm4ad-next.cn`，平台地址 |
| `LLM4AD_GATEWAY_NAME` | `--name` | `local-evaluator`，显示名称 |
| `LLM4AD_ALLOWED_USER_IDS` | `--allow-user` | 必填，逗号分隔 UUID；CLI 可重复传入 |
| `LLM4AD_GATEWAY_ADAPTER` | `--adapter` | 默认 `custom`。试用平方误差案例可设为 `example` |
| `LLM4AD_EVALUATOR` | `--evaluator` | 自定义评估器必填，`FILE.py:Class` 或 `package.module:Class` |
| `LLM4AD_GATEWAY_STATE_DIR` | `--state-dir` | `.llm4ad-gateway`，身份、队列和恢复记录 |
| `LLM4AD_GATEWAY_CONCURRENCY` | `--concurrency` | `1`，通用网关的并行评估数 |

重启时保留完整状态目录，每个实例独占自己的目录。网关自动创建恢复密钥，无需配置平台账号密码；仅向可信用户授权，评估代码以本地账号权限运行。

### 面板与清理

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `LLM4AD_GATEWAY_UI` | `true` | `false` 或 `--no-ui` 关闭面板 |
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

基础案例和自定义模板使用 bridge 网络，面板地址变量控制宿主端口映射。容器默认启动面板，`--no-open-browser` 控制浏览器自动打开。面板密码通过运行时环境传入。

## 实现自定义评估器

继承 `GatewayEvaluator`，声明 `EvaluationContract` 并实现 `async evaluate(job, reporter)`。平台聚合 `score` 越大越好，各指标方向由契约指定。基础案例的得分为负均方误差。[SDK 接口源码](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/src/llm4ad/gateway/sdk/contracts.py) · [基础评估器源码](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/src/llm4ad/gateway/examples/basic/evaluator.py)

| 接口 | 用途 |
| --- | --- |
| `contract` | `evaluator_id` 标识实现，`version` / `data_version` 标识评估逻辑及本地数据版本，`metrics` 声明指标方向 |
| `job.project_root` / `timeout` / `evaluation_profile` | 候选目录、执行预算及 `standard` / `elite` 档位 |
| `job.parameters` | 通过任务配置显式传入的自定义参数 |
| `reporter.progress(...)` / `message(...)` | 上报进度和状态事件 |
| `reporter.metadata(dict)` | 保存额外 JSON 信息 |
| `reporter.artifact(name, path, media_type)` | 声明需上传的报告、图片或 Trace |
| `reporter.local_log(name, description=...)` | 登记仅供网关面板查看的本地日志 |
| `EvaluationResult` | 返回 `score`、`metrics`、`monitor_metrics`、`metadata`、`evolution_feedback`、`duration_ms`、`artifacts` |
| `EvaluationError` | 已知评估失败，可携带反馈、元数据和附件 |
| `EvaluationConfigurationError` | 参数或评估器配置错误，直接结束当前评估 |
| `EvaluationUnavailableError` | 瞬时基础设施故障，在原执行预算内自动重试 |
| `EvaluationPreparationError` | 确认未下发外部任务的准备失败，直接返回失败而非无限等待恢复 |
| `TaskCheckpoints(job, directory, inputs=..., context=...)` | SDK 冻结输入并保存本地编译、预处理步骤的检查点 |

评估工作在独立进程中执行。本地数据和工具通过 `__file__` 或绝对路径定位；工作目录是候选目录。

候选代码或输出错误使用 `EvaluationError(..., metadata={"failure_category": "candidate"})`。临时基础设施故障使用 `EvaluationUnavailableError`。未分类的评估器异常按 `evaluator` 处理。

以下扩展均可选，普通评估器无需实现调度、恢复或取消：

| 扩展 | 用途 |
| --- | --- |
| `before_task(job, reporter, operation)` | 操作前编译或准备资源 |
| `after_task(job, reporter, operation, *, result=None, error=None)` | 操作后的诊断和清理；强制终止不能保证执行 |
| `reporter.save_state(dict)` / `saved_state` | 持久化、读取外部执行句柄 |
| `recover(job, reporter)` | 保留的兼容接口；有限次数的新执行由 SDK 管理 |
| `cancel(job, saved_state)` | 仅在确认外部执行停止后返回 `True` |
| `cleanup_timeout` | 正有限数值，清理超时秒数；默认 `30`，应覆盖钩子与外部资源恢复 |
| `create_runtime()` / `runtime.local_logs(...)` | 自定义运行时、就绪检查及外部日志 |

`operation` 为 `evaluate` 或 `cancel`；`recover` 保留兼容。外部提交使用 `job.id` / `attempt_id` 加 `job.execution_id` 区分每次本地执行，并在等待前保存执行句柄。SDK 负责终止本地进程，`cancel` 负责恢复已修改的外部资源。

耗时的本地准备可声明步骤，由 SDK 保存和校验检查点；案例无需自行读写检查点文件。重新进入同一步骤时，SDK 复用已完成产物，保留未完成步骤的残留文件后重跑。输入和产物的哈希不匹配时明确报错。

```python
from llm4ad.gateway.sdk import TaskCheckpoints

steps = TaskCheckpoints(job, build_dir, inputs={"source.cpp": source}, context={"version": "1"})
steps.run("compile", lambda: compile_source(steps.path("source.cpp"), build_dir / "binary"),
          outputs=["binary", "compiler.log"], required=["binary"])
```

检查点用于可重复执行的本地准备。已提交的外部任务按原幂等标识接续。

## 日志、附件与恢复

| 内容 | 查看位置 / 保留规则 |
| --- | --- |
| 状态事件 | 网关面板，支持筛选和历史查询；最终保留于 `metadata.evaluation_messages` |
| 本地执行日志 | 网关面板中的 stdout/stderr；不自动上传，按日志保留时间清理 |
| 明确声明的附件 | 点击演化节点，在右侧「节点信息」查看 metadata 和附件；图片缩略图可点击放大，其他文件以紧凑列表展示并支持预览、下载 |
| 生命周期计时 | 面板展示真实开始、结束及含前后处理的总耗时；`duration_ms` 保留评估器原值 |

本地评估器在 `cfg.artifact_dir` 写文件，并通过 `EvaluationResult.artifacts` 声明。远程评估器可在 `job.artifact_dir` 写文件，使用 `reporter.artifact()` 或结果对象声明。RustFS/S3 保存已声明的附件和返回的行为可视化数据。[附件示例源码](https://github.com/Optima-CityU/LLM4AD_Next/tree/main/examples/evaluation_artifacts)

上传失败时重传相应文件。网关收到文件和结果确认，并完成评估器所需的外部资源恢复后，才清理证据。本地日志独立保留。平台资料随任务保留，删除任务时清理。

| 情况 | 行为 |
| --- | --- |
| 网络中断 | SSE 自动重连，网关通过心跳核对执行状态，并继续回传待确认的进度和结果 |
| 网关重启 | 使用原状态目录核对未完成的执行及清理状态，恢复可继续的工作 |
| 瞬时基础设施故障 | 平台在有限重试预算内将候选重新排队，目标仍为原注册 |
| 读取失败 | 无副作用的读取可使用 `retry_read(...)`，最多调用 3 次；无效候选不重试 |
| 本地准备中断 | SDK 检查点保存准备产物；每次新执行使用独立目录 |
| 执行超时 / 平台停止 | 网关终止对应的本地执行，调用已配置的清理逻辑并保存状态 |
| 资源清理失败 | 网关保留待清理记录，后续领取受清理状态约束 |

SDK 先持久化结果，再确认本地队列收据。结果和附件可从已保存的记录重新传输。

```bash
llm4ad evaluator-gateway health --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
```

## HGamey 原神示例

[HGamey 原神示例](../examples/hgamey-remote-evaluation.md)

## 平台部署配置

评估器或数据变更由网关发布。使用新评估契约时，重新注册并为新任务选择该网关卡片。升级远程评估协议时，同步更新后端、worker、task-runner、前端和网关 SDK，并应用 Alembic 迁移。

标准 `docker/compose.yml` 已包含 `remote-evaluation-worker`。生产部署可在原有 Compose 文件和覆盖配置上加用 `compose.remote-evaluation.yml`。

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
