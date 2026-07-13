"""产物（Artifact）子域：只读扫描会话 ``run_dir`` 汇总产出文件。

- ``list_artifacts``：扁平列出所有产出文件（按名/后缀猜类别、抽 stage 号）；
- ``list_generated_solutions``：内联 ``**/generated/*.json``、按 stage 分组，大字段
  按演化持久化口径剥离；
- ``get_artifact_tree``：目录树，供前端文件浏览器；
- ``resolve_artifact_path``：把相对路径解析成真实文件，并防目录穿越。

本模块纯读文件系统，不写库、不改状态。
"""

from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from loguru import logger
from sqlmodel import Session

from app import models
from app.schemas.research import (
    ResearchArtifactItem,
    ResearchArtifactListResponse,
    ResearchArtifactTreeNode,
    ResearchArtifactTreeResponse,
    ResearchGeneratedItem,
    ResearchGeneratedResponse,
    ResearchGeneratedStageGroup,
)
from app.utils.log_persist import strip_generated_fields_for_list

from ._common import _get_session

_ARTIFACT_KIND_HINTS: dict[str, str] = {
    "paper_final.md": "paper_final",
    "paper_revised.md": "paper_final",
    "paper_draft.md": "paper_draft",
    "config.arc.yaml": "config",
    "results.json": "data",
    "evolution_state.json": "state",
}


def _classify_artifact(path: Path) -> str:
    """按文件名 / 后缀猜产物类别。"""
    name = path.name.lower()
    if name in _ARTIFACT_KIND_HINTS:
        return _ARTIFACT_KIND_HINTS[name]
    if name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".svg")):
        return "figure"
    if name.endswith(".py") or name.endswith(".ipynb"):
        return "code"
    if name.endswith((".yaml", ".yml")):
        return "config"
    if name.endswith((".json", ".csv", ".tsv", ".parquet", ".jsonl")):
        return "data"
    if name.endswith((".log", ".txt", ".md")):
        return "log" if name.endswith(".log") else "other"
    return "other"


def _stage_of(rel_path: str) -> int | None:
    """从 ``stage-12_EXPERIMENT_RUN/...`` 抽出 stage 号；找不到返 None。

    兼容回跳产生的版本目录：``stage-10_v1`` / ``stage-10.v1`` / ``stage-10-xxx``
    都取到前导数字 10。
    """
    head = rel_path.split("/", 1)[0]
    if not head.startswith("stage-"):
        return None
    tail = head.removeprefix("stage-")
    for sep in ("_", "-", "."):
        if sep in tail:
            tail = tail.split(sep, 1)[0]
    try:
        return int(tail)
    except ValueError:
        return None


def list_artifacts(
    db: Session, session_id: uuid.UUID, user: models.User
) -> ResearchArtifactListResponse:
    """扫描 run_dir，返回所有已产出的文件（不含目录）。"""
    session = _get_session(db, session_id, user)
    items: list[ResearchArtifactItem] = []
    root = Path(session.run_dir) if session.run_dir else None
    if root and root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            # 跳过内部点文件/点目录（.events-<turn>.jsonl、.app_config.json 等
            # 容器管线中转文件）：内容已落 DB/Redis，露出来只会污染产物面板。
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            try:
                stat = path.stat()
                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            except OSError:
                size = None
                mtime = None
            mime, _ = mimetypes.guess_type(rel)
            items.append(
                ResearchArtifactItem(
                    path=rel,
                    kind=_classify_artifact(path),  # type: ignore[arg-type]
                    stage=_stage_of(rel),
                    size=size,
                    mtime=mtime,
                    mime=mime,
                )
            )
    return ResearchArtifactListResponse(
        session_id=session.id,
        run_dir=session.run_dir,
        items=items,
    )


def _load_stripped_generated(path: Path) -> dict[str, Any] | None:
    """读一个 ``generated/*.json`` 并剥离大字段；解析失败返 None。

    复用演化任务持久化的 :func:`strip_generated_fields_for_list`，把
    ``code_artifacts`` / ``generation_meta`` / ``worktree`` / ``description``
    就地置空，保持与 log-list API 一致的剥离口径。
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    # strip_* 只在 type == "generated" 时生效：临时包一层 entry，data 与 obj 同引用，
    # 就地置空后 obj 即为剥离后的结果。
    strip_generated_fields_for_list({"type": "generated", "data": obj})
    return obj


def list_generated_solutions(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    stage: int | None = None,
) -> ResearchGeneratedResponse:
    """扫描 run_dir 下所有 ``**/generated/*.json``，内容内联、按 stage 分组。

    大字段按演化持久化口径剥离（见 :func:`_load_stripped_generated`），前端一次
    拿全，无需再逐个 download。``stage`` 非空时只返回该阶段。
    """
    session = _get_session(db, session_id, user)
    grouped: dict[int | None, list[ResearchGeneratedItem]] = {}
    root = Path(session.run_dir) if session.run_dir else None
    if root and root.is_dir():
        for path in root.rglob("generated/*.json"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            st = _stage_of(rel)
            if stage is not None and st != stage:
                continue
            parts = rel.split("/")
            gi = parts.index("generated") if "generated" in parts else -1
            run_id = parts[gi - 1] if gi > 0 else None
            try:
                stat = path.stat()
                size: int | None = stat.st_size
                mtime: datetime | None = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            except OSError:
                size = None
                mtime = None
            grouped.setdefault(st, []).append(
                ResearchGeneratedItem(
                    path=rel,
                    name=path.name,
                    stage=st,
                    run_id=run_id,
                    size=size,
                    mtime=mtime,
                    data=_load_stripped_generated(path),
                )
            )
    # None（无法解析 stage）排最后；组内按文件名稳定排序
    groups = [
        ResearchGeneratedStageGroup(
            stage=st,
            items=sorted(items, key=lambda it: it.path),
        )
        for st, items in sorted(
            grouped.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)
        )
    ]
    return ResearchGeneratedResponse(
        session_id=session.id,
        run_dir=session.run_dir,
        groups=groups,
    )


def get_artifact_tree(
    db: Session, session_id: uuid.UUID, user: models.User
) -> ResearchArtifactTreeResponse:
    """产物目录树（供前端文件浏览器）。"""
    session = _get_session(db, session_id, user)
    root_dir = Path(session.run_dir) if session.run_dir else None
    if not root_dir or not root_dir.is_dir():
        return ResearchArtifactTreeResponse(
            session_id=session.id, run_dir=session.run_dir, root=None
        )

    def build(path: Path, rel: str) -> ResearchArtifactTreeNode:
        try:
            stat = path.stat()
            size = stat.st_size if path.is_file() else None
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        except OSError:
            size = None
            mtime = None
        node = ResearchArtifactTreeNode(
            name=path.name if rel else "",
            path=rel,
            is_dir=path.is_dir(),
            size=size,
            mtime=mtime,
        )
        if path.is_dir():
            for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name.startswith("."):
                    continue  # 隐藏 .events-*.jsonl / .app_config.json 等内部文件
                child_rel = f"{rel}/{child.name}" if rel else child.name
                node.children.append(build(child, child_rel))
        return node

    return ResearchArtifactTreeResponse(
        session_id=session.id,
        run_dir=session.run_dir,
        root=build(root_dir, ""),
    )


def resolve_artifact_path(
    db: Session, session_id: uuid.UUID, user: models.User, relative: str
) -> Path:
    """把 API 层传来的相对路径解析成真实文件路径；防目录穿越。"""
    session = _get_session(db, session_id, user)
    if not session.run_dir:
        raise HTTPException(status_code=404, detail="run_dir not initialized")
    root = Path(session.run_dir).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes run_dir") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return target


def create_artifacts_archive(
    db: Session, session_id: uuid.UUID, user: models.User
) -> tuple[Path, str]:
    """把 run_dir 下所有产物打包成临时 zip，返回 ``(zip 路径, 下载文件名)``。

    收录口径与 :func:`list_artifacts` 一致：跳过 ``.`` 开头的内部点文件/点目录
    （容器中转文件），zip 内保留相对 run_dir 的目录结构。zip 落临时目录，调用方
    （路由）负责用 ``BackgroundTask`` 在响应后删除。无产物时返回空 zip。
    """
    session = _get_session(db, session_id, user)
    root = Path(session.run_dir) if session.run_dir else None
    if not root or not root.is_dir():
        raise HTTPException(status_code=404, detail="run_dir not initialized")

    fd, tmp = tempfile.mkstemp(prefix=f"research-{session_id}-", suffix=".zip")
    os.close(fd)  # 只借文件名，交给 ZipFile 自行开写
    zip_path = Path(tmp)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            try:
                zf.write(path, arcname=rel)
            except OSError:
                logger.opt(exception=True).warning(f"skip zip entry: {rel}")

    safe_title = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in (session.title or "")
    ).strip("_")
    download_name = f"{safe_title or 'artifacts'}-{str(session_id)[:8]}.zip"
    return zip_path, download_name


def write_artifact(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    relative: str,
    content: str,
) -> Path:
    """覆写一个已存在的产物文件（门控编辑用），返回写入路径。

    安全三关全部复用 :func:`resolve_artifact_path`：user 归属校验（跨用户 404）、
    防目录穿越（越出 run_dir 400）、只允许改**已存在**的文件（不允许凭空创建路径）。
    覆写前把原文备份到 ``run_dir/hitl/snapshots/``（对齐 ARC CLI 的 EDIT，给用户
    后悔药）；备份失败不阻断写入。

    与 ARC EDIT 语义一致：文件在盘上就地改好后，门控提交 ``approve`` 从下一 stage
    续跑即用改后内容，无需重跑本 stage。
    """
    session = _get_session(db, session_id, user)   # user 归属校验（跨用户 404）
    target = resolve_artifact_path(db, session_id, user, relative)  # 防穿越 + 只认已存在文件

    # 备份原文到 run_dir/hitl/snapshots/（run_dir 内，随 session 天然隔离）。相对
    # 路径打平成文件名避免子目录嵌套；同名只备份一次，保留最早的原始版本。
    try:
        snapshots_dir = Path(session.run_dir).resolve() / "hitl" / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        flat = relative.replace("\\", "/").replace("/", "__")
        backup = snapshots_dir / f"{flat}.orig"
        if not backup.exists():
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        logger.opt(exception=True).warning(f"backup before edit failed: {relative}")

    target.write_text(content, encoding="utf-8")
    return target
