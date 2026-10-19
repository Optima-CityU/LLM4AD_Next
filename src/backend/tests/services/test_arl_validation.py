"""Validator 验证服务探测与同步测试。

探测的四项条件（token / config.json / 端口 / task.md）任一不满足都必须给出
**可直接展示给用户的 reason**，而不是只返回一个布尔——用户要知道差哪一步。

两个容易写错的点在这里钉住：

1. 连接主机必须是 Validator 的**容器名**，不能用 ``config.json`` 里
   ``public_url`` 的主机部分（那是 Validator 自视角的地址，Worker 连不上）。
2. 半写入的 ``config.json``（JSON 解析失败）是**中间态**而非错误，下一轮
   轮询即恢复，所以不能抛异常。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.arl import validation
from app.services.arl.types import ARLRole
from app.services.arl.validation import (
    ValidationStatus,
    probe_validation,
    sync_to_worker,
)

SESSION_ID = "1afab57a-befd-4b11-b1f6-67abd0327036"


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """把会话挂载根指向 tmp_path，返回两个角色的工作目录。"""

    def fake_session_root(session_id: str) -> tuple[str, str]:
        root = tmp_path / session_id
        return str(root), str(root)

    monkeypatch.setattr("app.services.arl.runtime._session_root", fake_session_root)

    dirs = {}
    for role in (ARLRole.VALIDATOR, ARLRole.WORKER):
        path = validation.role_workspace(SESSION_ID, role)
        path.mkdir(parents=True, exist_ok=True)
        dirs[role] = path
    return dirs


def _write_service(
    workspace: Path, public_url: str, token: str = "  tok-123\n"
) -> None:
    """在 Validator 工作目录里铺好验证服务的产物。"""
    validation_dir = workspace / validation.VALIDATION_DIR
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / validation.TOKEN_FILENAME).write_text(token, encoding="utf-8")
    (validation_dir / validation.CONFIG_FILENAME).write_text(
        json.dumps({"service": {"public_url": public_url}}), encoding="utf-8"
    )


# ---- 探测 ----


@pytest.mark.usefixtures("workspaces")
def test_probe_reports_not_started_without_token() -> None:
    """什么都没有时要明确说「验证服务尚未启动」。

    只要 fixture 的副作用（把挂载根指向 tmp_path），不用它的返回值。
    """
    status = probe_validation(SESSION_ID)

    assert status.configured is False
    assert status.url is None
    assert "尚未启动" in (status.reason or "")


def test_probe_strips_token_whitespace(workspaces) -> None:
    """token 要去首尾空白——文件多半以换行结尾，带着它去鉴权会 401。"""
    _write_service(workspaces[ARLRole.VALIDATOR], "http://localhost:8931")
    (workspaces[ARLRole.VALIDATOR] / validation.TASK_FILENAME).write_text(
        "# task", encoding="utf-8"
    )

    status = probe_validation(SESSION_ID)

    assert status.configured is True
    assert status.token == "tok-123"


def test_probe_uses_container_name_not_public_url_host(workspaces) -> None:
    """主机必须换成 Validator 容器名，只从 public_url 取端口。

    public_url 里写的是 Validator 容器自己看到的地址（localhost / 容器内
    IP），Worker 容器照着连必然失败。
    """
    _write_service(workspaces[ARLRole.VALIDATOR], "http://127.0.0.1:8931/mcp")
    (workspaces[ARLRole.VALIDATOR] / validation.TASK_FILENAME).write_text(
        "# task", encoding="utf-8"
    )

    status = probe_validation(SESSION_ID)

    assert status.configured is True
    assert status.url == f"http://llm4ad-arl-v-{SESSION_ID}:8931"
    assert "127.0.0.1" not in (status.url or "")


def test_probe_empty_token_is_pending_not_ready(workspaces) -> None:
    """token 文件存在但为空是写入中间态，不能判为已就绪。"""
    _write_service(
        workspaces[ARLRole.VALIDATOR], "http://localhost:8931", token="   \n"
    )

    status = probe_validation(SESSION_ID)

    assert status.configured is False
    assert "为空" in (status.reason or "")


def test_probe_half_written_config_does_not_raise(workspaces) -> None:
    """config.json 写了一半（非法 JSON）只报未就绪，不抛异常。"""
    validation_dir = workspaces[ARLRole.VALIDATOR] / validation.VALIDATION_DIR
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / validation.TOKEN_FILENAME).write_text("tok", encoding="utf-8")
    (validation_dir / validation.CONFIG_FILENAME).write_text(
        '{"service": {"public_u', encoding="utf-8"
    )

    status = probe_validation(SESSION_ID)

    assert status.configured is False
    assert "JSON" in (status.reason or "")


def test_probe_missing_public_url(workspaces) -> None:
    """config.json 合法但缺 service.public_url。"""
    validation_dir = workspaces[ARLRole.VALIDATOR] / validation.VALIDATION_DIR
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / validation.TOKEN_FILENAME).write_text("tok", encoding="utf-8")
    (validation_dir / validation.CONFIG_FILENAME).write_text(
        json.dumps({"service": {}}), encoding="utf-8"
    )

    status = probe_validation(SESSION_ID)

    assert status.configured is False
    assert "public_url" in (status.reason or "")


def test_probe_configured_but_no_task_is_still_configured(workspaces) -> None:
    """服务就绪但没 task.md：configured 仍为 True，由 has_task 单独表达。

    两件事要分开：服务地址已经可以写给 Worker 了，缺的只是任务说明。
    混成一个布尔会让前端无法区分「去左边起服务」和「等 task.md」。
    """
    _write_service(workspaces[ARLRole.VALIDATOR], "http://localhost:8931")

    status = probe_validation(SESSION_ID)

    assert status.configured is True
    assert status.has_task is False
    assert "task.md" in (status.reason or "")


def test_probe_default_ports_by_scheme(workspaces) -> None:
    """public_url 没写端口时按 scheme 回落。"""
    _write_service(workspaces[ARLRole.VALIDATOR], "https://validator-host/mcp")
    (workspaces[ARLRole.VALIDATOR] / validation.TASK_FILENAME).write_text(
        "# t", encoding="utf-8"
    )

    status = probe_validation(SESSION_ID)

    assert status.url is not None
    assert status.url.endswith(":443")


# ---- 推送去重 ----


def test_payload_does_not_leak_token() -> None:
    """推给前端的 payload 不得带 token 明文。

    前端不需要它（调验证服务的是容器里的 agent），而 WS 帧会进浏览器
    控制台和前端日志。
    """
    status = ValidationStatus(
        configured=True, url="http://v:1", token="secret-token", has_task=True
    )

    payload = status.to_payload()

    assert "secret-token" not in json.dumps(payload)
    assert payload["has_token"] is True


def test_same_state_dedupes_identical_probes() -> None:
    """状态没变就不该重推——每 5 秒一帧会把前端刷满。"""
    a = ValidationStatus(configured=True, url="http://v:1", token="t", has_task=True)
    b = ValidationStatus(configured=True, url="http://v:1", token="t", has_task=True)

    assert a.same_state_as(b) is True
    assert a.same_state_as(None) is False


def test_same_state_detects_task_appearing() -> None:
    """task.md 出现是用户可见变化，必须重推（按钮要变可用）。"""
    before = ValidationStatus(
        configured=True, url="http://v:1", token="t", has_task=False
    )
    after = ValidationStatus(
        configured=True, url="http://v:1", token="t", has_task=True
    )

    assert after.same_state_as(before) is False


def test_same_state_detects_token_rotation() -> None:
    """token 从有到无（服务被停）要重推。"""
    before = ValidationStatus(configured=True, url="http://v:1", token="t")
    after = ValidationStatus(configured=True, url="http://v:1", token=None)

    assert after.same_state_as(before) is False


# ---- 同步 ----


def test_sync_copies_task_and_writes_guide(workspaces) -> None:
    """同步后 Worker 目录里要有 task.md、连接信息文件和指向它的 CLAUDE.md。"""
    validator_ws = workspaces[ARLRole.VALIDATOR]
    worker_ws = workspaces[ARLRole.WORKER]
    _write_service(validator_ws, "http://localhost:8931")
    (validator_ws / validation.TASK_FILENAME).write_text(
        "# 任务\n实现一个算法", encoding="utf-8"
    )

    status = probe_validation(SESSION_ID)
    result = sync_to_worker(SESSION_ID, status)

    assert result["task_synced"] is True
    assert result["connection_written"] is True
    assert (worker_ws / validation.TASK_FILENAME).read_text(
        encoding="utf-8"
    ) == "# 任务\n实现一个算法"

    connection = json.loads(
        (worker_ws / validation.WORKER_CONNECTION_PATH).read_text(encoding="utf-8")
    )
    assert connection["url"] == f"http://llm4ad-arl-v-{SESSION_ID}:8931"
    assert connection["token"] == "tok-123"

    guide = (worker_ws / validation.WORKER_GUIDE_FILENAME).read_text(encoding="utf-8")
    # 凭据不进 CLAUDE.md，只给出连接信息文件的位置
    assert "tok-123" not in guide
    assert validation.WORKER_CONNECTION_PATH in guide
    # 要指向同步过来的 task.md，否则 Worker 不知道任务在哪
    assert validation.TASK_FILENAME in guide


def test_sync_preserves_agent_edits_in_guide(workspaces) -> None:
    """CLAUDE.md 里 agent 自己写的内容要保留，系统段落只替换不重复。"""
    validator_ws = workspaces[ARLRole.VALIDATOR]
    worker_ws = workspaces[ARLRole.WORKER]
    _write_service(validator_ws, "http://localhost:8931")
    (validator_ws / validation.TASK_FILENAME).write_text("任务", encoding="utf-8")
    guide_path = worker_ws / validation.WORKER_GUIDE_FILENAME
    guide_path.write_text("# Agent 笔记\n已完成第一步\n", encoding="utf-8")

    sync_to_worker(SESSION_ID, probe_validation(SESSION_ID))
    # 模拟 agent 运行中在系统段落后面继续追加内容
    guide_path.write_text(
        guide_path.read_text(encoding="utf-8") + "\n## 后续\n第二步\n",
        encoding="utf-8",
    )
    sync_to_worker(SESSION_ID, probe_validation(SESSION_ID))

    guide = guide_path.read_text(encoding="utf-8")
    assert "已完成第一步" in guide
    assert "第二步" in guide
    assert guide.count(validation.GUIDE_BEGIN_MARKER) == 1
    assert guide.count(validation.GUIDE_END_MARKER) == 1


def test_sync_rewrites_connection_when_guide_rewritten(workspaces) -> None:
    """agent 整篇重写 CLAUDE.md 丢了段落后，再次同步要补回来。"""
    validator_ws = workspaces[ARLRole.VALIDATOR]
    worker_ws = workspaces[ARLRole.WORKER]
    _write_service(validator_ws, "http://localhost:8931")
    (validator_ws / validation.TASK_FILENAME).write_text("任务", encoding="utf-8")
    guide_path = worker_ws / validation.WORKER_GUIDE_FILENAME

    sync_to_worker(SESSION_ID, probe_validation(SESSION_ID))
    guide_path.write_text("# 全新内容\n", encoding="utf-8")
    sync_to_worker(SESSION_ID, probe_validation(SESSION_ID))

    guide = guide_path.read_text(encoding="utf-8")
    assert guide.startswith("# 全新内容")
    assert validation.WORKER_CONNECTION_PATH in guide


def test_sync_overwrites_existing_task(workspaces) -> None:
    """重复同步要覆盖，而不是追加或报错。"""
    validator_ws = workspaces[ARLRole.VALIDATOR]
    worker_ws = workspaces[ARLRole.WORKER]
    _write_service(validator_ws, "http://localhost:8931")
    (worker_ws / validation.TASK_FILENAME).write_text("旧内容", encoding="utf-8")
    (validator_ws / validation.TASK_FILENAME).write_text("新内容", encoding="utf-8")

    sync_to_worker(SESSION_ID, probe_validation(SESSION_ID))

    assert (worker_ws / validation.TASK_FILENAME).read_text(
        encoding="utf-8"
    ) == "新内容"


@pytest.mark.usefixtures("workspaces")
def test_sync_refuses_when_not_configured() -> None:
    """未就绪时拒绝同步，避免把一个连不上的地址写给 Worker。"""
    with pytest.raises(ValueError):
        sync_to_worker(SESSION_ID, ValidationStatus(configured=False))


def test_sync_preserves_unicode(workspaces) -> None:
    """中文任务说明不得乱码（Windows 默认编码不是 UTF-8）。"""
    validator_ws = workspaces[ARLRole.VALIDATOR]
    _write_service(validator_ws, "http://localhost:8931")
    (validator_ws / validation.TASK_FILENAME).write_text(
        "# 目标\n最小化总完工时间", encoding="utf-8"
    )

    sync_to_worker(SESSION_ID, probe_validation(SESSION_ID))

    assert "最小化总完工时间" in (
        workspaces[ARLRole.WORKER] / validation.TASK_FILENAME
    ).read_text(encoding="utf-8")
