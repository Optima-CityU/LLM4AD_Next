"""
Authentication extension models.

These tables keep third-party login bindings and one-time GitHub star rewards
separate from the core user table.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.core.encryption import EncryptedString
from app.models.base import TimeMixin


class StarRewardStatus(StrEnum):
    """Persistent state for the one-time star reward."""

    PENDING = "pending"
    GRANTED = "granted"
    FAILED = "failed"


class UserOidcAccount(TimeMixin, SQLModel, table=True):
    """OIDC/OAuth account linked to a local user."""

    __tablename__ = "user_oidc_account"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_user_oidc_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_user_oidc_user_provider"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    provider: str = Field(max_length=64, index=True)
    provider_subject: str = Field(max_length=255, index=True)
    provider_username: str | None = Field(default=None, max_length=255)
    provider_email: str | None = Field(default=None, max_length=255)
    access_token: str = Field(default="", sa_type=EncryptedString)
    token_type: str = Field(default="bearer", max_length=64)
    scope: str = Field(default="", max_length=512)
    last_login_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))


class UserStarReward(TimeMixin, SQLModel, table=True):
    """One reward row per user/repository pair."""

    __tablename__ = "user_star_reward"
    __table_args__ = (
        UniqueConstraint("user_id", "repo", name="uq_user_star_reward_user_repo"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    provider: str = Field(default="github", max_length=64)
    repo: str = Field(max_length=255, index=True)
    github_user_id: str | None = Field(default=None, max_length=255)
    starred_at_checked: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    reward_amount: float = Field(default=0.0)
    reward_status: StarRewardStatus = Field(default=StarRewardStatus.PENDING, max_length=32)
    granted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    failure_reason: str | None = Field(default=None, max_length=512)


class StarRewardStatusPublic(SQLModel):
    """Current user's star reward status response."""

    starred: bool | None = None
    reward_eligible: bool = False
    reward_granted: bool = False
    reward_granted_now: bool = False
    api_key_cache_refreshed: bool | None = None
    reward_amount: float = 0.0
    repo: str
    message: str = ""
