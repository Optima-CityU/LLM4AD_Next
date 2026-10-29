"""ARL 工作区打包下载测试。

钉住三条口径：保留点文件、剔除缓存、不跟随软链（软链可能指向宿主任意路径）；
以及 ``all`` 时按角色分目录、单角色时不加前缀。
"""

from __future__ import annotations

import io
import os
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.research_service import arl_archive


def _zip_names(chunks) -> list[str]:
    return sorted(zipfile.ZipFile(io.BytesIO(b"".join(chunks))).namelist())


def _workspace(root: Path, role: str) -> Path:
    ws = root / role
    (ws / ".aris").mkdir(parents=True)
    (ws / ".aris" / "install.json").write_text("{}")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"x")
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("print(1)")
    (ws / "src" / "main.pyc").write_bytes(b"x")
    return ws


def _can_symlink(tmp_path: Path) -> bool:
    try:
        (tmp_path / "probe").symlink_to(tmp_path)
    except OSError:
        return False
    (tmp_path / "probe").unlink()
    return True


def test_workspace_entries_keep_dotfiles_and_drop_caches(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "worker")

    names = [rel for rel, _ in arl_archive.iter_workspace_entries(ws, "worker/")]

    assert names == ["worker/.aris/install.json", "worker/src/main.py"]


def test_workspace_entries_skip_symlinks(tmp_path: Path) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not permitted on this platform")
    secret = tmp_path / "outside"
    secret.mkdir()
    (secret / "host.txt").write_text("secret")
    ws = _workspace(tmp_path, "worker")
    os.symlink(secret, ws / "escape", target_is_directory=True)
    os.symlink(secret / "host.txt", ws / "link.txt")

    names = [rel for rel, _ in arl_archive.iter_workspace_entries(ws)]

    assert names == [".aris/install.json", "src/main.py"]


@pytest.fixture
def arl_session(tmp_path: Path, monkeypatch):
    session = SimpleNamespace(session_type="arl", title="我的 研究")
    monkeypatch.setattr(arl_archive, "_get_session", lambda *_a: session)
    monkeypatch.setattr(
        arl_archive, "role_workspace", lambda _sid, role: tmp_path / role.value
    )
    return session


@pytest.mark.usefixtures("arl_session")
def test_archive_all_prefixes_roles(tmp_path: Path) -> None:
    _workspace(tmp_path, "worker")
    _workspace(tmp_path, "validator")
    sid = uuid.UUID("1afab57a-befd-4b11-b1f6-67abd0327036")

    filename, stream = arl_archive.open_arl_workspace_archive(None, sid, None, "all")

    assert filename == "我的_研究-1afab57a.zip"
    assert _zip_names(stream) == [
        "validator/.aris/install.json",
        "validator/src/main.py",
        "worker/.aris/install.json",
        "worker/src/main.py",
    ]


@pytest.mark.usefixtures("arl_session")
def test_archive_single_role_has_no_prefix(tmp_path: Path) -> None:
    _workspace(tmp_path, "worker")

    filename, stream = arl_archive.open_arl_workspace_archive(
        None, uuid.uuid4(), None, "worker"
    )

    assert filename.endswith("-worker.zip")
    assert _zip_names(stream) == [".aris/install.json", "src/main.py"]


def test_archive_rejects_bad_requests(tmp_path: Path, arl_session) -> None:
    sid = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        arl_archive.open_arl_workspace_archive(None, sid, None, "all")
    assert exc.value.status_code == 404  # 两个工作区都还没建

    _workspace(tmp_path, "worker")
    with pytest.raises(HTTPException) as exc:
        arl_archive.open_arl_workspace_archive(None, sid, None, "../etc")
    assert exc.value.status_code == 400

    arl_session.session_type = "research"
    with pytest.raises(HTTPException) as exc:
        arl_archive.open_arl_workspace_archive(None, sid, None, "worker")
    assert exc.value.status_code == 400
