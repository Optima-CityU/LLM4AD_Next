"""Metadata linking a native AutoDesign conversation to task inputs and packages."""

import uuid

from sqlmodel import JSON, Column, Field, SQLModel

from app.models.base import TimeMixin


class AutoDesignSession(SQLModel, TimeMixin, table=True):
    """Associate one runtime workspace with an optional execution-task version."""

    __tablename__ = "autodesign_session"

    workspace_id: uuid.UUID = Field(primary_key=True, foreign_key="paper_workspace.id", ondelete="CASCADE")
    project_id: uuid.UUID | None = Field(default=None, foreign_key="project.id", ondelete="SET NULL", index=True)
    task_id: uuid.UUID | None = Field(default=None, foreign_key="task.id", ondelete="SET NULL", unique=True, index=True)
    package_object_key: str | None = Field(default=None, max_length=1024)
    package_sha256: str | None = Field(default=None, max_length=64)
    package_name: str | None = Field(default=None, max_length=255)
    validation_report: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    published_package_sha256: str | None = Field(default=None, max_length=64)
    published_task_id: uuid.UUID | None = Field(default=None, foreign_key="task.id", ondelete="SET NULL")
