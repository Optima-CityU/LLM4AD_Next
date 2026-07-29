"""Profile 跨类切换子域：清空过期 stage 产物与数据。

``ml_vision``（sandbox 直跑）与 ``algorithm_evolution`` 等（llm4ad 演化）是两类
不同的实验形态（experiment.mode 分别为 ``sandbox`` / ``llm4ad_agent``）。当用户
在这两类之间切换 profile 时，第 9 步（EXPERIMENT_DESIGN）之后的产物是按旧类型
生成的，与新类型不兼容，必须清空重来。

本模块提供三个纯函数，由 :func:`sessions.update_session` 在检测到跨类切换时调用：
- :func:`is_cross_type_switch` — 判定新旧 profile 是否 sandbox ↔ 非 sandbox；
- :func:`purge_stage_artifacts` — 删磁盘 run_dir 下 stage>=9 的目录（含 ``_vN`` 版本）；
- :func:`purge_stage_data` — 删 DB 里 stage>=9 的 message/log 行并重置 session 进度字段。

清理均为 best-effort：磁盘与 DB 失败都只记日志、不阻断 profile 切换（切换才是
权威结果，残留旧产物下一轮启动前会被覆盖/忽略）。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from loguru import logger
from sqlmodel import Session, delete

from app.models.research import ResearchLog, ResearchMessage, ResearchSession
from app.services.research_service.analysis import _stage_num

# 清理边界：stage 号 >= 该值的产物/数据全部清空（含第 9 步 EXPERIMENT_DESIGN）。
_RESET_FROM_STAGE = 9


def _is_sandbox_profile(profile: str | None) -> bool:
    """profile 是否属于 sandbox 直跑类（当前仅 ``ml_vision``）。

    复用 config_builder 的 ``_SANDBOX_PROFILES``，避免两处硬编码同一集合。
    """
    from app.tasks.research_runner.config_builder import _SANDBOX_PROFILES

    return profile in _SANDBOX_PROFILES


def is_cross_type_switch(old_profile: str | None, new_profile: str | None) -> bool:
    """新旧 profile 是否发生 sandbox ↔ 非 sandbox 的跨类切换。

    同类切换（如 algorithm_evolution → algorithm_design，同为非 sandbox）返回
    False，不触发清理。
    """
    return _is_sandbox_profile(old_profile) != _is_sandbox_profile(new_profile)


def purge_stage_artifacts(
    run_dir: str | None, from_stage: int = _RESET_FROM_STAGE
) -> int:
    """删除 run_dir 下 stage 号 >= from_stage 的所有 stage 目录（含版本后缀）。

    复用 :func:`analysis._stage_num` 解析目录名，天然覆盖 ``stage-09`` /
    ``stage-9-v1`` / ``stage-10_v2`` / ``stage-09.v3`` 等全部变体。非 stage 目录
    （kb/、hitl/ 等）与 stage 号 < from_stage 的目录保留。

    best-effort：单个目录删除失败只记 warning 继续，整体不抛。

    Args:
        run_dir: 宿主视角的 run_dir 绝对路径；None/不存在直接返回 0。
        from_stage: 清理下界（含）；默认 :data:`_RESET_FROM_STAGE`。

    Returns:
        成功删除的 stage 目录数。
    """
    if not run_dir:
        return 0
    root = Path(run_dir)
    if not root.is_dir():
        return 0

    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        num = _stage_num(child.name)
        if num is None or num < from_stage:
            continue
        try:
            shutil.rmtree(child)
            removed += 1
        except OSError:
            logger.opt(exception=True).warning(
                f"purge_stage_artifacts: rmtree failed {child}"
            )
    if removed:
        logger.info(
            f"purge_stage_artifacts: removed {removed} stage dir(s) >= {from_stage} "
            f"under {run_dir}"
        )
    return removed


def purge_stage_data(
    db: Session,
    session_id: uuid.UUID,
    from_stage: int = _RESET_FROM_STAGE,
) -> None:
    """删除 DB 里 stage >= from_stage 的 message/log 行并重置 session 进度字段。

    - ``ResearchMessage`` / ``ResearchLog`` 中 ``stage`` 非空且 >= from_stage 的行
      被删除（``stage IS NULL`` 的对话 / guidance / collab 消息不受影响）；删后
      :func:`sessions.get_state` 回放出的 stages 列表只剩 < from_stage 的阶段。
    - 重置 session 的 ``active_stage`` / ``active_stage_name`` / ``best_objective`` /
      ``best_code_sha256``（这些反映的是被清掉的后段进度）。

    best-effort：内部自行 commit；失败则回滚并记日志，异常不向上冒泡，以免连累
    调用方（update_session）的 profile 落库。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        from_stage: 清理下界（含）；默认 :data:`_RESET_FROM_STAGE`。
    """
    try:
        db.exec(
            delete(ResearchMessage).where(
                ResearchMessage.session_id == session_id,
                ResearchMessage.stage.is_not(None),
                ResearchMessage.stage >= from_stage,
            )
        )
        db.exec(
            delete(ResearchLog).where(
                ResearchLog.session_id == session_id,
                ResearchLog.stage.is_not(None),
                ResearchLog.stage >= from_stage,
            )
        )
        session = db.get(ResearchSession, session_id)
        if session is not None:
            session.active_stage = None
            session.active_stage_name = None
            session.best_objective = None
            session.best_code_sha256 = None
            db.add(session)
        db.commit()
    except Exception:
        db.rollback()
        logger.opt(exception=True).warning(
            f"purge_stage_data failed session={session_id} (profile switch continues)"
        )
