"""
LLM 供应商（LLMProvider）请求/响应 Schema。

定义供应商 CRUD 操作所需的数据验证和序列化模型。
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_serializer

from app.models import LLMProviderBase, ProviderType

_MASKED_SECRET = "sk-***"
_MASKED_URL = "***"

# ---- 请求 Schema ----

class ProviderCreate(LLMProviderBase):
    """供应商创建请求。"""

    name: str
    type: ProviderType = ProviderType.OPENAI


class ProviderUpdate(BaseModel):
    """供应商更新请求（所有字段均可选）。"""

    name: str | None = None
    type: ProviderType | None = None
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    max_retries: int | None = None


class ProviderTestRequest(BaseModel):
    """供应商联通性测试请求。"""

    type: ProviderType = ProviderType.OPENAI
    model: str
    api_key: str = ""
    auth_token: str = ""
    base_url: str | None = None
    prompt: str = "hi"


class ProviderTestByIdRequest(BaseModel):
    """已存储供应商的联通性测试请求。

    通过供应商 ID 复用数据库中保存的凭据（适用于内置供应商，
    前端无法获取明文 api_key/base_url），仅需指定要测试的模型名称。

    编辑态下前端可携带表单当前值作为「字段级覆盖」：某字段非空则用该值，
    为空则回退到数据库中保存的真实值，从而支持「表单新值 + 库中旧值」混合测试。
    出于安全考虑，覆盖仅对用户自有供应商生效；内置供应商一律忽略覆盖、只用
    库中凭据，避免内置网关密钥被诱导发往任意 base_url。
    """

    model: str
    prompt: str = "hi"
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None


# ---- 响应 Schema ----

class ProviderResponse(BaseModel):
    """供应商响应。

    所有供应商的 api_key/auth_token 均不返回明文：已设置的凭据替换为占位符
    ``sk-***``，未设置的返回空串，使前端既能区分"是否已配置"，又无法读到真实
    凭据。内置供应商（is_builtin=True）额外屏蔽 base_url（含网关地址/令牌）；
    用户自有供应商的 base_url 非敏感，原样返回以便编辑。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_time: datetime
    updated_time: datetime
    name: str
    type: ProviderType
    api_key: str
    auth_token: str
    base_url: str | None
    model: str
    temperature: float
    max_tokens: int
    timeout: float
    max_retries: int
    is_builtin: bool = False
    visible_to_all: bool = False
    user_id: uuid.UUID | None = None

    @model_serializer(mode="wrap")
    def _mask_secrets(self, handler):
        data = handler(self)
        # 凭据：已设置→占位符，未设置→空串（保留"是否已配置"信息）
        data["api_key"] = _MASKED_SECRET if data.get("api_key") else ""
        data["auth_token"] = _MASKED_SECRET if data.get("auth_token") else ""
        # 内置供应商的 base_url 含网关地址/令牌，额外屏蔽
        if data.get("is_builtin"):
            data["base_url"] = _MASKED_URL
        return data


# ---- 分页 ----

class PaginatedProviderResponse(BaseModel):
    """供应商分页响应。"""

    items: list[ProviderResponse]
    total: int
    skip: int
    limit: int


class ProviderTestResponse(BaseModel):
    """供应商联通性测试响应。"""

    success: bool
    message: str
    data: Any = None


class BuiltinProviderQuotaResponse(BaseModel):
    """内置供应商额度信息响应。"""

    available: bool = False
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    spend: float | None = None
    budget: float | None = None
    remaining: float | None = None
    currency: str = "USD"
    message: str = ""
