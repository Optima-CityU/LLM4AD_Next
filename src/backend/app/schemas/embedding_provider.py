"""Embedding 供应商请求/响应 Schema。"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_serializer

from app.models import EmbeddingMode, EmbeddingProviderBase, EmbeddingProviderType
from app.schemas.provider import _MASKED_SECRET


class EmbeddingProviderCreate(EmbeddingProviderBase):
    """Embedding 供应商创建请求。"""

    name: str
    type: EmbeddingProviderType = EmbeddingProviderType.JINA


class EmbeddingProviderUpdate(BaseModel):
    """Embedding 供应商更新请求（所有字段均可选）。"""

    name: str | None = None
    type: EmbeddingProviderType | None = None
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None
    mode: EmbeddingMode | None = None
    model: str | None = None
    dim: int | None = None
    timeout: float | None = None
    embedding_func_max_async: int | None = None
    text_type: EmbeddingProviderType | None = None
    text_base_url: str | None = None
    text_api_key: str | None = None
    text_auth_token: str | None = None
    text_model: str | None = None
    text_task: str | None = None
    code_type: EmbeddingProviderType | None = None
    code_base_url: str | None = None
    code_api_key: str | None = None
    code_auth_token: str | None = None
    code_model: str | None = None
    code_task: str | None = None


class EmbeddingProviderTestRequest(EmbeddingProviderBase):
    """Embedding 供应商连通性测试请求。"""

    task_type: Literal["text", "code"]
    name: str = "embedding-test"
    sample: str | None = None


class EmbeddingProviderTestByIdRequest(EmbeddingProviderUpdate):
    """已存储 embedding 供应商连通性测试请求。"""

    task_type: Literal["text", "code"]
    sample: str | None = None


class EmbeddingProviderTestResponse(BaseModel):
    """Embedding 供应商连通性测试响应。"""

    success: bool
    message: str
    task_type: Literal["text", "code"]
    dimension: int | None = None
    sample: list[float] | None = None


class EmbeddingProviderResponse(BaseModel):
    """Embedding 供应商响应，凭据不返回明文。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_time: datetime
    updated_time: datetime
    user_id: uuid.UUID
    name: str
    type: EmbeddingProviderType
    api_key: str
    auth_token: str
    base_url: str | None
    mode: EmbeddingMode
    model: str
    dim: int
    timeout: float
    embedding_func_max_async: int
    text_type: EmbeddingProviderType
    text_base_url: str | None
    text_api_key: str
    text_auth_token: str
    text_model: str
    text_task: str
    code_type: EmbeddingProviderType
    code_base_url: str | None
    code_api_key: str
    code_auth_token: str
    code_model: str
    code_task: str

    @model_serializer(mode="wrap")
    def _mask_secrets(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        for field in ("api_key", "auth_token", "text_api_key", "text_auth_token", "code_api_key", "code_auth_token"):
            data[field] = _MASKED_SECRET if data.get(field) else ""
        return data


class PaginatedEmbeddingProviderResponse(BaseModel):
    """Embedding 供应商分页响应。"""

    items: list[EmbeddingProviderResponse]
    total: int
    skip: int
    limit: int
