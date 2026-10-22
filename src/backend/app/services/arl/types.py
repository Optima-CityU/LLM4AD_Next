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
    STOPPED = "stopped"  # 已停止


class ARLContainerInfo(BaseModel):
    """单个 ARL 容器信息。"""

    name: str  # 容器名称
    port: int  # 映射到宿主机的端口（daemon WS 端口）
    password: str  # Paseo daemon 鉴权密码（HMAC 派生）
    status: ARLSessionStatus  # 容器状态


class ARLSession(BaseModel):
    """ARL 会话元数据。"""

    session_id: str  # 会话 ID（32 字节十六进制）
    project_id: int  # 关联的项目 ID
    user_id: str  # 创建用户 ID（User.id 是 UUID，这里按字符串存）
    status: ARLSessionStatus  # 会话整体状态
    worker: ARLContainerInfo  # Worker 容器信息
    validator: ARLContainerInfo  # Validator 容器信息
    created_at: datetime  # 创建时间
    error_message: str | None = None  # 错误信息（状态为 ERROR 时）


class ARLDaemonConfig(BaseModel):
    """传给容器的 daemon 配置参数。"""

    # Worker (Claude) 配置
    base_url: str  # Anthropic API Base URL
    auth_token: str  # Anthropic API Token
    model: str  # 默认模型（如 claude-opus-5）
    small_fast_model: str | None = None  # 快速模型（如 claude-haiku-4-5-20251001）

    # Validator (Codex/OpenAI) 配置
    validator_base_url: str | None = None  # OpenAI API Base URL
    validator_api_key: str | None = None  # OpenAI API Key
    validator_model: str | None = None  # Validator 模型（如 gpt-5.5）

