"""会话 / 轮次的运行时快照与路径解析。

Celery 任务的主循环会跑数分钟到数小时，其间需要频繁读会话与轮次的配置
字段（mode / provider / workspace 等）。ORM 对象在 ``with get_db_session()``
块结束后会 detach，SQLAlchemy 的 ``expire_on_commit=True`` 也会让已加载
属性被过期——之后再访问就抛 ``DetachedInstanceError``。所以进入长循环
之前先把所有会用到的字段拷贝到 frozen dataclass 里，主循环只依赖快照。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.models.research import ResearchSession, ResearchTurn


@dataclass(frozen=True)
class SessionSnapshot:
    """会话在 run 触发点的只读快照，包含桥接主循环需要的全部字段。"""

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    topic: str
    profile: str
    mode: str
    provider_id: str | None
    model_name: str | None
    run_dir: str | None


@dataclass(frozen=True)
class TurnSnapshot:
    """轮次快照：承载 mode / provider / stage / respond_to 覆盖字段。"""

    id: uuid.UUID
    mode: str | None
    provider_id: str | None
    model_name: str | None
    from_stage: str | None
    to_stage: str | None
    user_input: str | None


def snap_session(session: ResearchSession) -> SessionSnapshot:
    return SessionSnapshot(
        id=session.id,
        user_id=session.user_id,
        title=session.title,
        topic=session.topic or "",
        profile=session.profile,
        mode=session.mode,
        provider_id=session.provider_id,
        model_name=session.model_name,
        run_dir=session.run_dir,
    )


def snap_turn(turn: ResearchTurn) -> TurnSnapshot:
    return TurnSnapshot(
        id=turn.id,
        mode=turn.mode,
        provider_id=turn.provider_id,
        model_name=turn.model_name,
        from_stage=turn.from_stage,
        to_stage=turn.to_stage,
        user_input=turn.user_input,
    )


def resolve_run_dir(session: SessionSnapshot) -> Path:
    """决定 ARC run_dir 落地路径。

    优先级：``session.run_dir`` 已有 > 环境变量 ``RESEARCH_DATA_ROOT`` >
    默认 ``{DOCKER_PROJECT_HOME}/research``。

    布局 ``research/{user_id}/{session_id}/``：

    - 单独的 ``research/`` 子树，与 code-server 的 ``code_user-*``、任务的
      ``code_user-*/{task_id}`` 等隔开，避免顶层目录混杂；
    - 其下按 ``user_id`` 分层做物理隔离，配合 API 层 ``user_id`` 过滤堵住跨用户访问；
    - 容器部署时 ``DOCKER_PROJECT_HOME`` 是挂载卷（重启不丢）。

    注：文献检索缓存 ``.researchclaw_cache/`` 由 researchclaw 按**运行时 CWD** 落盘；
    容器入口 ``os.chdir(run_dir)``，故缓存自然落到本 session 的 run_dir 下（不再散在
    顶层），并在同一 session 的多轮 resume 间复用。
    """
    from app.core.config import settings

    if session.run_dir:
        return Path(session.run_dir)
    root = os.environ.get("RESEARCH_DATA_ROOT") or settings.DOCKER_PROJECT_HOME
    return Path(root) / "research" / str(session.user_id) / str(session.id)
