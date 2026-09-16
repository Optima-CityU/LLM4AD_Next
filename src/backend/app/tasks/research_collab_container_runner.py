"""容器化 Collaborate Agent 执行入口（AgentScope ReAct 一轮）。

在 gate 暂停期间，用户可发起「人 + AI 协作改产物」子会话。每条协作消息由宿主
起一个**短命容器**跑本入口一轮：加载上一轮序列化的 ``AgentState`` 续接，跑一次
AgentScope ``Agent.reply``（ReAct 循环，工具读写 ``stage-NN/`` 产物），把过程事件
经 NDJSON 事件文件回传，末尾把新 ``AgentState`` 序列化落盘供下一轮加载。

工作目录是 ``DATA_DIR``（整个 run 的根，含所有 ``stage-NN/``）；prompt 里只指明
「当前是哪个 stage」，不把工作目录窄化成当前 stage。所有给 agent 的路径都写成
绝对路径——Read/Write/Edit 只接受绝对路径，相对路径直接报错。

与 :mod:`app.tasks.research_container_runner` 同构：
- 只挂 ``run_dir``、容器有网络（调 LLM）、零 ``app.core`` 依赖；
- 配置整体加密经环境变量密钥解密（``task_config_crypto``）；
- 事件走 ``EVENTS_FILENAME`` NDJSON，宿主 tail 转发 Redis/DB；
- 终态经 ``__result__`` 事件回传（不写独立标记文件）。

**协作不推进 pipeline**：agent 只改 ``stage-NN/`` 里的产物文件；本轮结束后控制权
回到原 gate 表单，用户再决定 approve/reject。
"""

import asyncio
import json
import logging
import os
import sys
import threading
import traceback
from typing import Any

# 与本文件同目录，容器内以脚本方式启动时 sys.path[0] 即本目录
import task_config_crypto  # noqa: E402

# run_dir 的容器内挂载路径，须与 app.core.constants.RESEARCH_CONTAINER_DATA_DIR 一致
DATA_DIR = "/research/run"
CONFIG_FILENAME = ".app_config.json"          # = app.core.constants.APP_CONFIG_FILENAME
# 事件文件名由宿主经 env 传入 per-turn 值（.events-<turn_id>.jsonl），避免多轮共享
# 同一文件时宿主 tailer 从 offset 0 重读上一轮的 __collab_text__（前端重放旧回复）。
EVENTS_FILENAME = os.environ.get("RESEARCH_EVENTS_FILENAME") or ".events.jsonl"
COLLAB_STATE_SUBDIR = "hitl/collab"           # = app.core.constants.RESEARCH_COLLAB_STATE_SUBDIR

logger = logging.getLogger("research_collab_container_runner")


class EventsSink:
    """线程安全地向 NDJSON 事件文件追加事件。可作上下文管理器。"""

    def __init__(self, events_path: str) -> None:
        # "w" 覆盖：per-turn 文件正常是新文件，同 turn 复用时清掉残留，杜绝宿主从头
        # tail 读到本 turn 上一次的事件。
        self._fp = open(events_path, "w", encoding="utf-8")  # noqa: SIM115 - 由 close 释放
        self._lock = threading.Lock()

    def emit(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self._fp.write(line)
                self._fp.flush()
            except Exception as exc:
                # 静默吞掉会让宿主收不到 __collab_text__/__result__，退回 exit_code 兜底。
                # 至少打到 stderr（宿主 on_stdout 捕获），便于定位丢了哪个事件。
                etype = event.get("type", "?")
                print(f"[EventsSink] emit failed type={etype}: {exc}", file=sys.stderr, flush=True)  # noqa: T201 - 容器入口，stderr 由宿主 on_stdout 捕获

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass

    def __enter__(self) -> "EventsSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _collab_dir(stage_num: int) -> str:
    """本 stage 的协作会话态目录（``run_dir/hitl/collab/stage-NN/``）。"""
    d = os.path.join(DATA_DIR, COLLAB_STATE_SUBDIR, f"stage-{stage_num:02d}")
    os.makedirs(d, exist_ok=True)
    return d


def _list_stage_artifacts(stage_num: int) -> list[str]:
    """列出 ``stage-NN/`` 下的产物文件名（供 system prompt 上下文）。"""
    stage_dir = os.path.join(DATA_DIR, f"stage-{stage_num:02d}")
    if not os.path.isdir(stage_dir):
        return []
    try:
        return sorted(
            f for f in os.listdir(stage_dir)
            if os.path.isfile(os.path.join(stage_dir, f))
        )
    except OSError:
        return []


# list_dir 单次返回的条目上限：产物目录正常不会这么大，纯粹防爆上下文。
_LIST_DIR_MAX_ENTRIES = 200

# 工具入参 / 出参上报给前端的单字段字符上限。``Read`` 一个大产物、``Grep`` 全库
# 搜索都可能产出几十 KB 文本，原样塞进 SSE 帧和 DB 行会明显放大（SSE 每帧都要
# 跨进程传输、DB 每条消息都要落库）。超限即截断并标注，前端提示「已截断」。
_TOOL_IO_MAX_CHARS = 2000
_TOOL_TRUNCATED_SUFFIX = "... (truncated)"


def _clip_tool_text(text: str, limit: int = _TOOL_IO_MAX_CHARS) -> str:
    """把工具入参 / 出参文本截断到 ``limit`` 字符，超限补截断标记。"""
    if len(text) <= limit:
        return text
    return text[:limit] + _TOOL_TRUNCATED_SUFFIX


def _parse_tool_args(raw: str) -> Any:
    """把累积的工具入参 JSON 片段解析成对象；解析不了则返回 ``None``。

    返回 ``None`` 表示「这不是合法 JSON」（模型可能被中断在半个字符串上），
    调用方据此把原始片段放进 ``input_raw`` 字段兜底展示。
    """
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _list_dir(path: str | None = None) -> str:
    """List the files and subdirectories at an absolute path.

    Prefer this over Glob when you need to see what a directory contains:
    Glob only returns files at its final pattern segment, so subdirectories
    are invisible to it.

    Args:
        path: Absolute path of the directory to list. Omit it to list the
            run root, which holds every stage directory.

    Returns:
        One entry per line, sorted, with a trailing ``/`` on subdirectories.
        A short explanatory string if the path is not a directory or cannot
        be read.
    """
    # 默认值在调用时取 DATA_DIR，而非写成 def _list_dir(path=DATA_DIR)：函数默认值在
    # 定义时求值，写成字面量会把路径写死在签名里，也和 _run_pipeline 的模块全局风格
    # 不一致。
    target = (path or DATA_DIR).strip()
    if not os.path.isabs(target):
        return (
            f"Error: path must be absolute, got '{target}'. "
            f"The run root is '{DATA_DIR}'."
        )
    if not os.path.isdir(target):
        return f"Error: not a directory: {target}"
    try:
        entries = sorted(os.listdir(target))
    except OSError as exc:
        return f"Error: cannot list {target}: {exc}"
    if not entries:
        return "(empty directory)"
    lines: list[str] = []
    for name in entries[:_LIST_DIR_MAX_ENTRIES]:
        full = os.path.join(target, name)
        lines.append(f"{name}/" if os.path.isdir(full) else name)
    if len(entries) > _LIST_DIR_MAX_ENTRIES:
        lines.append(f"... ({len(entries) - _LIST_DIR_MAX_ENTRIES} more entries omitted)")
    return "\n".join(lines)


def _build_system_prompt(
    stage_num: int,
    stage_name: str,
    topic: str,
    is_gate: bool,
    pipeline_context: str = "",
) -> str:
    """构造协作 agent 的 system prompt：流水线上下文 + stage 产物 + 协作契约。"""
    artifacts = _list_stage_artifacts(stage_num)
    # 绝对路径：Read/Write/Edit 都要求 absolute file_path（相对路径直接报错），只给
    # 相对目录 agent 就无从推断自己在哪，只能瞎猜（实测会答 "/"）。
    # 拼 POSIX 路径而非 os.path.join：本脚本只在 Linux 容器里跑，DATA_DIR 是固定
    # 常量，若在 Windows 上渲染 prompt 会拼出 `\research\run\stage-10` 这种反斜杠。
    stage_dir_abs = f"{DATA_DIR}/stage-{stage_num:02d}"
    artifact_lines = "\n".join(f"  - {stage_dir_abs}/{f}" for f in artifacts) or "  (none yet)"
    # 宿主拼好的 ARC 总览 + 上一步/本步/下一步用途（build_stage_context）。空则退回
    # 只报 stage 名，保持无上下文时也能工作。
    context_block = (
        f"{pipeline_context}\n\n" if pipeline_context else ""
    )
    if is_gate:
        pipeline_block = (
            "\n\nThe pipeline is currently PAUSED at an approval gate for this "
            "stage. If the human clearly asks to move the pipeline (e.g. \"looks "
            "good, continue\" / \"reject, redo this stage\" / \"stop this run\"), "
            "call the `run_pipeline` tool: `next` approves and advances, `current` "
            "rejects and re-runs this stage, `previous` goes back one stage, "
            "`stage:N` jumps to stage N, `abort` stops the run. Only call it on an "
            "explicit request — otherwise just chat and revise files. Prefer a brief "
            "clarifying question if the intent is ambiguous."
        )
    else:
        pipeline_block = (
            "\n\nThe pipeline is NOT running right now. If the human clearly asks to "
            "run or advance it (e.g. \"run the pipeline\" / \"go to the next stage\" "
            "/ \"restart from the beginning\"), call the `run_pipeline` tool: "
            "`next` runs the next stage, `previous` the previous one, `current` "
            "re-runs the current stage, `restart` runs from stage 1, `stage:N` runs "
            "from stage N. Only call it on an explicit request — otherwise just chat "
            "and revise files. Prefer a brief clarifying question if ambiguous."
        )
    return (
        "You are a research collaboration assistant working WITH a human on the "
        f"output of pipeline stage {stage_num} ({stage_name}).\n\n"
        f"{context_block}"
        f"Research topic:\n{topic}\n\n"
        f"Your working directory is `{DATA_DIR}` — the root of this whole run. It "
        "holds every stage's output in one directory per stage (`stage-01/`, "
        "`stage-02/`, …), so you can read earlier stages' files as reference and "
        "know what later stages will consume. All your file tools need ABSOLUTE "
        f"paths; always spell the full path (e.g. `{stage_dir_abs}/xxx.md`), never "
        "a bare `stage-01/...`.\n\n"
        f"The run root also contains `{DATA_DIR}/deliverables/`, where the final "
        "human-facing results are gathered once all 23 stages finish (final paper "
        "in Markdown / LaTeX / PDF, bibliography, figures, code, verification "
        "reports, `manifest.json`). Send the human there when they ask for the "
        "final paper or want to download the finished results; if that directory "
        "is missing, the run has not finished yet.\n\n"
        f"The current stage is stage {stage_num}, whose directory is "
        f"`{stage_dir_abs}/`. Files produced by this stage (inspect and revise "
        f"these) are:\n{artifact_lines}\n\n"
        "Use the `list_dir` tool to see what a directory contains — it reports "
        "subdirectories too. `Glob` only returns files, so it cannot show you the "
        "stage directories.\n\n"
        "Default to revising the current stage's files, and put any new file in "
        f"`{stage_dir_abs}/` unless the human says otherwise. Do not modify other "
        "stages' files unless the human explicitly asks.\n\n"
        "Your job: discuss the stage output with the human and, when they ask, "
        "revise the files using your Read/Edit/Write tools. Always Read a file "
        "before editing it. Keep edits focused and explain what you changed. When "
        "revising, keep the output compatible with what the next stage expects. "
        "Do not fabricate results; base every revision on the actual file contents."
        f"{pipeline_block}"
    )


def _load_agent_state(state_path: str):
    """加载上一轮序列化的 ``AgentState``；不存在或损坏则回落全新 state。"""
    from agentscope.state import AgentState

    if not os.path.isfile(state_path):
        return AgentState()
    try:
        with open(state_path, encoding="utf-8") as f:
            return AgentState.model_validate_json(f.read())
    except Exception:
        logger.warning("collab agent_state load failed, starting fresh", exc_info=True)
        return AgentState()


def _save_agent_state(state_path: str, state) -> None:
    """把本轮结束后的 ``AgentState`` 序列化落盘，供下一轮容器续接。"""
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json())
    except Exception:
        logger.warning("collab agent_state save failed", exc_info=True)


# agent 调用 run_pipeline 工具时把意图写这里；main 结束时读进 marker，宿主据它推进
# pipeline（门控中经 _reply_to_gate，非门控经 start_pipeline_turn；鉴权/建轮全在宿主，
# agent 不碰 DB）。模块全局安全：每个容器只跑一轮 turn 就退出，无跨 turn 复用 / 并发。
_PIPELINE_INTENT_HOLDER: dict = {}


def _run_pipeline(target: str, message: str = "") -> str:
    """Run / advance the research pipeline (host applies it after this turn ends).

    Use this only when the human clearly asks to run or move the pipeline.

    Args:
        target: Which run to trigger. One of:
            ``current`` (re-run the current stage), ``next`` (advance to the next
            stage / approve the gate), ``previous`` (go back one stage),
            ``restart`` (run from the first stage), ``stage:N`` (run from stage N,
            1-based), or ``abort`` (stop this run — only meaningful at a gate).
        message: Optional guidance / reason, injected into the target stage.

    Returns:
        A confirmation string; the actual run is started by the host once this
        collaboration turn finishes.
    """
    tgt = (target or "").strip().lower()
    _PIPELINE_INTENT_HOLDER["target"] = tgt
    _PIPELINE_INTENT_HOLDER["message"] = (message or "").strip()
    return f"Pipeline action '{tgt}' recorded; it will be applied when this turn ends."


def _build_agent(provider_cfg: dict, system_prompt: str, state):
    """构造 AgentScope ReAct Agent：OpenAI 兼容 model + 产物读写工具集。

    权限设为 ``BYPASS``：容器已隔离（只挂 run_dir、无跨用户访问、无网络到内网），
    是「沙箱 + 无人值守」场景——Read/Write/Edit 不再 ASK 卡住主循环。

    始终注册 ``run_pipeline`` 工具，让用户可用自然语言驱动流水线首跑 / 续跑 / 回跳
    （门控与非门控的语义差异由 system prompt 措辞承担）。

    另注册 ``list_dir``：内置 ``Glob`` 只看得到文件，列不出子目录，用它补上。

    三项健壮性配置（无人值守长协作场景刚需）：
    - ``context_config``：多轮 memory 逼近上下文上限时自动压缩早期消息；单条工具
      结果超限即截断——协作 agent 会 ``Read`` 整个 stage 产物，不截断易塞满上下文。
    - ``model_config``：第三方 OpenAI 兼容端点瞬态 5xx/超时常见，默认零重试会让整轮
      collab 直接 FAILED，这里给 2 次重试兜底。
    - ``react_config``：无人拦截的 BYPASS 沙箱里给 ReAct 迭代封顶，防死循环烧 token。
    """
    from agentscope.agent import Agent, ContextConfig, ModelConfig, ReActConfig
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel
    from agentscope.permission import PermissionMode
    from agentscope.tool import Edit, FunctionTool, Glob, Grep, Read, Toolkit, Write

    # 沙箱内放行工具，避免 DEFAULT 模式下 Read/Write 等待用户确认而挂死。
    try:
        state.permission_context.mode = PermissionMode.BYPASS
    except Exception:
        logger.warning("set BYPASS permission mode failed", exc_info=True)

    credential = OpenAICredential(
        api_key=provider_cfg.get("api_key") or "",
        base_url=provider_cfg.get("base_url") or None,
    )
    model = OpenAIChatModel(
        credential=credential,
        model=provider_cfg.get("primary_model") or "gpt-4o",
        stream=True,
    )
    tools = [Read(), Write(), Edit(), Grep(), Glob()]
    # Glob 只返回文件（helper 在最后一级 pattern 上 `if entry.is_file(...)` 过滤掉了
    # 目录），问「当前目录有什么」看不到 stage-NN/ 这类子目录。补一个原生 list_dir。
    # name 显式给成 "list_dir"：函数名自带下划线，默认会成为工具名暴露给模型。
    tools.append(FunctionTool(_list_dir, name="list_dir", is_read_only=True))
    tools.append(FunctionTool(_run_pipeline, is_read_only=False))
    toolkit = Toolkit(tools=tools)
    return Agent(
        name="collab_agent",
        system_prompt=system_prompt,
        model=model,
        toolkit=toolkit,
        state=state,
        context_config=ContextConfig(
            trigger_ratio=0.8,       # 用量到 80% 上下文时压缩早期消息
            reserve_ratio=0.1,       # 压缩后保留最近 10% 原文
            tool_result_limit=8000,  # 单条工具结果超 8000 token 截断（默认 50000 过大）
        ),
        model_config=ModelConfig(max_retries=2),
        react_config=ReActConfig(max_iters=20),
    )


async def _run_collab_turn(agent, user_message: str, events: EventsSink) -> str:
    """跑一轮 ReAct，把过程事件经 events 通道回传，返回 agent 最终文本。

    事件类型（宿主翻译成 SSE ``collab_message`` / ``collab_tool``）：
    - ``__collab_text__``：agent 流式文本增量（``TextBlockDeltaEvent.delta``）；
    - ``__collab_tool__``（phase=start）：工具调用开始，只有工具名，用于前端
      「正在调用 X…」的实时提示；
    - ``__collab_tool__``（phase=end）：同一调用结束时补齐入参 / 出参 / 终态。

    注：``EventBase`` 用 ``use_enum_values=True``，故 ``event.type`` 是字符串
    （如 ``"TEXT_BLOCK_DELTA"``）；与 ``EventType`` StrEnum 成员直接 == 比较成立。

    参数与结果都是**增量**给的：``TOOL_CALL_DELTA.delta`` 是入参 JSON 的片段，
    ``TOOL_RESULT_TEXT_DELTA.delta`` 是结果文本的片段，按 ``tool_call_id`` 各自
    拼接才得到完整内容——所以 end 事件必须等 ``TOOL_RESULT_END`` 才发。若断言
    「start 就报完整调用」会拿到半截 JSON。

    一轮结束时仍有未闭合的调用（agent 在中途被 ``max_iters`` 截断 / 异常，拿不到
    ``TOOL_RESULT_END``），在收尾处统一补齐成 ``state=interrupted``，否则前端会
    留下永远转圈的卡片。
    """
    from agentscope.event import EventType
    from agentscope.message import UserMsg

    final_text_parts: list[str] = []
    # tool_call_id → {name, args 片段, 结果片段}。同一轮里模型可能多次调用同一个工具，
    # 只有 call_id 能区分它们，所以 key 用它；call_id 为空（异常情况）时同键覆盖会丢
    # 掉一条，此时前端按「序号 + 名字」兜底归并，不至于把两次调用混成一条。
    pending: dict[str, dict[str, Any]] = {}

    def _flush_one(call_id: str, state: str | None) -> None:
        """把一条累积中的调用作为 end 事件发出（幂等：发过即从 pending 摘除）。"""
        info = pending.pop(call_id, None)
        if info is None:
            return
        raw_args = "".join(info["args"]).strip()
        args = _parse_tool_args(raw_args)
        output = _clip_tool_text("".join(info["result"]))
        events.emit({
            "type": "__collab_tool__",
            "phase": "end",
            "name": info["name"],
            "tool_call_id": call_id,
            "input": args,
            "input_raw": _clip_tool_text(raw_args) if args is None else "",
            "output": output,
            "state": state or info["state"] or "success",
        })

    async for event in agent.reply_stream(UserMsg(name="user", content=user_message)):
        etype = getattr(event, "type", None)
        try:
            if etype == EventType.TEXT_BLOCK_DELTA:
                delta = getattr(event, "delta", "") or ""
                if delta:
                    final_text_parts.append(delta)
                    events.emit({"type": "__collab_text__", "delta": delta})
            elif etype == EventType.TOOL_CALL_START:
                call_id = getattr(event, "tool_call_id", "") or ""
                name = getattr(event, "tool_call_name", "") or ""
                pending[call_id] = {"name": name, "args": [], "result": [], "state": ""}
                events.emit({
                    "type": "__collab_tool__",
                    "phase": "start",
                    "name": name,
                    "tool_call_id": call_id,
                })
            elif etype == EventType.TOOL_CALL_DELTA:
                info = pending.get(getattr(event, "tool_call_id", "") or "")
                if info is not None:
                    info["args"].append(getattr(event, "delta", "") or "")
            elif etype == EventType.TOOL_RESULT_TEXT_DELTA:
                info = pending.get(getattr(event, "tool_call_id", "") or "")
                if info is not None:
                    info["result"].append(getattr(event, "delta", "") or "")
            elif etype == EventType.TOOL_RESULT_END:
                call_id = getattr(event, "tool_call_id", "") or ""
                info = pending.get(call_id)
                if info is not None:
                    # ToolResultState 用 use_enum_values，取到的已是字符串
                    info["state"] = str(getattr(event, "state", "") or "")
                _flush_one(call_id, None)
        except Exception:
            logger.debug("collab event forward error", exc_info=True)

    # 收尾：ReAct 被 max_iters 截断 / 中途异常时，调用可能只走到 start。补齐 end，
    # 否则前端那条「正在调用」永远停在加载态。
    for call_id in list(pending):
        _flush_one(call_id, "interrupted")

    return "".join(final_text_parts)


def main() -> None:
    """容器入口：解密配置 → 跑一轮协作 → 序列化 state → 写终态。"""
    try:
        config_key = os.environ.pop("RESEARCH_COLLAB_CONFIG_KEY", None)
        config_path = os.path.join(DATA_DIR, CONFIG_FILENAME)

        with open(config_path, encoding="utf-8") as f:
            token = f.read()
        try:
            os.remove(config_path)
        except OSError:
            pass
        if not config_key:
            raise RuntimeError("缺少 RESEARCH_COLLAB_CONFIG_KEY，无法解密协作配置")
        data = task_config_crypto.decrypt_config(token, config_key)
        del token, config_key

        stage_num = int(data.get("stage_num") or 0)
        stage_name = str(data.get("stage_name") or "")
        topic = str(data.get("topic") or "")
        user_message = str(data.get("user_message") or "").strip()
        provider_cfg = data.get("provider_config") or {}
        is_gate = bool(data.get("is_gate", False))
        pipeline_context = str(data.get("pipeline_context") or "")

        os.chdir(DATA_DIR)

        collab_dir = _collab_dir(stage_num)
        state_path = os.path.join(collab_dir, "agent_state.json")

        with EventsSink(os.path.join(DATA_DIR, EVENTS_FILENAME)) as events:
            if not user_message:
                events.emit({"type": "__result__", "marker": {
                    "outcome": "collab_done", "reason": "empty user message",
                }})
                return

            system_prompt = _build_system_prompt(
                stage_num, stage_name, topic, is_gate, pipeline_context
            )
            state = _load_agent_state(state_path)
            agent = _build_agent(provider_cfg, system_prompt, state)

            final_text = asyncio.run(_run_collab_turn(agent, user_message, events))

            _save_agent_state(state_path, agent.state)

            # 终态经 events 通道回传（含 agent 最终文本 + 可选 pipeline 意图）。
            # pipeline_target 非空时宿主据它推进流水线（门控中经 _reply_to_gate，
            # 非门控经 start_pipeline_turn）。
            marker = {
                "outcome": "collab_done",
                "stage": stage_num,
                "final_text": final_text,
            }
            if _PIPELINE_INTENT_HOLDER.get("target"):
                marker["pipeline_target"] = _PIPELINE_INTENT_HOLDER["target"]
                marker["pipeline_message"] = _PIPELINE_INTENT_HOLDER.get("message", "")
            events.emit({"type": "__result__", "marker": marker})

        logger.info("collab turn finished stage=%s", stage_num)

    except Exception as exc:
        logger.error(f"collab turn failed: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
