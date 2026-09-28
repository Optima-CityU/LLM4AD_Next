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


_FOLDER_ORDER = (
    ResearchFolder.is_pinned.desc(),
    ResearchFolder.sort_order,
    ResearchFolder.created_time,
    ResearchFolder.id,
)
"""文件夹列表 / 树的统一排序键：置顶优先 → 手动序号 → 创建时间 → id。

置顶做成独立的布尔维度，而不是靠往 ``sort_order`` 里塞极小值来实现：后者
会让「序号」同时承载「用户意图的位置」和「是否置顶」两种语义，取消置顶时
无法还原到原来的位置。独立布尔列则让置顶 / 取消置顶成为纯粹的开关切换，
完全不碰 ``sort_order``（见 ``set_folder_pinned``），因此天然无损。

末位 ``id`` 只为消除并列时间戳的不确定性（同批插入的若干行 ``created_time``
可能完全相同），与 session 列表的复合排序保持一致的思路，让顺序稳定可复现。
"""


def _top_sort_order(
    db: Session, user: models.User, parent_id: uuid.UUID | None
) -> int:
    """同一父级下「最前」用的新序号：现有最小值 - 1，无同级时 -1。

    新建文件夹默认排在同级最前，避免 ``sort_order`` 全为 0 时新文件夹被
    ``created_time`` 兜底压到列表末尾导致用户找不到。

    ``parent_id`` 为 ``None``（根文件夹）时必须用 ``IS NULL``：SQL 里
    ``parent_id = NULL`` 恒为假，写等值比较会退化成跨父级取最小值。

    起始值取 ``-1`` 而非 ``0``：历史数据 ``sort_order`` 全是 0，从 0 起会与
    既有文件夹撞号并再次落到末尾。向下递减无下限压力（int4 余量充足）。
    """
    same_level = (
        ResearchFolder.parent_id.is_(None)
        if parent_id is None
        else ResearchFolder.parent_id == parent_id
    )
    current_min = db.exec(
        select(func.min(ResearchFolder.sort_order))
        .where(ResearchFolder.user_id == user.id)
        .where(same_level)
    ).one()
    return current_min - 1 if current_min is not None else -1


def _sort_folder_items(
    rows: list[ResearchFolder], counts: dict[uuid.UUID | None, int]
) -> list[ResearchFolderItem]:
    """把 ORM 行按 ``_FOLDER_ORDER`` 转成响应项并填 session_count。"""
    items: list[ResearchFolderItem] = []
    for f in rows:
        item = ResearchFolderItem.model_validate(f)
        item.session_count = counts.get(f.id, 0)
        items.append(item)
    return items


def list_folders(db: Session, user: models.User) -> ResearchFolderListResponse:
    """返回该用户的所有文件夹 + 每个 folder 的直接归属会话数 + 未分组会话数。"""
    folders = db.exec(
        select(ResearchFolder)
        .where(ResearchFolder.user_id == user.id)
        .order_by(*_FOLDER_ORDER)
    ).all()
    # 一次 GROUP BY 拿全部 folder → session 计数，避免 N+1
    counts = _session_counts_by_folder(db, user)
    items = _sort_folder_items(list(folders), counts)
    return ResearchFolderListResponse(
        items=items,
        total=len(folders),
        ungrouped_session_count=counts.get(None, 0),
    )


def create_folder(
    db: Session, request: ResearchFolderCreateRequest, user: models.User
) -> ResearchFolderItem:
    """新建文件夹。父级不存在或跨用户时 404。

    未显式指定 ``sort_order`` 时排到同级最前（同级最小值 - 1），避免新文件夹
    被既有的 0 号与 ``created_time`` 一起压到列表末尾；显式传入则尊重请求值，
    供「按指定位置插入」这类调用方使用。

    新建的文件夹 ``is_pinned`` 恒为 False：它排在最前是位置结果，不代表用户
    置顶意图，用户若真要钉住需显式调用置顶接口。
    """
    if request.parent_id is not None:
        _get_folder(db, request.parent_id, user)  # 校验 parent 归属
        parent_id = request.parent_id
    else:
        parent_id = None

    if request.sort_order is None:
        sort_order = _top_sort_order(db, user, parent_id)
    else:
        sort_order = request.sort_order

    folder = ResearchFolder(
        user_id=user.id,
        parent_id=parent_id,
        name=request.name.strip(),
        sort_order=sort_order,
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
    # 按新的排序键返回，方便前端直接替换（置顶维度也一并生效）
    counts = _session_counts_by_folder(db, user, folder_ids=ids)
    ordered = sorted(
        rows,
        key=lambda x: (
            not x.is_pinned,
            x.sort_order,
            x.created_time,
            x.id,
        ),
    )
    return _sort_folder_items(ordered, counts)


def set_folder_pinned(
    db: Session,
    folder_id: uuid.UUID,
    user: models.User,
    *,
    pinned: bool,
) -> ResearchFolderItem:
    """置顶 / 取消置顶文件夹（幂等）。

    只翻转 ``is_pinned``，绝不改 ``sort_order``。序号是用户在组内手动排序
    的意图，置顶是另一个正交维度（见 ``_FOLDER_ORDER``）；若置顶时把序号
    改小，取消置顶就再也回不到原来的位置，反复置顶还会让序号单调累积。

    组内置顶项之间的先后沿用各自的 ``sort_order``，不按置顶时间重排；
    这样置顶/取消置顶对序号完全无损。

    重复调用同一状态不会改变 ``updated_time`` 之外的任何东西，前端可以放心
    重试。
    """
    folder = _get_folder(db, folder_id, user)
    if folder.is_pinned != pinned:
        folder.is_pinned = pinned
        folder.updated_time = datetime.now(UTC)
        db.add(folder)
        db.commit()
        db.refresh(folder)
    return ResearchFolderItem.model_validate(folder)


def get_folder_tree(
    db: Session, user: models.User
) -> ResearchFolderTreeResponse:
    """返回嵌套树形结构，一次查完，前端不用自己组织 parent-child。"""
    folders = db.exec(
        select(ResearchFolder)
        .where(ResearchFolder.user_id == user.id)
        .order_by(*_FOLDER_ORDER)
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
            is_pinned=f.is_pinned,
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
