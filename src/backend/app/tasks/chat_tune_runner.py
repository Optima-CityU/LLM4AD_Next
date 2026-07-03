"""调参（chat-tune）隔离容器入口：容器内 SSE 服务。

在复用 ``TASK_RUNNER_IMAGE`` 的即用即销容器中执行
``llm4ad.consultant.core.process_needs_gathering_turn_stream``，把
needs-gathering 这一可能触发文件读写的逻辑从特权 backend 容器中隔离出来。

- 端口**不发布**到宿主，仅在 docker 用户网络内可达；backend 通过容器名
  做 DNS 解析后消费本服务的 SSE。
- ``read_file`` 工具被沙箱限制在挂载目录 ``/task/data`` 内，无法逃逸到
  镜像其余文件系统。
- Provider 配置（含 api_key）随请求体传入，不落盘、不进环境变量。

事件协议（``text/event-stream``，每帧一行 JSON）：

- ``{"type": "chunk", "content": "..."}``：LLM 增量文本。
- ``{"type": "response", ...}``：本轮结构化结果（消息/选项/完成）。
- ``{"type": "build_result", "blueprint_data": {...}}``：构建成功。
- ``{"type": "review_response", ...}``：review 结构化结果。
- ``{"type": "done"}``：正常结束。
- ``{"type": "error", "error": "..."}``：异常结束。
"""

import json
import os
import re
import traceback
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import StreamingResponse

DATA_DIR = os.environ.get("CHAT_TUNE_DATA_DIR", "/task/data")
PORT = int(os.environ.get("PORT", "8800"))
_MAX_FILE_LINES = 500
_MAX_ERROR_LEN = 2000

app = FastAPI(title="llm4ad-chat-tune-runner")


class RunRequest(BaseModel):
    """``POST /run`` 请求体。"""

    provider_config: dict[str, Any]
    user_content: str = ""
    gathering_context: dict[str, Any] | None = None
    max_tool_rounds: int = 3


class BuildRequest(BaseModel):
    """``POST /build`` 请求体。"""

    provider_config: dict[str, Any]
    gathering_context: dict[str, Any] | None = None


class ReviewRequest(BaseModel):
    """``POST /review`` 请求体。"""

    provider_config: dict[str, Any]
    user_content: str = ""
    gathering_context: dict[str, Any] | None = None


class AgentBuildRequest(BaseModel):
    """``POST /agent`` 请求体（AI 构建 beta）。"""

    provider_config: dict[str, Any]
    gathering_context: dict[str, Any] | None = None
    user_content: str = ""
    allow_build: bool = False
    agent_state: dict[str, Any] | None = None
    proposed: dict[str, Any] | None = None
    max_iters: int = 40


def _resolve_within_sandbox(base: Path, path: str) -> Path | None:
    """Resolve a user-supplied path against the sandbox root.

    解析规则（按 LLM 常见误传场景设计）：

    1. 相对路径（如 ``code/x.py`` 或 ``./code/x.py``）按 ``base`` 解析。
    2. 以 ``base`` 为前缀的绝对路径直接放行。
    3. 其它绝对路径——包括 LLM 误把 ``code/x.py`` 写成 ``/code/x.py`` 这种
       本意指代项目内部的路径——会重写为相对路径再按规则 1 解析。
    4. 上述步骤后若 ``..`` 仍然逃逸出 ``base``，返回 ``None``。

    LLM 无法从工具描述里知道挂载点是 ``/task/data/``，常会自作主张加上 ``/``
    前缀或拼一个错的绝对路径；规则 3 把这些常见误传修正为容器里能命中的
    路径，同时不放宽对真实越权访问的限制。

    Args:
        base: 沙箱根目录（已 resolve）。
        path: 用户/LLM 传入的路径。

    Returns:
        位于 ``base`` 内的已解析绝对路径；逃逸时返回 ``None``。
    """
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            # 已经位于 base 内：直接采用
            candidate.resolve().relative_to(base)
            resolved = candidate.resolve()
        except ValueError:
            # 不在 base 内：剥掉首根再当相对路径处理（容忍 "/code/x.py" 这类误传）
            rel = Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path()
            resolved = (base / rel).resolve()
    else:
        resolved = (base / candidate).resolve()

    if resolved != base and base not in resolved.parents:
        return None
    return resolved


def _make_sandboxed_file_reader(base_dir: str):
    """构造限制在 ``base_dir`` 内的 file_reader。

    路径修正规则见 :func:`_resolve_within_sandbox`。

    Args:
        base_dir: 沙箱根目录（容器内挂载点）。

    Returns:
        与 ``llm4ad`` ``FileReader`` 兼容的可调用对象。
    """
    base = Path(base_dir).resolve()

    def _reader(path: str) -> str:
        resolved = _resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes data directory: {path}"
        if not resolved.is_file():
            # Fallback: the LLM may have referenced a file by its bare name
            # (dropping the parent prefix shown in the directory listing).
            # Search the sandbox by basename; if exactly one match is found,
            # tell the LLM the correct relative path so it can retry.
            basename = Path(path).name
            if basename and basename not in ("", ".", ".."):
                try:
                    matches = [
                        p for p in base.rglob(basename)
                        if p.is_file()
                    ][:5]
                except OSError:
                    matches = []
                if len(matches) == 1:
                    rel = matches[0].relative_to(base).as_posix()
                    return (
                        f"Error: File not found: {path}. "
                        f"Did you mean `{rel}`? Retry with the full relative path."
                    )
                if len(matches) > 1:
                    rels = ", ".join(
                        f"`{m.relative_to(base).as_posix()}`" for m in matches
                    )
                    return (
                        f"Error: File not found: {path}. "
                        f"Multiple files match this name: {rels}. "
                        f"Retry with the full relative path."
                    )
            return f"Error: File not found: {path}"
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"Error reading file: {e}"
        lines = content.splitlines()
        if len(lines) > _MAX_FILE_LINES:
            content = (
                    "\n".join(lines[:_MAX_FILE_LINES])
                    + f"\n\n... (truncated, {len(lines)} total lines)"
            )
        return content

    return _reader


def _make_sandboxed_directory_reader(base_dir: str):
    """构造限制在 ``base_dir`` 内的 directory_reader。

    路径修正规则见 :func:`_resolve_within_sandbox`；通过校验后委托给
    ``llm4ad.consultant.core.default_directory_reader`` 完成目录遍历，
    避免在此处重复实现 tree-walking 逻辑。

    Args:
        base_dir: 沙箱根目录（容器内挂载点）。

    Returns:
        与 ``llm4ad`` ``DirectoryReader`` 兼容的可调用对象。
    """
    from llm4ad.consultant.core import default_directory_reader

    base = Path(base_dir).resolve()

    def _reader(path: str) -> str:
        resolved = _resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes data directory: {path}"
        if not resolved.is_dir():
            return f"Error: Directory not found: {path}"
        output = default_directory_reader(str(resolved))
        # default_directory_reader uses resolved.name as the tree root and lists
        # entries by basename only, so the LLM tends to drop the parent prefix
        # (e.g. `code/`) when it later references a file. Fix three things:
        #   1. Rewrite the tree root to the path relative to the sandbox base.
        #   2. Rewrite each `### filename` heading under "## Key Files" to the
        #      full relative path.
        #   3. Prepend an explicit note instructing the LLM to use full
        #      relative paths when referencing any listed file.
        try:
            display_root = resolved.relative_to(base).as_posix()
        except ValueError:
            display_root = resolved.name
        if display_root in ("", "."):
            display_root = resolved.name

        original_root = f"{resolved.name}/"
        new_root = f"{display_root}/"
        if original_root != new_root:
            output = output.replace(original_root, new_root, 1)

        # Rewrite each "### filename" under Key Files to "### display_root/filename".
        def _rewrite_key_file_heading(match: "re.Match[str]") -> str:
            filename = match.group(1).strip()
            return f"### {display_root.rstrip('/')}/{filename}"

        output = re.sub(
            r"^### (.+)$",
            _rewrite_key_file_heading,
            output,
            flags=re.MULTILINE,
        )

        prefix_note = (
            f"NOTE: All paths in this listing are relative to the workspace root. "
            f"When referencing any file shown below, you MUST prepend "
            f"`{display_root}/` to the file name (e.g., `{display_root}/foo.py`, "
            f"NOT just `foo.py`).\n\n"
        )
        return prefix_note + output

    return _reader


def _sse(obj: dict[str, Any]) -> str:
    """把一个事件 dict 编码为单帧 SSE 文本。"""
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _t(language: str | None, zh: str, en: str) -> str:
    """按语言挑选进度/状态文案，缺省回退中文。

    Args:
        language: 语言码（'zh'/'en' 等），来自 ``gathering_context['language']``。
        zh: 中文文案。
        en: 英文文案。

    Returns:
        ``language == 'en'`` 时返回英文，否则返回中文。
    """
    return en if (language or "zh") == "en" else zh


def _serialize_response(response: Any) -> dict[str, Any]:
    """把 ``NeedsGatheringResponse`` 序列化为可回传的事件 dict。

    展示用的 payload（表单结构）仍由 backend 构建，这里只回传 consultant
    的原始字段。
    """
    choices = None
    if response.choices is not None:
        choices = [
            {
                "number": c.number,
                "label": c.label,
                "description": c.description,
                "full_text": c.full_text,
                "is_custom": c.is_custom,
                "is_recommended": c.is_recommended,
                "ask_for_path": c.ask_for_path,
                "ask_for_dir": c.ask_for_dir,
            }
            for c in response.choices
        ]
    return {
        "type": "response",
        "response_type": response.response_type.value,
        "assistant_message": response.assistant_message,
        "choices": choices,
        "needs_profile": response.needs_profile,
        "tool_calls_executed": response.tool_calls_executed,
        "updated_messages": response.updated_messages,
    }


async def _event_stream(req: RunRequest) -> AsyncIterator[str]:
    """逐项把 ``process_needs_gathering_turn_stream`` 的输出编码为 SSE 帧。"""
    from llm4ad.consultant.core import (
        NeedsGatheringContext,
        NeedsGatheringResponse,
        process_needs_gathering_turn_stream,
    )
    from llm4ad.infra.provider.base import BaseProvider

    stream = None
    try:
        provider = BaseProvider.create(
            req.provider_config["type"], config=req.provider_config
        )

        gctx = req.gathering_context or {}
        ctx = NeedsGatheringContext(
            phase_messages=list(gctx.get("phase_messages") or []),
            user_context=gctx.get("user_context"),
            user_input=req.user_content,
            language=gctx.get("language") or "zh",
        )

        stream = process_needs_gathering_turn_stream(
            ctx,
            provider,
            file_reader=_make_sandboxed_file_reader(DATA_DIR),
            directory_reader=_make_sandboxed_directory_reader(DATA_DIR),
            max_tool_rounds=req.max_tool_rounds,
        )
        async for item in stream:
            if isinstance(item, str):
                yield _sse({"type": "chunk", "content": item})
            elif isinstance(item, NeedsGatheringResponse):
                yield _sse(_serialize_response(item))

        yield _sse({"type": "done"})
    except Exception as exc:
        tb = traceback.format_exc()
        msg = f"{type(exc).__name__}: {exc}\n{tb}"[:_MAX_ERROR_LEN]
        yield _sse({"type": "error", "error": msg})
    finally:
        if stream is not None and hasattr(stream, "aclose"):
            try:
                await stream.aclose()
            except Exception:
                pass


@app.get("/health")
async def health() -> dict[str, str]:
    """就绪探测端点。"""
    return {"status": "ok"}


@app.post("/run")
async def run(req: RunRequest) -> StreamingResponse:
    """执行一轮 needs-gathering 并以 SSE 流返回事件。"""
    return StreamingResponse(
        _event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Build ----


async def _build_event_stream(req: BuildRequest) -> AsyncIterator[str]:
    """在容器内执行 BuildOrchestrator.build 并以 SSE 帧返回。"""
    from llm4ad.builder.writer import write_task_directory
    from llm4ad.consultant.build_orchestrator import BuildError, BuildOrchestrator
    from llm4ad.consultant.needs import NeedsProfile
    from llm4ad.infra.provider.base import BaseProvider

    try:
        ctx = req.gathering_context or {}
        language = ctx.get("language") or "zh"
        needs_dict = ctx.get("needs_profile")
        if not needs_dict:
            yield _sse({"type": "error", "error": _t(
                language, "缺少 needs_profile，无法构建",
                "Missing needs_profile; cannot build",
            )})
            return

        yield _sse({"type": "chunk", "content": _t(
            language, "🔨 **开始 AI 构建**\n\n", "🔨 **Starting AI build**\n\n",
        )})
        yield _sse({"type": "chunk", "content": _t(
            language, "正在初始化构建环境...\n\n",
            "Initializing build environment...\n\n",
        )})

        provider = BaseProvider.create(
            req.provider_config["type"], config=req.provider_config
        )
        needs = NeedsProfile.from_dict(needs_dict)

        yield _sse({"type": "chunk", "content": _t(
            language, "正在分析需求并生成代码...\n\n",
            "Analyzing requirements and generating code...\n\n",
        )})

        try:
            builder = BuildOrchestrator(provider, console=None, max_repair_attempts=10)
            blueprint = await builder.build(needs)
        except BuildError as exc:
            yield _sse({"type": "chunk", "content": _t(
                language, f"\n❌ **构建失败**：{exc}\n",
                f"\n❌ **Build failed**: {exc}\n",
            )})
            yield _sse({"type": "error", "error": str(exc)[:_MAX_ERROR_LEN]})
            return

        yield _sse({"type": "chunk", "content": _t(
            language, "正在写入任务文件...\n\n", "Writing task files...\n\n",
        )})
        write_task_directory(blueprint, DATA_DIR)

        yield _sse({"type": "chunk", "content": _t(
            language,
            "\n✅ **构建完成！**你可以继续对话来 review 和调整配置。\n",
            "\n✅ **Build complete!** You can keep chatting to review and "
            "adjust the configuration.\n",
        )})
        yield _sse({"type": "build_result", "blueprint_data": asdict(blueprint)})
        yield _sse({"type": "done"})
    except Exception as exc:
        tb = traceback.format_exc()
        msg = f"{type(exc).__name__}: {exc}\n{tb}"[:_MAX_ERROR_LEN]
        yield _sse({"type": "error", "error": msg})


@app.post("/build")
async def build(req: BuildRequest) -> StreamingResponse:
    """执行 AI 构建并以 SSE 流返回事件。"""
    return StreamingResponse(
        _build_event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Review ----


def _serialize_review_response(response: Any, blueprint_data: dict[str, Any]) -> dict[str, Any]:
    """把 ReviewResponse 序列化为可回传的事件 dict。"""
    return {
        "type": "review_response",
        "action": response.action.value,
        "assistant_message": response.assistant_message,
        "updated_messages": response.updated_messages,
        "modification_text": response.modification_text,
        "blueprint_data": blueprint_data,
    }


async def _review_event_stream(req: ReviewRequest) -> AsyncIterator[str]:
    """在容器内执行 review 对话并以 SSE 帧返回。"""
    from llm4ad.builder.blueprint import TaskBlueprint
    from llm4ad.builder.writer import write_task_directory
    from llm4ad.consultant.build_orchestrator import BuildError, BuildOrchestrator
    from llm4ad.consultant.core import (
        ReviewAction,
        ReviewContext,
        ReviewResponse,
        process_review_turn_stream,
    )
    from llm4ad.consultant.needs import NeedsProfile
    from llm4ad.consultant.prompts import REVIEW_SYSTEM_PROMPT, build_review_prompt
    from llm4ad.infra.provider.base import BaseProvider

    stream = None
    try:
        ctx = req.gathering_context or {}
        language = ctx.get("language") or "zh"
        blueprint_data = ctx.get("blueprint_data")
        needs_dict = ctx.get("needs_profile")
        if not blueprint_data or not needs_dict:
            yield _sse({"type": "error", "error": _t(
                language, "缺少构建产物或需求分析结果",
                "Missing build artifact or needs analysis result",
            )})
            return

        provider = BaseProvider.create(
            req.provider_config["type"], config=req.provider_config
        )
        blueprint = TaskBlueprint(**blueprint_data)
        needs = NeedsProfile.from_dict(needs_dict)

        review_messages: list[dict[str, str]] = ctx.get("review_messages", [])

        is_first_review = len(review_messages) == 0
        initial_message = None
        user_input = None
        if is_first_review:
            initial_message = build_review_prompt(
                evaluator_code=blueprint.evaluator_code,
                description=needs.description,
                metrics=blueprint.metrics,
                language=language,
            )
        else:
            user_input = req.user_content or None

        review_ctx = ReviewContext(
            phase_messages=list(review_messages),
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_input=user_input,
            initial_message=initial_message,
            language=language,
        )

        review_response = None
        stream = process_review_turn_stream(review_ctx, provider)
        async for item in stream:
            if isinstance(item, str):
                yield _sse({"type": "chunk", "content": item})
            elif isinstance(item, ReviewResponse):
                review_response = item

        if review_response is None:
            yield _sse({"type": "error", "error": _t(
                language, "review 流未返回 ReviewResponse",
                "Review stream did not return a ReviewResponse",
            )})
            return

        action = review_response.action

        if action == ReviewAction.MODIFY_EVALUATOR and review_response.modification_text:
            yield _sse({"type": "chunk", "content": _t(
                language, "\n\n🔧 正在修改评估器...\n",
                "\n\n🔧 Modifying evaluator...\n",
            )})
            try:
                builder = BuildOrchestrator(provider, console=None)
                await builder.rebuild_evaluator(
                    blueprint, review_response.modification_text, needs
                )
                yield _sse({"type": "chunk", "content": _t(
                    language, "✅ 评估器已更新。\n", "✅ Evaluator updated.\n",
                )})
            except BuildError as exc:
                yield _sse({"type": "chunk", "content": _t(
                    language, f"⚠️ 修改失败：{exc}\n",
                    f"⚠️ Modification failed: {exc}\n",
                )})

        elif action == ReviewAction.REGENERATE:
            yield _sse({"type": "chunk", "content": _t(
                language, "\n\n🔄 正在重新构建...\n", "\n\n🔄 Rebuilding...\n",
            )})
            try:
                builder = BuildOrchestrator(provider, console=None)
                blueprint = await builder.build(needs)
                yield _sse({"type": "chunk", "content": _t(
                    language, "✅ 重新构建完成。\n", "✅ Rebuild complete.\n",
                )})
            except BuildError as exc:
                yield _sse({"type": "chunk", "content": _t(
                    language, f"⚠️ 重新构建失败：{exc}\n",
                    f"⚠️ Rebuild failed: {exc}\n",
                )})

        elif action == ReviewAction.CONFIRMED:
            yield _sse({"type": "chunk", "content": _t(
                language, "\n\n✅ **配置已确认。**\n",
                "\n\n✅ **Configuration confirmed.**\n",
            )})
            write_task_directory(blueprint, DATA_DIR)

        yield _sse(_serialize_review_response(review_response, asdict(blueprint)))
        yield _sse({"type": "done"})
    except Exception as exc:
        tb = traceback.format_exc()
        msg = f"{type(exc).__name__}: {exc}\n{tb}"[:_MAX_ERROR_LEN]
        yield _sse({"type": "error", "error": msg})
    finally:
        if stream is not None and hasattr(stream, "aclose"):
            try:
                await stream.aclose()
            except Exception:
                pass


@app.post("/review")
async def review(req: ReviewRequest) -> StreamingResponse:
    """执行一轮 review 对话并以 SSE 流返回事件。"""
    return StreamingResponse(
        _review_event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- AI build (beta): AgentScope single-agent route ----
# Defined here (rather than imported from app.tasks.agent_build_runner) so the
# task-runner image — which only copies chat_tune_runner.py from app/tasks and
# relies on the core llm4ad.agent package — has the /agent route without needing
# the backend-only wrapper module. Also avoids a chat_tune_runner <-> wrapper
# import cycle.


async def _agent_event_stream(req: AgentBuildRequest) -> AsyncIterator[str]:
    """Run the core agent build for this request and encode events as SSE frames."""
    from llm4ad.agent.runner import AgentBuildConfig, run_agent_build

    config = AgentBuildConfig(
        provider_config=req.provider_config,
        base_dir=DATA_DIR,
        user_content=req.user_content,
        gathering_context=req.gathering_context,
        allow_build=req.allow_build,
        prior_state=req.agent_state,
        proposed=req.proposed,
        max_iters=req.max_iters,
    )
    async for event in run_agent_build(config):
        yield _sse(event)


@app.post("/agent")
async def agent_build(req: AgentBuildRequest) -> StreamingResponse:
    """执行 AI 构建 (beta) 的 AgentScope agent 并以 SSE 流返回事件。"""
    return StreamingResponse(
        _agent_event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    """启动容器内 SSE 服务。"""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
