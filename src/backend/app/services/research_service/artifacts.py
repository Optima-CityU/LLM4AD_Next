"""产物（Artifact）子域：只读扫描会话 ``run_dir`` 汇总产出文件。

- ``list_artifacts``：扁平列出所有产出文件（按名/后缀猜类别、抽 stage 号）；
- ``list_generated_solutions``：内联 ``**/generated/*.json``、按 stage 分组，大字段
  按演化持久化口径剥离；
- ``get_artifact_tree``：目录树，供前端文件浏览器；
- ``resolve_artifact_path``：把相对路径解析成真实文件，并防目录穿越；
- ``create_artifacts_archive``：落临时 zip 再交给 ``FileResponse``（旧口径，未变）；
- ``iter_artifacts_archive``：边打包边出字节，供 ``StreamingResponse`` 使用。

本模块纯读文件系统，不写库、不改状态。
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import tempfile
import uuid
import zipfile
from collections.abc import Generator
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
from .archive_ticket import artifact_archive_download_name

# 只认「llm4ad 演化产物」这一条路径，且**只认 Stage 13**：
#   stage-13/task_packages/{算法名称}/runs/{任务名}/{run_id}/generated/{文件}.json
#
# task_packages/ 是 ARC Stage 13（ITERATIVE_REFINE）独有的产物——由 researchclaw 的
# ``_generate_llm4ad_task_packages()`` 写出，其 stage_dir 恒为 ``run_dir/"stage-13"``。
# 故这里把阶段号写死，而不是沿用 ``stage-\d+``：本面板是「本会话演化结果」的展示位，
# 多带一个 stage 分组只会误导。
#
# 目录名**必须精确等于 stage-13**，不接任何后缀（/ _v1 / _ITERATIVE_REFINE 都不认）：
# 演化产物只落在当前生效的那个 stage-13 目录里，回跳产生的版本快照是历史遗留，
# 一并列出来会让同一份结果出现两次。
#
# 其中 run_id 仅允许字母+数字组合（用户约定），任务名（runs 下一级）不固定。
# generated 目录下**只取一层的 .json 文件**（不递归），避免捞到无关嵌套数据。
_GENERATED_STAGE_DIR = "stage-13"
_GENERATED_PATH_RE = re.compile(
    rf"^{_GENERATED_STAGE_DIR}/task_packages/(?P<algo>[^/]+)/runs/[^/]+/"
    r"(?P<run_id>[A-Za-z0-9]+)/generated/(?P<file>[^/]+\.json)$"
)

_ARTIFACT_KIND_HINTS: dict[str, str] = {
    "paper_final.md": "paper_final",
    "paper_revised.md": "paper_final",
    "paper_draft.md": "paper_draft",
    "config.arc.yaml": "config",
    "results.json": "data",
    "evolution_state.json": "state",
}

# 产物目录里出现的无用缓存文件/目录。这些是 Python 解释器/工具链的中间产物，
# 对用户无价值（.pyc 可随时从 .py 重新编译），列出来只会污染产物面板与下载包。
_CACHE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".pyw", ".whl", ".egg-info", ".so"}
_CACHE_DIR_NAMES = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _is_useless_cache(rel: str) -> bool:
    """判断一次相对 ``run_dir`` 的路径是否是无用缓存文件/目录。

    覆盖两类：``__pycache__``/``.mypy_cache`` 等缓存目录，以及 ``.pyc``/``.whl``
    等字节码/打包产物。和点文件过滤（``.events-*.jsonl`` 等）一样，露出来只会污染
    产物面板、拖大下载包，故 list / tree / zip 三处统一剔除。
    """
    parts = rel.replace("\\", "/").split("/")
    for part in parts:
        if part in _CACHE_DIR_NAMES:
            return True
    name = parts[-1]
    return any(name.endswith(s) for s in _CACHE_SUFFIXES)


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
    if name.endswith(".log"):
        return "log"
    if name.endswith((".txt", ".md")):
        return "other"
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
            # 同样跳过 __pycache__/.pyc 这类无用缓存（见 _is_useless_cache）。
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if _is_useless_cache(rel):
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
    algorithm: str | None = None,
) -> ResearchGeneratedResponse:
    """扫描 run_dir 下 ``stage-13/task_packages/{算法}/runs/{任务}/{run_id}/generated/*.json``，
    内容内联、按**算法名称**分组。

    只认 llm4ad 演化产物这条固定路径（见 :data:`_GENERATED_PATH_RE`）——阶段锁死
    Stage 13、目录名不带后缀，不递归 ``generated`` 目录。大字段按演化持久化口径剥离
    （见 :func:`_load_stripped_generated`），前端一次拿全，无需再逐个 download。
    ``algorithm`` 非空时只返回该算法分组。
    """
    session = _get_session(db, session_id, user)
    grouped: dict[str, list[ResearchGeneratedItem]] = {}
    root = Path(session.run_dir) if session.run_dir else None
    if root and root.is_dir():
        # 只扫 generated 目录直接子层的 *.json；rglob 全文再正则约束，天然跳过无关文件。
        for path in root.rglob("generated/*.json"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            m = _GENERATED_PATH_RE.match(rel)
            if not m:
                continue
            algo = m.group("algo")
            if algorithm is not None and algo != algorithm:
                continue
            try:
                stat = path.stat()
                size: int | None = stat.st_size
                mtime: datetime | None = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            except OSError:
                size = None
                mtime = None
            grouped.setdefault(algo, []).append(
                ResearchGeneratedItem(
                    path=rel,
                    name=m.group("file"),
                    stage=algo,
                    run_id=m.group("run_id"),
                    size=size,
                    mtime=mtime,
                    data=_load_stripped_generated(path),
                )
            )
    # 算法名排序；组内按文件路径稳定排序
    groups = [
        ResearchGeneratedStageGroup(
            stage=algo,
            items=sorted(items, key=lambda it: it.path),
        )
        for algo, items in sorted(grouped.items())
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

    def build(path: Path, rel: str, depth: int = 0) -> ResearchArtifactTreeNode:
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
        # 只下钻真实目录，跳过符号链接目录：软链指回祖先会让递归无限打转。深度上限
        # 64 作二重保险（正常产物层级远不及此），命中即停止下钻。
        if path.is_dir() and not path.is_symlink() and depth < 64:
            for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name.startswith("."):
                    continue  # 隐藏 .events-*.jsonl / .app_config.json 等内部文件
                child_rel = f"{rel}/{child.name}" if rel else child.name
                if _is_useless_cache(child_rel):
                    continue  # __pycache__/ .pyc 等缓存，不下钻也不列
                node.children.append(build(child, child_rel, depth + 1))
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
    # zip 落盘后由调用方（路由）在响应后 BackgroundTask 删除；但打包途中若抛异常，
    # 那份 BackgroundTask 从未挂上，临时文件会永久泄漏在 temp 目录。故此处兜底：
    # 失败即就地删除临时文件再向上抛。
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                if any(part.startswith(".") for part in rel.split("/")):
                    continue
                if _is_useless_cache(rel):
                    continue  # __pycache__/ .pyc 等缓存，不进下载包
                try:
                    zf.write(path, arcname=rel)
                except OSError:
                    logger.opt(exception=True).warning(f"skip zip entry: {rel}")
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise

    safe_title = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in (session.title or "")
    ).strip("_")
    download_name = f"{safe_title or 'artifacts'}-{str(session_id)[:8]}.zip"
    return zip_path, download_name


# 流式打包的读块大小：单块 1 MiB，够摊薄 syscall 开销，又不至于长驻内存（产物目录
# 可能几十 GB，整文件读进内存会 OOM）。
_ARCHIVE_CHUNK_SIZE = 1024 * 1024

# zip 条目统一使用的固定时间戳（DOS 时间字段，最早可表示值 1980-01-01 00:00:00）：
# 不取文件 mtime，同一份产物重复下载得到完全一致的字节序列。
_ARCHIVE_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def open_artifacts_archive(
    db: Session, session_id: uuid.UUID, user: models.User
) -> tuple[str, Generator[bytes, None, None]]:
    """备好一次流式打包下载：返回 ``(下载文件名, zip 字节生成器)``。

    单独一个入口，是因为流式响应一旦开始就无法再改状态码 —— 会话归属校验与
    ``run_dir`` 检查都必须在生成器被消费之前做完，才能保证「会话不存在 / 越权」
    是一条正常的 404，而不是半截 zip。

    Args:
        db: 数据库会话。
        session_id: 会话 id。
        user: 当前登录用户，用于会话归属校验。

    Returns:
        ``(下载文件名, 生成器)``；文件名已按会话标题收敛成合法字符。

    Raises:
        HTTPException: 会话不存在/越权（404），或 run_dir 尚未初始化（404）。
    """
    session = _get_session(db, session_id, user)  # 归属校验（跨用户 404）
    root = Path(session.run_dir) if session.run_dir else None
    if not root or not root.is_dir():
        raise HTTPException(status_code=404, detail="run_dir not initialized")
    return (
        artifact_archive_download_name(session.title, session_id),
        iter_artifacts_archive(db, session_id, user),
    )


def iter_artifacts_archive(
    db: Session, session_id: uuid.UUID, user: models.User
) -> Generator[bytes, None, None]:
    """边遍历、边打包、边产出 run_dir 下全部产物的 zip 字节。

    与 :func:`create_artifacts_archive` 收录口径完全一致（同样跳过 ``.`` 开头的内部
    点文件与 ``__pycache__`` 等缓存，zip 内保留相对 run_dir 的目录结构），区别只在
    于这里不落临时文件：首个字节在读完第一个条目时就发出，不再有「整包压完才开始
    下载」的静默期，也没有需要 ``BackgroundTask`` 清理的残留。

    条目一律用 ``ZIP_STORED``（仅存储、不压缩），这是有意为之：run_dir 的大头是
    PDF / PNG / ``evolution_state.json`` 这类本就压不动的数据，zlib 在上面几乎省不下
    字节，却要吃满全程单核 CPU —— 那是「点了下载没反应」的主要来源。代价是包会比
    压缩后大一些。若日后想恢复压缩，应改用 ``zlib.compressobj(level=1)`` 之类的低
    档位，而不是 ``ZIP_DEFLATED`` 的默认 level。

    客户端中途断开时 Starlette 会停止消费本生成器，剩余文件不会被读，也不遗留半成
    品需要清理。

    Note:
        调用方必须在开始消费本生成器之前完成鉴权与 ``run_dir`` 校验：一旦响应头发出，
        400/404 已无从返回。

    Args:
        db: 数据库会话。
        session_id: 会话 id。
        user: 当前登录用户，用于会话归属校验。

    Yields:
        zip 字节分块，按序拼接即为完整 zip。

    Raises:
        HTTPException: 会话不存在/越权（404），或 run_dir 尚未初始化（404）。
    """
    session = _get_session(db, session_id, user)
    root = Path(session.run_dir) if session.run_dir else None
    if not root or not root.is_dir():
        raise HTTPException(status_code=404, detail="run_dir not initialized")

    class _ChunkSink(io.RawIOBase):
        """把 ZipFile 写出的连续字节攒成块，便于按块 yield 给响应。"""

        def __init__(self) -> None:
            self.chunks: list[bytes] = []

        def writable(self) -> bool:
            return True

        def write(self, b: bytes | bytearray) -> int:  # type: ignore[override]
            self.chunks.append(bytes(b))
            return len(b)

    sink = _ChunkSink()
    # ZipFile 支持非 seek 流：此时它会为每个条目自动补写 data descriptor（本地头里
    # CRC / 大小先写 0，真值落在尾部的中央目录），文件列表与大小仍然准确。
    with zipfile.ZipFile(sink, "w") as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if _is_useless_cache(rel):
                continue  # __pycache__/ .pyc 等缓存，不进下载包
            info = zipfile.ZipInfo(rel, date_time=_ARCHIVE_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED
            # external_attr 保住 unix 权限位（0o644 常规文件），否则解压出来可能不可读。
            info.external_attr = 0o644 << 16
            try:
                with zf.open(info, "w") as dst, path.open("rb") as src:
                    while True:
                        chunk = src.read(_ARCHIVE_CHUNK_SIZE)
                        if not chunk:
                            break
                        dst.write(chunk)
                        if sink.chunks:
                            yield b"".join(sink.chunks)
                            sink.chunks.clear()
            except OSError:
                # 单个条目读不动（被容器改着写 / 权限变动）不拖垮整包，跳过继续。
                logger.opt(exception=True).warning(f"skip zip entry: {rel}")
                sink.chunks.clear()
    # 退出 with 时 ZipFile 写出中央目录与 EOCD；无产物时整包内容也全在这里。
    if sink.chunks:
        yield b"".join(sink.chunks)
        sink.chunks.clear()


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

    # resolve_artifact_path 只防穿越 + 认已存在文件，但不挡 `.` 开头的内部点文件
    # （.app_config.json / .events-<turn>.jsonl 等容器契约中转文件）。门控编辑绝不该
    # 覆写它们——改坏会污染下一轮容器输入。与 list/tree/zip 的隐藏口径一致，拒之。
    if any(part.startswith(".") for part in relative.replace("\\", "/").split("/")):
        raise HTTPException(status_code=400, detail="cannot edit internal dotfiles")

    target = resolve_artifact_path(db, session_id, user, relative)  # 防穿越 + 只认已存在文件

    # 备份原文到 run_dir/hitl/snapshots/（run_dir 内，随 session 天然隔离）。相对
    # 路径打平成文件名避免子目录嵌套；同名只备份一次，保留最早的原始版本。
    try:
        snapshots_dir = Path(session.run_dir).resolve() / "hitl" / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        flat = relative.replace("\\", "/").replace("/", "__")
        backup = snapshots_dir / f"{flat}.orig"
        if not backup.exists():
            # 二进制产物 read_text 会抛 UnicodeDecodeError（ValueError 子类，非 OSError），
            # 旧实现只 except OSError 会漏网并 500。按字节备份，编解码无关，稳收所有产物。
            backup.write_bytes(target.read_bytes())
    except OSError:
        logger.opt(exception=True).warning(f"backup before edit failed: {relative}")

    target.write_text(content, encoding="utf-8")
    return target
