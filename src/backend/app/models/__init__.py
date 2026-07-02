"""
数据模型包。

统一导出所有模型类型，保持 ``from app.models import X`` 的向后兼容性。
使用方无需关心内部模块划分。

注意：导入顺序很重要（base → rbac → user → item → llm4ad），
确保 SQLModel Relationship 的字符串前向引用能正确解析。
"""

from sqlmodel import SQLModel  # noqa: F401

# 基础模型
from app.models.base import Message, NewPassword, TimeMixin, Token  # noqa: F401

# 第三方认证与奖励模型
from app.models.auth import (  # noqa: F401
    StarRewardStatus,
    StarRewardStatusPublic,
    UserOidcAccount,
    UserStarReward,
)

# Chat 调参模型
from app.models.chat_tune import (  # noqa: F401
    ChatTuneActiveStage,
    ChatTuneGenerationKind,
    ChatTuneMessage,
    ChatTuneMessageRole,
    ChatTuneSession,
    ChatTuneStageStatus,
    ChatTuneTurnStatus,
)

# 意见反馈模型
from app.models.feedback import (  # noqa: F401
    Feedback,
    FeedbackAdmin,
    FeedbackBase,
    FeedbackCreate,
    FeedbackPriority,
    FeedbackPublic,
    FeedbacksPublic,
    FeedbackStatistics,
    FeedbackStatus,
    FeedbackType,
    FeedbackUpdate,
)

# 条目模型
from app.models.item import (  # noqa: F401
    Item,
    ItemBase,
    ItemCreate,
    ItemPublic,
    ItemsPublic,
    ItemUpdate,
)

# 微信群活码模型
from app.models.live_code import (  # noqa: F401
    ContactDisplay,
    LiveCode,
    LiveCodeBase,
    LiveCodeStrategy,
    LiveCodeTarget,
    LiveCodeTargetBase,
)

# LLM4AD 业务模型
from app.models.llm4ad import (  # noqa: F401
    EmbeddingMode,
    EmbeddingProvider,
    EmbeddingProviderBase,
    EmbeddingProviderType,
    LLMProvider,
    LLMProviderBase,
    Project,
    ProjectBase,
    ProviderType,
    Task,
    TaskBase,
    TaskLog,
    TaskStatus,
    UserDefaultModel,
    UserDefaultModelBase,
)

# RBAC 权限模型（定义关联表，需在 User 之前导入）
from app.models.rbac import (  # noqa: F401
    Permission,
    PermissionBase,
    Role,
    RoleBase,
    RolePermissionLink,
    UserRoleLink,
)

# 用户模型
from app.models.user import (  # noqa: F401
    UpdatePassword,
    User,
    UserBase,
    UserCreate,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
