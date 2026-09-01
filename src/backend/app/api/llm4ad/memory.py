"""Memory backend utility routes."""

import json
import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.api.llm4ad.sse_utils import sse_response
from app.models import Message
from app.schemas import memory as schemas
from app.services import memory_service

router = APIRouter(prefix="/memory", tags=["llm4ad.memory"])


@router.post(
    "/test",
    response_model=schemas.MemoryTestResponse,
    summary="测试记忆后端连通性",
)
async def test_memory_backend(
    request: schemas.MemoryTestRequest,
    _current_user: CurrentUser,
) -> schemas.MemoryTestResponse:
    return await memory_service.test_memory_connectivity(request)


@router.get(
    "/health",
    response_model=schemas.MemoryHealthResponse,
    summary="检测系统 MindMemOS 记忆服务状态",
)
def get_memory_health(
    current_user: CurrentUser,
) -> schemas.MemoryHealthResponse:
    return memory_service.get_mindmemos_health(current_user)


@router.get(
    "/provider-binding",
    response_model=schemas.MemoryProviderBindingResponse,
    summary="获取当前用户 MindMemOS 供应商绑定状态",
)
def get_memory_provider_binding(
    db: SessionDep,
    current_user: CurrentUser,
) -> schemas.MemoryProviderBindingResponse:
    return memory_service.get_memory_provider_binding(db, current_user)


@router.put(
    "/provider-binding",
    response_model=schemas.MemoryProviderBindingResponse,
    summary="绑定当前用户 MindMemOS Chat 和 Embedding 供应商",
)
def upsert_memory_provider_binding(
    db: SessionDep,
    current_user: CurrentUser,
    request: schemas.MemoryProviderBindingUpdate,
) -> schemas.MemoryProviderBindingResponse:
    return memory_service.upsert_memory_provider_binding(db, current_user, request)


@router.get(
    "/user-config",
    response_model=schemas.UserMemoryConfigResponse,
    summary="获取当前用户记忆默认配置",
)
def get_user_memory_config(
    db: SessionDep,
    current_user: CurrentUser,
) -> schemas.UserMemoryConfigResponse:
    return memory_service.get_user_memory_config(db, current_user)


@router.patch(
    "/user-config",
    response_model=schemas.UserMemoryConfigResponse,
    summary="更新当前用户记忆默认配置",
)
def update_user_memory_config(
    db: SessionDep,
    current_user: CurrentUser,
    request: schemas.MemoryConfigUpdate,
) -> schemas.UserMemoryConfigResponse:
    return memory_service.update_user_memory_config(db, current_user, request)


@router.get(
    "/projects/{project_id}/config",
    response_model=schemas.ProjectMemoryConfigResponse,
    summary="获取项目记忆默认配置",
)
def get_project_memory_config(
    db: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
) -> schemas.ProjectMemoryConfigResponse:
    return memory_service.get_project_memory_config(db, project_id, current_user)


@router.patch(
    "/projects/{project_id}/config",
    response_model=schemas.ProjectMemoryConfigResponse,
    summary="更新项目记忆默认配置",
)
def update_project_memory_config(
    db: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    request: schemas.MemoryConfigUpdate,
) -> schemas.ProjectMemoryConfigResponse:
    return memory_service.update_project_memory_config(db, project_id, current_user, request)


@router.get(
    "/cards",
    response_model=schemas.MemoryCardPageResponse,
    summary="按 scope 获取记忆卡片",
)
def list_memory_cards(
    db: SessionDep,
    current_user: CurrentUser,
    scope: schemas.MemoryScope,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> schemas.MemoryCardPageResponse:
    return memory_service.list_memory_cards_page(
        db,
        current_user,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/cards",
    response_model=schemas.MemoryCardResponse,
    status_code=201,
    summary="按 scope 新增记忆卡片",
)
def create_memory_card(
    db: SessionDep,
    current_user: CurrentUser,
    scope: schemas.MemoryScope,
    request: schemas.MemoryCardUpsertRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> schemas.MemoryCardResponse:
    return memory_service.upsert_memory_card(
        db,
        current_user=current_user,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        request=request,
    )


@router.post(
    "/cards/extractions",
    response_model=schemas.MemoryCardExtractionResponse,
    status_code=201,
    summary="从原始描述提取并保存记忆",
)
def extract_memory_cards(
    db: SessionDep,
    current_user: CurrentUser,
    scope: schemas.MemoryScope,
    request: schemas.MemoryCardExtractionRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> schemas.MemoryCardExtractionResponse:
    return memory_service.extract_memory_cards(
        db,
        current_user=current_user,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        request=request,
    )


@router.post(
    "/cards/extractions/stream",
    summary="从原始描述流式提取并保存记忆",
)
def stream_extract_memory_cards(
    db: SessionDep,
    current_user: CurrentUser,
    scope: schemas.MemoryScope,
    request: schemas.MemoryCardExtractionRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
):
    async def stream():
        async for item in memory_service.stream_extract_memory_cards(
            db,
            current_user=current_user,
            scope=scope,
            project_id=project_id,
            task_id=task_id,
            request=request,
        ):
            event_name = str(item.get("event") or "progress")
            data = {key: value for key, value in item.items() if key != "event"}
            yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return sse_response(stream())


@router.post(
    "/cards/promotions/stream",
    summary="将任务记忆流式提升为项目记忆",
)
def stream_promote_task_memory_cards(
    db: SessionDep,
    current_user: CurrentUser,
    request: schemas.TaskMemoryPromotionRequest,
):
    async def stream():
        async for item in memory_service.stream_promote_task_memory_cards(
            db,
            current_user=current_user,
            request=request,
        ):
            event_name = str(item.get("event") or "progress")
            data = {key: value for key, value in item.items() if key != "event"}
            yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return sse_response(stream())


@router.patch(
    "/cards/{memory_id}",
    response_model=schemas.MemoryCardResponse,
    summary="按 scope 更新记忆卡片",
)
def update_memory_card(
    db: SessionDep,
    current_user: CurrentUser,
    memory_id: str,
    scope: schemas.MemoryScope,
    request: schemas.MemoryCardUpsertRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> schemas.MemoryCardResponse:
    return memory_service.upsert_memory_card(
        db,
        current_user=current_user,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        request=request.model_copy(update={"id": memory_id}),
    )


@router.patch(
    "/cards/{memory_id}/status",
    response_model=schemas.MemoryCardResponse,
    summary="按 scope 启用或禁用记忆卡片",
)
def update_memory_card_status(
    db: SessionDep,
    current_user: CurrentUser,
    memory_id: str,
    scope: schemas.MemoryScope,
    request: schemas.MemoryCardStatusUpdate,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> schemas.MemoryCardResponse:
    return memory_service.update_memory_card_status(
        db,
        current_user=current_user,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        memory_id=memory_id,
        enabled=request.enabled,
    )


@router.delete(
    "/cards/{memory_id}",
    response_model=Message,
    summary="按 scope 删除记忆卡片",
)
def delete_memory_card(
    db: SessionDep,
    current_user: CurrentUser,
    memory_id: str,
    scope: schemas.MemoryScope,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> Message:
    memory_service.delete_memory_card(
        db,
        current_user=current_user,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        memory_id=memory_id,
    )
    return Message(message="记忆卡片已删除")
