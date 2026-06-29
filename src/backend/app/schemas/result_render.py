"""任务结果渲染相关的请求/响应 Schema 定义。"""

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ResultRenderType(StrEnum):
    """结果渲染类型枚举。"""

    TRAJECTORY = "trajectory"


class ResultRenderStatus(StrEnum):
    """结果渲染状态枚举。"""

    COMPLETED = "completed"
    FAILED = "failed"


class ResultRenderGenerateRequest(BaseModel):
    """结果渲染生成请求体。"""

    result_type: ResultRenderType = Field(description="结果渲染类型")
    language: Literal["zh", "en"] = Field(
        default="zh",
        description="输出语言：'zh' 表示中文，'en' 表示英文",
    )
    theme: Literal["light", "dark"] = Field(
        default="light",
        description="主题：'light' 浅色，'dark' 深色",
    )
    force: bool = Field(
        default=False,
        description="是否强制重新生成，忽略已有缓存",
    )


class ResultRenderGenerateResponse(BaseModel):
    """结果渲染生成响应体。"""

    task_id: uuid.UUID
    result_type: ResultRenderType
    status: ResultRenderStatus
    message: str
    error_code: str | None = Field(default=None, description="机器可读错误码")
    data: Any = Field(default=None, description="结果渲染数据")
