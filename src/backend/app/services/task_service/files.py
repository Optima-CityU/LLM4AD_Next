"""任务输入数据的文件与文件夹管理。

提供目录树读取、文件读写/重命名/删除、文件夹创建/删除/重命名以及批量上传等
对象存储操作，统一通过 ``_helpers`` 中的占位文件与配额助手维护目录结构。
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile
from loguru import logger
from sqlmodel import Session

from app import models
from app.core.storage import storage
from app.models import Message
from app.schemas import task as schemas

from ._helpers import (
    _DEFAULT_FILE_CONTENT,
    _FOLDER_NAME_BASE,
    PLACEHOLDER_NAME,
    _backfill_empty_parent,
    _build_file_tree,
    _check_file_size,
    _check_storage_quota,
    _ensure_input_data_prefix,
    _get_task_input_data_path,
    _normalize_dir_path,
    _reject_placeholder_path,
    _remove_placeholder_if_present,
    _resolve_target_directory,
    _validate_file_path,
    _validate_folder_name,
)
from .auth import get_task_for_edit


def _touch_inputs(db: Session, task: models.Task) -> None:
    """Invalidate Agent baselines and verification after canonical file edits."""
    task.version_time = datetime.now(UTC)
    task.updated_time = task.version_time
    db.add(task)
    db.commit()


def upload_task_data(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    files: list[UploadFile],
) -> Message:
    """批量上传任务数据文件到 S3。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户
        files: 上传的文件列表

    Returns:
        上传成功的消息

    Raises:
        HTTPException: 未上传文件（400）
    """
    task = get_task_for_edit(db, task_id, current_user)

    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")
    if len(files) > 100:
        raise HTTPException(status_code=400, detail="单次上传文件数量不能超过 100 个")

    total_upload_size = 0
    for f in files:
        f.file.seek(0, 2)
        size = f.file.tell()
        f.file.seek(0)
        _check_file_size(size)
        total_upload_size += size

    # 生成存储目录：tasks/{task_id}/{timestamp}
    ts = int(datetime.now(UTC).timestamp())
    prefix = f"tasks/{task_id}/{ts}"

    # upload_task_data 会创建全新前缀，但需检查是否超出总限制
    _check_storage_quota(prefix, total_upload_size)

    storage.ensure_bucket()
    storage.upload_files(
        prefix=prefix,
        files=[(f.filename, f.file, f.content_type) for f in files],
    )

    # 更新任务的数据路径
    task.input_data_path = prefix
    task.updated_time = datetime.now(UTC)
    db.add(task)
    db.commit()

    _touch_inputs(db, task)
    return Message(message=f"上传成功，共 {len(files)} 个文件")


def get_task_data_tree(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> schemas.FileTreeResponse:
    """获取任务输入数据的目录树。"""
    task, prefix = _get_task_input_data_path(db, task_id, current_user)
    keys = storage.list_objects(prefix=prefix)
    tree = _build_file_tree(keys, prefix)
    return schemas.FileTreeResponse(tree=tree)


def get_task_data_file(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    file_path: str,
) -> schemas.FileContentResponse:
    """获取任务输入数据中指定文件的文本内容。"""
    _validate_file_path(file_path)
    _reject_placeholder_path(file_path)
    task, prefix = _get_task_input_data_path(db, task_id, current_user)

    key = f"{prefix}/{file_path}".replace("//", "/")
    try:
        data = storage.download(key)
    except Exception:
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="该文件不是文本文件，无法读取")

    return schemas.FileContentResponse(file_path=file_path, content=content)


def update_task_data_file(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    file_update: schemas.FileUpdateRequest,
) -> Message:
    """修改任务输入数据中指定文件的内容。

    内容以 UTF-8 写入对象存储；是否允许编辑某类文件由前端决定，后端不再做
    扩展名白名单校验。
    """
    task = get_task_for_edit(db, task_id, current_user)
    _validate_file_path(file_update.file_path)
    _reject_placeholder_path(file_update.file_path)
    task, prefix = _get_task_input_data_path(db, task_id, current_user)

    key = f"{prefix}/{file_update.file_path}".replace("//", "/")
    new_data = file_update.content.encode("utf-8")
    _check_file_size(len(new_data))
    _check_storage_quota(prefix, len(new_data))
    storage.upload(
        key,
        new_data,
        content_type="text/plain; charset=utf-8",
    )
    _touch_inputs(db, task)
    return Message(message="文件修改成功")


def delete_task_data_file(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    file_path: str,
) -> Message:
    """删除任务输入数据中的指定文件。"""
    task = get_task_for_edit(db, task_id, current_user)
    _validate_file_path(file_path)
    _reject_placeholder_path(file_path)
    task, prefix = _get_task_input_data_path(db, task_id, current_user)

    key = f"{prefix}/{file_path}".replace("//", "/")
    # 验证文件确实存在
    existing_keys = storage.list_objects(prefix=key)
    if key not in existing_keys:
        raise HTTPException(status_code=404, detail="文件不存在")

    storage.delete(key)

    _backfill_empty_parent(prefix, file_path)

    _touch_inputs(db, task)
    return Message(message="文件删除成功")


def rename_task_data_file(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    file_rename: schemas.FileRenameRequest,
) -> Message:
    """重命名任务输入数据中的指定文件（S3 层面为 copy + delete）。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。
        file_rename: 重命名请求，包含原路径和新路径。

    Returns:
        操作成功的消息。

    Raises:
        HTTPException: 路径非法、源文件不存在或目标文件已存在。
    """
    task = get_task_for_edit(db, task_id, current_user)
    _validate_file_path(file_rename.old_path)
    _validate_file_path(file_rename.new_path)
    _reject_placeholder_path(file_rename.old_path)
    _reject_placeholder_path(file_rename.new_path)
    task, prefix = _get_task_input_data_path(db, task_id, current_user)

    old_key = f"{prefix}/{file_rename.old_path}".replace("//", "/")
    new_key = f"{prefix}/{file_rename.new_path}".replace("//", "/")

    if old_key == new_key:
        return Message(message="文件名未变更")

    case_only_rename = old_key.lower() == new_key.lower()

    # 校验源文件存在
    existing_keys = storage.list_objects(prefix=old_key)
    if old_key not in existing_keys:
        raise HTTPException(status_code=404, detail="源文件不存在")

    # 校验目标路径不存在；case-only rename 时源/目标在 case-insensitive 后端是同一对象，
    # list 会返回源自身，跳过避免误报
    if not case_only_rename:
        target_keys = storage.list_objects(prefix=new_key)
        if new_key in target_keys:
            raise HTTPException(status_code=409, detail="目标路径已存在同名文件")

    # copy + delete
    bucket = storage.default_bucket
    if case_only_rename:
        # 仅大小写变化：部分 S3 兼容存储底层为 case-insensitive 文件系统，
        # 直接 copy + delete 会作用在同一个物理对象上导致数据丢失。
        # 先 copy 到唯一临时 key 隔离冲突，再迁回目标 key。
        tmp_key = f"{old_key}.__rename_tmp__.{uuid.uuid4().hex}"
        try:
            storage.client.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": old_key},
                Key=tmp_key,
            )
            storage.delete(old_key)
            storage.client.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": tmp_key},
                Key=new_key,
            )
        finally:
            try:
                storage.delete(tmp_key)
            except Exception:
                logger.exception("清理重命名临时对象失败: %s", tmp_key)
    else:
        storage.client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": old_key},
            Key=new_key,
        )
        storage.delete(old_key)

    # 目标目录若仅含占位文件，移除占位（已有真实文件填充）
    new_parent = file_rename.new_path.rsplit("/", 1)[0] if "/" in file_rename.new_path else ""
    _remove_placeholder_if_present(prefix, new_parent)

    # 源目录若变空，则补占位文件保留空目录结构
    _backfill_empty_parent(prefix, file_rename.old_path)

    _touch_inputs(db, task)
    return Message(message="文件重命名成功")


def create_task_data_file(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    file_create: schemas.FileCreateRequest,
) -> schemas.FileCreateResponse:
    """在任务输入数据存储中创建一个带 hello-world 示例的 Python 文件。

    若任务尚无 ``input_data_path``，会自动创建。
    文件放置在用户指定路径下，文件名自动去重
    （``main.py``、``main_1.py``、``main_2.py``……）。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。
        file_create: 文件创建请求，包含目标路径。

    Returns:
        包含已创建文件相对路径的响应。
    """
    task = get_task_for_edit(db, task_id, current_user)

    target_dir = _resolve_target_directory(file_create.path)
    if target_dir:
        _validate_file_path(target_dir)

    if not task.input_data_path:
        ts = int(datetime.now(UTC).timestamp())
        prefix = f"tasks/{task_id}/{ts}"
        storage.ensure_bucket()
        task.input_data_path = prefix
        task.updated_time = datetime.now(UTC)
        db.add(task)
        db.commit()
        db.refresh(task)

    prefix = task.input_data_path

    dir_prefix = f"{prefix}/{target_dir}/" if target_dir else f"{prefix}/"
    existing_keys = storage.list_objects(prefix=dir_prefix)
    existing_names: set[str] = set()
    for key in existing_keys:
        relative = key[len(dir_prefix):]
        if "/" not in relative and relative and relative != PLACEHOLDER_NAME:
            existing_names.add(relative)

    base_name = "new_file"
    ext = ".py"
    filename = f"{base_name}{ext}"
    counter = 1
    while filename in existing_names:
        filename = f"{base_name}_{counter}{ext}"
        counter += 1

    relative_path = f"{target_dir}/{filename}" if target_dir else filename
    s3_key = f"{prefix}/{relative_path}"
    storage.upload(s3_key, _DEFAULT_FILE_CONTENT.encode("utf-8"), content_type="text/x-python")

    # 目录已写入真实文件，移除可能存在的占位文件
    _remove_placeholder_if_present(prefix, target_dir)

    _touch_inputs(db, task)
    return schemas.FileCreateResponse(file_path=relative_path)


def create_task_data_folder(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    folder_create: schemas.FolderCreateRequest,
) -> schemas.FolderCreateResponse:
    """在任务输入数据目录树中创建新文件夹。

    通过写入占位文件 ``.keep`` 的方式表达空目录的存在。
    不支持在根目录下创建顶级文件夹，``path`` 必须指向一个已有的父目录。
    若未提供 ``name``，将在父目录下按 ``new_folder``、``new_folder_1``... 自动去重。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。
        folder_create: 文件夹创建请求，包含父目录路径与目标名称。

    Returns:
        包含已创建文件夹相对路径的响应。
    """
    task = get_task_for_edit(db, task_id, current_user)

    parent_dir = _normalize_dir_path(folder_create.path)
    if not parent_dir:
        raise HTTPException(status_code=400, detail="不支持创建顶级文件夹，请在已有文件夹下创建子目录")
    _validate_file_path(parent_dir)

    prefix = _ensure_input_data_prefix(db, task)

    # 收集父目录直接子项的名称（仅一层），用于命名去重 / 冲突判断
    parent_prefix = f"{prefix}/{parent_dir}/"
    existing_keys = storage.list_objects(prefix=parent_prefix)
    direct_children: set[str] = set()
    for key in existing_keys:
        relative = key[len(parent_prefix):]
        if not relative:
            continue
        first_segment = relative.split("/", 1)[0]
        if first_segment and first_segment != PLACEHOLDER_NAME:
            direct_children.add(first_segment)

    if folder_create.name and folder_create.name.strip():
        name = folder_create.name.strip()
        _validate_folder_name(name)
        if name in direct_children:
            raise HTTPException(status_code=409, detail="同级目录下已存在同名条目")
    else:
        name = _FOLDER_NAME_BASE
        counter = 1
        while name in direct_children:
            name = f"{_FOLDER_NAME_BASE}_{counter}"
            counter += 1

    folder_relative = f"{parent_dir}/{name}"
    placeholder_key = f"{prefix}/{folder_relative}/{PLACEHOLDER_NAME}"
    storage.upload(placeholder_key, b"", content_type="text/plain; charset=utf-8")

    _touch_inputs(db, task)
    return schemas.FolderCreateResponse(folder_path=folder_relative)


def delete_task_data_folder(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    folder_path: str,
) -> Message:
    """递归删除任务输入数据中的指定文件夹及其所有内容。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。
        folder_path: 待删除的文件夹相对路径，不可为空。

    Returns:
        操作成功的消息。

    Raises:
        HTTPException: 路径非法、为空或文件夹不存在。
    """
    task = get_task_for_edit(db, task_id, current_user)
    folder_path = _normalize_dir_path(folder_path)
    if not folder_path:
        raise HTTPException(status_code=400, detail="不允许删除根目录")
    _validate_file_path(folder_path)
    task, prefix = _get_task_input_data_path(db, task_id, current_user)

    folder_prefix = f"{prefix}/{folder_path}/"
    keys = storage.list_objects(prefix=folder_prefix)
    if not keys:
        raise HTTPException(status_code=404, detail="文件夹不存在")

    for key in keys:
        storage.delete(key)

    _backfill_empty_parent(prefix, folder_path)

    _touch_inputs(db, task)
    return Message(message="文件夹删除成功")


def rename_task_data_folder(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    folder_rename: schemas.FolderRenameRequest,
) -> Message:
    """重命名任务输入数据中的指定文件夹（S3 层面为批量 copy + 批量 delete）。

    由于对象存储 key 不可变，重命名等价于对前缀下所有对象执行 copy 到新前缀再删除旧对象。
    复制采用线程池并发，删除采用 ``DeleteObjects`` 批量接口以减少请求次数。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。
        folder_rename: 重命名请求，包含原路径和新路径。

    Returns:
        操作成功的消息。

    Raises:
        HTTPException: 路径非法、源文件夹不存在、目标文件夹已存在或目标位于源内部。
    """
    task = get_task_for_edit(db, task_id, current_user)
    from concurrent.futures import ThreadPoolExecutor

    old_path = _normalize_dir_path(folder_rename.old_path)
    new_path = _normalize_dir_path(folder_rename.new_path)
    if not old_path:
        raise HTTPException(status_code=400, detail="不允许重命名根目录")
    if not new_path:
        raise HTTPException(status_code=400, detail="目标路径不能为空")
    _validate_file_path(old_path)
    _validate_file_path(new_path)
    task, prefix = _get_task_input_data_path(db, task_id, current_user)

    if old_path == new_path:
        return Message(message="文件夹名未变更")

    # 拒绝将目录移动到自身子目录下，避免无限自嵌套
    if new_path == old_path or new_path.startswith(old_path + "/"):
        raise HTTPException(status_code=400, detail="目标路径不能位于源文件夹内部")

    old_prefix = f"{prefix}/{old_path}/"
    new_prefix = f"{prefix}/{new_path}/"
    case_only_rename = old_prefix.lower() == new_prefix.lower()

    # 校验源文件夹存在
    src_keys = storage.list_objects(prefix=old_prefix)
    if not src_keys:
        raise HTTPException(status_code=404, detail="源文件夹不存在")

    # 校验目标父目录存在；同目录 rename（含仅大小写变化）父目录不变，无需校验
    old_parent = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
    new_parent = new_path.rsplit("/", 1)[0] if "/" in new_path else ""
    if old_parent != new_parent:
        if not new_parent:
            raise HTTPException(status_code=400, detail="不支持移动到顶级位置，请指定已有的父目录")
        parent_keys = storage.list_objects(prefix=f"{prefix}/{new_parent}/")
        if not parent_keys:
            raise HTTPException(status_code=404, detail="目标父目录不存在")

    # 校验目标路径下不存在同名条目；case-only rename 时源/目标在 case-insensitive 后端
    # 上是同一对象，list 会返回源自身，跳过避免误报
    if not case_only_rename:
        target_keys = storage.list_objects(prefix=new_prefix)
        if target_keys:
            raise HTTPException(status_code=409, detail="目标路径已存在同名文件夹")

    bucket = storage.default_bucket
    tmp_prefix = (
        f"{old_prefix.rstrip('/')}.__rename_tmp__.{uuid.uuid4().hex}/"
        if case_only_rename
        else None
    )

    def _copy_one(src_key: str) -> str:
        relative = src_key[len(old_prefix):]
        dst_key = f"{new_prefix}{relative}"
        storage.client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": src_key},
            Key=dst_key,
        )
        return dst_key

    def _copy_to_tmp(src_key: str) -> str:
        relative = src_key[len(old_prefix):]
        dst_key = f"{tmp_prefix}{relative}"
        storage.client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": src_key},
            Key=dst_key,
        )
        return dst_key

    def _copy_from_tmp(tmp_key: str) -> str:
        relative = tmp_key[len(tmp_prefix):]
        dst_key = f"{new_prefix}{relative}"
        storage.client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": tmp_key},
            Key=dst_key,
        )
        return dst_key

    if case_only_rename:
        # 仅大小写变化：先中转到唯一临时前缀，避开 case-insensitive 后端冲突。
        tmp_keys: list[str] = []
        try:
            with ThreadPoolExecutor(max_workers=16) as executor:
                tmp_keys = list(executor.map(_copy_to_tmp, src_keys))
            storage.delete_many(src_keys)
            with ThreadPoolExecutor(max_workers=16) as executor:
                list(executor.map(_copy_from_tmp, tmp_keys))
        finally:
            if tmp_keys:
                try:
                    storage.delete_many(tmp_keys)
                except Exception:
                    logger.exception("清理文件夹重命名临时对象失败: %s", tmp_prefix)
    else:
        # 并发复制（同 region 服务端复制，瓶颈在请求往返）
        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(_copy_one, src_keys))
        # 批量删除源对象
        storage.delete_many(src_keys)

    # 目标父目录若存在占位文件，移除占位（已有真实子目录填充）
    _remove_placeholder_if_present(prefix, new_parent)

    # 源父目录若变空，补占位文件保留目录结构
    _backfill_empty_parent(prefix, old_path)

    _touch_inputs(db, task)
    return Message(message="文件夹重命名成功")
