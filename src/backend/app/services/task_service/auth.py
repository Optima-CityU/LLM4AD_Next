"""任务权限校验与状态读取的基础助手。

被其他 task_service 子模块及外部 service 广泛复用，仅依赖
``project_service`` 与 ``models``，不反向依赖任何业务子模块。
"""

import uuid

from fastapi import HTTPException
from sqlmodel import Session

from app import models
from app.models import TaskStatus
from app.services.project_service import get_project_with_auth


def get_task_with_auth(db: Session, task_id: uuid.UUID, current_user: models.User) -> models.Task:
    """获取任务并通过所属项目校验权限。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户

    Returns:
        通过权限校验的任务对象

    Raises:
        HTTPException: 任务不存在（404）或无权访问（403）
    """
    task = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    get_project_with_auth(db, task.project_id, current_user)
    return task


def require_unstarted_task(task: models.Task) -> None:
    """Preserve executed versions by directing input changes to a new child."""
    if task.status != TaskStatus.UNINITIALIZED or task.celery_task_id:
        raise HTTPException(status_code=409, detail="This version has already started; create an unstarted child to change inputs or run again")


def get_task_for_edit(db: Session, task_id: uuid.UUID, current_user: models.User) -> models.Task:
    """Authorize and lock the version before replacing canonical task inputs."""
    task = get_task_with_auth(db, task_id, current_user)
    db.refresh(task, with_for_update=True)
    require_unstarted_task(task)
    return task


def get_active_status(db: Session, task: models.Task) -> TaskStatus:
    """返回任务当前活跃版本的状态。

    ``active_child_id`` 为空或指向自身时返回 ``task.status``；
    否则按 id 取出目标任务的状态。若目标任务异常缺失，回退到自身状态。
    """
    if task.active_child_id is None or task.active_child_id == task.id:
        return task.status
    active = db.get(models.Task, task.active_child_id)
    return active.status if active is not None else task.status
