"""任务的增删改查与版本树管理。

涵盖任务列表分页、创建（含模板应用）、复制、更新、标签、活跃子版本设置、
任务树读取、删除以及存储用量统计等生命周期操作。
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger
from sqlmodel import Session, func, select

from app import models
from app.core.config import settings
from app.core.storage import storage
from app.models import Message, TaskStatus
from app.schemas import task as schemas
from app.services.project_service import get_project_with_auth

from ._helpers import _MAX_STORAGE_BYTES, DEFAULT_TOP_FOLDER_NAME, _create_top_level_folder
from .auth import get_task_with_auth
from .templates import _apply_template


def _apply_memory_defaults(
    db: Session,
    input_args: dict,
    *,
    current_user: models.User,
    project_id: uuid.UUID,
    explicit_memory: dict | None = None,
) -> dict:
    """Merge user/project memory defaults into task input args."""
    from app.services import memory_service

    project_defaults = memory_service.get_project_memory_config(db, project_id, current_user)
    existing = input_args.get("memory")
    memory = dict(existing) if isinstance(existing, dict) else {}
    mindmemos_available = settings.mindmemos_runtime_available and bool(project_defaults.mindmemos_binding_id)
    defaults = {
        "enabled": True,
        "type": "mindmemos_cloud" if mindmemos_available else "local_yaml",
        "include_user_memory": project_defaults.include_user_memory,
        "include_project_memory": project_defaults.include_project_memory,
        "include_task_memory": project_defaults.include_task_memory,
        "user_memory_limit": project_defaults.user_memory_limit,
        "project_memory_limit": project_defaults.project_memory_limit,
        "task_memory_limit": project_defaults.task_memory_limit,
        "mindmemos_search_strategy": project_defaults.mindmemos_search_strategy,
        "mindmemos_rerank": project_defaults.mindmemos_rerank,
        "mindmemos_score_threshold": project_defaults.mindmemos_score_threshold,
        "mindmemos_fail_open": project_defaults.mindmemos_fail_open,
    }
    preserved = {key: value for key, value in memory.items() if key not in defaults}
    if explicit_memory:
        preserved.update(explicit_memory)
    merged = {**defaults, **preserved}
    if merged.get("enabled") is False:
        merged["type"] = "local_yaml"
    elif mindmemos_available and merged.get("type") in (None, "", "local_yaml"):
        merged["type"] = "mindmemos_cloud"
    elif not mindmemos_available:
        merged["type"] = "local_yaml"
    input_args["memory"] = merged
    return input_args


def get_task_storage_usage(task: models.Task) -> schemas.StorageUsage:
    """计算任务输入数据的存储用量。"""
    used = storage.get_prefix_total_size(task.input_data_path) if task.input_data_path else 0
    return schemas.StorageUsage(
        used_bytes=used,
        limit_bytes=_MAX_STORAGE_BYTES,
        used_mb=round(used / 1024 / 1024, 2),
        limit_mb=settings.TASK_MAX_STORAGE_MB,
    )


def list_tasks(
    db: Session,
    project_id: uuid.UUID,
    current_user: models.User,
    skip: int,
    limit: int,
) -> tuple[list[models.Task], int, dict[uuid.UUID, int], dict[uuid.UUID, TaskStatus]]:
    """分页查询项目下的任务列表。

    返回根任务列表本身的字段不会被改写；活跃版本（``active_child_id``
    指向的任务，为空时即任务自身）的状态通过 ``active_status_map`` 单独返回，
    供路由层填入响应的 ``active_status`` 字段。

    Args:
        db: 数据库会话。
        project_id: 项目 ID。
        current_user: 当前登录用户。
        skip: 跳过条数。
        limit: 每页条数。

    Returns:
        四元组 ``(tasks, total, children_count_map, active_status_map)``。
        ``active_status_map`` 的键为根任务 ID，值为对应活跃任务的状态。
    """
    get_project_with_auth(db, project_id, current_user)
    query = select(models.Task).where(
        models.Task.project_id == project_id,
        models.Task.parent_id.is_(None),  # type: ignore[union-attr]
    )
    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    query = query.order_by(models.Task.created_time.desc())
    page_query = query.offset(skip).limit(limit) if limit > 0 else query
    tasks = list(db.exec(page_query).all())

    if not tasks:
        return tasks, total, {}, {}

    root_ids = [t.id for t in tasks]

    count_rows = db.exec(
        select(
            models.Task.group_id,
            func.count().label("cnt"),
        )
        .where(models.Task.group_id.in_(root_ids))
        .group_by(models.Task.group_id)
    ).all()
    count_map: dict[uuid.UUID, int] = {row.group_id: row.cnt for row in count_rows}

    # active_child_id 可能为空（视作指向自身），也可能指向某个子任务
    active_ids = {
        t.active_child_id for t in tasks
        if t.active_child_id is not None and t.active_child_id != t.id
    }
    id_to_status: dict[uuid.UUID, TaskStatus] = {}
    if active_ids:
        active_rows = db.exec(
            select(models.Task.id, models.Task.status).where(models.Task.id.in_(active_ids))
        ).all()
        id_to_status = {row.id: row.status for row in active_rows}

    active_status_map: dict[uuid.UUID, TaskStatus] = {}
    for t in tasks:
        if t.active_child_id is None or t.active_child_id == t.id:
            active_status_map[t.id] = t.status
        elif t.active_child_id in id_to_status:
            active_status_map[t.id] = id_to_status[t.active_child_id]
        else:
            # 指向的任务已不存在，回退到自身
            active_status_map[t.id] = t.status

    return tasks, total, count_map, active_status_map


def create_task(db: Session, task_in: schemas.TaskCreate, current_user: models.User) -> models.Task:
    """创建任务，自动填充默认参数。

    当提供 template_name 时，会上传模板文件到存储，
    设置 input_data_path，并从 config.yaml 填充 input_args。

    Args:
        db: 数据库会话
        task_in: 任务创建请求数据
        current_user: 当前登录用户

    Returns:
        创建成功的任务对象
    """
    get_project_with_auth(db, task_in.project_id, current_user)

    input_args = task_in.input_args if task_in.input_args is not None else schemas.generate_default_input_args()
    requested_memory = (
        input_args.get("memory")
        if task_in.input_args is not None and isinstance(input_args.get("memory"), dict)
        else None
    )

    # 处理供应商列表和模型选择，运行任务时需处理后传入llm4ad
    if "planner" not in input_args:
        input_args["planner"] = {}
    if "coder" not in input_args:
        input_args["coder"] = {}
    if "evaluator" not in input_args:
        input_args["evaluator"] = {}
    input_args["planner"]["provider"] = "default"
    input_args["coder"]["provider"] = "default"
    input_args["evaluator"]["provider"] = "default"
    input_args["providers"] = []

    db_task = models.Task(
        name=task_in.name,
        description=task_in.description,
        project_id=task_in.project_id,
        input_args=input_args,
        status=TaskStatus.UNINITIALIZED,
        ai_built=task_in.ai_built,
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)

    if task_in.template_name:
        config_name = task_in.config_name or "config.yaml"
        _apply_template(db, db_task, task_in.template_name, config_name)
        if requested_memory is not None:
            template_args = dict(db_task.input_args or {})
            template_args["memory"] = requested_memory
            db_task.input_args = template_args
        db_task.input_args = _apply_memory_defaults(
            db,
            dict(db_task.input_args or {}),
            current_user=current_user,
            project_id=task_in.project_id,
            explicit_memory=requested_memory,
        )
        db.commit()
        db.refresh(db_task)
    else:
        db_task.input_args = _apply_memory_defaults(
            db,
            dict(db_task.input_args or {}),
            current_user=current_user,
            project_id=task_in.project_id,
            explicit_memory=requested_memory,
        )
        # 非模板任务自动创建一个顶级文件夹；模板任务已有以模板名命名的顶级目录
        _create_top_level_folder(db, db_task, DEFAULT_TOP_FOLDER_NAME)
        db.commit()
        db.refresh(db_task)

    from app.services.chat_tune_service import seed_initial_messages

    seed_initial_messages(
        db,
        db_task,
        current_user,
        language=task_in.language,
        template_name=task_in.template_name,
        config_name=task_in.config_name or "config.yaml",
    )

    return db_task


def copy_task(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    is_child: bool = False,
) -> models.Task:
    """复制任务：名称加后缀，数据存在则在 S3 中复制一份，状态重置为 UNINITIALIZED。

    Args:
        db: 数据库会话
        task_id: 源任务 ID
        current_user: 当前登录用户
        is_child: 是否作为子任务复制；为 True 时保留父子关系和分组信息

    Returns:
        复制后的新任务对象
    """
    src_task = get_task_with_auth(db, task_id, current_user)

    # 如果原始任务有数据，复制 S3 文件
    new_data_path = None
    new_task_id = uuid.uuid4()
    if src_task.input_data_path:
        ts = int(datetime.now(UTC).timestamp())
        new_data_path = f"tasks/{new_task_id}/{ts}"
        storage.copy_objects(src_task.input_data_path, new_data_path)

    group_id = None
    parent_id = None
    if is_child:
        parent_id = src_task.id
        group_id = src_task.group_id if src_task.group_id else src_task.id

    new_task = models.Task(
        id=new_task_id,
        name=f"{src_task.name}-副本",
        description=src_task.description,
        project_id=src_task.project_id,
        input_args=src_task.input_args,
        input_data_path=new_data_path,
        reports=src_task.reports,
        status=TaskStatus.UNINITIALIZED,
        version_time=datetime.now(UTC),
        celery_task_id=None,
        group_id=group_id,
        parent_id=parent_id,
        ai_built=src_task.ai_built,
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)

    # 子任务复制时，同步复制源任务的日志记录关联到新任务
    if is_child:
        from app.models import TaskLog

        src_logs = db.exec(select(TaskLog).where(TaskLog.task_id == src_task.id)).all()
        for src_log in src_logs:
            db.add(
                TaskLog(
                    task_id=new_task.id,
                    type=src_log.type,
                    level=src_log.level,
                    timestamp=src_log.timestamp,
                    message=src_log.message,
                    data=src_log.data,
                )
            )

        # 自动将根任务的活跃子版本指向新创建的子任务
        root_id = group_id  # group_id 即根任务 ID
        root_task = db.get(models.Task, root_id)
        if root_task is not None:
            root_task.active_child_id = new_task.id
            root_task.updated_time = datetime.now(UTC)
            db.add(root_task)

        db.commit()
        db.refresh(new_task)

    return new_task


def update_task(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    task_update: schemas.TaskUpdate,
) -> models.Task:
    """修改任务参数，input_args 变更时同步更新 version_time。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户
        task_update: 任务更新请求数据

    Returns:
        更新后的任务对象
    """
    task = get_task_with_auth(db, task_id, current_user)

    update_data = task_update.model_dump(exclude_unset=True)
    task.sqlmodel_update(update_data)
    task.updated_time = datetime.now(UTC)
    # input_args 变更时更新版本时间
    if "input_args" in update_data:
        task.version_time = datetime.now(UTC)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task_tag(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    tag_update: schemas.TaskTagUpdate,
) -> models.Task:
    """更新任务的标签（tag）。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前认证用户。
        tag_update: 标签更新请求数据。

    Returns:
        更新后的任务对象。
    """
    task = get_task_with_auth(db, task_id, current_user)
    task.tag = tag_update.tag
    task.updated_time = datetime.now(UTC)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def set_active_child(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    child_id: uuid.UUID | None,
) -> models.Task:
    """设置根任务当前选中的子任务版本。

    Args:
        db: 数据库会话。
        task_id: 根任务 ID。
        current_user: 当前认证用户。
        child_id: 子任务 ID；为 None 时清空选中，读取时回退为指向自身。

    Returns:
        更新后的根任务对象。

    Raises:
        HTTPException 400: 目标任务不是根任务。
        HTTPException 404: 子任务不存在或不属于该根任务的分组。
    """
    task = get_task_with_auth(db, task_id, current_user)
    if task.parent_id is not None:
        raise HTTPException(status_code=400, detail="仅根任务支持设置活跃子版本")

    if child_id is not None:
        child = db.get(models.Task, child_id)
        if child is None or child.group_id != task.id:
            raise HTTPException(
                status_code=404, detail="子任务不存在或不属于该任务的版本组"
            )

    task.active_child_id = child_id
    task.updated_time = datetime.now(UTC)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task_tree(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> schemas.TaskTreeResponse:
    """获取以指定任务为根的任务树。

    返回根任务以及所有 ``group_id`` 等于该根任务 ID 的子任务。
    每个条目仅包含轻量字段（不含 logs/results）。

    Args:
        db: 数据库会话。
        task_id: 根任务 ID。
        current_user: 当前认证用户。

    Returns:
        包含根节点与子节点列表的 ``TaskTreeResponse``。
    """
    root_task = get_task_with_auth(db, task_id, current_user)

    if root_task.parent_id is not None:
        raise HTTPException(status_code=400, detail="指定的任务不是根任务")

    children = db.exec(
        select(models.Task).where(models.Task.group_id == root_task.id).order_by(models.Task.created_time.asc())
    ).all()

    # 首次访问且有子任务时，自动选中最新子任务
    if root_task.active_child_id is None and children:
        latest = max(children, key=lambda c: c.created_time)
        root_task.active_child_id = latest.id
        root_task.updated_time = datetime.now(UTC)
        db.add(root_task)
        db.commit()
        db.refresh(root_task)

    return schemas.TaskTreeResponse(
        root=schemas.TaskTreeItem.model_validate(root_task),
        children=[schemas.TaskTreeItem.model_validate(c) for c in children],
    )


def delete_task(db: Session, task_id: uuid.UUID, current_user: models.User) -> Message:
    """删除任务及其关联的 S3 数据。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户

    Returns:
        成功消息

    Raises:
        HTTPException: 任务处于 PENDING/RUNNING 状态时（409）需先停止任务。
    """
    task = get_task_with_auth(db, task_id, current_user)

    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        raise HTTPException(
            status_code=409,
            detail="任务正在运行中，请先停止任务再删除",
        )

    if task.input_data_path:
        try:
            keys = storage.list_objects(prefix=task.input_data_path)
            for key in keys:
                storage.delete(key)
        except Exception as e:
            logger.warning(f"清理任务 {task_id} 存储数据失败: {e}")

    db.delete(task)
    db.commit()
    return Message(message="任务已删除")
