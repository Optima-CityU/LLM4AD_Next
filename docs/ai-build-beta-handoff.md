# AI 构建 (Beta) — AgentScope 单 Agent 构建：实现交接文档

> 本文件是给**接手的 AI 模型**看的完整交接说明。读者看不到此前的对话历史，
> 因此本文档力求自包含：包含背景、设计决策及其理由、已完成的全部改动（逐文件、
> 逐函数）、当前状态、剩余工作、安全分析、以及待用户拍板的开放问题。
> 编写日期：2026-06-29。所有改动均在 `src/backend/` 内，**未触碰前端**。

---

## 1. 背景与目标

### 1.1 项目是什么
LLM4AD_Next 是一个"用 LLM 做自动算法设计 (LLM4AD)"的平台。核心玩法：用户描述一个
优化问题，平台用进化算法 + LLM 不断改写一段被 `# EVOLVE_START` / `# EVOLVE_END`
标记包裹的代码，由一个自定义"评估器 (evaluator)"打分，迭代出更优算法。

平台有一个 Web UI 栈（FastAPI 后端 + Celery worker + 前端 + Postgres/Redis/RustFS/
nginx），通过 `docker/` 下的 compose 编排。其中"AI 构建"功能对应后端的 **chat_tune**
模块：用户通过对话，让系统自动生成一个可运行的 LLM4AD 任务包（评估器、带 EVOLVE 标记
的算法、config.yaml、调试脚本、样本数据等）。

### 1.2 用户的诉求（为什么做这个）
用户认为现有的 AI 构建**太死板**，希望用"**真正的 Agent**"来构建项目包，并能根据
用户需求灵活做事。经过多轮讨论，确定了以下关键约束和决策（**这些是用户拍板的，不是
我自行决定的**）：

1. **保留原有 AI 构建不动**，新增一个并行的 **"AI 构建 (Beta)"** 入口/模式。
2. **Agent 框架用 AgentScope**（阿里通义实验室开源，用户同事推荐）。
   - 关键原因：用户的用户群**不只用 Anthropic 模型**（还有 DeepSeek/通义/OpenAI 等）。
     Claude Agent SDK 绑死 Anthropic 协议，非 Anthropic 模型需要协议翻译网关（脆弱）；
     AgentScope 原生多 provider，且能走现有透明 LLM proxy **零改动**。
3. **改造范围 = 大改**：用一个 AgentScope agent 统一承接"对话 + 构建 + 评审"，
   取代原三阶段状态机。但作为 beta 与旧逻辑**并存**（特性开关控制）。
4. **构建核心走"混合"路线**（用户在被问及"对话死板还是代码生成死板"时选的）：
   - **BuildOrchestrator 生成**（复用现有稳妥引擎，config 程序化生成、自带 validation 闸），
   - **agent 用 `run_python` 验证**（跑 `debug_run.py`/`test_evaluator.py` 看报错），
   - 跑不通就调 BuildOrchestrator 的 `rebuild_evaluator` 重试。
5. **config.yaml 由程序化生成**（不让 agent 凭空写 10 段 schema）。混合路线下这天然成立——
   BuildOrchestrator 内部就是程序化生成 config 的。
6. **验证两层就够**（用户原话"两层就够了"）：
   - 第 1 层 = agent 自验（`run_python`）；
   - 第 2 层 = BuildOrchestrator 自带的确定性 validation 闸。
   - **不叠第三层**（不再在 agent 之外额外加一道独立 validator）。
7. **凭据走 LLM proxy + 一次性 token**（用户选的）：真实 key 不进容器。
8. **网络隔离沿用现有 chat_tune 容器级别**（用户让我按现有水平判断）：
   一次性容器、不挂 docker.sock、`no-new-privileges`、内存/CPU 限制、用完即销。
   不为 beta 单独搭内网出口代理（列为可选后续加固）。
9. **安全是头等约束**：agent 不能编辑其根目录 (`/task/data`) 之外的任何东西，
   防止线上服务被攻破。

### 1.3 为什么是"混合"而不是其它
讨论中澄清过两种极端：
- **纯复用 BuildOrchestrator**（agent 只聊需求）：只解决"对话死板"，代码生成仍是单次
  生成，且与现状差别不大。
- **agent 完全自己写代码**（write_file + run_python 自纠错）：最彻底解决"代码生成死板"，
  但与"config 程序化"决策有摩擦，需要一座桥（scaffold + 重建 blueprint），最复杂。

用户选了**混合**：引擎生成（稳） + agent 验证重试（智能）。这是当前实现的路线。
> 注：实现早期我一度写过 `write_file` 工具和一个 `agent_build_support.py`（scaffold/
> 重建/validate 桥接模块），那是"agent 自己写代码"路线的产物。选定混合路线后**已删除**
> `agent_build_support.py`，并把 `write_file` 从工具集移除。若在 git 历史里看到它们，
> 属于已废弃的早期尝试。

---

## 2. 关键技术事实（已核实，非猜测）

这些事实是动手前通过"读 AgentScope 2.0.3 已安装源码 + 读本仓库源码 + web 检索"逐一
核实的，接手时可直接信任，但若要改动相关处建议复核：

### 2.1 AgentScope 2.0.3
- **只用核心包 `agentscope`，绝不装 `agentscope-runtime`**。后者的 sandbox 会自己起
  Docker 容器（硬依赖 `docker>=7.1.0`），我们已在容器里跑，装它=容器套容器。
- 已通过 `uv lock` 加入 `src/backend/pyproject.toml`，解析为 `agentscope==2.0.3`，
  无版本冲突（含 OpenTelemetry 1.43 等传递依赖）。
- 要求 Python ≥3.11。backend 是 3.12，满足；**故 agentscope 放 backend 依赖，不进
  根 `llm4ad`**（根项目受 3.10 约束，CLAUDE.md 要求 `ruff` 用 3.10 跑）。
- 真实 API（v2.0.3，与网上部分 v1 教程不同）：
  - `from agentscope.agent import Agent, ReActConfig`（v2 把 ReActAgent 合并进统一 `Agent`）。
  - `Agent(name, system_prompt, model, toolkit=None, react_config=None, ...)`。
  - `agent.reply_stream(inputs)` → async 生成器，产出**事件对象**（不是字符串）。
  - 事件类型来自 `agentscope.event`：`TextBlockDeltaEvent(.delta)`、
    `ToolCallStartEvent(.tool_call_name)`、`ToolResultEndEvent`、等。
  - `from agentscope.model import OpenAIChatModel, AnthropicChatModel`；
    `from agentscope.credential import OpenAICredential, AnthropicCredential`。
    `OpenAIChatModel(credential=OpenAICredential(api_key=, base_url=), model=, stream=True)`。
    Anthropic 额外可经 `client_kwargs={"auth_token": ...}` 传 bearer。
  - `from agentscope.tool import Toolkit, FunctionTool`。
    `Toolkit(tools=[FunctionTool(fn), ...])`；`FunctionTool(fn, is_read_only=True)` 从
    函数签名+docstring 自动抽 JSON schema。
  - **权限坑**：`FunctionTool.check_permissions` 默认返回 `ASK`，容器内无人应答会卡。
    必须 `agent.state.permission_context.mode = PermissionMode.BYPASS`
    （`from agentscope.permission import PermissionMode`）。真正的安全保证不在这个弹窗，
    而在工具实现里的路径围栏。
  - **Msg 坑**：`Msg(content="字符串")` 会 pydantic 校验失败（`content` 必须是
    `list[ContentBlock]`，且只有 after-validator 做断言、没有 before-validator 做转换）。
    必须用工厂函数 `from agentscope.message import UserMsg`，`UserMsg(name=, content="str")`
    会自动把字符串包成 `TextBlock`。
  - **Windows 本地导入极慢**：`import agentscope` 在本机（Windows + 杀软）首次导入会被
    逐文件扫描拖到分钟级甚至超时。这是**本机现象，不是代码问题**（Linux 容器里正常）。
    所以本机别反复 spawn python 去 import agentscope 验证；改读源码或在容器里验证。

### 2.2 现有 LLM proxy（关键：零改动即可用）
- `src/backend/app/api/llm4ad/llm_proxy.py` 是**透明中继**：原样转发请求体、只替换鉴权头，
  天然兼容 OpenAI / openai_compatible / Anthropic 协议，支持流式 + 工具调用。
- 挂载在 `/api/v1/llm4ad/llmproxy`。
- `credential_broker.issue_token(...)` 发一次性 token（Fernet 加密存 Redis，带 TTL，
  按 task_id 归集）；容器持 token，proxy 用真实 key 转发上游。
- **复用模式**见 `src/backend/app/services/task_service/execution.py:311` 的 `_swap`：
  发 token → 改写 provider_cfg 的 `api_key`=token、`base_url`=proxy_base、`auth_token`=""。

### 2.3 chat_tune 容器与契约
- 容器入口 `src/backend/app/tasks/chat_tune_runner.py`，是一个 FastAPI SSE 应用，
  跑在复用的 `TASK_RUNNER_IMAGE` 里。原有端点 `/run`(needs)、`/build`、`/review`、`/health`。
- 容器隔离（`src/backend/app/services/container_service.py:346` `start_chat_tune_container`）：
  只挂载 `/task/data`、不挂 docker.sock、`no-new-privileges`、mem/cpu 限制、用完即销、
  端口不发布（仅 docker 网络内按容器名 DNS 可达）。
- **路径围栏** `_resolve_within_sandbox(base, path)`（chat_tune_runner.py:68）：把任意
  用户/LLM 传入路径钉死在 `/task/data` 内，`..`/越界绝对路径一律拒绝返回 None。
  **本次新工具全部复用这个函数**——已用 8 个用例本地测过拦截有效（见 §6）。
- SSE 事件协议（后端 `_run_*` 协程与前端都依赖，**必须保持不变**）：
  `{"type":"chunk","content":...}`、`{"type":"build_result","blueprint_data":{...}}`、
  `{"type":"done"}`、`{"type":"error","error":...}`。
- 后端编排协程 `_run_ai_build`（chat_tune_service.py:869 起，本次改动后行号下移）：
  prepare workdir → start 容器 → 连 SSE → 落库 → 上传产物到对象存储 + 回填
  `task.input_args`。**新协程 `_run_agent_build` 严格照搬此结构**。
- DB 模型 `ChatTuneSession` / `ChatTuneMessage`，枚举 `ChatTuneGenerationKind`
  （retry 时按它分发）、`ChatTuneStageStatus`、`ChatTuneActiveStage`。
- `generation_id`（Redis）用于取消：协程轮询若发现 id 变了就退出。
- `start_turn` 分发器（chat_tune_service.py:~380）按 target_stage/状态机决定
  generation_kind；`retry_turn`（~700）按存储的 generation_kind 重新分发。

### 2.4 BuildOrchestrator / NeedsProfile（混合路线的生成引擎）
- `src/llm4ad/consultant/build_orchestrator.py`：
  - `BuildOrchestrator(provider, console=None, max_repair_attempts=10)`。
    `console=None` 在容器里安全（它内部用 rich，但传 None 会自建，非 TTY 下 transient 进度
    无害）。
  - `async build(needs: NeedsProfile) -> TaskBlueprint`：跑 Analyze→Create→Validate→
    Requirements→Configure（config 程序化），内部已含 validation 闸，不通过会抛 `BuildError`。
  - `async rebuild_evaluator(blueprint, modification_request, needs) -> TaskBlueprint`。
- `src/llm4ad/consultant/needs.py`：`NeedsProfile` 是 dataclass，主字段 `description`，
  其它可选：`project_name`、`metrics_hints: list[str]`、`evaluation_hints`、`data_path`、
  `code_path`、`language` 等。有 `to_dict`/`from_dict`。
- `src/llm4ad/builder/writer.py`：`write_task_directory(blueprint, output_dir)` 把
  blueprint 写成 `output_dir/{project_name}/` 下的文件。
- `provider` 这里指 **llm4ad 的 `BaseProvider`**（`BaseProvider.create(type, config=cfg)`），
  与 AgentScope 的 model 是两套东西：AgentScope model 驱动 agent 的推理循环，
  llm4ad BaseProvider 驱动 BuildOrchestrator 内部的生成调用。两者用**同一份 provider_config**。

---

## 3. 架构总览（混合路线，最终形态）

```
用户(UI 配置自己的任意 provider 凭据，加密入库)
        │  beta=true 且后端 ENABLE_AI_AGENT_BUILD 开启
后端 start_turn → generation_kind = AI_AGENT → _run_agent_build 协程
        │ _maybe_proxy_provider_config: 若 LLM_PROXY_ENABLE,
        │   发一次性 token(TTL 2h) → provider_config.api_key=token, base_url=proxy
        ▼
一次性隔离容器(chat_tune_runner 的 FastAPI app, POST /agent)
   AgentScope 单 agent (provider_config → OpenAI/Anthropic model)
   工具(全部沙箱在 /task/data):
     read_file / list_dir         —— 只读，看用户已有代码/数据做需求分析
     run_python                    —— 跑脚本自验(debug_run/test_evaluator)
     build_task                    —— 调 BuildOrchestrator.build() 生成+validation闸
     rebuild_evaluator             —— 调 BuildOrchestrator.rebuild_evaluator() 修
   流程: 聊需求 → build_task → run_python 验证 → 跑不通 rebuild → 再验证
   agent 推理 → 调模型 → (proxy 时)经后端 proxy → 上游(真实 key 留后端)
        │ reply_stream 事件 → 映射成 chunk/build_result/done/error (SSE)
        ▼
后端 _run_agent_build: 落库 + 上传产物到对象存储 + 回填 task.input_args
   (完全复用 _run_ai_build 的同款逻辑)
```

**两层验证**：(1) agent 用 `run_python` 自验；(2) `build_task` 内 BuildOrchestrator 自带
的确定性 validation 闸。无第三层。

**安全分层**：① 容器隔离(沿用现状) ② 工具路径围栏(硬保证，不能越出 `/task/data`)
③ 无通用 Bash(消除 shell 逃逸面) ④ 真实 key 经 proxy 不进容器 ⑤ 权限引擎 BYPASS
(因为只有上述被围栏的工具，且无人值守)。

---

## 4. 已完成的改动（逐文件）

> 全部在 `src/backend/`。所有文件已通过 `uv run --python 3.10 --with ruff ruff check`（All checks passed）。

### 4.1 `src/backend/pyproject.toml`（改）
- dependencies 末尾新增 `"agentscope>=2.0,<3.0"`，附注释说明只用核心包、绝不用
  agentscope-runtime。已 `uv lock` 成功。

### 4.2 `src/backend/app/tasks/agent_build_runner.py`（新，核心引擎）
容器内跑的模块。要点：
- `AgentBuildRequest`：`POST /agent` 请求体（`provider_config`、`gathering_context`、
  `user_content`、`max_iters=40`）。
- `build_model(provider_config)`：按 `type` 映射 AgentScope model。`anthropic`→
  `AnthropicChatModel`，其余→`OpenAIChatModel`。`base_url`/`api_key` 直透（指向 proxy 即可）。
- `_build_llm_provider(provider_config)`：建 llm4ad `BaseProvider`（给 BuildOrchestrator 用）。
- `_BuildState`：持有当前 `blueprint`/`needs`/`project_name`，供 rebuild 工具跨调用使用。
- `make_tools(base_dir, provider_config, state)`：返回 5 个 `FunctionTool`：
  - `read_file(path)` / `list_dir(path)`：只读，路径过 `_resolve_within_sandbox`。
  - `run_python(path, args)`：`subprocess.run` 跑沙箱内 .py，超时 600s，输出截断。
  - `build_task(description, project_name, metrics_hints, evaluation_hints, data_path,
    code_path, language)`：构造 `NeedsProfile` → `BuildOrchestrator.build()` →
    `write_task_directory` 写入 → 存入 state → 返回产物清单。`BuildError` 时返回闸错误字符串。
  - `rebuild_evaluator(modification_request)`：用 state 里的 blueprint/needs 调
    `BuildOrchestrator.rebuild_evaluator()` → 重写 → 更新 state。
- `run_agent_build(req)`：async 生成器。建 model+toolkit+Agent，设
  `permission_context.mode = BYPASS`，用 `UserMsg`（**不是 `Msg`**）构造输入，
  `agent.reply_stream` 事件 → 映射 `chunk`/工具进度行/`✓` → 末尾
  `build_result`(含 `_blueprint_data(state)`，带 `built` 标志) + `done`；异常 → `error`。
- `router = APIRouter()`，`POST /agent` → SSE。

### 4.3 `src/backend/app/tasks/agent_build_skill.py`（新，领域知识）
- `build_system_prompt(workspace_dir)`：系统提示词。讲清"你不自己写文件，用 build_task
  让引擎生成，再用 run_python 验证、rebuild_evaluator 修"，工具用法，工作流，完成判据
  （build_task 成功 + test_evaluator.py 能加载 + debug_run.py 跑通），并提示"路径越界=
  你试图离开工作区"。要求用用户语言回答。
- `build_task_message(gathering_context, user_content)`：把 needs_profile/description/
  本轮 user_content 拼成给 agent 的任务消息。

### 4.4 `src/backend/app/tasks/chat_tune_runner.py`（改，挂载路由）
- 文件末尾（`main()` 前）新增：
  ```python
  from app.tasks.agent_build_runner import router as _agent_router  # noqa: E402
  app.include_router(_agent_router)
  ```
  放末尾是为了解开循环导入（agent_build_runner 顶部从本模块 import `DATA_DIR`/
  `_resolve_within_sandbox`/`_sse`）。

### 4.5 `src/backend/app/models/chat_tune.py`（改）
- `ChatTuneGenerationKind` 枚举新增 `AI_AGENT = "ai_agent"`。

### 4.6 `src/backend/app/schemas/chat_tune.py`（改）
- `ChatTuneTurnStartRequest` 新增字段 `beta: bool = False`（仅当后端
  `ENABLE_AI_AGENT_BUILD` 开启时生效）。

### 4.7 `src/backend/app/core/config.py`（改）
- `Settings` 新增 `ENABLE_AI_AGENT_BUILD: bool = False`（在 CHAT_TUNE 容器配置段附近）。

### 4.8 `src/backend/app/services/chat_tune_service.py`（改，编排）
- 模块常量新增 `_AGENT_BUILD_TOKEN_TTL = 2 * 3600`（agent 构建代理 token 的短 TTL）。
- `start_turn` 分发器：在原 generation_kind 判定后追加——
  `if request.beta and settings.ENABLE_AI_AGENT_BUILD: generation_kind = AI_AGENT`
  （覆盖阶段判定，因为 agent 一轮内承接全部）。
- `start_turn` 调度分支：新增 `elif generation_kind is AI_AGENT:` →
  `asyncio.create_task(_run_agent_build(..., user_id=current_user.id,
  user_content=llm_user_content, ...))`。
- `retry_turn` 调度分支：新增 `elif kind == AI_AGENT.value:` → 同上（用 `user_msg.content`）。
- 新增 `_maybe_proxy_provider_config(provider_config, user_id, task_id)`：
  LLM_PROXY 开启时发一次性 token（TTL=`_AGENT_BUILD_TOKEN_TTL`）并改写 provider_config
  （api_key=token, base_url=proxy, auth_token="");关闭/mock 时原样返回。
- 新增 `_run_agent_build(...)` 协程：照搬 `_run_ai_build` 结构，区别：
  - 先调 `_maybe_proxy_provider_config` 得到 `container_provider_config`；
  - POST 到 `{base_url}/agent`，body 含 `provider_config`(已脱敏)、`gathering_context`、
    `user_content`；
  - `build_result` 仅当 `blueprint_data.get("built")` 为真才上传产物；
  - `finally` **不调** `revoke_task_tokens`（见 §5 bug 2），靠短 TTL 兜底。

---

## 5. 复查中发现并已修复的两个真 bug（重要）

接手时请知悉这两个坑，改相关代码时别回退：

### Bug 1：`Msg(content="字符串")` 会崩
AgentScope 的 `Msg` 只有 after-validator 断言 content 是 block 列表，没有 before-validator
做 str→block 转换；`_to_blocks` 只在 `UserMsg`/`AssistantMsg` 工厂里调用。
**修复**：`run_agent_build` 用 `from agentscope.message import UserMsg` +
`UserMsg(name="user", content=task_text)`。

### Bug 2：`revoke_task_tokens(task_id)` 会误杀演化任务的 token
`revoke_task_tokens` 删的是该 task_id 下**所有** token，而 `evolution.py` 为**同一个**
task_id 也签发任务级 token。我最初在 `_run_agent_build` 的 `finally` 里调它，会把并发/
后续演化任务的 proxy token 一并删掉。且原 `_run_ai_build` 本就**不** revoke（靠 TTL）。
**修复**：移除 `finally` 里的 revoke，改为发 token 时用短 TTL（`_AGENT_BUILD_TOKEN_TTL=2h`）
自然过期。

---

## 6. 已验证 / 未验证

### 已验证
- `ruff check`：全部新增/修改文件通过（All checks passed）。
- `py_compile`：三个新/改文件语法 OK。
- **沙箱路径围栏**：用 8 个用例本地测 `_resolve_within_sandbox`（runner 工具复用的同款
  函数）——`proj/ok.py`/`./proj/ok.py`/内部不存在路径=放行；`../escape.py`/
  `../../etc/passwd`/`proj/../../../etc/x`/`/etc/passwd`=拒绝。8/8 通过。
- AgentScope API：直接读 v2.0.3 已安装源码核实了所有签名、事件类、权限模式、Msg 工厂。

### 未验证（需要真实环境，见 §7）
- **AgentScope 端到端**：agent 真的跑起来、事件映射正确、工具被正确调用、产出能过闸、
  rebuild 循环有效——需要 ① 可用 provider 的 key+base_url ② 重建镜像 ③ Linux 容器里跑
  （本机 Windows 因杀软导致 agentscope 导入超时，不适合在 host 上验证）。

---

## 7. 剩余工作（按优先级）

1. **重建镜像 + 容器内端到端验证**（最高优先，最早暴露真实问题）：
   - `agentscope` 是新依赖，**backend 与 task-runner 两个镜像都要重建**（task-runner
     复用 backend 依赖；它跑 `chat_tune_runner` 进程，agent 就在这里）。
   - 重建坑：见 §9 —— `./dev.sh full` 默认带 `--build`，task-runner 的 `Dockerfile.task`
     `apt-get` 会连不上 `deb.debian.org`。要么配 `APT_MIRROR`，要么只重建 backend
     的 uv 依赖层（task-runner 镜像本地已有，但需要它带上 agentscope —— 故 task-runner
     也要重建其依赖层）。
   - 验证：开 `ENABLE_AI_AGENT_BUILD=true`（+ 建议 `LLM_PROXY_ENABLE=true` 配好
     `LLM_PROXY_BASE_URL`），配一个 OpenAI 兼容 provider（如 DeepSeek）和一个 Anthropic
     provider 各跑一次 beta 构建；确认产出能过 BuildOrchestrator 闸 + agent 自验通过；
     构造一个诱导 agent 写/读越界路径的需求，确认工具返回拒绝。
2. **前端 Beta 入口**（用户明确说**暂不做**，本次未碰）：
   - 在 `src/frontend` 的 ChatTuneView 加一个 "AI 构建 (Beta)" 入口，发 `startTurn` 时带
     `beta: true`。需重新生成前端 API client（schema 加了 `beta` 字段）。
   - 仅当后端开关开启时显示（需要后端暴露该 flag 给前端，或前端配置）。
3. **可选后续加固**：为 beta 容器搭专用 internal 网络、只放行到 proxy 出口
   （当前沿用 chat_tune 共享网络级别，与现状一致）。

---

## 8. 待用户拍板的开放问题

1. **`_AGENT_BUILD_TOKEN_TTL = 2h` 是否合适**：我自行定的（一轮构建分钟级，2h 给多轮
   debug 留足余量）。若用户认为该更短/更长，改 `chat_tune_service.py` 的常量即可。
2. **`run_python` 超时 600s、`max_iters=40`**：经验值，未与用户确认。多轮 rebuild + 跑
   完整 `debug_run.py`（会真实调 LLM 进化）可能不够或过长。
3. **`build_task` 是否暴露 `multimodal` 入口**：当前没暴露（NeedsProfile 支持 multimodal，
   但 beta 工具签名未加），多模态任务走 beta 暂不支持。
4. **完成判据的"严格度"**：当前靠系统提示词要求 agent 跑通两个自验脚本 + 引擎闸。
   没有在后端再做强制 gate（符合"两层就够"）。若线上发现弱模型 agent 谎报完成，
   可能要加一道后端 gate（但这违反"两层"的决策，需用户重新确认）。

---

## 9. 部署/环境备忘（来自本会话踩的坑）

- 项目可本地 Docker 部署。`docker/.env` 仓库只有示例（`.env.develop.local.example`）。
  本会话已创建过 `docker/.env`（全 Docker 模式：端点用 compose 服务名 db/redis/rustfs/
  mailcatcher/backend；密钥已生成；`HOST_PROJECT_HOME` 指向 `docker/app-data/project_home`）。
- 启动：`docker compose -f compose.yml -f compose.deploy.debug.yml --profile debug up -d`。
  前端 `localhost:18041`，后端 `localhost:8000`/`/docs`，超管 `admin@example.com`/`Admin12345`。
- **CRLF 坑**：`src/backend/scripts/*.sh` 曾被 Windows git autocrlf 转成 CRLF，导致
  `prestart` 容器（迁移+建超管）`\r` 报错。已 `sed -i 's/\r$//'` 修复并重建 backend 镜像。
  若重建镜像后 prestart 又挂，先查这个。
- **task-runner 构建坑**：`./dev.sh full` 带 `--build` 时 `Dockerfile.task` 的 apt
  连不上 deb.debian.org，整批构建 CANCELED。本会话当时用不带 `--build` 的 `up -d` 复用
  既有镜像绕过。**但本次加了 agentscope 依赖，必须重建**——届时需配 `APT_MIRROR`
  （如 `mirrors.aliyun.com`）或确保容器联网，否则 task-runner 镜像重建会失败。
- ruff 在本仓库要用 `uv run --python 3.10 --with ruff ruff check src/...`
  （ruff 不是默认依赖，要 `--with ruff`；CLAUDE.md 要求用 3.10 跑）。

---

## 10. 改动文件清单（速查）

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/backend/pyproject.toml` | 改 | 加 agentscope 依赖 |
| `src/backend/app/tasks/agent_build_runner.py` | 新 | 容器内 agent 引擎(model映射/工具/循环/SSE) |
| `src/backend/app/tasks/agent_build_skill.py` | 新 | agent 系统提示词 + 任务消息构造 |
| `src/backend/app/tasks/chat_tune_runner.py` | 改 | 末尾 include `/agent` 路由 |
| `src/backend/app/models/chat_tune.py` | 改 | 枚举加 `AI_AGENT` |
| `src/backend/app/schemas/chat_tune.py` | 改 | 请求加 `beta` 字段 |
| `src/backend/app/core/config.py` | 改 | 加 `ENABLE_AI_AGENT_BUILD` 开关 |
| `src/backend/app/services/chat_tune_service.py` | 改 | `_AGENT_BUILD_TOKEN_TTL`/`_maybe_proxy_provider_config`/`_run_agent_build`/分发器+retry 分支 |
| `src/backend/app/tasks/agent_build_support.py` | 已删 | 早期"agent自写代码"路线的桥接，选混合后废弃 |

> 提示：未做 DB 迁移。`generation_kind` 是 `ChatTuneMessage` 上的 varchar 字段，加枚举值
> `ai_agent` 不需要 schema 迁移（值是字符串）。若有疑虑可核对 `models/chat_tune.py` 该字段
> 定义（max_length 20，"ai_agent" 长度 8，安全）。
