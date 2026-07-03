"""Chat 调参相关的请求/响应 Schema。"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.chat_tune import (
    ChatTuneActiveStage,
    ChatTuneMessageRole,
    ChatTuneStageStatus,
    ChatTuneTurnStatus,
)

# ---- 请求 ----


class ChatTuneTurnStartRequest(BaseModel):
    """触发新一轮调参的请求体。

    支持两种输入来源（可叠加）：
    - 普通文字消息：仅填 ``content``。
    - 响应上一轮的表单：填 ``respond_to_message_id`` + ``submission``，
      ``content`` 可省略；后端会原子地完成"锁定旧消息 + 触发新一轮"。
    """

    content: str | None = Field(
        default=None,
        max_length=10000,
        description="用户文字内容；与表单提交可叠加，但两者至少要有其一",
    )
    respond_to_message_id: uuid.UUID | None = Field(
        default=None,
        description="若本轮是对某条 assistant 消息的表单回复，填该消息 ID",
    )
    submission: dict[str, Any] | None = Field(
        default=None,
        description="表单填写值；与 ``respond_to_message_id`` 必须同时出现或同时缺省",
    )
    provider_id: str | None = Field(
        default=None,
        description="LLM Provider ID；支持空/'default'/'mock'/真实 UUID",
    )
    model_name: str | None = Field(
        default=None, description="覆盖 Provider 默认模型的模型名"
    )
    target_stage: ChatTuneActiveStage | None = Field(
        default=None,
        description=(
            "显式指定本轮要执行的阶段（gathering/build/review），用于前端"
            "覆盖后端的默认分发；为 None 时由后端按会话状态自动判断。"
            "仅 start_turn 接受此参数；retry_turn 不受影响。"
        ),
    )
    language: Literal["zh", "en"] = Field(
        default="zh",
        description="本轮对话使用的语言（zh/en），驱动 LLM 回答语言",
    )
    beta: bool = Field(
        default=False,
        description=(
            "是否走 AI 构建 (Beta)：用 AgentScope 单 agent 构建。仅当后端 "
            "ENABLE_AI_AGENT_BUILD 开启时生效，否则忽略并回退到默认分发。"
        ),
    )

    @model_validator(mode="after")
    def _validate_inputs(self) -> "ChatTuneTurnStartRequest":
        """``submission`` 与 ``respond_to_message_id`` 必须配对出现，且至少要有内容来源。"""
        has_submission = self.submission is not None
        has_respond_to = self.respond_to_message_id is not None
        if has_submission != has_respond_to:
            raise ValueError(
                "submission 与 respond_to_message_id 必须同时提供或同时缺省"
            )
        if not has_submission and not (self.content and self.content.strip()):
            raise ValueError(
                "必须至少提供 content 或 (respond_to_message_id + submission) 之一"
            )
        return self


class ChatTuneTurnRetryRequest(BaseModel):
    """重试失败/已停止轮次的请求体。

    可选地覆盖 Provider/Model；为空则走用户默认报告模型，再回退到任务的
    第一个 Provider，与历史行为保持一致。
    """

    provider_id: str | None = Field(
        default=None,
        description="LLM Provider ID；支持空/'default'/'mock'/真实 UUID",
    )
    model_name: str | None = Field(
        default=None, description="覆盖 Provider 默认模型的模型名"
    )


# ---- 响应 ----


class ChatTuneMessageItem(BaseModel):
    """单条调参消息的响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    role: ChatTuneMessageRole
    content: str
    turn_status: ChatTuneTurnStatus
    error: str | None = None
    payload: dict[str, Any] | None = None
    payload_locked: bool = False
    payload_locked_at: datetime | None = None
    payload_submission: dict[str, Any] | None = None
    created_time: datetime
    updated_time: datetime


class ChatTuneSessionItem(BaseModel):
    """调参会话的响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    task_id: uuid.UUID
    active_turn_id: uuid.UUID | None = None
    latest_config: dict[str, Any] = Field(default_factory=dict)
    # ---- 三阶段状态机 ----
    gathering_status: ChatTuneStageStatus = ChatTuneStageStatus.NOT_STARTED
    build_status: ChatTuneStageStatus = ChatTuneStageStatus.NOT_STARTED
    review_status: ChatTuneStageStatus = ChatTuneStageStatus.NOT_STARTED
    active_stage: ChatTuneActiveStage = ChatTuneActiveStage.GATHERING
    created_time: datetime
    updated_time: datetime


class ChatTuneSessionDetailResponse(BaseModel):
    """会话详情，含分页历史消息。"""

    session: ChatTuneSessionItem
    messages: list[ChatTuneMessageItem] = Field(default_factory=list)
    has_more: bool = Field(
        default=False,
        description="是否还有更早的历史消息未返回；为 True 时前端可用最早一条消息的 id 作为 before 游标继续翻页",
    )


class ChatTuneTurnStartResponse(BaseModel):
    """触发新一轮调参生成后的响应。"""

    session_id: uuid.UUID
    turn_id: uuid.UUID
    user_message: ChatTuneMessageItem
    assistant_message: ChatTuneMessageItem
    # 若本轮由表单提交触发，则同时返回被锁定的那条 assistant 消息（最新状态），
    # 让前端可以一次拿到锁定结果，无需额外刷新
    locked_message: ChatTuneMessageItem | None = None


class ChatTuneStopResponse(BaseModel):
    """停止当前正在生成的轮次后的响应。"""

    session_id: uuid.UUID
    turn_id: uuid.UUID
    status: ChatTuneTurnStatus
    message: str


class ChatTuneRetryResponse(BaseModel):
    """重试失败轮次后的响应。

    复用原 ``turn_id`` 原地重跑，前端可继续订阅同一个 ``/stream`` 端点。
    """

    session_id: uuid.UUID
    turn_id: uuid.UUID
    assistant_message: ChatTuneMessageItem
