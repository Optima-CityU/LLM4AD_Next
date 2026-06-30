"""Memory backend utility routes."""

from fastapi import APIRouter

from app.api.deps import CurrentUser
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
