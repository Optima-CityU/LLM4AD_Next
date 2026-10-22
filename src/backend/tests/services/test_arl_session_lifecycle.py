"""ARL 会话编排与 WS 收摊契约测试。

这里盯的是两类曾经出过问题的行为：

1. 会话状态必须以 Docker 为准（而非进程内缓存），且「复用 / 原地拉起 /
   删除重建」的判定要正确——判错会导致新凭据不生效，或把别的连接正在用
   的容器强删。
2. 前端断开后 WS 处理器必须真正收摊。原先 ``asyncio.gather`` 会一直等
   timeline 任务（其循环条件是 daemon 侧状态，与前端无关），导致 daemon
   连接永不关闭。
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from app.core.config import settings
from app.services.arl import runtime
from app.services.arl.types import ARLDaemonConfig, ARLRole, ARLSessionStatus


class _FakeContainer:
    """最小可用的 Docker 容器替身。"""

    def __init__(self, name: str, status: str = "running", labels: dict | None = None):
        self.name = name
        self.id = f"id-{name}"
        self.status = status
        self.labels = labels or {}
        self.attrs = {
            "Created": "2026-01-01T00:00:00.123456789Z",
            "Image": "image-id",
            "NetworkSettings": {"Ports": {}},
        }
        self.removed = False
        self.started = False
        self.stopped = False

    def reload(self):
        pass

    def remove(self, **_kwargs):
        self.removed = True

    def start(self):
        self.started = True
        self.status = "running"

    def stop(self, **_kwargs):
        self.stopped = True
        self.status = "exited"

    def logs(self, **_kwargs):
        return b"boom"


class _FakeDocker:
    """按名字索引的假 Docker 客户端。"""

    def __init__(self, containers: dict[str, _FakeContainer] | None = None):
        self._map = containers or {}
        self.created: list[dict] = []
        self.containers = SimpleNamespace(
            get=self._get, run=self._run, list=self._list
        )
        self.images = SimpleNamespace(get=lambda _ref: SimpleNamespace(id="image-id"))

    def _get(self, name):
        if name not in self._map:
            raise NotFound(name)
        return self._map[name]

    def _run(self, **kwargs):
        self.created.append(kwargs)
        container = _FakeContainer(
            kwargs["name"], labels=kwargs.get("labels", {})
        )
        self._map[kwargs["name"]] = container
        return container

    def _list(self, **_kwargs):
        return list(self._map.values())


def _config() -> ARLDaemonConfig:
    return ARLDaemonConfig(
        base_url="https://gw.example.com",
        auth_token="tok",
        model="claude-opus-5",
        validator_base_url="https://gw.example.com/v1",
        validator_api_key="key",
        validator_model="gpt-5.5",
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    """隔离 Docker / Redis / 文件系统，并禁用代理。"""
    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", str(tmp_path))
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", False)
    monkeypatch.setattr(runtime, "use_container_network", lambda: True)
    # 不碰 Redis
    monkeypatch.setattr(runtime, "acquire_arl_session_lock", lambda *a: True)
    monkeypatch.setattr(runtime, "release_arl_session_lock", lambda *a: True)
    monkeypatch.setattr(runtime, "touch_arl_session_active", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "forget_arl_session_active", lambda *a: None)
    return monkeypatch


def _install_docker(monkeypatch, docker: _FakeDocker) -> None:
    monkeypatch.setattr(runtime, "get_docker_client", lambda: docker)


def _stub_ready(monkeypatch, *, healthy: bool = True) -> None:
    """跳过真实 HTTP 探活与安装器等待。"""

    async def _health(_self, _info, _timeout):
        return healthy

    async def _installer(_self, _sid, _role, _timeout):
        return True

    monkeypatch.setattr(runtime.ARLRuntimeService, "_wait_for_health", _health)
    monkeypatch.setattr(runtime.ARLRuntimeService, "_wait_for_installer", _installer)


# ---- Docker 为权威状态 ----


@pytest.mark.asyncio
async def test_get_session_reads_docker_not_memory(env) -> None:
    """会话状态来自 Docker：换一个客户端视图，结果就随之变化。"""
    session_id = "s1"
    names = {role: runtime.container_name(session_id, role) for role in ARLRole}
    docker = _FakeDocker({n: _FakeContainer(n) for n in names.values()})
    _install_docker(env, docker)
    service = runtime.ARLRuntimeService()

    session = await service.get_session(session_id)
    assert session is not None
    assert session.status == ARLSessionStatus.READY

    # 容器消失后立刻反映出来——内存缓存做不到这件事
    _install_docker(env, _FakeDocker({}))
    assert await service.get_session(session_id) is None


@pytest.mark.asyncio
async def test_single_sided_containers_report_error(env) -> None:
    """只剩一个容器时判为 ERROR，交给 ensure_session 重建成完整的一对。"""
    session_id = "s2"
    worker = runtime.container_name(session_id, ARLRole.WORKER)
    _install_docker(env, _FakeDocker({worker: _FakeContainer(worker)}))
    service = runtime.ARLRuntimeService()

    session = await service.get_session(session_id)
    assert session is not None
    assert session.status == ARLSessionStatus.ERROR
    assert "validator" in (session.error_message or "")


# ---- 复用 / 拉起 / 重建 ----


@pytest.mark.asyncio
async def test_running_containers_with_same_config_are_reused(env) -> None:
    """配置没变且 daemon 健康时直接复用，不得重建。"""
    session_id = "s3"
    config = _config()
    fingerprint = runtime._config_fingerprint(config)
    containers = {
        runtime.container_name(session_id, role): _FakeContainer(
            runtime.container_name(session_id, role),
            labels={runtime.CONFIG_FINGERPRINT_LABEL: fingerprint},
        )
        for role in ARLRole
    }
    docker = _FakeDocker(containers)
    _install_docker(env, docker)
    _stub_ready(env)
    service = runtime.ARLRuntimeService()

    session = await service.ensure_session(
        session_id=session_id, user_id=str(uuid.uuid4()), daemon_config=config
    )

    assert session.status == ARLSessionStatus.READY
    assert docker.created == []
    assert not any(c.removed for c in containers.values())


@pytest.mark.asyncio
async def test_stopped_containers_are_started_in_place(env) -> None:
    """配置没变时已停止的容器原地拉起，保住 bind mount 里的 agent 会话。"""
    session_id = "s4"
    config = _config()
    fingerprint = runtime._config_fingerprint(config)
    containers = {
        runtime.container_name(session_id, role): _FakeContainer(
            runtime.container_name(session_id, role),
            status="exited",
            labels={runtime.CONFIG_FINGERPRINT_LABEL: fingerprint},
        )
        for role in ARLRole
    }
    docker = _FakeDocker(containers)
    _install_docker(env, docker)
    _stub_ready(env)
    service = runtime.ARLRuntimeService()

    await service.ensure_session(
        session_id=session_id, user_id=str(uuid.uuid4()), daemon_config=config
    )

    assert docker.created == []
    assert all(c.started for c in containers.values())
    assert not any(c.removed for c in containers.values())


@pytest.mark.asyncio
async def test_changed_credentials_force_recreate(env) -> None:
    """凭据变了必须删容器重建——环境变量在创建时固化，重启不会生效。"""
    session_id = "s5"
    containers = {
        runtime.container_name(session_id, role): _FakeContainer(
            runtime.container_name(session_id, role),
            labels={runtime.CONFIG_FINGERPRINT_LABEL: "stale-fingerprint"},
        )
        for role in ARLRole
    }
    docker = _FakeDocker(dict(containers))
    _install_docker(env, docker)
    _stub_ready(env)
    service = runtime.ARLRuntimeService()

    await service.ensure_session(
        session_id=session_id, user_id=str(uuid.uuid4()), daemon_config=_config()
    )

    assert all(c.removed for c in containers.values())
    assert len(docker.created) == 2


@pytest.mark.asyncio
async def test_missing_containers_are_created(env) -> None:
    """首次连接创建两个容器，并带上会话标签。"""
    session_id = "s6"
    docker = _FakeDocker({})
    _install_docker(env, docker)
    _stub_ready(env)
    service = runtime.ARLRuntimeService()
    user_id = str(uuid.uuid4())

    await service.ensure_session(
        session_id=session_id, user_id=user_id, daemon_config=_config(), folder_id="f1"
    )

    assert len(docker.created) == 2
    labels = docker.created[0]["labels"]
    assert labels[runtime.SESSION_LABEL] == session_id
    assert labels[runtime.USER_LABEL] == user_id
    assert labels[runtime.FOLDER_LABEL] == "f1"


@pytest.mark.asyncio
async def test_fresh_start_failure_cleans_up_but_reuse_failure_does_not(env) -> None:
    """冷启动失败才清理容器；复用路径失败不许删——那可能是别人正在用的容器。"""
    session_id = "s7"
    config = _config()
    fingerprint = runtime._config_fingerprint(config)
    _stub_ready(env, healthy=False)

    # 冷启动失败 → 清理
    docker = _FakeDocker({})
    _install_docker(env, docker)
    service = runtime.ARLRuntimeService()
    with pytest.raises(RuntimeError):
        await service.ensure_session(
            session_id=session_id, user_id=str(uuid.uuid4()), daemon_config=config
        )
    assert all(c.removed for c in docker._map.values())

    # 复用已有容器但探活失败 → 不清理（重建一次后仍失败也只删重建的那批）
    containers = {
        runtime.container_name(session_id, role): _FakeContainer(
            runtime.container_name(session_id, role),
            status="exited",
            labels={runtime.CONFIG_FINGERPRINT_LABEL: fingerprint},
        )
        for role in ARLRole
    }
    docker2 = _FakeDocker(containers)
    _install_docker(env, docker2)
    service2 = runtime.ARLRuntimeService()
    with pytest.raises(RuntimeError):
        await service2.ensure_session(
            session_id=session_id, user_id=str(uuid.uuid4()), daemon_config=config
        )
    assert not any(c.removed for c in containers.values())


@pytest.mark.asyncio
async def test_concurrent_ensure_serialises_and_reuses(env, monkeypatch) -> None:
    """同会话并发连接：编排串行执行，且只有第一个真正编排。

    三件事要同时成立：

    1. 容器只建一次（不会撞名）；
    2. 编排临界区不重入——并发进入会让两个进程同时 ``containers.run``；
    3. 后到者在持锁后的重判里走复用快路径，不再碰编排逻辑。第 3 条是
       「持锁后重新判定」的存在理由，去掉它这个断言就会失败。
    """
    session_id = "s8"
    docker = _FakeDocker({})
    _install_docker(env, docker)
    _stub_ready(env)
    service = runtime.ARLRuntimeService()

    reconcile_calls = 0
    in_flight = 0
    max_in_flight = 0
    original = runtime.ARLRuntimeService._reconcile_containers

    async def _tracked(self, **kwargs):
        nonlocal reconcile_calls, in_flight, max_in_flight
        reconcile_calls += 1
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            # 让出控制权，给其他协程进入临界区的机会（若锁失效就会被抓到）
            await asyncio.sleep(0)
            return await original(self, **kwargs)
        finally:
            in_flight -= 1

    monkeypatch.setattr(runtime.ARLRuntimeService, "_reconcile_containers", _tracked)

    await asyncio.gather(
        *(
            service.ensure_session(
                session_id=session_id,
                user_id=str(uuid.uuid4()),
                daemon_config=_config(),
            )
            for _ in range(4)
        )
    )

    assert len(docker.created) == 2  # 两个容器，各建一次
    assert max_in_flight == 1  # 临界区从未重入
    assert reconcile_calls == 1  # 后到者走复用快路径
    # 锁字典不泄漏
    assert service._locks == {}
    assert service._lock_refs == {}


# ---- 清理与回收 ----


@pytest.mark.asyncio
async def test_cleanup_removes_containers_and_revokes_tokens(env, monkeypatch) -> None:
    """删除会话时容器、活跃记录、代理 token 一起清掉。"""
    session_id = "s9"
    containers = {
        runtime.container_name(session_id, role): _FakeContainer(
            runtime.container_name(session_id, role)
        )
        for role in ARLRole
    }
    _install_docker(env, _FakeDocker(containers))
    revoked: list[str] = []
    forgotten: list[str] = []
    monkeypatch.setattr(runtime, "forget_arl_session_active", forgotten.append)

    def _revoke(sid: str) -> int:
        revoked.append(sid)
        return 1

    # cleanup_session 里是函数内 import，故要打在真实模块的属性上
    from app.services import credential_broker

    monkeypatch.setattr(credential_broker, "revoke_task_tokens", _revoke)
    service = runtime.ARLRuntimeService()

    await service.cleanup_session(session_id)

    assert all(c.removed for c in containers.values())
    assert forgotten == [session_id]
    assert revoked == [session_id]


@pytest.mark.asyncio
async def test_stop_session_stops_without_removing(env) -> None:
    """空闲回收只停容器不删：数据在 bind mount 里，重连原地恢复。"""
    session_id = "s10"
    containers = {
        runtime.container_name(session_id, role): _FakeContainer(
            runtime.container_name(session_id, role)
        )
        for role in ARLRole
    }
    _install_docker(env, _FakeDocker(containers))
    service = runtime.ARLRuntimeService()

    assert await service.stop_session(session_id) is True
    assert all(c.stopped for c in containers.values())
    assert not any(c.removed for c in containers.values())


def test_reconcile_removes_orphans_and_keeps_live_sessions(env, monkeypatch) -> None:
    """对账删掉数据库里已不存在的会话容器，保留存活会话的容器。"""
    live_id, orphan_id = "live", "orphan"
    containers = {}
    for sid in (live_id, orphan_id):
        for role in ARLRole:
            name = runtime.container_name(sid, role)
            containers[name] = _FakeContainer(
                name, labels={runtime.SESSION_LABEL: sid, runtime.ROLE_LABEL: role.value}
            )
    _install_docker(env, _FakeDocker(containers))
    monkeypatch.setattr(runtime, "_live_arl_session_ids", lambda: {live_id})
    touched: list[str] = []
    monkeypatch.setattr(
        runtime, "touch_arl_session_active", lambda sid, *a, **k: touched.append(sid)
    )

    removed = runtime.reconcile_arl_containers()

    assert removed == 1
    assert all(
        containers[runtime.container_name(orphan_id, role)].removed for role in ARLRole
    )
    assert not any(
        containers[runtime.container_name(live_id, role)].removed for role in ARLRole
    )
    # 运行中的存活会话要补活跃时间戳，否则空闲回收永远看不到它
    assert touched == [live_id]


def test_reconcile_keeps_everything_when_db_lookup_fails(env, monkeypatch) -> None:
    """查不到存活会话时宁可留下容器，也不误删正在用的。"""
    containers = {}
    for role in ARLRole:
        name = runtime.container_name("x", role)
        containers[name] = _FakeContainer(name, labels={runtime.SESSION_LABEL: "x"})
    _install_docker(env, _FakeDocker(containers))
    monkeypatch.setattr(runtime, "_live_arl_session_ids", lambda: None)
    monkeypatch.setattr(runtime, "touch_arl_session_active", lambda *a, **k: None)

    assert runtime.reconcile_arl_containers() == 0
    assert not any(c.removed for c in containers.values())
