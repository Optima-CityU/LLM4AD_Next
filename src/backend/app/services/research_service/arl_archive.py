"""ARL 会话工作区打包下载。

两个容器的 ``/workspace`` 都是宿主侧 bind mount（见 :func:`app.services.arl.validation.role_workspace`），
backend 直接遍历宿主目录打 zip 即可，不必经 daemon 一个个读文件。

与普通会话的产物包（:func:`.artifacts.iter_artifacts_archive`）口径不同：

- **保留点文件**：ARL 文件面板默认展示隐藏文件，``.aris/`` / ``.claude/`` 这类配置
  也是用户想带走的；只剔除 ``__pycache__`` / ``.pyc`` 等缓存。
- **不跟随符号链接**：工作区由 agent 随意写，软链可能指向容器外乃至 backend 宿主的
  任意路径，跟进去就等于把宿主文件打包给用户。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session

from app import models
from app.services.arl.types import ARLRole
from app.services.arl.validation import role_workspace

from ._common import _get_session
from .archive_ticket import artifact_archive_download_name
from .artifacts import _CACHE_DIR_NAMES, _is_useless_cache, stream_zip


def _scope_roles(scope: str) -> list[ARLRole]:
    """把下载范围展开成角色列表，非法取值报 400。"""
    if scope == "all":
        return [ARLRole.VALIDATOR, ARLRole.WORKER]
    try:
        return [ARLRole(scope)]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid role: {scope}") from exc


def iter_workspace_entries(root: Path, prefix: str = "") -> Iterator[tuple[str, Path]]:
    """列出工作区里要进 zip 的 ``(zip 内路径, 磁盘文件)``。

    用 ``os.walk(followlinks=False)`` 而不是 ``rglob``：前者能就地剪掉缓存目录，
    缓存目录整棵不进；软链文件和软链目录一律跳过。

    Args:
        root: 角色工作区在 backend 侧的路径。
        prefix: zip 内路径前缀（如 ``worker/``），单角色下载时为空。

    Yields:
        ``(arcname, path)``，``arcname`` 用 ``/`` 分隔。
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        # 原地改 dirnames 才能剪枝；软链目录 os.walk 本就不进，这里一并排除缓存目录
        dirnames[:] = sorted(
            d for d in dirnames if d not in _CACHE_DIR_NAMES and not (base / d).is_symlink()
        )
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _is_useless_cache(rel):
                continue
            yield f"{prefix}{rel}", path


def open_arl_workspace_archive(
    db: Session, session_id: uuid.UUID, user: models.User, scope: str
) -> tuple[str, Generator[bytes, None, None]]:
    """备好一次 ARL 工作区的流式打包下载：返回 ``(下载文件名, zip 字节生成器)``。

    校验全部在返回生成器之前做完，理由同 :func:`.artifacts.open_artifacts_archive`：
    流式响应开始后就改不了状态码。

    Args:
        db: 数据库会话。
        session_id: 会话 id。
        user: 当前用户，用于会话归属校验。
        scope: ``worker`` / ``validator`` / ``all``。

    Returns:
        ``(下载文件名, 生成器)``。``all`` 时 zip 顶层是 ``validator/`` 与 ``worker/``。

    Raises:
        HTTPException: 会话不存在/越权（404）、不是 ARL 会话（400）、范围非法（400），
            或所选工作区都还不存在（404，容器从未启动过）。
    """
    session = _get_session(db, session_id, user)
    if session.session_type != "arl":
        raise HTTPException(status_code=400, detail="not an ARL session")
    roles = _scope_roles(scope)
    roots = [(role, role_workspace(str(session_id), role)) for role in roles]
    roots = [(role, root) for role, root in roots if root.is_dir()]
    if not roots:
        raise HTTPException(status_code=404, detail="workspace not initialized")

    def entries() -> Iterator[tuple[str, Path]]:
        for role, root in roots:
            prefix = f"{role.value}/" if scope == "all" else ""
            yield from iter_workspace_entries(root, prefix)

    stem = artifact_archive_download_name(session.title, session_id).removesuffix(".zip")
    suffix = "" if scope == "all" else f"-{scope}"
    return f"{stem}{suffix}.zip", stream_zip(entries())
