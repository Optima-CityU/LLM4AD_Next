"""Durable gateway identities and remote evaluation attempts."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


class EvaluationGateway(SQLModel, table=True):
    """A shared local evaluator, authenticated independently of platform accounts."""

    __tablename__ = "evaluation_gateway"
    id: uuid.UUID = Field(primary_key=True)
    name: str = Field(max_length=255)
    secret_hash: str = Field(max_length=64)
    allowed_user_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    contract: dict = Field(sa_column=Column(JSON, nullable=False))
    contract_key: str = Field(index=True, max_length=64)
    capacity: int = Field(default=1)
    protocol_version: int = Field(default=1)
    ready: bool = Field(default=True)
    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))


class RemoteEvaluationJob(SQLModel, table=True):
    """One candidate with durable, fenced execution ownership."""

    __tablename__ = "remote_evaluation_job"
    id: uuid.UUID = Field(primary_key=True)
    attempt_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    task_id: uuid.UUID = Field(foreign_key="task.id", index=True, ondelete="CASCADE")
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    gateway_id: uuid.UUID | None = Field(default=None, foreign_key="evaluation_gateway.id", index=True)
    selected_gateway_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    selection_mode: str = Field(default="selected", max_length=16)
    max_attempts: int = Field(default=1)
    attempt_count: int = Field(default=0)
    attempt_history: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    lease_expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True), index=True)
    contract_key: str = Field(max_length=64, index=True)
    state: str = Field(default="queued", index=True, max_length=32)
    object_key: str = Field(max_length=1024)
    request: dict = Field(sa_column=Column(JSON, nullable=False))
    report: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    sequence: int = Field(default=-1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True))
