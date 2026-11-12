# 远程评估网关

可以让网页平台生成算法，在自己的工作站、集群入口或实验室机器上完成评估。网关主动通过 HTTPS 连接平台，并使用 SSE（Server-Sent Events）接收通知，本地无需开放入站端口。需要 Python 3.12+、与平台一致的 LLM4AD 版本，以及评估器自己的依赖环境。

## 安装 SDK 与选择启动方式

PyPI 分发包名为 `llm4ad-next`，命令名为 `llm4ad`。网关核心、可选适配器和部署资源
均位于包内 `llm4ad/gateway/`；用户可以直接运行，也可以先生成基础文件后自行实现。

```bash
pip install -U llm4ad-next
llm4ad evaluator-gateway start --adapter example --server https://你的平台地址 --name 我的网关 --allow-user 你的用户UUID
```

### 可选本地管理面板

安装 NiceGUI 4.0.x 可选依赖后，仍使用同一个网关入口：

```bash
pip install -U 'llm4ad-next[gateway-ui]'
llm4ad evaluator-gateway start
```

默认打开 `http://127.0.0.1:8768`。首次页面可补全网关名称、允许的用户 UUID、
评估器等必填项，保存后再连接平台。平台默认地址为 `https://llm4ad-next.cn`，
可通过页面、`--server`、JSON 配置或 `LLM4AD_SERVER_URL` 修改。
`--no-open-browser` 保留面板但不自动打开浏览器；`--no-ui` 仅运行后台网关。
基础安装及原有无界面用法无需安装 UI 依赖；只有启用面板时才导入 NiceGUI、
FastAPI 和 Uvicorn。

配置优先级为：显式 CLI 参数 > 显式 `--config` JSON > 已保存的面板配置 >
环境变量 > 默认值。被高优先级启动参数固定的值，应在对应来源修改。
名称、允许用户、并发数和清理策略可运行中更新。平台地址、评估器和状态目录的变更
需等待活动任务结束、外部执行清理/现场恢复完成，并确认结果和附件已上传后再应用；
等待期间面板展示待应用状态。

可通过 `LLM4AD_GATEWAY_UI_PASSWORD` 设置独立的本地面板密码。
向远程开放面板时必须使用本地 UI 密码，与平台凭据无关。面板和共享配置不应暴露
恢复密钥及原始凭据。默认仅监听回环地址，也可通过 SSH 转发访问：

```bash
ssh -L 8768:127.0.0.1:8768 YOUR_GATEWAY_HOST
```

导出的 SDK 仍在 `requirements.txt` 中保留原有四项轻量依赖。启用面板时安装
`requirements-ui.txt`，它引用基础文件并从 `pyproject.toml` 的 `gateway-ui`
extra 派生 UI 依赖。导出会复制 UI 源码；导出和无界面使用均不需要导入 UI 依赖。

内置 `example` 会实际执行平方误差示例；`hgamey` 是可选的原神硬件适配器。
直接启动不需要 `init`、导出源码或创建项目。普通适配器声明契约并实现评估函数，负责
编译、执行、本地数据和评分，通过 `--evaluator ./evaluator.py:Evaluator` 加载，
也支持已安装的 `package.module:Class`。SDK 统一处理候选排队与并发限制、SSE、
心跳、断线重连、持久化回报、本地工作进程恢复、进程树取消、超时、进度和附件。
只有对接外部调度器或共享设备时，才需要补充对应资源的协调逻辑。

```bash
llm4ad evaluator-gateway check --adapter example
llm4ad evaluator-gateway source --adapter hgamey
```

`source` 只显示实际安装目录，可查看通用 `sdk/`、独立的 `templates/`，以及
`examples/hgamey/` 内的编译、执行器、无线 ADB、业务扩展和部署代码；不会操作设备。
`check` 通过与真实任务相同的隔离工作进程执行候选，返回实测结果和附件。

### 可选模板与 Docker

需要可编辑的基础示例或 Docker 工程时，可以选择模板生成：

```bash
llm4ad evaluator-gateway init ./my-gateway --server https://你的平台地址 --name 我的网关 --allow-user 你的用户UUID
cd my-gateway
llm4ad evaluator-gateway check --evaluator ./evaluator.py:Evaluator --candidate ./candidate
docker compose up -d --build
```

生成的 `evaluator.py` 来自 SDK 自身的示例实现，已有文件会被保护。用户可修改示例、
`requirements.txt` 和 Dockerfile，也可使用自己的启动脚本或容器；最终都调用同一个
SDK 网关。源码功能发布前，可把构建的 `llm4ad_next-*.whl` 放进生成目录的 `wheels/`。

标准 `docker compose up -d --build` 默认安装 `gateway-ui` extra，并在同一网关
容器中启动面板；从 PyPI 或本地 wheel 安装均包含 extra。容器使用
`--no-open-browser`，启动后自行访问 `http://127.0.0.1:8768`。

基础/自定义案例使用 bridge 网络，`LLM4AD_GATEWAY_UI_HOST` 和
`LLM4AD_GATEWAY_UI_PORT` 配置发布到宿主的地址与端口，默认 `127.0.0.1:8768`；
容器内固定监听 `0.0.0.0:8768`。HGamey 保持 host 网络以支持无线 ADB，相同变量
直接指定其宿主监听地址与端口，不添加 Compose 端口映射。内部执行器 8767 端口
保持私有。基础/HGamey 案例通过运行时 `env_file` 配置 `LLM4AD_GATEWAY_UI_PASSWORD`；
自定义模板从 Compose 环境文件或宿主环境传入。远程访问面板必须使用独立的本地密码。

设置 `LLM4AD_GATEWAY_UI=false` 可使用完整网关配置无界面启动，UI extra 仍保留
安装。旧的 `-f compose.yaml -f compose.ui.yaml` 命令继续兼容，使用相同环境参数，
无需再通过覆盖文件启用面板。

可复用 Docker 模板位于 `templates/custom/`，完整案例位于 `examples/`。每个案例
将自己的源码、配置和 `deployment/` 放在一起。基础案例的 Compose 可直接启动 SDK。
`source` 可查看其路径。直接使用该 Compose 时，填写 `LLM4AD_GATEWAY_ENV_FILE` 和
`LLM4AD_GATEWAY_DATA_DIR` 的绝对路径，并通过 `--env-file` 传入环境文件。
`GATEWAY_BASE_IMAGE`、`LLM4AD_PACKAGE`、`PIP_INDEX_URL` 支持按运行环境配置。
用户也可以完全自行实现容器入口。身份、日志和工作目录保存在持久化数据目录中。

### 统一健康检查与生命周期

普通自定义评估器与 HGamey 使用同一套 SDK 服务，不需要每个案例复制 service、
health 或 retention。声明 `contract` 并实现 `GatewayEvaluator.evaluate()` 即可接入，
无需自行实现调度、恢复或取消。SDK 管理执行租约，在确认旧本地工作进程已停止且
没有外部执行句柄时自动重放，并负责本地取消和超时。可选的 `before_task()` /
`after_task()` 用于准备资源和观察操作结果；外部工作可补充 `recover()` 和 `cancel()`
核实业务状态。持久化设备执行器可通过可选 `create_runtime()` 提供业务资源、自测及
恢复确认扩展，SDK 统一管理其生命周期和清理调度。

```bash
llm4ad evaluator-gateway health --state-dir /你的网关状态目录
```

通用模板和案例容器均使用此命令检查实际就绪状态。平台连接与本地执行器状态分别
展示；健康检查不触发新评估。通用清理参数为 `LLM4AD_GATEWAY_CLEANUP_ENABLED`、
`LLM4AD_GATEWAY_CLEANUP_INTERVAL_SECONDS`、`LLM4AD_GATEWAY_EVIDENCE_KEEP_SECONDS`。
结果和附件确认上传后才清理，外部设备还需通过案例提供的恢复与归属确认。

### 配置与硬件案例

通用环境配置：`LLM4AD_SERVER_URL`、`LLM4AD_GATEWAY_NAME`、
`LLM4AD_ALLOWED_USER_IDS`（逗号分隔）、`LLM4AD_GATEWAY_ADAPTER`、
`LLM4AD_EVALUATOR`、`LLM4AD_GATEWAY_STATE_DIR` 和 `LLM4AD_GATEWAY_CONCURRENCY`。
`--config gateway.json` 接受环境键值的 JSON 对象；优先级依次为显式 CLI 参数、
显式 JSON 配置、已保存的面板配置、环境变量、默认值。配置中的自定义环境键也会
传递给评估器和隔离执行进程。

原神案例选择 `--adapter hgamey`。SDK 中包含实际使用的 HGamey Rust/C++/Android
源码及构建脚本；案例的 Dockerfile 从这些源码构建 phonefarm、Vulkan 层、设备运行器
和 refbench APK，并准备 ADB、NDK 与解析工具，不依赖私有基础镜像。系统直接启动时
需自行准备这些工具。手机 Root、无线授权、原神及评测 APK 的准备要求见下面的
环境依赖说明。配置 `HGAMEY_DEVICES_JSON`、真实参考帧和游戏验证参数后使用；原生依赖
仍是本硬件案例专用，不作为通用网关的安装前提，也不在领取评估任务时下载。
HGamey 的设备与游戏参数继续保留在案例已有的环境/JSON 文件中，每台设备并发数固定为 1。
面板仅管理通用网关设置，使用同一个网关与执行器服务。

#### HGamey 原神案例的环境依赖

通用网关支持 Python 3.12+，可直接由 Python/CLI 启动，也可使用 Docker；其跨平台
能力不等于 Android 硬件案例的跨平台能力。当前 HGamey 构建与执行方案为
**Linux amd64**，Docker Compose 使用 host 网络连接无线 ADB。macOS、Windows 和
ARM64 主机尚未验证此案例；可将网关部署到能访问手机的 Linux amd64 机器。

| 层级 | 需要的环境 | 由谁准备 |
| --- | --- | --- |
| 通用网关 | Python 3.12+、匹配平台版本的 `llm4ad-next`；网页控制台另需 `gateway-ui` extra | 直接运行时安装；容器从 wheel 或 PyPI 安装 |
| 原生构建 | Rust 1.99、OpenJDK 17、C++ 编译器、Bash、unzip、file；Android NDK r27d（27.3.13750724）、SDK API 35 与 build-tools 35.0.0 | 案例 Dockerfile 在构建期准备；直接运行需自行构建 |
| 本地执行 | phonefarm、GLSL 编译器、SPIR-V 校验器、ADB 36.0.2、FFmpeg、NumPy、Pillow、Perfetto Python 0.59.1 和官方 Trace Processor | 容器提供；直接运行需准备并配置相应路径 |
| 手机 | Android 11+ ARM64、Root、Vulkan 1.1 compute 与 GPU 时间戳能力、可读取和控制的 Qualcomm KGSL 或 MediaTek GED 频率接口 | 操作者准备；不能仅凭 GPU 品牌判断兼容 |
| 评测材料 | 合法取得的原神参考 PNG/JPEG、设备实际序列号、无线地址与已授权 ADB 密钥、独立持久化目录 | 操作者提供，不随 SDK 打包 |

容器会从附带源码构建 Android ARM64 的算子运行器、Vulkan 层和 refbench APK，
**不会假设手机已经安装这些组件**。在已有 `phonefarm`、授权 ADB 和构建产物的环境中，
可先执行 `adb -s YOUR_WIRELESS_ADDRESS shell getprop ro.serialno` 核实连接与设备身份；refbench APK
需要由操作者通过 `adb -s YOUR_WIRELESS_ADDRESS install -r refbench-mali.apk` 安装。
原神应由用户从官方渠道安装、完成资源下载及登录。Root 与 ADB 授权同样需要用户
事先完成，容器不会替用户获取权限、账号或游戏资源。
GPU 算子在手机上运行，网关主机不需要 CUDA。真实参考图不得小于 1920×1080；当前
headless 画质检查只读取参考目录第一帧，并从其中心裁剪，不代表多场景或动态画质验证。

真实游戏验证还要求能进入游戏场景、加载 Vulkan 层，并具备对应帧呈现和追踪数据源。
`game-check.json` 的分辨率、等待时间和 `enter_script` 应按设备与游戏启动界面调整；
Linux 容器应使用明确的进入脚本，不能依赖上游仅适用于 macOS 的自动 OCR 路径。
功耗当前默认关闭；无法测得的数据保留为未测得，不以 GPU 耗时换算功耗。

首次构建需要联网下载 Rust crates、Android 工具链、Python 包和 Trace Processor；
这些下载不在领取候选任务时进行。可通过 `APT_MIRROR`、`CARGO_REGISTRY_MIRROR`、
`ANDROID_REPOSITORY_BASE`、`PIP_INDEX_URL` 和 `TRACE_PROCESSOR_URL` 配置源地址，
替代下载仍须满足工具的校验要求。手机连接依靠已授权的无线 ADB，不强制连接 USB。
持久化目录需要允许容器 UID 1000 写入；每台手机串行评估，多台手机使用各自的锁、
身份与状态目录。ADB 私钥、签名密钥、真实参考帧、`.env` 和设备配置均应只保存在本地。

当前源码变更尚未发布到 PyPI，安装已构建的 wheel 可验证这些入口；打包不会自动发布。

## 启动网关

在账号设置或任务的远程评估器选项中复制「我的用户 ID」，参考 `examples/remote_evaluation/evaluator.py` 编写适配器，然后执行。Linux、macOS 和 Windows PowerShell 使用同一条命令：

```bash
llm4ad evaluator-gateway start --server https://llm4ad-next.cn --name lab-workstation --allow-user YOUR_USER_UUID --evaluator ./evaluator.py:Evaluator --state-dir ./.llm4ad-gateway --concurrency 2
```

可重复传入 `--allow-user` 授权多人。无需登录平台或手动配置账号 token：CLI 自动生成独立的恢复密钥，保存在 `identity.json` 中，服务端只保存其哈希。重启时保留整个状态目录；不同网关、不同平台使用不同目录。同一目录只允许一个 CLI 实例运行。授权用户或并发数可通过面板热更新；无界面运行时使用原目录和新参数重新启动。

评估器以本地账号的权限运行用户代码，应使用已有的隔离评估环境。网关不会下载平台模型凭据或任务运行配置。

## 适配器接口

继承 `llm4ad.gateway.sdk.GatewayEvaluator`，声明 `EvaluationContract`，实现 `async evaluate(job, reporter) -> EvaluationResult`。示例会真正通过子进程评估 `solution.py:solve(x)`，使用负均方误差作为得分。平台的聚合 `score` 方向为越大越好，各项指标则在契约中声明 `minimize` 或 `maximize`。

| 字段 | 含义 |
| --- | --- |
| `contract.evaluator_id` / `version` | 评估器标识及实现版本 |
| `contract.data_version` | 本地数据集版本 |
| `contract.metrics` | 现有 `Metric` 定义，包含方向、权重 |
| `job.id` / `attempt_id` | 作业标识和执行批次，用于幂等及拒绝旧结果 |
| `job.project_root` | 解压后的候选代码目录 |
| `job.timeout` | 单个完整候选的执行超时，单位秒 |
| `job.evaluation_profile` | 算法请求的 `standard` / `elite` 评估级别 |
| `job.behavior_storage` | 平台已有的行为输出偏好 |
| `job.parameters` | 任务中显式提供的评估器参数 |
| `reporter.progress(message, completed, total)` | 上报真实进度，数量可省略 |
| `reporter.message(text, level="info", data={})` | 独立保存消息及结构化上下文，不改变进度和计时 |
| `reporter.metadata(dict)` | 在最终结果产生前保存额外 JSON 信息 |
| `reporter.artifact(name, path, media_type, description="...")` | 保存明确声明的文件快照及说明 |
| `reporter.save_state(dict)` | 保存外部调度系统的执行标识，供恢复使用 |
| `reporter.saved_state` | 读取上次持久化的执行信息 |

每个作业是一个完整候选，包含自行决定的数据实例和随机种子。`evaluate()` 负责选择本地数据、执行测量和聚合计分，SDK 负责候选排队、调度及网关并发限制。只有接入外部执行系统时才需要相应的提交或资源协调逻辑；普通评估器无需实现网关的调度或恢复状态机。

执行层采用限制并发的一任务一进程方式，不是复用型 `joblib.Parallel` 进程池，便于单独取消某条评估及其子进程。Joblib 的运行中 Future 无法单独取消，后端的强制中止操作面向整个执行器（[官方后端说明](https://joblib.readthedocs.io/en/stable/user_guide/custom_parallel_backend.html)）。评估器内部可按需用 joblib 并行计算独立数据实例，但持久队列和恢复状态仍由 SDK 管理。轻量网关不依赖 joblib；使用它的评估器在自己的运行环境中声明依赖。

可选操作钩子为 `before_task(job, reporter, operation)` 和
`after_task(job, reporter, operation, *, result=None, error=None)`，
`operation` 取 `"evaluate"`、`"recover"` 或 `"cancel"`。可用它们进行操作前准备、
诊断和清理。后置钩子会收到操作结果或异常，也包括前置钩子抛出的异常；钩子异常
不会覆盖已有的业务异常。对于 `"cancel"`，前置钩子先运行，随后由 SDK 持久化停止
请求、结束本地进程树并核实外部取消状态。后置钩子收到布尔值确认结果或异常，取消仍待确认时
结果可以为 `False`。这些钩子用于扩展业务逻辑；执行归属、重放、取消确认、超时及
持久化回报仍由 SDK 统一决定和管理，普通适配器无需实现任何钩子。

消息和进度事件按执行批次保存到本地日志。运行状态保留最新提示，完整历史放在 `metadata.evaluation_messages`，包括时间、级别和结构化 `data`。重启后可恢复，并合并到最终结果；旧执行批次不能污染新批次。普通额外信息使用 `metadata`，外部调度器的恢复句柄仍使用 `save_state`。

### 状态事件、本地输出与生命周期计时

面板的事件视图读取 `reporter.message()` 和 `reporter.progress()` 的结构化记录，
默认展示最近 100 条匹配事件，可按级别和文字筛选、向前加载历史，并通过不包含
边界记录的序号游标增量更新。所有查询均限定网关绑定、作业和执行批次；原有分页
消息接口继续可用。

本地执行日志独立展示工作进程及其子进程真实的 stdout/stderr。这些文件不是状态
事件，也不会自动作为附件上传平台。评估器可显式创建更多命名来源：

```python
log_path = reporter.local_log("solver.stdout", description="Solver process stdout")
with log_path.open("ab") as output:
    subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
```

后端可实现 `runtime.local_logs(job, saved_state)`，返回可读来源名称到已归属本地
文件的映射。发现日志只读取已接收任务的本地回执，不启动评估、自测或外部进程。
面板仅列出已登记来源，按字节分段读取，并通过临时快照下载；来源元数据不暴露
文件路径及恢复状态。显式声明的 `EvaluationArtifact` 仍按原有流程上传平台。

本地日志副本默认独立保留七天，由 `LLM4AD_GATEWAY_LOG_KEEP_SECONDS`
（`local_log_keep_seconds`，默认 `604800`）配置，支持运行中修改。统一收集器仅在
执行结束且本地执行已停止后过期清理副本，与附件上传确认及证据保留时间分开管理。
外部后端还需通过归属及现场恢复确认，未确认时继续保留日志；清理不修改回报或附件
的上传确认状态。
运行中的日志继续保留，过期来源仍可识别为不可用。

任务详情展示真实 `started_at`、`finished_at` 和包含前后钩子的总 `elapsed_ms`。
评估器返回的 `duration_ms` 保持原值，可能仅衡量评估主体。旧记录缺失的时间仍为
`null`，不会把最近更新时间当成结束时间。`metadata.lifecycle` 与
`lifecycle_stages` 记录 preparing/evaluating/finalizing 及
running/completed/failed；终态报告被围栏保护后，私有本地生命周期记录仍可保存
后置清理状态。具体钩子行为及额外日志来源名称由各案例实现。

启动就绪检查使用独立本地日志库。面板通过 `purpose="startup"` 查看最近启动检查
摘要、事件与本地输出，不重新触发检查，也不把这些记录当作平台评估作业或结果。

实测结果通过 `EvaluationResult` 返回；实测失败也可返回 `success=False`。支持评分指标 `metrics`、辅助指标 `monitor_metrics`、额外信息 `metadata`、演化反馈 `evolution_feedback`、耗时 `duration_ms`、行为数据和明确声明的 `artifacts`。未填写耗时则由 SDK 记录工作进程耗时。成功和失败结果都保留这些信息及附件，使用平台已有的评估详情存储。附件先上传、最终结果再确认；暂不提供执行中的附件实时预览。

已知错误可抛出 `llm4ad.gateway.sdk.EvaluationError(message, metadata=..., evolution_feedback=..., artifacts=...)`。未捕获的工作进程异常统一保存异常类型、原因和堆栈附件；`subprocess.CalledProcessError` 还会保留可用的 stdout、stderr 和退出码。可选附件采集出错写入 `metadata.artifact_errors`，不改变实测得分。设备暂不可用或执行结果不确定时可抛出 `EvaluationUnavailableError`，作业进入 `recovery_required` 等待恢复，不直接判定候选失败，也不自动重复外部工作。外部系统可通过 `save_state` 保存执行句柄，按需覆盖 `recover` / `cancel` 以核实其业务含义；普通本地评估使用 SDK 默认行为。

默认只传输识别到的源代码和构建清单，排除 `config.yaml`、`.env*`、运行状态及 `data/`、`datasets/`。需要额外候选资源时，通过 `candidate_paths` 显式填写相对 glob，例如 `['src/**/*.py', 'assets/*.txt']`。本地评估器应通过 `__file__` 或绝对路径定位自己的数据和辅助程序，因为执行时的工作目录是候选目录。

## 网页中配置任务

在现有任务配置步骤中，将评估器切换为「远程评估器」，勾选授权给自己的网关。一个池中的网关需声明相同的评估契约。每个候选只由其中一个网关评估，多选用于分担排队候选。离线网关可保留选中，任务启动后等待它上线。

对应配置示例：

```yaml
evaluator:
  type: remote
  gateway_ids:
    - YOUR_GATEWAY_UUID
  timeout: 300
  parallel: true
  batch_size: 4
  candidate_paths: ["solution.py"]
  parameters: {}
```

平台启动前会解析、校验评估契约。远程提交依赖平台任务运行环境，单独执行 `llm4ad run` 不会创建平台远程任务。平台仍需提供候选源代码和 EVOLVE 标记，评估器、数据集及依赖保留在本地。任务工作台「远程评估」页显示最近 100 个作业、网关连接状态、真实进度和得分。

## 断线、恢复与停止

SDK 使用独立的进程树守护器，在子进程终止期间继续持有执行归属，包括忽略常规终止信号的子进程。无法确认进程树已停止时保留不确定状态，不直接重放。钩子实例位于各自进程中，需要跨进程或跨重启保留的信息通过日志及恢复句柄传递。强制结束工作进程无法运行其 Python 收尾代码，取消钩子则在网关进程中围绕停止及确认流程执行。

- SQLite 保存已领取任务、进度和结果。断网不停止本地评估，也不把排队候选记成失败。HTTP 上报使用递增序号，重复结果只记一次。
- 已领取作业始终属于原网关，心跳过期不会转派。等待上线和等待容量不计入执行超时。
- SDK 管理每个本地工作进程的执行租约，包括网关退出后仍存活的工作进程，等待其停止后才重放。网关重启后，没有外部执行句柄的未完成本地作业，在确认旧工作进程已停止后可自动重放。默认重放会清理尚未上传的 SDK 未完成输出文件，重置执行计时，并保留消息历史。重放从头重新调用 `evaluate()`，不会还原任意位置的 Python 调用栈检查点，也无需适配器覆盖 `recover()`。
- 真正执行后才开始计时，断网时仍执行本地超时控制并结束本地进程树。如果保存过外部调度标识，超时或崩溃后保留为状态不确定，等待核实外部执行状态。
- 平台停止时立即取消排队任务，并请求取消已领取任务。SDK 持久化收到的取消请求，结束本地进程树，并为普通评估器提供默认取消行为。已记录的取消请求在重启后仍生效，防止该本地作业再次重放。
- 外部执行句柄需要按业务语义核实：可选 `recover(job, reporter)` 读取 `reporter.saved_state` 重新连接并返回已有结果，无法判断时返回 `None`；可选 `cancel(job, saved_state) -> bool` 只有确认外部执行已取消才返回 `True`。HGamey 等资源集成仍可覆盖这两个方法，生命周期调度继续由 SDK 负责。外部取消未确认时保留对应网关槽位。不要盲目重复外部实验；建议用 `job.id` / `attempt_id` 作为调度器幂等键，保存提交意图、执行标识后再等待。
- 仍需人工处理不确定作业时，先确认旧执行已结束，再使用下列命令。`retry` 会创建一个新的服务端执行批次，允许兼容的已选网关领取；旧批次的迟到结果会被拒绝。

```bash
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action retry --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action cancelled --state-dir ./.llm4ad-gateway
```

崩溃后使用原命令、原状态目录启动。已领取但记录为从未开始执行的作业会自动继续。旧版本日志中已标记运行、但缺少 SDK 执行控制记录的作业仍保留不确定状态，需要核实旧执行是否停止；日志整体缺失时也不会推定旧执行已停止。外部系统是否只执行一次，需要适配器配合幂等和恢复。平台无法远程结束离线机器上的进程，取消请求会在恢复连接后对账。

## 平台部署

原神超分 Shader 案例已提供可直接加载的网关适配器，包含固定基线、真实编译、
本地参考帧、断线恢复和执行器兼容补丁。参见
[原神网关示例源码](../../../src/llm4ad/gateway/examples/hgamey/)，
网页端选择 `hgamey_shader` 示例后绑定模型和已授权网关即可配置演化。
该案例衡量独立算子性能，游戏内帧率收益需要另行实测。

应用新增的 Alembic 迁移，重建 backend、worker、task-runner 和 frontend。标准 `docker/compose.yml` 已包含独立的 `remote-evaluation-worker`，消费 `remote_evaluation` 队列，`REMOTE_EVALUATION_CONCURRENCY` 默认 10。远程任务等待期间续期执行锁和模型代理凭据，本地任务保持原有超时行为。

使用 `compose-cloud.yml` 的生产分支可添加通用覆盖文件，并保留原有其他覆盖文件：

需要长时间离线等待后继续调用模型时，应开启 `LLM_PROXY_ENABLE=True`，并配置任务容器可达的 `LLM_PROXY_BASE_URL`。代理会按需刷新内置供应商的访问令牌；直接使用供应商 URL 时仍受原令牌有效期影响。

```bash
cd docker
LLM4AD_COMPOSE_BASE=compose-cloud.yml docker compose -f compose-cloud.yml -f compose.remote-evaluation.yml up -d --build
```

PowerShell 中可将 `LLM4AD_COMPOSE_BASE` 写入 `.env`。SSE 通过带身份认证的 HTTP GET 建立通知流，每 10 秒发送一次保活事件。断流、读取超时或代理错误后，网关等待两秒自动重连；通知流不受普通 HTTP 请求总超时限制。SSE 不可用时，HTTP 心跳仍会同步任务状态，任务领取、结果回报和状态查询继续通过 HTTP 持久化，已领取任务的归属也不依赖每条通知都成功到达。用户网关机器无需公开监听端口或暴露 Docker socket。升级时需要同步更新平台后端与网关 SDK，使用独立容器时也要重新导出其运行时代码。

## 回传评估证据文件

自定义评估器可在返回结果前调用
`reporter.artifact("trace.pftrace", local_path, "application/octet-stream")`。
SDK 冻结文件副本、保留 SHA-256 和上传状态，先上传文件，再回报最终结果。
网络恢复只重传文件，不重新评估。平台把文件保存到 RustFS/S3，结果的
`metadata.artifacts` 提供文件清单和需要任务所属账号登录的下载路径；二进制不放入
数据库结果行，也不作为 base64 塞进 JSON。平台后端和网关 SDK 都需同步更新。

## 统一评估详情与附件

任务工作台的**评估详情**同时支持本地评估器和远程网关。可按候选和评估阶段查看指标、实例说明及附件，支持 JSON、文本、Markdown 和常见图片预览；Trace 等其他格式可下载原始文件。旧版 `reporter.artifact()` 的附件仍可读取。

本地评估器在 `cfg.artifact_dir` 中写文件，并通过 `EvaluationResult.artifacts` 返回 `EvaluationArtifact(name="reports/report.json", path="report.json", media_type="application/json")` 等明确声明。远程网关继续兼容 `reporter.artifact()`，也可在 `job.artifact_dir` 中写文件后返回相同声明。`examples/evaluation_artifacts` 提供无需模型、无需手机即可运行的完整示例。

文件和完整 JSON 记录保存在 RustFS，关联到具体任务版本、候选、代码指纹、阶段和实例。只保留明确声明的附件及评估器返回的行为可视化数据，不会自动扫描临时目录或上传全部截图、视频。

上传失败只重试文件传输，不重新执行实验，也不改变评分。文件及清单保存确认后才清理对应本地副本；评估器已有的外部资料清理策略继续生效。平台资料默认随任务保留，删除任务时统一清理。`EVALUATION_PREVIEW_MAX_BYTES` 可配置预览大小，默认 2 MiB，仅影响预览，不影响原始文件下载。
