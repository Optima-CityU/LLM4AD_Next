"""任务管理路由。

提供任务的创建、查询、更新、运行、结果获取、数据上传等端点，
以及日志获取、SSE 实时日志流和 code-server 认证端点。
所有端点（除 code_auth 外）需要用户登录。
"""

import json
import uuid

from fastapi import APIRouter, File, Query, Request, UploadFile
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.api.llm4ad.sse_utils import redis_sse_stream, sse_response
from app.core.redis import task_logs_key
from app.models import Message
from app.schemas import memory as memory_schemas
from app.schemas import task as schemas
from app.schemas.result_render import (
    ResultRenderGenerateRequest,
    ResultRenderGenerateResponse,
)
from app.services import memory_service, task_service

# tags 加前缀防止前端 OpenAPI 重名冲突
router = APIRouter(prefix="/tasks", tags=["llm4ad.tasks"])


# ---- 模板 ----


@router.get("/templates", response_model=schemas.ExampleTemplateListResponse, summary="获取可用的示例模板列表")
def list_example_templates():
    """遍历 examples 目录，返回包含 config.yaml 结尾文件的一级文件夹及其配置文件列表。"""
    return schemas.ExampleTemplateListResponse(templates=task_service.list_example_templates())


# ---- 任务列表 ----


@router.get("/list_tasks", response_model=schemas.PaginatedTaskResponse, summary="获取项目下的任务列表")
def list_tasks(
    db: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID = Query(..., description="项目ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=0, le=200),
):
    """分页查询指定项目下的所有任务。"""
    tasks, total, count_map, active_status_map = task_service.list_tasks(
        db, project_id, current_user, skip, limit
    )
    items = []
    for t in tasks:
        resp = schemas.TaskResponse.model_validate(t)
        resp.children_count = count_map.get(t.id, 0)
        resp.active_status = active_status_map.get(t.id, t.status)
        items.append(resp)
    return schemas.PaginatedTaskResponse(items=items, total=total, skip=skip, limit=limit)


# ---- 任务 CRUD ----


@router.get("/{task_id}", response_model=schemas.TaskResponse, summary="获取任务详情")
def get_task(db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID):
    """获取单个任务的详细信息，包含存储用量。"""
    task = task_service.get_task_with_auth(db, task_id, current_user)
    resp = schemas.TaskResponse.model_validate(task)
    resp.storage_usage = task_service.get_task_storage_usage(task)
    resp.active_status = task_service.get_active_status(db, task)
    return resp


@router.post("/", response_model=schemas.TaskResponse, status_code=201, summary="创建任务（自动生成默认参数）")
def create_task(task_in: schemas.TaskCreate, db: SessionDep, current_user: CurrentUser):
    """创建新任务。若未提供 input_args，将自动填充 AppConfig 默认值。"""
    return task_service.create_task(db, task_in, current_user)


@router.patch("/{task_id}", response_model=schemas.TaskResponse, summary="修改任务参数")
def update_task(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    task_update: schemas.TaskUpdate,
):
    """修改任务的名称、描述或运行参数。"""
    return task_service.update_task(db, task_id, current_user, task_update)


@router.patch("/{task_id}/tag", response_model=schemas.TaskResponse, summary="修改任务标签")
def update_task_tag(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    tag_update: schemas.TaskTagUpdate,
):
    """修改任务的标签。"""
    return task_service.update_task_tag(db, task_id, current_user, tag_update)


@router.put(
    "/{task_id}/active-child",
    response_model=schemas.TaskResponse,
    summary="设置根任务当前选中的子任务版本",
)
def set_active_child(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    body: schemas.SetActiveChildRequest,
):
    """设置根任务的活跃子版本。传 child_id=null 可清除选中（读取时默认回退为指向自身）。"""
    return task_service.set_active_child(db, task_id, current_user, body.child_id)


@router.get("/{task_id}/tree", response_model=schemas.TaskTreeResponse, summary="获取任务树")
def get_task_tree(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """根据根任务 ID 获取任务树，包含根任务及其所有子任务的常用信息。"""
    return task_service.get_task_tree(db, task_id, current_user)


@router.post("/{task_id}/copy", response_model=schemas.TaskResponse, status_code=201, summary="复制任务")
def copy_task(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    body: schemas.TaskCopyRequest = schemas.TaskCopyRequest(),
):
    """复制指定任务，名称加后缀，数据存在则复制一份，状态重置为未初始化。"""
    return task_service.copy_task(db, task_id, current_user, is_child=body.is_child)


@router.delete("/{task_id}", response_model=Message, summary="删除任务")
def delete_task(db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID):
    """删除任务及其关联的存储数据。任务运行中（pending/running）时不允许操作，需先停止任务。"""
    return task_service.delete_task(db, task_id, current_user)


# ---- 任务记忆管理 ----


@router.get("/{task_id}/memory", response_model=memory_schemas.MemoryCardPageResponse, summary="获取任务记忆卡片")
def list_task_memory(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
):
    """获取 MindMemOS 中的任务级记忆。"""
    return memory_service.list_memory_cards_page(
        db,
        current_user,
        scope="task",
        task_id=task_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{task_id}/memory/observability",
    response_model=schemas.TaskMemoryObservabilityResponse,
    summary="获取任务记忆使用统计",
)
def get_task_memory_observability(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """聚合任务日志中的 MindMemOS 注入事件，返回任务级记忆使用统计。"""
    return task_service.get_task_memory_observability(db, task_id, current_user)


@router.get(
    "/{task_id}/memory/pinned",
    response_model=memory_schemas.PinnedMemoryResponse,
    summary="获取任务固定注入的共享记忆",
)
def get_task_pinned_memory(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """读取手动模式下任务固定注入的全局/项目记忆 id 列表。"""
    pinned = memory_service.filter_active_task_pinned_memory_ids(
        db,
        current_user=current_user,
        task_id=task_id,
        pinned_card_ids=task_service.get_task_pinned_memory(db, task_id, current_user),
    )
    return memory_schemas.PinnedMemoryResponse(task_id=task_id, pinned_card_ids=pinned)


@router.put(
    "/{task_id}/memory/pinned",
    response_model=memory_schemas.PinnedMemoryResponse,
    summary="更新任务固定注入的共享记忆",
)
def set_task_pinned_memory(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    request: memory_schemas.PinnedMemoryUpdate,
):
    """替换任务固定注入的记忆 id 集合；运行中的任务下一轮注入即生效。"""
    active_pinned_ids = memory_service.filter_active_task_pinned_memory_ids(
        db,
        current_user=current_user,
        task_id=task_id,
        pinned_card_ids=request.pinned_card_ids,
    )
    pinned = task_service.set_task_pinned_memory(
        db, task_id, current_user, active_pinned_ids
    )
    return memory_schemas.PinnedMemoryResponse(task_id=task_id, pinned_card_ids=pinned)


@router.post(
    "/{task_id}/memory",
    response_model=memory_schemas.MemoryCardResponse,
    status_code=201,
    summary="新增或更新任务记忆卡片",
)
def upsert_task_memory(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    card: memory_schemas.MemoryCardUpsertRequest,
):
    """新增或更新 MindMemOS 中的任务级记忆。"""
    return memory_service.upsert_task_memory_card(db, task_id, current_user, card)


@router.patch(
    "/{task_id}/memory/{memory_id}",
    response_model=memory_schemas.MemoryCardResponse,
    summary="更新任务记忆卡片",
)
def update_task_memory(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    memory_id: str,
    card: memory_schemas.MemoryCardUpsertRequest,
):
    """按 ID 更新 MindMemOS 中的任务级记忆。"""
    return memory_service.upsert_task_memory_card(
        db,
        task_id,
        current_user,
        card.model_copy(update={"id": memory_id}),
    )


@router.delete("/{task_id}/memory/{memory_id}", response_model=Message, summary="删除任务记忆卡片")
def delete_task_memory(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    memory_id: str,
):
    """删除 MindMemOS 中的任务级记忆。"""
    memory_service.delete_task_memory_card(db, task_id, current_user, memory_id)
    return Message(message="记忆卡片已删除")


# ---- 运行 / 停止 ----


@router.post("/{task_id}/run", response_model=schemas.TaskRunResponse, summary="运行任务（提交到Celery）")
def run_task(db: SessionDep, current_user: CurrentUser, token: TokenDep, task_id: uuid.UUID):
    """运行指定任务，提交到 Celery 异步执行。"""
    return task_service.run_task(db, task_id, current_user, token)


@router.post("/{task_id}/stop", response_model=schemas.TaskResponse, summary="停止任务")
def stop_task(db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID):
    """停止正在运行或等待中的任务，撤销 Celery 任务并将状态置为失败。"""
    return task_service.stop_task(db, task_id, current_user)


# ---- 结果 / 统计 ----


@router.get("/{task_id}/result", response_model=schemas.TaskResultResponse, summary="获取任务在Celery中的执行结果")
def get_task_result(db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID):
    """查询任务的 Celery 执行结果，并将状态同步回数据库。"""
    return task_service.get_task_result(db, task_id, current_user)


@router.get("/{task_id}/stats", response_model=schemas.TaskStatsResponse, summary="获取任务基本统计信息")
def get_task_stats(db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID):
    """获取任务的基本统计信息：解的个数、解的平均分、解的最高分。"""
    return task_service.get_task_stats(db, task_id, current_user)


@router.post(
    "/{task_id}/result-render/generate",
    response_model=ResultRenderGenerateResponse,
    summary="生成任务结果渲染数据",
)
def generate_result_render(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    request: ResultRenderGenerateRequest,
):
    """生成指定任务的结果渲染数据。

    若 result_render 中已存在该类型的已完成结果则直接返回缓存，
    否则调用生成逻辑生成新数据。
    """
    return task_service.generate_result_render(db, task_id, current_user, request)


@router.get("/{task_id}/config_schema", response_model=schemas.AppConfigSchemaResponse, summary="获取参数配置的schema")
def get_config_schema(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """获取参数配置的schema。"""
    return task_service.get_config_schema(db, task_id, current_user)


@router.get("/{task_id}/workspace/download", summary="下载任务 IDE 工作区")
def download_task_workspace(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """Download the authorized task IDE workspace as a ZIP archive."""
    archive_path, filename = task_service.download_task_workspace(db, task_id, current_user)
    return FileResponse(
        archive_path,
        media_type="application/zip",
        headers={
            "Content-Disposition": task_service.workspace_attachment_disposition(filename),
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


# ---- 数据文件管理 ----


@router.post("/{task_id}/upload-data", response_model=Message, summary="批量上传任务输入数据文件")
def upload_task_data(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    files: list[UploadFile] = File(..., description="上传的文件列表"),
):
    """批量上传任务的输入数据文件到 S3 存储。"""
    return task_service.upload_task_data(db, task_id, current_user, files)


@router.get(
    "/{task_id}/data-tree",
    response_model=schemas.FileTreeResponse,
    summary="获取任务输入数据的目录树",
)
def get_task_data_tree(db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID):
    """获取任务 input_data_path 对应存储中的文件目录树。"""
    return task_service.get_task_data_tree(db, task_id, current_user)


@router.post(
    "/{task_id}/data-file",
    response_model=schemas.FileCreateResponse,
    status_code=201,
    summary="在任务输入数据中创建新文件",
)
def create_task_data_file(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    file_create: schemas.FileCreateRequest,
):
    """在任务输入数据目录中创建一个带 hello-world 示例的 Python 文件。"""
    return task_service.create_task_data_file(db, task_id, current_user, file_create)


@router.get(
    "/{task_id}/data-file",
    response_model=schemas.FileContentResponse,
    summary="获取任务输入数据中的文件内容",
)
def get_task_data_file(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    file_path: str = Query(..., description="文件相对路径"),
):
    """获取任务输入数据目录中指定文件的文本内容。"""
    return task_service.get_task_data_file(db, task_id, current_user, file_path)


@router.put("/{task_id}/data-file", response_model=Message, summary="修改任务输入数据中的文件内容")
def update_task_data_file(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    file_update: schemas.FileUpdateRequest,
):
    """修改任务输入数据目录中指定文件的内容。内容以 UTF-8 写入；是否允许编辑由前端控制。"""
    return task_service.update_task_data_file(db, task_id, current_user, file_update)


@router.delete("/{task_id}/data-file", response_model=Message, summary="删除任务输入数据中的文件")
def delete_task_data_file(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    file_path: str = Query(..., description="文件相对路径"),
):
    """删除任务输入数据目录中的指定文件。"""
    return task_service.delete_task_data_file(db, task_id, current_user, file_path)


@router.patch(
    "/{task_id}/data-file/rename",
    response_model=Message,
    summary="重命名任务输入数据中的文件",
)
def rename_task_data_file(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    file_rename: schemas.FileRenameRequest,
):
    """重命名任务输入数据目录中的指定文件。"""
    return task_service.rename_task_data_file(db, task_id, current_user, file_rename)


@router.post(
    "/{task_id}/data-folder",
    response_model=schemas.FolderCreateResponse,
    summary="在任务输入数据中创建子文件夹（占位文件方式）",
)
def create_task_data_folder(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    folder_create: schemas.FolderCreateRequest,
):
    """在已有目录下新建空文件夹，通过写入占位文件标记目录存在。不支持创建顶级文件夹。"""
    return task_service.create_task_data_folder(db, task_id, current_user, folder_create)


@router.delete(
    "/{task_id}/data-folder",
    response_model=Message,
    summary="删除任务输入数据中的文件夹（递归）",
)
def delete_task_data_folder(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    folder_path: str = Query(..., description="待删除文件夹的相对路径"),
):
    """递归删除指定文件夹及其所有内容。"""
    return task_service.delete_task_data_folder(db, task_id, current_user, folder_path)


@router.patch(
    "/{task_id}/data-folder/rename",
    response_model=Message,
    summary="重命名任务输入数据中的文件夹",
)
def rename_task_data_folder(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    folder_rename: schemas.FolderRenameRequest,
):
    """重命名任务输入数据目录中的指定文件夹（递归 copy + delete）。"""
    return task_service.rename_task_data_folder(db, task_id, current_user, folder_rename)


# ---- 日志 ----


@router.get(
    "/{task_id}/logs",
    response_model=schemas.TaskLogsResponse,
    summary="获取任务日志（游标分页，倒序查询）",
)
def get_task_logs(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    cursor: str | None = Query(None, description="分页游标，首次请求不传，后续传上一次返回的 next_cursor"),
    limit: int = Query(100, ge=0, le=10000, description="每页条数，0 表示不分页返回全部"),
    log_type: str | None = Query(None, description="按日志类型过滤，多个用英文逗号分隔"),
    level: list[str] | None = Query(None, description="按日志级别过滤，支持多选"),
    q: str | None = Query(None, description="全文搜索日志内容"),
):
    """获取任务日志，游标分页倒序查询。首次加载最新一页，后续向前翻页。"""
    log_types = [t.strip() for t in log_type.split(",") if t.strip()] if log_type else None
    return task_service.get_task_logs(
        db, task_id, current_user,
        cursor=cursor, limit=limit,
        log_type=log_types, level=level, message_query=q,
    )


def _task_log_entry_handler(fields: dict) -> tuple[str, bool] | None:
    """解析任务日志流条目为 SSE 帧。

    对脏数据宽松忽略，避免单个坏帧破坏整条 SSE 流。
    """
    try:
        entry = json.loads(fields["data"])
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(entry, dict):
        return None
    sse_text = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
    is_terminal = entry.get("type") == "end"
    return sse_text, is_terminal


@router.get("/{task_id}/logs/stream", summary="SSE 实时日志流")
async def stream_task_logs(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    last_id: str = Query(default="0-0", pattern=r"^\d+-\d+$"),
):
    """SSE 端点，实时推送任务日志和状态更新。

    事件类型:
    - connected: 连接建立
    - data: 队列条目（日志/状态/中间结果等）
    - heartbeat: 空闲时每 ~15s 发送一次心跳保活
    - done: 任务结束（completed 或 failed）
    - timeout: 30 分钟无数据安全兜底
    """
    task = task_service.get_task_with_auth(db, task_id, current_user)

    has_persisted_logs = task_service.has_persisted_logs(db, task_id)

    if has_persisted_logs:
        async def _done_stream():
            yield f"event: done\ndata: {json.dumps({'status': 'already_persisted'})}\n\n"
        return sse_response(_done_stream())

    if task.status in (
        task_service.TaskStatus.COMPLETED,
        task_service.TaskStatus.FAILED,
    ):
        status_value = task.status.value
        async def _finished_stream():
            yield f"event: done\ndata: {json.dumps({'status': status_value})}\n\n"
        return sse_response(_finished_stream())

    return sse_response(
        redis_sse_stream(
            redis_key=task_logs_key(task_id),
            connected_data={"task_id": str(task_id)},
            entry_handler=_task_log_entry_handler,
            last_id=last_id,
            max_idle=1800.0,
            use_draining=True,
        )
    )


# ---- Code-Server 认证（不使用标准 OAuth2，通过 cookie token 验证） ----


@router.get("/code_auth/", summary="code认证")
async def code_auth(request: Request, db: SessionDep):
    """验证 code-server 请求的 cookie token，返回用户信息用于 iframe 代理。"""
    return task_service.verify_code_auth(request, db)
