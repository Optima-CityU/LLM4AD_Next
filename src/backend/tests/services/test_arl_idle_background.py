"""ARL 空闲回收：后台进程保活与代理 token 续期。

agent 用 ``nohup`` 起训练后回合结束、agent 回到 idle，但训练仍在跑。
回收前要从进程表里认出这类后台任务；常驻进程（daemon、agent 本体、
空 shell、验证服务）不能算，否则容器永远回收不掉。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from app.core.config import settings
from app.services import credential_broker
from app.services.arl import runtime
from app.services.arl.types import ARLRole

# 取自真实 ARL 容器 ``docker top`` 的命令行
RESIDENT = [
    "/usr/bin/tini -- /usr/local/bin/arl-role-entrypoint",
    "Paseo Supervisor",
    "Paseo Daemon",
    "/usr/local/bin/node /usr/local/lib/node_modules/@getpaseo/server/dist/server/terminal/terminal-worker-process.js",
    "/usr/local/lib/node_modules/@getpaseo/server/node_modules/@esbuild/linux-x64/bin/esbuild --service=0.25.12 --ping",
    "/usr/local/bin/claude --output-format stream-json --verbose --input-format stream-json --model glm-5.3-flash",
    "node /usr/local/bin/codex app-server --enable goals",
    "/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex app-server",
    "node .aris/dist/tools/validation-cli.js serve --project .",
    "/bin/sh",
    "bash",
    "[python] <defunct>",
    "/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex-code-mode-host",
    "/bin/sh -c node -e \"const listen=process.env.PASEO_LISTEN||'0.0.0.0:6767'; "
    "require('http').get({hostname:'127.0.0.1',port,path:'/api/health'},r=>process.exit(0))\"",
    # 进程名不区分大小写
    "paseo daemon",
    "/usr/local/bin/Claude --print",
    "NODE /opt/Terminal-Worker-Process.js",
    "BASH",
    # 只看不干活的交互工具：留在终端里不应让容器永久保活
    "top",
    "tail -f logs/train.log",
    "watch -n 5 nvidia-smi",
    "vim train.py",
]

BACKGROUND = [
    "node train.js",
    "node scripts/validation-cli-wrapper.js",
    "sh -c python train.py",
    "bash run.sh",
    "python train.py --epochs 100",
    "nohup python -u run_experiment.py",
    "/bin/bash -c ./run_all.sh",
    "torchrun --nproc_per_node=1 train.py",
]


@pytest.mark.parametrize("command", RESIDENT)
def test_resident_processes_are_not_background_work(command: str) -> None:
    assert runtime._is_resident_process(command) is True


@pytest.mark.parametrize("command", BACKGROUND)
def test_agent_started_jobs_are_background_work(command: str) -> None:
    assert runtime._is_resident_process(command) is False


class _TopContainer:
    def __init__(self, commands: list[str], status: str = "running"):
        self.status = status
        self._commands = commands

    def reload(self):
        pass

    def top(self):
        return {
            "Titles": ["UID", "PID", "PPID", "C", "STIME", "TTY", "TIME", "CMD"],
            "Processes": [
                ["paseo", str(i), "1", "0", "06:00", "?", "00:00:00", cmd]
                for i, cmd in enumerate(self._commands)
            ],
        }


def _install(monkeypatch, containers: dict[str, _TopContainer]) -> None:
    def _get(name):
        if name not in containers:
            raise NotFound(name)
        return containers[name]

    client = SimpleNamespace(containers=SimpleNamespace(get=_get))
    monkeypatch.setattr(runtime, "get_docker_client", lambda: client)


def test_background_processes_scans_both_roles(monkeypatch) -> None:
    sid = "s1"
    _install(
        monkeypatch,
        {
            runtime.container_name(sid, ARLRole.WORKER): _TopContainer(
                RESIDENT[:6] + ["python train.py"]
            ),
            runtime.container_name(sid, ARLRole.VALIDATOR): _TopContainer(RESIDENT),
        },
    )
    assert runtime._background_processes_sync(sid) == ["python train.py"]


def test_idle_or_stopped_containers_have_no_background(monkeypatch) -> None:
    sid = "s2"
    _install(
        monkeypatch,
        {
            runtime.container_name(sid, ARLRole.WORKER): _TopContainer(RESIDENT),
            # 已停止的容器不读进程表
            runtime.container_name(sid, ARLRole.VALIDATOR): _TopContainer(
                ["python train.py"], status="exited"
            ),
        },
    )
    assert runtime._background_processes_sync(sid) == []


def test_missing_containers_have_no_background(monkeypatch) -> None:
    _install(monkeypatch, {})
    assert runtime._background_processes_sync("gone") == []


# ---- 回收流程 ----


@pytest.fixture
def idle_env(monkeypatch):
    """一个已超过空闲阈值、agent 不在跑回合的会话。"""
    session = SimpleNamespace(session_id="s9", user_id="u1")
    touched: list[str] = []
    stopped: list[str] = []
    extended: list[tuple] = []

    async def _get_session(_self, _sid):
        return session

    async def _no_turn(_session):
        return False

    async def _stop(_runtime, sid):
        stopped.append(sid)
        return True

    monkeypatch.setattr(runtime, "pop_idle_arl_sessions", lambda _ts: ["s9"])
    # s9 已超阈值；s8 还在活跃，只打「未到阈值」日志
    monkeypatch.setattr(
        runtime,
        "list_arl_session_last_active",
        lambda: {"s9": 0.0, "s8": runtime.time.time()},
    )
    monkeypatch.setattr(runtime.ARLRuntimeService, "get_session", _get_session)
    monkeypatch.setattr(runtime, "_has_running_turn", _no_turn)
    monkeypatch.setattr(runtime, "_stop_if_still_idle", _stop)
    monkeypatch.setattr(runtime, "touch_arl_session_active", touched.append)
    monkeypatch.setattr(
        credential_broker,
        "extend_task_tokens",
        lambda sid, ttl, user_id=None: extended.append((sid, ttl, user_id)) or 2,
    )
    monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", True)
    return SimpleNamespace(
        monkeypatch=monkeypatch, touched=touched, stopped=stopped, extended=extended
    )


async def test_background_job_keeps_session_and_extends_tokens(idle_env) -> None:
    idle_env.monkeypatch.setattr(
        runtime, "_background_processes_sync", lambda _sid: ["python train.py"]
    )

    assert await runtime.stop_idle_arl_sessions() == 0
    assert idle_env.stopped == []
    assert idle_env.touched == ["s9"]
    assert idle_env.extended == [("s9", settings.LLM_PROXY_TOKEN_TTL, "u1")]


async def test_no_turn_and_no_background_is_stopped(idle_env) -> None:
    idle_env.monkeypatch.setattr(runtime, "_background_processes_sync", lambda _sid: [])

    assert await runtime.stop_idle_arl_sessions() == 1
    assert idle_env.stopped == ["s9"]
    assert idle_env.extended == []


async def test_running_turn_keeps_session_without_scanning(idle_env) -> None:
    async def _turn(_session):
        return True

    def _scan(_sid):
        raise AssertionError("process scan should be skipped")

    idle_env.monkeypatch.setattr(runtime, "_has_running_turn", _turn)
    idle_env.monkeypatch.setattr(runtime, "_background_processes_sync", _scan)

    assert await runtime.stop_idle_arl_sessions() == 0
    assert idle_env.touched == ["s9"]
    assert len(idle_env.extended) == 1


async def test_proxy_disabled_only_touches(idle_env) -> None:
    idle_env.monkeypatch.setattr(settings, "LLM_PROXY_ENABLE", False)
    idle_env.monkeypatch.setattr(
        runtime, "_background_processes_sync", lambda _sid: ["python train.py"]
    )

    assert await runtime.stop_idle_arl_sessions() == 0
    assert idle_env.touched == ["s9"]
    assert idle_env.extended == []


# ---- token 续期 ----


class _FakePipe:
    def __init__(self, store: dict):
        self.store = store
        self.ops: list[tuple] = []

    def expire(self, key, ttl):
        self.ops.append((key, ttl))

    def execute(self):
        return [key in self.store for key, _ in self.ops]


class _FakeRedis:
    def __init__(self, store: dict, members: set):
        self.store = store
        self.members = members
        self.pipe = _FakePipe(store)

    def smembers(self, _key):
        return self.members

    def pipeline(self):
        return self.pipe


def test_extend_task_tokens_refreshes_existing_only(monkeypatch) -> None:
    store = {"llmproxy:token:a": b"x", "llmproxy:task:s1": b"set", "llmproxy:user:u1": b"set"}
    fake = _FakeRedis(store, {"a", "b"})  # b 已过期
    monkeypatch.setattr(credential_broker, "get_sync_redis", lambda: fake)

    assert credential_broker.extend_task_tokens("s1", 100, user_id="u1") == 1
    keys = {key for key, _ in fake.pipe.ops}
    assert keys == {
        "llmproxy:token:a",
        "llmproxy:token:b",
        "llmproxy:task:s1",
        "llmproxy:user:u1",
    }
    assert all(ttl == 100 for _, ttl in fake.pipe.ops)


def test_extend_task_tokens_without_tokens_is_noop(monkeypatch) -> None:
    fake = _FakeRedis({}, set())
    monkeypatch.setattr(credential_broker, "get_sync_redis", lambda: fake)
    assert credential_broker.extend_task_tokens("s1", 100) == 0
    assert fake.pipe.ops == []
