"""
用户默认模型配置请求/响应 Schema。

定义用户默认模型配置的获取与更新所需的数据验证和序列化模型。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ---- 请求 Schema ----


class ModelSlotUpdate(BaseModel):
    """单个模型槽位更新请求。"""

    provider_id: uuid.UUID | None = None
    model_name: str | None = None


class UserDefaultModelUpdate(BaseModel):
    """用户默认模型配置更新请求（所有字段均可选）。"""

    planner_provider_id: uuid.UUID | None = None
    planner_model_name: str | None = None
    coder_provider_id: uuid.UUID | None = None
    coder_model_name: str | None = None
    report_provider_id: uuid.UUID | None = None
    report_model_name: str | None = None
    other_provider_id: uuid.UUID | None = None
    other_model_name: str | None = None
    embedding_enabled: bool | None = None
    embedding_provider_id: uuid.UUID | None = None


# ---- 响应 Schema ----


class UserDefaultModelResponse(BaseModel):
    """用户默认模型配置响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_time: datetime
    updated_time: datetime
    user_id: uuid.UUID
    planner_provider_id: uuid.UUID | None
    planner_provider_name: str | None = None
    planner_model_name: str | None
    coder_provider_id: uuid.UUID | None
    coder_provider_name: str | None = None
    coder_model_name: str | None
    report_provider_id: uuid.UUID | None
    report_provider_name: str | None = None
    report_model_name: str | None
    other_provider_id: uuid.UUID | None
    other_provider_name: str | None = None
    other_model_name: str | None
    embedding_enabled: bool
    embedding_provider_id: uuid.UUID | None
    embedding_provider_name: str | None = None
