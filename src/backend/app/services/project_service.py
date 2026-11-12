"""
项目服务层。

封装项目相关的业务逻辑，包括权限校验、CRUD 操作等。
路由层调用本模块的函数，实现关注点分离。
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger
from sqlmodel import Session, func, or_, select

from app import models
from app.models import Message, TaskStatus


def get_project_with_auth(db: Session, project_id: uuid.UUID, current_user: models.User) -> models.Project:
    """获取项目并校验当前用户的访问权限。

    Args:
        db: 数据库会话
        project_id: 项目 ID
        current_user: 当前登录用户

    Returns:
        通过权限校验的项目对象

    Raises:
        HTTPException: 项目不存在（404）或无权访问（403）
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not current_user.is_superuser and project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return project


def create_project(db: Session, name: str, description: str | None, user_id: uuid.UUID) -> models.Project:
    """创建新项目。

    Args:
        db: 数据库会话
        name: 项目名称
        description: 项目描述
        user_id: 所属用户 ID

    Returns:
        创建成功的项目对象
    """
    db_project = models.Project(
        name=name,
        description=description,
        user_id=user_id,
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


_PROJECT_SORT_COLUMNS = {
    "created_time": models.Project.created_time,
    "name": models.Project.name,
}


def list_projects(
    db: Session,
    user_id: uuid.UUID,
    skip: int,
    limit: int,
    sort: str = "created_time",
    order: str = "desc",
    q: str | None = None,
) -> tuple[list[models.Project], int]:
    """分页查询用户的项目列表。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        skip: 跳过条数
        limit: 每页条数
        sort: 排序字段，仅允许 ``created_time`` / ``name``，非白名单值回退到
            ``created_time``。
        order: 排序方向，``asc`` 或 ``desc``，非法值回退到 ``desc``。
        q: 名称/描述模糊搜索关键字（不区分大小写）；为 None 或空白时不过滤。

    Returns:
        (项目列表, 总数) 元组
    """
    query = select(models.Project).where(models.Project.user_id == user_id)

    if q and q.strip():
        # 转义 LIKE 通配符，避免用户输入的 % / _ 被当成模式符
        keyword = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{keyword}%"
        query = query.where(
            or_(
                models.Project.name.ilike(pattern, escape="\\"),
                models.Project.description.ilike(pattern, escape="\\"),
            )
        )

    total = db.exec(select(func.count()).select_from(query.subquery())).one()

    sort_col = _PROJECT_SORT_COLUMNS.get(sort, models.Project.created_time)
    direction = (order or "").lower()
    ordered = sort_col.asc() if direction == "asc" else sort_col.desc()
    # 次级键 id 兜底：name 可能重复、created_time 在低精度时钟下也可能撞值
    query = query.order_by(ordered, models.Project.id.desc())

    page_query = query.offset(skip).limit(limit) if limit > 0 else query
    projects = db.exec(page_query).all()
    return list(projects), total


def update_project(
    db: Session,
    project_id: uuid.UUID,
    current_user: models.User,
    update_data: dict,
) -> models.Project:
    """更新项目信息。

    Args:
        db: 数据库会话
        project_id: 项目 ID
        current_user: 当前登录用户
        update_data: 更新字段字典（仅包含用户显式提供的字段）

    Returns:
        更新后的项目对象

    Raises:
        HTTPException: 项目不存在（404）或无权更新（403）
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not current_user.is_superuser and project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权更新该项目")

    project.sqlmodel_update(update_data)
    project.updated_time = datetime.now(UTC)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project_id: uuid.UUID, current_user: models.User) -> Message:
    """删除项目及其下所有任务。

    若项目下有 PENDING/RUNNING 状态的 Celery 任务，先强制终止再删除。

    Args:
        db: 数据库会话
        project_id: 项目 ID
        current_user: 当前登录用户

    Returns:
        删除成功的消息

    Raises:
        HTTPException: 项目不存在（404）或无权删除（403）
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not current_user.is_superuser and project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权删除该项目")

    # Force-stop any running/pending tasks before deleting
    running_tasks = db.exec(
        select(models.Task).where(
            models.Task.project_id == project_id,
            models.Task.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
        )
    ).all()

    if running_tasks:
        from app.services.task_service import _force_stop_celery_task

        for task in running_tasks:
            try:
                _force_stop_celery_task(db, task, reason="项目被删除，任务已中断")
            except Exception as e:
                logger.warning(f"强制停止任务 {task.id} 失败: {e}")

    from app.services.evaluation_data import cleanup_task_evidence, task_outbox

    evidence = [
        (task.id, task_outbox(db, task))
        for task in db.exec(select(models.Task).where(models.Task.project_id == project_id))
    ]
    db.delete(project)
    db.commit()
    for task_id, outbox in evidence:
        cleanup_task_evidence(task_id, outbox)
    return Message(message="项目已删除")
