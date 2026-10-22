"""
ARL 类型定义。

定义 ARL 会话、角色、状态等核心数据结构。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class ARLRole(StrEnum):
    """ARL 容器角色枚举。"""

    WORKER = "worker"  # 工作角色（Claude Code）
    VALIDATOR = "validator"  # 验证角色（Codex）


class ARLSessionStatus(StrEnum):
    """ARL 会话状态枚举。"""

    STARTING = "starting"  # 容器启动中
    READY = "ready"  # 就绪，可接受连接
    ERROR = "error"  # 启动或运行错误
    STOPPED = "stopped"  # 已停止（容器壳仍在，可原地 start 恢复）


class ARLContainerInfo(BaseModel):
    """单个 ARL 容器信息。"""

    name: str  # 容器名称（按 session_id + role 确定性派生）
    #: 映射到宿主机的 daemon WS 端口。仅宿主机模式（backend 裸跑）才发布端口；
    #: backend 在容器内时走 Docker 网络 + 容器名 DNS，不发布端口，故为 None。
    port: int | None
    password: str  # Paseo daemon 鉴权密码（HMAC 派生）
    status: ARLSessionStatus  # 容器状态


class ARLSession(BaseModel):
    """ARL 会话元数据。

    这是一份**派生快照**：权威状态始终在 Docker（容器是否存在/在跑）与
    ``research_session`` 表（归属、配置）里，本对象每次按需从二者重建，
    不作为跨请求的缓存持有。
    """

    session_id: str  # 会话 ID（即 research_session.id 的字符串形式）
    folder_id: str | None  # 归属分组（research_session.folder_id），未分组为 None
    user_id: str  # 创建用户 ID（User.id 是 UUID，这里按字符串存）
    status: ARLSessionStatus  # 会话整体状态
    worker: ARLContainerInfo  # Worker 容器信息
    validator: ARLContainerInfo  # Validator 容器信息
    created_at: datetime  # 容器创建时间（取自 Docker，非本对象构造时间）
    error_message: str | None = None  # 错误信息（状态为 ERROR 时）


class ARLDaemonConfig(BaseModel):
    """传给容器的 daemon 配置参数。

    这里装的是**真实凭据**。是否换发为代理 token 由
    :mod:`app.services.arl.runtime` 在组装容器环境变量时决定，调用方无需关心。
    """

    # Worker (Claude) 配置
    base_url: str  # Anthropic API Base URL
    auth_token: str  # Anthropic API Token
    model: str  # 默认模型（如 claude-opus-5）
    small_fast_model: str | None = None  # 快速模型（如 claude-haiku-4-5-20251001）
    #: Worker provider 的 ARC 类型（anthropic / openai / openai-compatible），
    #: 换发代理 token 时据此决定上游鉴权头形态。
    provider_type: str = "anthropic"

    # Validator (Codex/OpenAI) 配置
    validator_base_url: str | None = None  # OpenAI API Base URL
    validator_api_key: str | None = None  # OpenAI API Key
    validator_model: str | None = None  # Validator 模型（如 gpt-5.5）
    validator_provider_type: str = "openai"  # 同上，Validator 侧
