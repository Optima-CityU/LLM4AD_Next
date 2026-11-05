"""Validator 验证服务连接约定与探测测试。

钉住几个容易写错的点：

1. token 必须确定性派生（容器重建前后一致）、每会话不同、长度满足 >= 32。
2. Validator 环境变量与 Worker ``CLAUDE.md`` 里写的地址、token 必须是同一组值。
3. 写 ``CLAUDE.md`` 只替换系统段落，保留 setup 与 agent 写的内容，且幂等。
4. 探测失败不抛异常，给出可展示的 reason。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.services.arl import validation
from app.services.arl.runtime import container_name, validation_host_port
from app.services.arl.types import ARLRole
from app.services.arl.validation import (
    ValidationStatus,
    derive_validation_token,
    probe_validation,
    validator_env,
    write_worker_guide,
)

SESSION_ID = "1afab57a-befd-4b11-b1f6-67abd0327036"
OTHER_SESSION_ID = "2bfab57a-befd-4b11-b1f6-67abd0327036"


@pytest.fixture
def worker_workspace(tmp_path, monkeypatch):
    """把会话挂载根指向 tmp_path，返回 Worker 工作目录（不预先创建）。"""

    def fake_session_root(session_id: str) -> tuple[str, str]:
        root = tmp_path / session_id
        return str(root), str(root)

    monkeypatch.setattr("app.services.arl.runtime._session_root", fake_session_root)
    return validation.role_workspace(SESSION_ID, ARLRole.WORKER)


# ---- token 与环境变量 ----


def test_token_is_deterministic_and_long_enough() -> None:
    """同一会话总是同一个 token，不同会话互不相同，长度 >= 32。"""
    token = derive_validation_token(SESSION_ID)
    assert token == derive_validation_token(SESSION_ID)
    assert token != derive_validation_token(OTHER_SESSION_ID)
    assert len(token) >= 32


def test_validator_env_uses_container_name_and_fixed_port() -> None:
    """地址用 Validator 容器名，监听 0.0.0.0，端口取配置。"""
    env = validator_env(SESSION_ID)
    port = settings.ARL_VALIDATION_PORT
    host = container_name(SESSION_ID, ARLRole.VALIDATOR)

    assert env["CONTREJOUR_HOST"] == "0.0.0.0"
    assert env["CONTREJOUR_PORT"] == str(port)
    assert env["CONTREJOUR_URL"] == f"http://{host}:{port}"
    assert env["CONTREJOUR_TOKEN"] == derive_validation_token(SESSION_ID)


# ---- Worker CLAUDE.md ----


def test_worker_guide_contains_same_url_and_token(worker_workspace) -> None:
    """CLAUDE.md 里写的地址与 token 与 Validator 环境变量一致。"""
    assert write_worker_guide(SESSION_ID) is True

    guide = (worker_workspace / validation.WORKER_GUIDE_FILENAME).read_text(
        encoding="utf-8"
    )
    env = validator_env(SESSION_ID)
    assert f"{env['CONTREJOUR_URL']}/mcp" in guide
    assert env["CONTREJOUR_TOKEN"] in guide


def test_worker_guide_preserves_other_content_and_is_idempotent(
    worker_workspace,
) -> None:
    """段落外的内容保留，重复写入不重复段落，内容未变时不写盘。"""
    worker_workspace.mkdir(parents=True)
    guide_path = worker_workspace / validation.WORKER_GUIDE_FILENAME
    guide_path.write_text("# 角色段\n\nagent 的笔记\n", encoding="utf-8")

    assert write_worker_guide(SESSION_ID) is True
    assert write_worker_guide(SESSION_ID) is False

    guide = guide_path.read_text(encoding="utf-8")
    assert "agent 的笔记" in guide
    assert guide.count(validation.GUIDE_BEGIN_MARKER) == 1
    assert guide.count(validation.GUIDE_END_MARKER) == 1


def test_worker_guide_replaces_stale_block(worker_workspace) -> None:
    """旧版段落（如手动同步时代的指引）原位替换，前后内容不动。"""
    worker_workspace.mkdir(parents=True)
    guide_path = worker_workspace / validation.WORKER_GUIDE_FILENAME
    guide_path.write_text(
        f"前文\n{validation.GUIDE_BEGIN_MARKER}\n旧指引\n"
        f"{validation.GUIDE_END_MARKER}\n后文\n",
        encoding="utf-8",
    )

    write_worker_guide(SESSION_ID)

    guide = guide_path.read_text(encoding="utf-8")
    assert "旧指引" not in guide
    assert guide.startswith("前文\n")
    assert guide.endswith("后文\n")
    assert derive_validation_token(SESSION_ID) in guide


# ---- 探测 ----


@pytest.fixture
def container_network(monkeypatch):
    """模拟 backend 在容器内运行，探测走 Docker 网络。"""
    monkeypatch.setattr(validation, "use_container_network", lambda: True)


@pytest.mark.usefixtures("container_network")
def test_probe_healthy(monkeypatch) -> None:
    """``/health`` 返回 200 时就绪，探测的就是 Worker 要连的地址。"""
    requested: list[str] = []

    def fake_get(url: str, **_kwargs) -> httpx.Response:
        requested.append(url)
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(validation.httpx, "get", fake_get)
    status = probe_validation(SESSION_ID)

    assert status.configured is True
    assert status.reason is None
    assert requested == [f"{validator_env(SESSION_ID)['CONTREJOUR_URL']}/health"]


def test_probe_host_mode_uses_published_port(monkeypatch) -> None:
    """backend 在宿主机时探映射到 127.0.0.1 的端口，返回的 url 仍是 Worker 侧地址。"""
    monkeypatch.setattr(validation, "use_container_network", lambda: False)
    requested: list[str] = []

    def fake_get(url: str, **_kwargs) -> httpx.Response:
        requested.append(url)
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(validation.httpx, "get", fake_get)
    status = probe_validation(SESSION_ID)

    host_port = validation_host_port(SESSION_ID)
    assert requested == [f"http://127.0.0.1:{host_port}/health"]
    assert status.configured is True
    assert status.url == validator_env(SESSION_ID)["CONTREJOUR_URL"]


@pytest.mark.usefixtures("container_network")
def test_probe_unreachable_is_not_an_error(monkeypatch) -> None:
    """连不上是常态（服务尚未拉起），不抛异常，给出 reason。"""

    def fake_get(*_args, **_kwargs) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(validation.httpx, "get", fake_get)
    status = probe_validation(SESSION_ID)

    assert status.configured is False
    assert status.reason
    assert "error" in status.detail


def test_payload_never_contains_token() -> None:
    """推给前端的 payload 不含 token。"""
    payload = ValidationStatus(configured=True, url="http://v:8765").to_payload()
    assert set(payload) == {"configured", "url", "reason"}


def test_same_state_ignores_detail() -> None:
    """detail 只是诊断信息，变化不触发重推。"""
    a = ValidationStatus(configured=False, url="u", reason="r", detail={"x": 1})
    b = ValidationStatus(configured=False, url="u", reason="r", detail={"x": 2})
    assert a.same_state_as(b)
    assert not a.same_state_as(None)
    assert not a.same_state_as(ValidationStatus(configured=True, url="u"))
