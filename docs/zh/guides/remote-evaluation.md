# 远程评估网关

可以让网页平台生成算法，在自己的工作站、集群入口或实验室机器上完成评估。网关主动连接平台的 HTTPS/WebSocket，本地无需开放入站端口。需要 Python 3.12+、与平台一致的 LLM4AD 版本，以及评估器自己的依赖环境。

## 启动网关

在账号设置或任务的远程评估器选项中复制「我的用户 ID」，参考 `examples/remote_evaluation/evaluator.py` 编写适配器，然后执行。Linux、macOS 和 Windows PowerShell 使用同一条命令：

```bash
llm4ad evaluator-gateway start --server https://llm4ad-next.cn --name lab-workstation --allow-user YOUR_USER_UUID --evaluator ./evaluator.py:Evaluator --state-dir ./.llm4ad-gateway --concurrency 2
```

可重复传入 `--allow-user` 授权多人。无需登录平台或手动配置账号 token：CLI 自动生成独立的恢复密钥，保存在 `identity.json` 中，服务端只保存其哈希。重启时保留整个状态目录；不同网关、不同平台使用不同目录。同一目录只允许一个 CLI 实例运行。调整授权用户或并发数时，用原目录和新参数重新启动。

评估器以本地账号的权限运行用户代码，应使用已有的隔离评估环境。网关不会下载平台模型凭据或任务运行配置。

## 适配器接口

继承 `llm4ad.remote.GatewayEvaluator`，声明 `EvaluationContract`，实现 `async evaluate(job, reporter) -> EvaluationResult`。示例会真正通过子进程评估 `solution.py:solve(x)`，使用负均方误差作为得分。平台的聚合 `score` 方向为越大越好，各项指标则在契约中声明 `minimize` 或 `maximize`。

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
| `reporter.save_state(dict)` | 保存外部调度系统的执行标识，供恢复使用 |
| `reporter.saved_state` | 读取上次持久化的执行信息 |

每个作业是一个完整候选，包含自行决定的数据实例和随机种子。数据集、聚合计分、子任务分发、共享资源锁都由适配器管理。网关的并发数限制的是候选数量。

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

- SQLite 保存已领取任务、进度和结果。断网不停止本地评估，也不把排队候选记成失败。HTTP 上报使用递增序号，重复结果只记一次。
- 已领取作业始终属于原网关，心跳过期不会转派。等待上线和等待容量不计入执行超时。
- 真正执行后才开始计时，断网时仍执行本地超时控制并结束本地进程树。如果保存过外部调度标识，超时或崩溃后保留为状态不确定，等待核实外部执行状态。
- 外部调度适配器可实现 `recover(job, reporter)`，读取 `reporter.saved_state` 重新连接并返回已有结果；无法判断时返回 `None`，不要直接重复提交。建议用 `job.id` / `attempt_id` 作为调度器幂等键，保存提交意图、执行标识后再等待。
- 外部执行需要实现 `cancel(job, saved_state) -> bool`，确认已取消后才返回 `True`。平台停止时立即取消排队任务，并请求取消已领取任务；取消未确认时继续保留对应网关槽位。
- 人工确认旧执行已结束后，可以使用下列命令处理不确定作业。`retry` 会创建一个新的服务端执行批次，允许兼容的已选网关领取；旧批次的迟到结果会被拒绝。

```bash
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action retry --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action cancelled --state-dir ./.llm4ad-gateway
```

崩溃后使用原命令、原状态目录启动。日志缺失时会保留不确定状态；外部系统是否只执行一次，需要适配器配合幂等和恢复。平台无法远程结束离线机器上的进程，取消请求会在恢复连接后对账。

## 平台部署

应用新增的 Alembic 迁移，重建 backend、worker、task-runner 和 frontend。标准 `docker/compose.yml` 已包含独立的 `remote-evaluation-worker`，消费 `remote_evaluation` 队列，`REMOTE_EVALUATION_CONCURRENCY` 默认 10。远程任务等待期间续期执行锁和模型代理凭据，本地任务保持原有超时行为。

使用 `compose-cloud.yml` 的生产分支可添加通用覆盖文件，并保留原有其他覆盖文件：

需要长时间离线等待后继续调用模型时，应开启 `LLM_PROXY_ENABLE=True`，并配置任务容器可达的 `LLM_PROXY_BASE_URL`。代理会按需刷新内置供应商的访问令牌；直接使用供应商 URL 时仍受原令牌有效期影响。

```bash
cd docker
LLM4AD_COMPOSE_BASE=compose-cloud.yml docker compose -f compose-cloud.yml -f compose.remote-evaluation.yml up -d --build
```

PowerShell 中可将 `LLM4AD_COMPOSE_BASE` 写入 `.env`。WebSocket 用于可重连的通知提示，任务领取、结果回报和状态查询均经 HTTP 持久化，通知断开仍可继续。用户网关机器无需公开监听端口或暴露 Docker socket。
