"""ARL 容器运行时契约测试。

覆盖的是「容易悄悄回退」的那几条约定：容器资源限制必须用 Linux 生效的
字段、凭据不得明文进容器、配置变更要能被指纹识别出来、探活要走 backend
真实可达的路径、以及会话状态必须以 Docker 为准而不是内存缓存。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.arl import runtime
from app.services.arl.types import ARLContainerInfo, ARLDaemonConfig, ARLRole, ARLSessionStatus


def _daemon_config(**overrides) -> ARLDaemonConfig:
    """构造一份可用的 daemon 配置，便于按字段覆盖。"""
    base = {
        "base_url": "https://gateway.example.com",
        "auth_token": "real-anthropic-token",
        "model": "claude-opus-5",
        "provider_type": "anthropic",
        "validator_base_url": "https://gateway.example.com/v1",
        "validator_api_key": "real-openai-key",
        "validator_model": "gpt-5.5",
        "validator_provider_type": "openai",
    }
    base.update(overrides)
    return ARLDaemonConfig(**base)


def _session(session_id: str = "abc123") -> runtime.ARLSession:
    """构造一个 STARTING 态的会话快照。"""
    return runtime.ARLSession(
        session_id=session_id,
        folder_id=None,
        user_id=str(uuid.uuid4()),
        status=ARLSessionStatus.STARTING,
        worker=runtime._placeholder_info(session_id, ARLRole.WORKER),
        validator=runtime._placeholder_info(session_id, ARLRole.VALIDATOR),
        created_at=runtime.datetime.now(runtime.UTC),
    )


# ---- 容器创建参数 ----


class _Containers:
    """捕获 containers.run 的调用参数。"""

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="c" * 12, status="running")

    def get(self, _name):
        from docker.errors import NotFound

        raise NotFound("absent")


@pytest.fixture
def captured_run(monkeypatch, tmp_path):
    """让 _start_container 跑在假 Docker 上，返回捕获器。"""
    containers = _Containers()
    client = SimpleNamespace(
        containers=containers,
        images=SimpleNamespace(get=lambda _ref: SimpleNamespace(id="image-id")),
    )
    monkeypatch.setattr(runtime, "get_docker_client", lambda: client)
    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", str(tmp_path))
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    return containers


@pytest.mark.asyncio
async def test_container_cpu_limit_uses_nano_cpus(captured_run, monkeypatch) -> None:
    """CPU 限制必须用 nano_cpus：cpu_count 只在 Windows 生效，Linux 上静默失效。"""
    monkeypatch.setattr(runtime, "use_container_network", lambda: True)
    service = runtime.ARLRuntimeService()
    config = _daemon_config()

    await service._start_container(
        role=ARLRole.WORKER,
        session=_session(),
        daemon_config=config,
        fingerprint="fp",
    )

    kwargs = captured_run.kwargs
    assert kwargs is not None
    assert "cpu_count" not in kwargs
    assert kwargs["nano_cpus"] == int(settings.ARL_CONTAINER_CPU_LIMIT * 1e9)
    assert kwargs["mem_limit"] == settings.ARL_CONTAINER_MEMORY_LIMIT
    # 宿主 Docker 重启后自动恢复，省掉一次冷启动
    assert kwargs["restart_policy"] == {"Name": "unless-stopped"}


@pytest.mark.asyncio
async def test_container_network_mode_publishes_no_port(captured_run, monkeypatch) -> None:
    """backend 在容器内时走容器名 DNS，不该发布宿主端口。"""
    monkeypatch.setattr(runtime, "use_container_network", lambda: True)
    service = runtime.ARLRuntimeService()

    await service._start_container(
        role=ARLRole.WORKER,
        session=_session(),
        daemon_config=_daemon_config(),
        fingerprint="fp",
    )

    assert "ports" not in captured_run.kwargs
    assert captured_run.kwargs["network"] == settings.arl_network_name


@pytest.mark.asyncio
async def test_host_mode_publishes_adjacent_ports(captured_run, monkeypatch) -> None:
    """宿主机模式下 worker/validator 占相邻端口，同会话内不自撞。"""
    monkeypatch.setattr(runtime, "use_container_network", lambda: False)
    service = runtime.ARLRuntimeService()
    session = _session("deadbeef")

    ports = {}
    for role in ARLRole:
        await service._start_container(
            role=role,
            session=session,
            daemon_config=_daemon_config(),
            fingerprint="fp",
        )
        binding = captured_run.kwargs["ports"][f"{settings.ARL_PASEO_PORT}/tcp"]
        assert binding[0] == "127.0.0.1"
        ports[role] = binding[1]

    assert ports[ARLRole.VALIDATOR] == ports[ARLRole.WORKER] + 1


# ---- 凭据不进容器 ----


@pytest.mark.asyncio
async def test_proxy_enabled_keeps_real_credentials_out_of_container(
    captured_run, monkeypatch
) -> None:
    """开启代理后容器里只有不透明 token，真实 key 不出现在环境变量里。"""
    monkeypatch.setattr(runtime, "use_container_network", lambda: True)
    monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", True)
    monkeypatch.setattr(
        settings, "LLM_PROXY_BASE_URL", "http://backend:8000/api/v1/llm4ad/llmproxy"
    )
    issued: list[dict] = []

    def _fake_issue(**kwargs):
        issued.append(kwargs)
        return f"proxy-token-{len(issued)}"

    monkeypatch.setattr(runtime, "_issue_proxy_credential", _fake_issue)
    service = runtime.ARLRuntimeService()
    config = _daemon_config()

    await service._start_container(
        role=ARLRole.WORKER,
        session=_session(),
        daemon_config=config,
        fingerprint="fp",
    )
    env = captured_run.kwargs["environment"]

    assert env["ANTHROPIC_AUTH_TOKEN"] == "proxy-token-1"
    assert config.auth_token not in env.values()
    assert env["ANTHROPIC_BASE_URL"] == "http://backend:8000/api/v1/llm4ad/llmproxy"
    # 真实上游地址与凭据交给 broker 保管
    assert issued[0]["base_url"] == "https://gateway.example.com"
    assert issued[0]["api_key"] == "real-anthropic-token"


@pytest.mark.asyncio
async def test_proxy_disabled_passes_credentials_through(captured_run, monkeypatch) -> None:
    """代理关闭时沿用直连，保持与历史行为一致。"""
    monkeypatch.setattr(runtime, "use_container_network", lambda: True)
    monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", False)
    service = runtime.ARLRuntimeService()

    await service._start_container(
        role=ARLRole.VALIDATOR,
        session=_session(),
        daemon_config=_daemon_config(),
        fingerprint="fp",
    )
    env = captured_run.kwargs["environment"]

    assert env["OPENAI_API_KEY"] == "real-openai-key"
    # Codex CLI 要求 base URL 显式带 /v1
    assert env["OPENAI_BASE_URL"] == "https://gateway.example.com/v1"


def test_worker_base_url_drops_v1_suffix() -> None:
    """Claude CLI 自行拼 /v1/messages，base URL 不能带 /v1。"""
    assert (
        runtime._normalize_base_url("https://x.example.com/v1", ensure_v1=False)
        == "https://x.example.com"
    )
    assert (
        runtime._normalize_base_url("https://x.example.com", ensure_v1=True)
        == "https://x.example.com/v1"
    )


# ---- 配置指纹 ----


def test_fingerprint_changes_when_credentials_change() -> None:
    """换了 key / 模型 / base_url，指纹必须变——否则重启容器也用不上新配置。"""
    base = runtime._config_fingerprint(_daemon_config())

    assert runtime._config_fingerprint(_daemon_config(auth_token="other")) != base
    assert runtime._config_fingerprint(_daemon_config(model="claude-sonnet-5")) != base
    assert (
        runtime._config_fingerprint(_daemon_config(base_url="https://other.example.com"))
        != base
    )
    assert (
        runtime._config_fingerprint(_daemon_config(validator_api_key="other")) != base
    )


def test_fingerprint_is_stable_for_equivalent_config() -> None:
    """同一份配置（含 /v1 后缀差异）指纹不变，避免每次连接都重建容器。"""
    first = runtime._config_fingerprint(_daemon_config())
    second = runtime._config_fingerprint(
        _daemon_config(base_url="https://gateway.example.com/")
    )
    assert first == second


def test_fingerprint_does_not_leak_credentials() -> None:
    """指纹要写进容器标签，不能含凭据明文。"""
    fingerprint = runtime._config_fingerprint(_daemon_config())
    assert "real-anthropic-token" not in fingerprint
    assert "real-openai-key" not in fingerprint


def test_fingerprint_tracks_proxy_toggle(monkeypatch) -> None:
    """代理开关会改变容器里的 base_url 指向，必须入指纹。"""
    monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", False)
    direct = runtime._config_fingerprint(_daemon_config())
    monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", True)
    proxied = runtime._config_fingerprint(_daemon_config())
    assert direct != proxied


# ---- 连接地址 ----


def test_daemon_endpoints_match_the_path_backend_actually_uses(monkeypatch) -> None:
    """探活与 WS 必须共用同一套地址，否则会「探通了但连不上」。"""
    info = ARLContainerInfo(
        name="llm4ad-arl-w-abc",
        port=16042,
        password="pw",
        status=ARLSessionStatus.READY,
    )

    monkeypatch.setattr(runtime, "use_container_network", lambda: True)
    http_base, ws_url = runtime.daemon_endpoints(info)
    assert http_base == f"http://llm4ad-arl-w-abc:{settings.ARL_PASEO_PORT}"
    assert ws_url == f"ws://llm4ad-arl-w-abc:{settings.ARL_PASEO_PORT}/ws"

    monkeypatch.setattr(runtime, "use_container_network", lambda: False)
    http_base, ws_url = runtime.daemon_endpoints(info)
    assert http_base == "http://127.0.0.1:16042"
    assert ws_url == "ws://127.0.0.1:16042/ws"


def test_daemon_endpoints_rejects_unpublished_port_in_host_mode(monkeypatch) -> None:
    """宿主机模式遇到没发布端口的容器要明确报错，而不是拼出坏地址。"""
    monkeypatch.setattr(runtime, "use_container_network", lambda: False)
    info = ARLContainerInfo(
        name="llm4ad-arl-w-abc", port=None, password="pw", status=ARLSessionStatus.READY
    )
    with pytest.raises(RuntimeError, match="未发布宿主机端口"):
        runtime.daemon_endpoints(info)


# ---- 确定性派生 ----


def test_container_name_and_password_are_deterministic() -> None:
    """同一会话每次都算出同一个容器名与密码，无需持久化。"""
    session_id = str(uuid.uuid4())
    assert runtime.container_name(session_id, ARLRole.WORKER) == runtime.container_name(
        session_id, ARLRole.WORKER
    )
    assert runtime.derive_password(session_id, ARLRole.WORKER) == runtime.derive_password(
        session_id, ARLRole.WORKER
    )
    # 两个角色的密码必须不同
    assert runtime.derive_password(session_id, ARLRole.WORKER) != runtime.derive_password(
        session_id, ARLRole.VALIDATOR
    )


def test_port_allocation_tolerates_non_hex_session_ids() -> None:
    """端口派生不能假设 ID 尾部是合法十六进制。"""
    port = runtime._allocate_port("not-hex-at-all-zzzz", ARLRole.WORKER)
    assert runtime._PORT_BASE <= port < runtime._PORT_BASE + runtime._PORT_SLOTS * 2
