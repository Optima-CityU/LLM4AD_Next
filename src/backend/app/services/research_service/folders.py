"""分组文件夹（Folder）子域：CRUD、树形展开、批量重排、名字冲突校验。

会话数统计一律用一次 ``GROUP BY`` 拿全，避免 N+1。文件夹移动做深度不定的
父链检测，防止 A→B→A 环形归属。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app import models
from app.models.research import ResearchFolder, ResearchSession
from app.schemas.research import (
    ResearchFolderCreateRequest,
    ResearchFolderItem,
    ResearchFolderListResponse,
    ResearchFolderReorderRequest,
    ResearchFolderTreeNode,
    ResearchFolderTreeResponse,
    ResearchFolderUpdateRequest,
)

from ._common import _get_folder


def _session_counts_by_folder(
    db: Session,
    user: models.User,
    folder_ids: list[uuid.UUID] | None = None,
) -> dict[uuid.UUID | None, int]:
    """一次 GROUP BY 拿 ``folder_id -> 会话数``，避免 N+1。

    ``folder_ids`` 为 None → 统计该用户全部会话（含未分组，key 为 None）；
    传入列表 → 仅统计这些 folder（不含未分组）。
    """
    stmt = (
        select(
            ResearchSession.folder_id,
            func.count(ResearchSession.id),
        )
        .where(ResearchSession.user_id == user.id)
    )
    if folder_ids is not None:
        stmt = stmt.where(ResearchSession.folder_id.in_(folder_ids))
    stmt = stmt.group_by(ResearchSession.folder_id)
    return {fid: int(n) for fid, n in db.exec(stmt).all()}


def list_folders(db: Session, user: models.User) -> ResearchFolderListResponse:
    """返回该用户的所有文件夹 + 每个 folder 的直接归属会话数 + 未分组会话数。"""
    folders = db.exec(
        select(ResearchFolder)
        .where(ResearchFolder.user_id == user.id)
        .order_by(ResearchFolder.sort_order, ResearchFolder.created_time)
    ).all()
    # 一次 GROUP BY 拿全部 folder → session 计数，避免 N+1
    counts = _session_counts_by_folder(db, user)
    items: list[ResearchFolderItem] = []
    for f in folders:
        item = ResearchFolderItem.model_validate(f)
        item.session_count = counts.get(f.id, 0)
        items.append(item)
    return ResearchFolderListResponse(
        items=items,
        total=len(folders),
        ungrouped_session_count=counts.get(None, 0),
    )


def create_folder(
    db: Session, request: ResearchFolderCreateRequest, user: models.User
) -> ResearchFolderItem:
    """新建文件夹。父级不存在或跨用户时 404。"""
    if request.parent_id is not None:
        _get_folder(db, request.parent_id, user)  # 校验 parent 归属

    folder = ResearchFolder(
        user_id=user.id,
        parent_id=request.parent_id,
        name=request.name.strip(),
        sort_order=request.sort_order,
    )
    db.add(folder)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="folder name already exists"
        ) from exc
    db.refresh(folder)
    return ResearchFolderItem.model_validate(folder)


def update_folder(
    db: Session,
    folder_id: uuid.UUID,
    request: ResearchFolderUpdateRequest,
    user: models.User,
) -> ResearchFolderItem:
    """改名 / 移动 / 排序。

    ``parent_id`` 三值语义靠 Pydantic v2 的 ``model_fields_set`` 区分：
    - 请求未提供 ``parent_id`` → 不改
    - 显式提供 ``parent_id: null`` → 移到根
    - 提供 UUID → 移到目标目录
    """
    folder = _get_folder(db, folder_id, user)
    provided = request.model_fields_set
    if request.name is not None:
        folder.name = request.name.strip()
    if "parent_id" in provided:
        if request.parent_id is not None:
            if request.parent_id == folder.id:
                raise HTTPException(
                    status_code=400, detail="cannot move folder into itself"
                )
            parent = _get_folder(db, request.parent_id, user)
            if _would_create_cycle(db, folder, parent):
                raise HTTPException(
                    status_code=400, detail="folder move creates a cycle"
                )
            folder.parent_id = request.parent_id
        else:
            folder.parent_id = None
    if request.sort_order is not None:
        folder.sort_order = request.sort_order
    folder.updated_time = datetime.now(UTC)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="folder name already exists"
        ) from exc
    db.refresh(folder)
    return ResearchFolderItem.model_validate(folder)


def _would_create_cycle(
    db: Session, folder: ResearchFolder, new_parent: ResearchFolder
) -> bool:
    """深度不定的父链检测。防止 A→B→A 这种环形移动。"""
    cursor: ResearchFolder | None = new_parent
    depth = 0
    while cursor is not None and depth < 64:
        if cursor.id == folder.id:
            return True
        if cursor.parent_id is None:
            return False
        cursor = db.get(ResearchFolder, cursor.parent_id)
        depth += 1
    # 走到深度上限仍没触底：父链要么已损坏、要么本就藏着环。此时放行移动会把新环
    # 焊死，故保守判为「会成环」拒绝——正常层级远不及 64 层，命中上限即异常。
    return True


def reorder_folders(
    db: Session,
    request: ResearchFolderReorderRequest,
    user: models.User,
) -> list[ResearchFolderItem]:
    """批量重排：一次事务里更新多个文件夹的 ``sort_order``。

    - 全部 folder 归属校验，任一不属于该用户 → 404，整个事务回滚；
    - 未列出的文件夹 sort_order 不变；
    - 返回被修改的文件夹（含新的 sort_order）。
    """
    ids = [it.id for it in request.items]
    rows = db.exec(
        select(ResearchFolder).where(ResearchFolder.user_id == user.id).where(
            ResearchFolder.id.in_(ids)
        )
    ).all()
    row_map = {r.id: r for r in rows}
    missing = [i for i in ids if i not in row_map]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"folder(s) not found: {','.join(str(m) for m in missing)}",
        )
    now = datetime.now(UTC)
    for it in request.items:
        folder = row_map[it.id]
        folder.sort_order = it.sort_order
        folder.updated_time = now
        db.add(folder)
    db.commit()
    for r in rows:
        db.refresh(r)
    # 按新 sort_order 返回，方便前端直接替换
    counts = _session_counts_by_folder(db, user, folder_ids=ids)
    result: list[ResearchFolderItem] = []
    for r in sorted(rows, key=lambda x: x.sort_order):
        item = ResearchFolderItem.model_validate(r)
        item.session_count = counts.get(r.id, 0)
        result.append(item)
    return result


def get_folder_tree(
    db: Session, user: models.User
) -> ResearchFolderTreeResponse:
    """返回嵌套树形结构，一次查完，前端不用自己组织 parent-child。"""
    folders = db.exec(
        select(ResearchFolder)
        .where(ResearchFolder.user_id == user.id)
        .order_by(ResearchFolder.sort_order, ResearchFolder.created_time)
    ).all()
    counts = _session_counts_by_folder(db, user)

    # 构造 id → TreeNode 映射；一次遍历建父子关系
    nodes: dict[uuid.UUID, ResearchFolderTreeNode] = {}
    for f in folders:
        nodes[f.id] = ResearchFolderTreeNode(
            id=f.id,
            parent_id=f.parent_id,
            name=f.name,
            sort_order=f.sort_order,
            session_count=counts.get(f.id, 0),
        )
    roots: list[ResearchFolderTreeNode] = []
    for f in folders:
        node = nodes[f.id]
        if f.parent_id and f.parent_id in nodes:
            nodes[f.parent_id].children.append(node)
        else:
            roots.append(node)
    return ResearchFolderTreeResponse(
        tree=roots,
        ungrouped_session_count=counts.get(None, 0),
    )


def delete_folder(
    db: Session, folder_id: uuid.UUID, user: models.User
) -> None:
    """删除文件夹；子文件夹与会话通过 ON DELETE SET NULL 脱离归属。"""
    folder = _get_folder(db, folder_id, user)
    db.delete(folder)
    db.commit()
