"""Chat 调参路由。

会话与任务严格 1:1 绑定，所有端点都通过 ``task_id`` 定位会话。
首次访问任意端点时若调参会话不存在会自动创建，无需独立的 session CRUD。
"""

import json
import uuid

from fastapi import APIRouter, Query, UploadFile, status
from fastapi.params import File

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.api.llm4ad.sse_utils import redis_sse_stream, sse_response
from app.core.redis import chat_tune_stream_key
from app.schemas.chat_tune import (
    ChatTuneRetryResponse,
    ChatTuneSessionDetailResponse,
    ChatTuneSessionItem,
    ChatTuneStopResponse,
    ChatTuneTurnRetryRequest,
    ChatTuneTurnStartRequest,
    ChatTuneTurnStartResponse,
)
from app.schemas.task import ChatTuneUploadDataResponse, ChatTuneUploadFileResponse
from app.services import chat_tune_service, task_service

router = APIRouter(prefix="/tasks", tags=["llm4ad.chat_tune"])


# ---- 会话查询 / 重置 ----


@router.get(
    "/{task_id}/chat-tune",
    response_model=ChatTuneSessionDetailResponse,
    summary="Get chat tune session for a task with paginated history",
)
async def get_session(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    before: uuid.UUID | None = Query(
        default=None,
        description="游标：返回该消息 ID 之前（更早）的消息；缺省时返回最新一页",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
        description="每页条数，默认 50，上限 100",
    ),
):
    """获取任务对应的调参会话详情，含游标分页历史消息。

    首次加载不传 ``before``，拿到最新一页；滚到顶部后用最早一条消息的
    ``id`` 作为 ``before`` 继续翻页，直到 ``has_more`` 为 False。
    """
    return chat_tune_service.get_session_detail(
        db, task_id, current_user, before=before, limit=limit
    )


@router.post(
    "/{task_id}/chat-tune/reset",
    response_model=ChatTuneSessionItem,
    summary="Reset chat tune history for a task",
)
async def reset_session(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """清空任务的调参对话历史，会话保留并重置为初始配置。"""
    return chat_tune_service.reset_session(db, task_id, current_user)


# ---- 调参轮次 ----


@router.post(
    "/{task_id}/chat-tune/turns",
    response_model=ChatTuneTurnStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a new chat tune turn for a task",
)
async def start_turn(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    task_id: uuid.UUID,
    request: ChatTuneTurnStartRequest,
):
    """触发新一轮调参生成。

    输入支持两种来源（可叠加）：
        - 普通文字消息：仅填 ``content``。
        - 表单回复：填 ``respond_to_message_id`` + ``submission``，后端会原子地
          完成"锁定旧消息 + 触发新一轮"。

    立即返回 user / assistant 两条消息（以及被锁定的旧消息，若有），
    前端拿 ``turn_id`` 去订阅 SSE 流。

    Note:
        路由必须为 ``async def``：内部以 ``asyncio.create_task`` 调度后台
        流式协程，需要在主事件循环上下文中执行。
    """
    return chat_tune_service.start_turn(db, task_id, current_user, request, token)


@router.post(
    "/{task_id}/chat-tune/turns/{turn_id}/stop",
    response_model=ChatTuneStopResponse,
    summary="Stop the current chat tune turn",
)
async def stop_turn(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    turn_id: uuid.UUID,
):
    """停止指定轮次的生成。幂等：对已结束的轮次返回当前状态。"""
    return chat_tune_service.stop_turn(db, task_id, turn_id, current_user)


@router.post(
    "/{task_id}/chat-tune/turns/{turn_id}/retry",
    response_model=ChatTuneRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed or stopped chat tune turn",
)
async def retry_turn(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    task_id: uuid.UUID,
    turn_id: uuid.UUID,
    request: ChatTuneTurnRetryRequest = ChatTuneTurnRetryRequest(),
):
    """对失败或已停止的轮次原地重跑。

    复用同一 ``turn_id``，不新建消息：直接把目标 assistant 消息状态重置回
    ``GENERATING`` 并基于原始用户输入重新触发生成。前端拿到响应后继续订阅
    同一个 ``/stream`` 端点即可。

    可选地通过请求体覆盖 ``provider_id`` / ``model_name``；都缺省时回退到
    用户默认报告模型，再回退到任务第一个 Provider，与历史行为一致。

    限制：
        - 仅 ``FAILED``（异常失败）和 ``CANCELLED``（用户手动停止）可重试，
          其它状态返回 409。
        - 走与 ``start_turn`` 相同的速率限制。
        - 不限重试次数。
    """
    return chat_tune_service.retry_turn(
        db, task_id, turn_id, current_user, token, request
    )


# ---- 调参对话中的文件上传 ----


@router.post(
    "/{task_id}/chat-tune/upload-file",
    response_model=ChatTuneUploadFileResponse,
    summary="调参对话中上传文件到任务数据目录",
)
async def chat_tune_upload_file(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    message_id: uuid.UUID = Query(..., description="调参消息 ID"),
    card_id: str = Query(..., description="payload 卡片 ID"),
    option_index: int = Query(..., description="选项在 payload.options 中的索引"),
    files: list[UploadFile] = File(..., description="上传的文件列表"),
):
    """上传文件到 ``input_data_path`` 下的第一个子目录。

    校验消息归属与 payload cardId 后执行上传。若对应选项中已有
    ``file_path``，则先删除旧文件。上传完成后将新路径写入
    ``payload.options[option_index]`` 并持久化。返回更新后的 payload。
    """
    return task_service.chat_tune_upload_file(
        db, task_id, current_user, message_id, card_id, option_index, files
    )


@router.post(
    "/{task_id}/chat-tune/upload-data",
    response_model=ChatTuneUploadDataResponse,
    summary="调参对话中上传目录到任务数据目录",
)
async def chat_tune_upload_data(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    message_id: uuid.UUID = Query(..., description="调参消息 ID"),
    card_id: str = Query(..., description="payload 卡片 ID"),
    option_index: int = Query(..., description="选项在 payload.options 中的索引"),
    files: list[UploadFile] = File(..., description="上传的文件列表（filename 可含 / 以表达子目录结构）"),
):
    """上传目录到 ``input_data_path`` 下的第一个子目录。

    上传逻辑与 ``upload-data`` 一致（多文件、filename 保留目录结构）。
    校验消息归属与 payload cardId 后执行上传。若对应选项中已有
    ``dir_path``，则先递归删除旧目录。上传完成后将新路径写入
    ``payload.options[option_index]`` 并持久化。返回更新后的 payload。
    """
    return task_service.chat_tune_upload_data(
        db, task_id, current_user, message_id, card_id, option_index, files
    )


def _chat_tune_entry_handler(
    _entry_id: str, fields: dict
) -> tuple[str, bool] | None:
    """解析调参 Stream 条目为 SSE 帧。

    将 done / error / cancelled 帧标记为终止事件。对脏数据宽松忽略，
    避免单个坏帧破坏整条 SSE 流。

    id 行由 :func:`redis_sse_stream` 统一拼接，handler 只产出 event/data。
    """
    try:
        entry = json.loads(fields["data"])
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(entry, dict):
        return None
    sse_text = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
    is_terminal = entry.get("type") in ("done", "error", "cancelled")
    return sse_text, is_terminal


def _build_replay_stream(ctx: chat_tune_service.TurnStreamContext):
    """根据已落库的轮次状态构造 SSE 回放流。

    格式与实时 Redis 流一致：connected → chunk/payload → terminal → done。
    """

    async def _generator():
        yield (
            f"event: connected\n"
            f"data: {json.dumps({'session_id': str(ctx.session_id), 'turn_id': str(ctx.turn_id)}, ensure_ascii=False)}\n\n"
        )
        if ctx.content:
            yield f"data: {json.dumps({'type': 'chunk', 'content': ctx.content}, ensure_ascii=False)}\n\n"
        if ctx.payload is not None:
            yield f"data: {json.dumps({'type': 'payload', 'data': ctx.payload}, ensure_ascii=False)}\n\n"
        terminal_payload: dict = {"type": ctx.terminal_type}
        if ctx.error:
            terminal_payload["error"] = ctx.error
        yield f"data: {json.dumps(terminal_payload, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {json.dumps({'status': 'finished'})}\n\n"

    return _generator()


@router.get(
    "/{task_id}/chat-tune/turns/{turn_id}/stream",
    summary="SSE stream for a chat tune turn",
)
async def stream_turn(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    turn_id: uuid.UUID,
):
    """SSE 端点：实时推送指定轮次的调参生成进度。

    多次刷新友好：
        - 仍在生成中：从 Stream 头部重放已产出的所有 chunk，
          然后续接增量数据，前端按 chunk 拼接即可还原完整内容。
        - 已结束：回放 chunk/payload 后推送终止事件并关闭流。
        - 轮次或会话不存在：返回 404。

    事件类型：
        - connected: 连接建立。
        - data: 形如 ``{"type": "chunk", "content": "..."}`` 或
          ``{"type": "payload", "data": {...}}`` 的增量帧。
        - heartbeat: 周期性心跳保活（约 15s）。
        - done / cancelled / error: 终止事件（data 帧）。
        - event: done: SSE 终止信号。
        - timeout: 5 分钟无新数据的安全兜底。
    """
    ctx = chat_tune_service.get_turn_stream_context(
        db, task_id, turn_id, current_user
    )

    if ctx.is_finished:
        return sse_response(_build_replay_stream(ctx))

    return sse_response(
        redis_sse_stream(
            redis_key=chat_tune_stream_key(ctx.session_id, ctx.turn_id),
            connected_data={
                "session_id": str(ctx.session_id),
                "turn_id": str(ctx.turn_id),
            },
            entry_handler=_chat_tune_entry_handler,
            max_idle=600.0,
            use_draining=True,
        )
    )
