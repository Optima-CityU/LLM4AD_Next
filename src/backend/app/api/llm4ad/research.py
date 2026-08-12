"""自动科研（Research）路由：分组、会话、轮次、SSE 流、产物。

所有端点走 ``CurrentUser`` 依赖校验；跨用户访问一律 404 而非 403，避免枚举。
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Header, Query, status
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.api.llm4ad.sse_utils import redis_sse_stream, sse_response
from app.core.redis import report_stream_key, research_stream_key
from app.models.research import ResearchMessageRole, ResearchSessionStatus, ResearchTurnStatus
from app.schemas.research import (
    ResearchAnalysisDetailResponse,
    ResearchAnalysisGenerateRequest,
    ResearchAnalysisGenerateResponse,
    ResearchAnalysisStopResponse,
    ResearchArtifactListResponse,
    ResearchArtifactTranslateRequest,
    ResearchArtifactTranslateResponse,
    ResearchArtifactTranslateStopResponse,
    ResearchArtifactTreeResponse,
    ResearchArtifactWriteRequest,
    ResearchArtifactWriteResponse,
    ResearchCollabStartRequest,
    ResearchCollabStartResponse,
    ResearchDeleteResponse,
    ResearchFolderCreateRequest,
    ResearchFolderItem,
    ResearchFolderListResponse,
    ResearchFolderReorderRequest,
    ResearchFolderTreeResponse,
    ResearchFolderUpdateRequest,
    ResearchGeneratedResponse,
    ResearchLogPageResponse,
    ResearchMessageListResponse,
    ResearchSessionCreateRequest,
    ResearchSessionDetailResponse,
    ResearchSessionItem,
    ResearchSessionListResponse,
    ResearchSessionUpdateRequest,
    ResearchStageGuideRequest,
    ResearchStageGuideResponse,
    ResearchStateResponse,
    ResearchTurnItem,
    ResearchTurnListResponse,
    ResearchTurnRetryRequest,
    ResearchTurnStartRequest,
    ResearchTurnStartResponse,
    ResearchTurnStopResponse,
)
from app.services import research_service

router = APIRouter(prefix="/research", tags=["llm4ad.research"])


# ---- 分组文件夹 ----


@router.get(
    "/folders",
    response_model=ResearchFolderListResponse,
    summary="列出当前用户所有科研分组文件夹",
)
def list_folders(db: SessionDep, current_user: CurrentUser):
    """扁平返回所有文件夹（含每个文件夹直接归属的会话数）+ 未分组会话计数。"""
    return research_service.list_folders(db, current_user)


@router.get(
    "/folders/tree",
    response_model=ResearchFolderTreeResponse,
    summary="嵌套树形返回所有文件夹（含每节点 session_count）",
)
def get_folder_tree(db: SessionDep, current_user: CurrentUser):
    return research_service.get_folder_tree(db, current_user)


@router.post(
    "/folders/reorder",
    response_model=list[ResearchFolderItem],
    summary="批量重排文件夹（一次事务改多个 sort_order）",
)
def reorder_folders(
    request: ResearchFolderReorderRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """全部文件夹必须归属该用户；任一不合法都会 404 整体回滚。"""
    return research_service.reorder_folders(db, request, current_user)


@router.post(
    "/folders",
    response_model=ResearchFolderItem,
    status_code=status.HTTP_201_CREATED,
    summary="新建科研分组文件夹",
)
def create_folder(
    request: ResearchFolderCreateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """创建文件夹，重名（同 parent 下）返回 409。"""
    return research_service.create_folder(db, request, current_user)


@router.patch(
    "/folders/{folder_id}",
    response_model=ResearchFolderItem,
    summary="改文件夹（改名 / 移动 / 排序）",
)
def update_folder(
    folder_id: uuid.UUID,
    request: ResearchFolderUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """PATCH 语义：字段未提供 = 不改；``parent_id`` 显式传 null 表示移到根。"""
    return research_service.update_folder(
        db, folder_id, request, current_user
    )


@router.delete(
    "/folders/{folder_id}",
    response_model=ResearchDeleteResponse,
    summary="删除文件夹（子文件夹 / 会话不删，脱离归属）",
)
def delete_folder(
    folder_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """删除文件夹本体；子内容通过 ``ON DELETE SET NULL`` 保留。"""
    research_service.delete_folder(db, folder_id, current_user)
    return ResearchDeleteResponse(id=folder_id)


# ---- 会话 ----


@router.get(
    "/sessions",
    response_model=ResearchSessionListResponse,
    summary="列出会话（可按分组或未分组过滤）",
)
def list_sessions(
    db: SessionDep,
    current_user: CurrentUser,
    folder_id: uuid.UUID | None = Query(
        default=None, description="按分组过滤；不传且不指定 ungrouped 则返回全部"
    ),
    ungrouped: bool = Query(
        default=False, description="仅返回未归属分组的会话，忽略 folder_id"
    ),
    statuses: list[ResearchSessionStatus] | None = Query(
        default=None,
        description="按 session 状态过滤，可传多个；不传 = 全部状态",
    ),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="关键词搜索：对 topic + title 做大小写不敏感模糊匹配（ILIKE）",
    ),
    cursor: str | None = Query(
        default=None,
        description="上一页最后一条的 updated_time ISO；首次不传",
    ),
    limit: int = Query(default=50, ge=1, le=200),
):
    """会话游标分页列表，按 updated_time 倒序。"""
    return research_service.list_sessions(
        db,
        current_user,
        folder_id=folder_id,
        ungrouped_only=ungrouped,
        statuses=statuses,
        q=q,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/sessions",
    response_model=ResearchSessionItem,
    status_code=status.HTTP_201_CREATED,
    summary="创建会话（不立即启动首轮）",
)
def create_session(
    request: ResearchSessionCreateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """新建后需要再调 ``POST /sessions/{id}/turns`` 触发首轮。"""
    return research_service.create_session(db, request, current_user)


@router.get(
    "/sessions/{session_id}",
    response_model=ResearchSessionDetailResponse,
    summary="会话详情 + 分页历史消息 + 最近一轮",
)
def get_session(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    include_messages: bool = Query(
        default=False,
        description="是否附带一页历史消息；推荐前端走独立的 /messages 端点做分页",
    ),
    before: uuid.UUID | None = Query(
        default=None, description="消息游标：返回该消息之前（更早）的消息",
    ),
    limit: int = Query(default=50, ge=1, le=100),
):
    """会话详情 + active_turn 元数据。默认不返回 messages。"""
    return research_service.get_session_detail(
        db, session_id, current_user,
        include_messages=include_messages, before=before, limit=limit,
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=ResearchMessageListResponse,
    summary="消息分页（不含 log；turn_id 可选切换会话级/单轮，支持双向游标）",
)
def list_messages(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    turn_id: uuid.UUID | None = Query(
        default=None, description="传则只返回该轮消息；不传则跨全会话",
    ),
    order: str = Query(
        default="desc",
        pattern="^(asc|desc)$",
        description="翻页方向（单页恒升序返回）：desc 从最新往旧翻（默认）/ asc 从最旧往新翻",
    ),
    cursor: str | None = Query(
        default=None, description="上一页末条的不透明游标（next_cursor 原样回传）",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    event_type: list[str] | None = Query(
        default=None,
        description="白名单：只保留这些类型（如 stage_transition）；空则不限。log 走 /logs",
    ),
    role: ResearchMessageRole | None = Query(
        default=None, description="按 role 过滤：user/assistant/system",
    ),
):
    """只查 ``research_message`` 表（对话 + stage/artifact/guidance 等系统事件，
    **不含 log**——日志走 ``GET /logs``）。

    ``turn_id`` 统一会话级与单轮：传了 = 单轮（附带 turn 归属校验），不传 = 跨全
    会话。``order`` 统一历史翻页（``desc``）与 SSE 回放（``asc``）——刷新恢复流程：
    先 ``?turn_id=<tid>&order=asc`` 拿该轮完整历史，再连 ``/stream`` 实时 tail。
    翻页用返回的 ``next_cursor`` 原样回传，为 None 表示到底。
    """
    return research_service.list_messages(
        db, session_id, current_user,
        turn_id=turn_id, order=order, cursor=cursor, limit=limit,
        event_type=event_type, role=role,
    )


@router.get(
    "/sessions/{session_id}/logs",
    response_model=ResearchLogPageResponse,
    summary="日志双端游标窗口（独立 research_log 表；turn_id 可选，可上下双向翻页）",
)
def list_logs(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    turn_id: uuid.UUID | None = Query(
        default=None, description="传则只返回该轮日志；不传则跨全会话",
    ),
    order: str = Query(
        default="desc",
        pattern="^(asc|desc)$",
        description="翻页方向（单页恒升序返回）：desc 从最新往旧翻（默认）/ asc 从最旧往新翻",
    ),
    cursor: str | None = Query(
        default=None, description="上一页末条的不透明游标（next_cursor 原样回传）",
    ),
    limit: int = Query(
        default=200, ge=0, le=2000,
        description="单页条数；传 0 表示不分页、一次返回全部匹配（无游标）",
    ),
    level: list[str] | None = Query(
        default=None, description="按日志级别过滤白名单（INFO/WARNING/ERROR/DEBUG）；空则不限",
    ),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="关键字：对日志 message 做大小写不敏感模糊匹配（ILIKE）；空则不限",
    ),
):
    """只查 ``research_log`` 表（占总量 90-95% 的 log 已从消息表拆出）。

    返回**双端游标窗口**：``items`` 恒升序（旧→新，渲染不反转），并给出窗口两端
    的游标与是否还有更多，供日志查看器上下双向翻页——
    ``older_cursor`` + ``order=desc`` 取更旧一页、``newer_cursor`` + ``order=asc``
    取更新一页。``has_older`` / ``has_newer`` 由独立 EXISTS 探测，恒定正确。

    返回 :class:`ResearchLogItem`，含 level/source/module/ts，不伪装成消息结构。
    ``q`` 关键字搜索日志正文；``limit=0`` 不分页取全部匹配行（两端游标为 None、
    has_* 均 False；导出 / 全量检索用，大会话可能上万行，慎用）。
    """
    return research_service.list_logs(
        db, session_id, current_user,
        turn_id=turn_id, order=order, cursor=cursor, limit=limit,
        level=level, q=q,
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=ResearchSessionItem,
    summary="改会话（改名 / 移分组 / 改默认 mode/provider/model / 改 profile）",
)
def update_session(
    session_id: uuid.UUID,
    request: ResearchSessionUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """``folder_id`` 显式传 null 表示移到未分组，未提供则不改。"""
    return research_service.update_session(
        db, session_id, request, current_user
    )


@router.delete(
    "/sessions/{session_id}",
    response_model=ResearchDeleteResponse,
    summary="删除会话（含 run_dir 落盘产物）",
)
def delete_session(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """会话必须处于终态；``RUNNING`` / ``PAUSED`` 会返回 409。"""
    research_service.delete_session(db, session_id, current_user)
    return ResearchDeleteResponse(id=session_id)


# ---- 轮次 ----


@router.post(
    "/sessions/{session_id}/turns",
    response_model=ResearchTurnStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="触发新一轮（首启 / 停止后继续 / 表单回填 均走这里）",
)
def start_turn(
    session_id: uuid.UUID,
    request: ResearchTurnStartRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """规则：

    - 会话必须不在 ``RUNNING`` 状态（否则 409）；
    - ``submission`` 与 ``respond_to_message_id`` 必须同时出现或同时缺省；
    - 3 秒内不允许同一会话再次触发（429）。
    """
    return research_service.start_turn(db, session_id, request, current_user)


@router.post(
    "/sessions/{session_id}/turns/{turn_id}/stop",
    response_model=ResearchTurnStopResponse,
    summary="停止指定轮次（pipeline 轮或协作轮）",
)
def stop_turn(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """**同步**停止：abort Celery + SIGKILL 容器 + 写 CANCELLED，返回即终态。

    同一接口停 pipeline 轮（``running``）与协作轮（``collaborating``）：前者连带 session
    一起 CANCELLED，后者只落 turn（协作是叠加层，不动 session）。
    """
    turn = research_service.stop_turn(db, session_id, turn_id, current_user)
    return ResearchTurnStopResponse(
        session_id=session_id,
        turn_id=turn.id,
        status=turn.status,
        message="stopped"
        if turn.status == ResearchTurnStatus.CANCELLED
        else "already stopped",
    )


@router.post(
    "/sessions/{session_id}/turns/{turn_id}/retry",
    response_model=ResearchTurnStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重试失败 / 停止的轮次（复用同一 turn_id）",
)
def retry_turn(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    request: ResearchTurnRetryRequest = ResearchTurnRetryRequest(),
):
    """只允许 ``FAILED`` / ``CANCELLED`` 状态重试，其它状态 409。"""
    return research_service.retry_turn(db, session_id, turn_id, request, current_user)


# ---- 协作（Collaborate Agent）----


@router.post(
    "/sessions/{session_id}/collab",
    response_model=ResearchCollabStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="向常驻协作 agent 发消息（答疑 / 改产物）",
)
def start_collab(
    session_id: uuid.UUID,
    request: ResearchCollabStartRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """规则：

    - 只要流水线没在跑（session 非 ``running``）就能发——pending / paused / 终态皆可；
    - 同一会话已有 ``collaborating`` turn 在跑时 409；
    - 协作是与门控按钮平行的独立通道：agent 只读写 ``stage-NN/`` 产物 + 答疑，
      **不推进流水线、不改 session 主状态**。
    """
    return research_service.start_collab_turn(db, session_id, request, current_user)


@router.get(
    "/sessions/{session_id}/turns",
    response_model=ResearchTurnListResponse,
    summary="列出会话下的所有轮次（倒序游标分页）",
)
def list_turns(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    cursor: str | None = Query(None, description="上一页最后一条的 created_time ISO"),
    limit: int = Query(50, ge=1, le=200, description="每页条数"),
):
    return research_service.list_session_turns(
        db, session_id, current_user, cursor=cursor, limit=limit,
    )


@router.get(
    "/sessions/{session_id}/turns/{turn_id}",
    response_model=ResearchTurnItem,
    summary="回读单轮元数据",
)
def get_turn(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """轻量端点：只返回 turn 表状态，不含消息。"""
    return research_service.get_turn(db, session_id, turn_id, current_user)


# ---- SSE 流 ----


# 已经写完 done/error 的 turn 不需要真正开流，重连直接短路发一条 done。
#
# 注意：PAUSED_GATE 不算终态。命中门控只是「本轮任务暂停等恢复」，其 Redis
# stream 仍保留完整历史（含 waiting_for_input）。且 retry 复用同一 turn_id，
# 若把 PAUSED_GATE 短路掉，会导致：刷新/重连时拿不到 connected、拿不到历史
# 回放、拿不到 gate 表单——前端表现为「stream 打通了却什么都没返回」。故让
# PAUSED_GATE 走真正的 redis_sse_stream，交由客户端按 last_id 回放。
_TERMINAL_TURN_STATUSES = frozenset({
    ResearchTurnStatus.COMPLETED.value,
    ResearchTurnStatus.FAILED.value,
    ResearchTurnStatus.CANCELLED.value,
})


def _research_entry_handler(
    _entry_id: str, fields: dict
) -> tuple[str, bool] | None:
    """把 Redis Stream 条目转为 SSE 帧。终止事件：``done`` / ``error``。

    id 行由 :func:`redis_sse_stream` 统一拼接，handler 只产出 event/data。
    """
    try:
        entry = json.loads(fields["data"])
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(entry, dict):
        return None
    sse_text = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
    is_terminal = entry.get("type") in ("done", "error")
    return sse_text, is_terminal


async def _short_circuit_done_stream(turn_status: str):
    """turn 已在终态时直接发一条 done 帧关流，不真订阅 Redis。"""
    yield f"event: done\ndata: {json.dumps({'status': turn_status})}\n\n"


@router.get(
    "/sessions/{session_id}/turns/{turn_id}/stream",
    summary="SSE 流：实时推送科研 pipeline 事件（支持 Last-Event-ID 断线续传）",
)
async def stream_turn(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    last_id: str | None = Query(
        None, description='上次收到的 Redis Stream ID；断线续传用。默认 "0-0" 从头重放',
    ),
    last_event_id: str | None = Header(
        None, alias="Last-Event-ID",
        description="标准 SSE 断线重连头；若 query 没传 last_id 则退回本 header",
    ),
):
    """事件类型（前端按 ``type`` 分派）：

    - ``stage_transition``：22 阶段推进；
    - ``waiting_for_input``：ARC gate 等表单回填；
    - ``artifact_ready``：产物就绪；
    - ``log``：日志（ARC + LLM4AD + bridge 三源合流）；
    - ``done`` / ``error``：终止信号。

    **断线续传**：客户端记住上一条帧的 Redis Stream ID，重连时通过
    ``?last_id=`` query 或 ``Last-Event-ID`` header 传回，服务端从该点后
    继续推。默认 ``0-0`` 从流头重放（Redis 保留窗口 2h）。

    **已终态短路**：如果 turn 已经 COMPLETED/FAILED/CANCELLED，直接返回一条
    ``done`` 帧关流，不订阅 Redis（避免客户端白等 30 分钟 idle timeout）。
    PAUSED_GATE 不在此列——门控暂停的 turn 仍需回放历史与 gate 表单。
    """
    turn = research_service.get_stream_context(
        db, session_id, turn_id, current_user
    )
    if turn.status in _TERMINAL_TURN_STATUSES:
        return sse_response(_short_circuit_done_stream(turn.status))

    resume_id = last_id or last_event_id or "0-0"
    return sse_response(
        redis_sse_stream(
            redis_key=research_stream_key(session_id, turn_id),
            connected_data={
                "session_id": str(session_id),
                "turn_id": str(turn_id),
                "resume_from": resume_id,
            },
            entry_handler=_research_entry_handler,
            last_id=resume_id,
            # 永不因静默关流：ARC 单阶段可能跑几十分钟，静默是常态。终止只由
            # done/error 事件或已终态短路驱动；心跳（15s）保活连接。
            max_idle=None,
            use_draining=True,
        )
    )


# ---- Stage 引导注入 ----


@router.post(
    "/sessions/{session_id}/stages/{stage_num}/guide",
    response_model=ResearchStageGuideResponse,
    summary="为某个 stage 注入引导文本（对应 ARC CLI `researchclaw guide`）",
)
def inject_stage_guidance(
    session_id: uuid.UUID,
    stage_num: int,
    request: ResearchStageGuideRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """在指定 stage 目录下落一份 ``hitl_guidance.md``。

    ARC 下次跑到该 stage 时会自动读取并注入到 LLM prompt 里。
    可以**提前预注入**（stage 还没跑到），也可以**中途注入**。
    对同一 stage 再调一次会覆盖上一次的内容。
    """
    return research_service.inject_stage_guidance(
        db, session_id, stage_num, request, current_user
    )


# ---- 产物 ----


@router.get(
    "/sessions/{session_id}/artifacts",
    response_model=ResearchArtifactListResponse,
    summary="扫描 run_dir，返回所有产物文件",
)
def list_artifacts(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """扁平文件清单 + 按名字/后缀猜的类别；前端可按 ``kind`` 分组显示。"""
    return research_service.list_artifacts(db, session_id, current_user)


@router.get(
    "/sessions/{session_id}/artifacts/tree",
    response_model=ResearchArtifactTreeResponse,
    summary="产物目录树",
)
def artifact_tree(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    return research_service.get_artifact_tree(db, session_id, current_user)


@router.get(
    "/sessions/{session_id}/artifacts/download",
    summary="下载单个产物文件",
)
def download_artifact(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    path: str = Query(..., description="相对 run_dir 的路径"),
):
    """按需下载产物。防目录穿越；仅允许 ``run_dir`` 之下的文件。

    强制 ``Content-Disposition: attachment``：产物内容用户可编辑，若以 inline 方式
    在浏览器同源渲染（HTML/SVG），会形成存储型 XSS。附件下载彻底规避。
    """
    target = research_service.resolve_artifact_path(
        db, session_id, current_user, path
    )
    return FileResponse(
        target,
        filename=target.name,
        content_disposition_type="attachment",
    )


@router.post(
    "/sessions/{session_id}/artifacts/translate",
    response_model=ResearchArtifactTranslateResponse,
    summary="翻译单个产物文件（带磁盘缓存，支持强制重译）",
)
async def translate_artifact(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    request: ResearchArtifactTranslateRequest,
    path: str = Query(..., description="相对 run_dir 的路径，如 stage-05/outline.md"),
):
    """把产物文件译成目标语言：命中缓存回 ``cached``+全文，否则 ``translating``
    并后台启动翻译，前端连 ``/artifacts/translate/stream?source_hash=<返回值>``
    接收增量；``force=true`` 跳过缓存重译。

    Note:
        必须是 ``async`` 路由：服务层用 ``asyncio.create_task`` 提交后台协程，
        同步路由会被丢进线程池，导致「no current event loop」。
    """
    return research_service.translate_artifact(
        db, session_id, current_user, path, request, token
    )


@router.post(
    "/sessions/{session_id}/artifacts/translate/stop",
    response_model=ResearchArtifactTranslateStopResponse,
    summary="停止在跑的产物翻译（协作式取消后台任务）",
)
async def stop_translate_artifact(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    source_hash: str = Query(..., description="POST /translate 返回的源文件哈希"),
    target_language: str = Query("zh", description="目标语言：'zh' / 'en'"),
):
    """中断后台翻译协程：清 generation_id 让其协作式退出、不写缓存，并给
    仍连着的 SSE 推一条 ``cancelled`` 终止帧。无在跑任务时返回 ``idle``（幂等）。
    """
    return research_service.stop_translation(
        db, session_id, current_user, source_hash, target_language
    )


def _translate_entry_handler(_entry_id: str, fields: dict) -> tuple[str, bool] | None:
    """解析翻译流条目为 SSE 帧。终止事件：``done`` / ``error`` / ``cancelled``。

    id 行由 :func:`redis_sse_stream` 统一拼接，handler 只产出 event/data。
    """
    entry = json.loads(fields["data"])
    sse_text = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
    is_terminal = isinstance(entry, dict) and entry.get("type") in (
        "done",
        "error",
        "cancelled",
    )
    return sse_text, is_terminal


@router.get(
    "/sessions/{session_id}/artifacts/translate/stream",
    summary="SSE：实时推送产物翻译增量",
)
async def stream_translate(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    source_hash: str = Query(..., description="POST /translate 返回的源文件哈希"),
    target_language: str = Query("zh", description="目标语言：'zh' / 'en'"),
):
    """订阅产物翻译的 Redis Stream，推送增量译文。

    键控 ``translate:<hash>:<lang>``；断线可重连从流头重放（Redis 保留 1h）。
    事件：``chunk`` / ``done`` / ``error`` / ``cancelled``。
    """
    report_type = research_service.get_translate_stream_type(
        db, session_id, current_user, source_hash, target_language
    )
    return sse_response(
        redis_sse_stream(
            redis_key=report_stream_key(session_id, report_type),
            connected_data={
                "session_id": str(session_id),
                "source_hash": source_hash,
                "target_language": target_language,
            },
            entry_handler=_translate_entry_handler,
            max_idle=300.0,
            use_draining=True,
        )
    )


@router.get(
    "/sessions/{session_id}/artifacts/archive",
    summary="打包下载全部产物（zip）",
)
def download_artifacts_archive(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """把 run_dir 下全部产物打成 zip 一次性下载（跳过内部点文件）。

    临时 zip 落磁盘，经 ``BackgroundTask`` 在响应发送后删除，避免堆积。
    """
    zip_path, filename = research_service.create_artifacts_archive(
        db, session_id, current_user
    )
    return FileResponse(
        zip_path,
        filename=filename,
        media_type="application/zip",
        content_disposition_type="attachment",
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )


@router.put(
    "/sessions/{session_id}/artifacts/content",
    response_model=ResearchArtifactWriteResponse,
    summary="覆写单个产物文件内容（门控编辑）",
)
def write_artifact(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    request: ResearchArtifactWriteRequest,
    path: str = Query(..., description="相对 run_dir 的路径，如 stage-05/outline.md"),
):
    """门控编辑：把用户改后的全文覆写回产物文件。

    安全同下载：user 归属校验（跨用户 404）+ 防目录穿越 + 只允许改已存在文件；
    覆写前原文备份到 ``hitl/snapshots/``。编辑完前端提交门控 ``approve`` 即用改后
    内容从下一 stage 续跑。
    """
    target = research_service.write_artifact(
        db, session_id, current_user, path, request.content
    )
    return ResearchArtifactWriteResponse(
        session_id=session_id,
        path=path,
        size=target.stat().st_size,
    )


@router.get(
    "/sessions/{session_id}/generated",
    response_model=ResearchGeneratedResponse,
    summary="获取所有 generated 解（内容内联，剥离大字段，按 stage 分组）",
)
def list_generated(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    stage: int | None = Query(
        default=None, description="仅返回该 stage 的解；不传返回全部"
    ),
):
    """一次拿全 ``**/generated/*.json`` 解内容，免去前端逐个 download。

    大字段（``code_artifacts`` / ``generation_meta`` / ``worktree`` /
    ``description``）按演化任务持久化口径剥离，按 stage 分组返回。
    """
    return research_service.list_generated_solutions(
        db, session_id, current_user, stage=stage
    )


# ---- 状态快照 ----


@router.get(
    "/sessions/{session_id}/state",
    response_model=ResearchStateResponse,
    summary="会话当前状态结构化快照（不含消息）",
)
def get_state(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    return research_service.get_state(db, session_id, current_user)


@router.get(
    "/sessions/{session_id}/config.yaml",
    summary="下载 ARC 使用的 config.arc.yaml",
    response_class=FileResponse,
)
def download_arc_config(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """便于调试；文件已在 subprocess 启动时落到 run_dir 根。"""
    target = research_service.resolve_artifact_path(
        db, session_id, current_user, "config.arc.yaml"
    )
    return FileResponse(target, filename="config.arc.yaml", media_type="application/x-yaml")


# ---- 结果分析（Analysis）----


@router.get(
    "/sessions/{session_id}/analysis",
    response_model=ResearchAnalysisDetailResponse,
    summary="结构化聚合数据 + 最近一次 LLM 分析报告",
)
def get_analysis(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """一次性返回结果分析页所需的全部数据。

    ``data`` 为纯读盘聚合快照（零 LLM、恒有值）；``report`` 为最近一次 LLM
    叙述报告，未生成过时为 None。
    """
    return research_service.get_analysis(db, session_id, current_user)


@router.post(
    "/sessions/{session_id}/analysis/generate",
    response_model=ResearchAnalysisGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="触发结果分析报告的 LLM 后台生成",
)
async def generate_analysis(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    request: ResearchAnalysisGenerateRequest,
):
    """触发指定会话的分析报告后台生成，立即返回 202 表示已受理。

    入参 ``provider_id`` / ``model_name`` / ``language`` 由用户传入；若已有生成
    任务在跑，会协作式取消旧任务后启动新的。

    Note:
        必须是 ``async`` 路由：``generate_analysis_report`` 内部通过
        ``get_event_loop().create_task`` 提交后台协程，需要运行在事件循环
        线程中；同步路由会被丢进线程池，导致「no current event loop」。
    """
    return research_service.generate_analysis_report(
        db, session_id, current_user, request, token
    )


@router.post(
    "/sessions/{session_id}/analysis/stop",
    response_model=ResearchAnalysisStopResponse,
    summary="停止进行中的分析报告生成",
)
def stop_analysis(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """用户主动取消正在进行中的分析报告生成。"""
    return research_service.stop_analysis_report(db, session_id, current_user)


def _analysis_entry_handler(_entry_id: str, fields: dict) -> tuple[str, bool] | None:
    """解析分析报告流条目为 SSE 帧。

    id 行由 :func:`redis_sse_stream` 统一拼接，handler 只产出 event/data。
    """
    entry = json.loads(fields["data"])
    sse_text = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
    is_terminal = isinstance(entry, dict) and entry.get("type") in (
        "done",
        "error",
        "cancelled",
    )
    return sse_text, is_terminal


@router.get(
    "/sessions/{session_id}/analysis/stream",
    summary="SSE：实时推送分析报告生成进度",
)
async def stream_analysis(
    session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser
):
    """SSE 端点：订阅 Redis Stream 持续推送分析报告增量内容。

    复用 chat 报告的通用流基建，``report_type`` 段固定为 ``analysis``、按
    ``session_id`` 键控。
    """
    # 归属校验：跨用户 404。
    research_service.get_analysis(db, session_id, current_user)
    return sse_response(
        redis_sse_stream(
            redis_key=report_stream_key(session_id, "analysis"),
            connected_data={"session_id": str(session_id), "report_type": "analysis"},
            entry_handler=_analysis_entry_handler,
            max_idle=300.0,
            use_draining=True,
        )
    )


# 便于在 tests 里 assert
__all__ = ["router"]
