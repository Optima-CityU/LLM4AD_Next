"""
ARL 运行时服务：容器生命周期管理。

设计要点——**Docker 是权威状态，本模块不持有会话缓存**：

- 容器名、daemon 密码都按 ``session_id`` 确定性派生，宿主端口可从容器
  attrs 读回，归属与配置在 ``research_session`` 表里。也就是说一个 ARL 会话
  的全部状态都能从 Docker + DB 重建，内存里再存一份只会引入不一致——多
  uvicorn worker 下尤其危险：某个 worker 的失败清理会强删另一个 worker
  正在用的容器。故 :meth:`ARLRuntimeService.get_session` 每次查 Docker。
- 编排路径（启动/重启/重建）用 Redis 锁跨进程串行化，见
  :func:`app.core.redis.acquire_arl_session_lock`。
- docker-py 是同步客户端，所有调用都经 ``asyncio.to_thread`` 下沉，避免
  阻塞 event loop（health 轮询 + 镜像拉取动辄数十秒）。

职责：
- 按需启动/恢复一个会话的两个容器（worker + validator）
- 等待 daemon 就绪（HTTP ``/api/health``，走 backend 真实可达路径）与
  安装器完成（``.aris/install.json``）
- 凭据变更 / 镜像更新时重建容器
- 空闲回收、启动期对账、会话删除时清理
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import os
import posixpath
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
from docker.client import DockerClient
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container
from loguru import logger

from app.core.config import settings
from app.core.docker import get_docker_client
from app.core.env_detect import get_project_home, use_container_network
from app.core.redis import (
    acquire_arl_session_lock,
    forget_arl_session_active,
    get_arl_session_last_active,
    list_arl_session_last_active,
    pop_idle_arl_sessions,
    release_arl_session_lock,
    touch_arl_session_active,
)
from app.services.arl.types import (
    ARLContainerInfo,
    ARLDaemonConfig,
    ARLRole,
    ARLSession,
    ARLSessionStatus,
)

# 会话标签，用于 Docker 容器过滤
SESSION_LABEL = "llm4ad.arl.session_id"
ROLE_LABEL = "llm4ad.arl.role"
USER_LABEL = "llm4ad.arl.user_id"
FOLDER_LABEL = "llm4ad.arl.folder_id"
#: 凭据/模型指纹标签。容器环境变量在创建时固化，用户事后改了 provider、
#: base_url 或模型，光重启容器是不会生效的——必须重建。把指纹记在标签上，
#: 下次连接时比对即可判断「配置变了」。
CONFIG_FINGERPRINT_LABEL = "llm4ad.arl.config_fingerprint"
#: 容器名前缀，启动期对账按它批量捞容器
CONTAINER_NAME_PREFIX = "llm4ad-arl-"

#: Anthropic 与 OpenAI 对 base URL 的 /v1 后缀要求相反
V1_SUFFIX = "/v1"

#: 宿主机模式下分配 daemon 端口的区间。仅本地调试用：backend 在容器内时
#: 走容器名 DNS，根本不发布端口。区间 5000 个槽位、每会话占 2 个相邻槽，
#: 同槽碰撞概率极低，不值得为此维护端口池。
_PORT_BASE = 16000
_PORT_SLOTS = 5000

#: 探活轮询间隔（秒）
_PROBE_INTERVAL = 1.0
#: 容器异常退出时附带的日志行数
_CRASH_LOG_TAIL = 50


def _normalize_base_url(url: str | None, *, ensure_v1: bool) -> str | None:
    """规范化 API base URL 的 ``/v1`` 后缀。

    Claude CLI（Anthropic SDK）会自行拼接 ``/v1/messages``，因此
    ``ANTHROPIC_BASE_URL`` 不能带 ``/v1``；Codex CLI 则要求
    ``OPENAI_BASE_URL`` 显式带 ``/v1``。

    Args:
        url: 原始 base URL，None 或空串表示未配置
        ensure_v1: True 表示补全 ``/v1`` 后缀，False 表示去掉 ``/v1`` 后缀

    Returns:
        规范化后的 URL；输入为空时原样返回
    """
    if not url:
        return url
    trimmed = url.rstrip("/")
    if ensure_v1:
        return trimmed if trimmed.endswith(V1_SUFFIX) else f"{trimmed}{V1_SUFFIX}"
    return trimmed[: -len(V1_SUFFIX)] if trimmed.endswith(V1_SUFFIX) else trimmed


def container_name(session_id: str, role: ARLRole) -> str:
    """生成容器名称。

    格式: ``llm4ad-arl-{w|v}-{session_id}``。确定性派生，是「同一会话复用
    同一容器」的基础。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        容器名
    """
    abbrev = "w" if role == ARLRole.WORKER else "v"
    return f"{CONTAINER_NAME_PREFIX}{abbrev}-{session_id}"


def derive_password(session_id: str, role: ARLRole) -> str:
    """派生每会话每角色的 Paseo daemon 密码（HMAC-SHA256，十六进制）。

    确定性派生而非随机生成：容器重启、backend 重启、换 worker 进程后都能
    算出同一个密码，无需持久化。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        派生的密码（64 字符十六进制）
    """
    message = f"{session_id}:{role.value}".encode()
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, message, digestmod="sha256").hexdigest()


def _allocate_port(session_id: str, role: ARLRole) -> int:
    """为容器分配宿主机端口（仅宿主机模式使用）。

    按 session_id 的哈希取槽位，worker/validator 固定占相邻的偶/奇端口，
    因此同一会话内不会自撞；不同会话只有落到同一槽位才冲突。

    用哈希而非 ``int(session_id[-4:], 16)``：后者假设 ID 尾部是合法十六进制，
    换一种 ID 格式就会 ValueError。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        宿主机端口
    """
    digest = hashlib.sha256(session_id.encode()).digest()
    slot = int.from_bytes(digest[:4], "big") % _PORT_SLOTS
    offset = 0 if role == ARLRole.WORKER else 1
    return _PORT_BASE + slot * 2 + offset


def daemon_endpoints(info: ARLContainerInfo) -> tuple[str, str]:
    """返回 daemon 的 ``(http_base, ws_url)``。

    backend 在容器内时走 Docker 网络 + 容器名 DNS；裸跑时走宿主机映射端口。
    探活与 WS 连接必须用同一套地址，否则探的不是实际要走的路径——容器内
    loopback 通、端口映射没生效这类故障就探不出来。

    Args:
        info: 容器信息

    Returns:
        ``(http_base, ws_url)``，如 ``("http://llm4ad-arl-w-x:6767", "ws://.../ws")``

    Raises:
        RuntimeError: 宿主机模式下容器未发布端口（无法连接）
    """
    if use_container_network():
        host = f"{info.name}:{settings.ARL_PASEO_PORT}"
    else:
        if not info.port:
            raise RuntimeError(
                f"容器 {info.name} 未发布宿主机端口，宿主机模式下无法连接；"
                "请删除该容器后重连以按当前模式重建"
            )
        host = f"127.0.0.1:{info.port}"
    return f"http://{host}", f"ws://{host}/ws"


def _config_fingerprint(daemon_config: ARLDaemonConfig) -> str:
    """计算凭据/模型配置指纹，用于判断容器是否需要重建。

    只覆盖**会固化进容器环境变量**的字段。密钥本身不入指纹明文——取其
    HMAC 后再参与哈希，故标签里不会泄漏任何凭据信息。

    对**真实凭据**取指纹：代理 token 由会话 ID 派生、不随配置变化，拿它
    算指纹识别不出换 key。

    Args:
        daemon_config: 真实凭据配置

    Returns:
        16 字符十六进制指纹
    """
    key = settings.SECRET_KEY.encode("utf-8")

    def _mask(value: str | None) -> str:
        """对敏感值取 HMAC，使其可比对但不可还原。"""
        if not value:
            return ""
        return hmac.new(key, value.encode("utf-8"), digestmod="sha256").hexdigest()[:16]

    parts = [
        _normalize_base_url(daemon_config.base_url, ensure_v1=False) or "",
        _mask(daemon_config.auth_token),
        daemon_config.model or "",
        daemon_config.small_fast_model or "",
        daemon_config.provider_type or "",
        _normalize_base_url(daemon_config.validator_base_url, ensure_v1=True) or "",
        _mask(daemon_config.validator_api_key),
        daemon_config.validator_model or "",
        daemon_config.validator_provider_type or "",
        # 代理开关也要入指纹：开/关会改变容器里的 base_url 指向。
        # 带版本号：早期容器里是随机 token，需重建一次换成确定性 token
        "proxy-v2" if settings.LLM_PROXY_ENABLE else "direct",
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def _session_root(session_id: str) -> tuple[str, str]:
    """返回会话挂载根目录的 ``(docker_path, host_path)``。

    ``docker_path`` 是 backend 进程自己可读写的路径（用于 mkdir / 清理 /
    探测 install.json），``host_path`` 是传给 ``volumes`` 的宿主机路径
    （bind 挂载源由宿主 Docker daemon 解析，不在 backend 的命名空间里）。
    backend 裸跑时两者相同。

    Args:
        session_id: 会话 ID

    Returns:
        ``(docker_path, host_path)``
    """
    from app.services.container_service import resolve_host_path

    base = get_project_home(
        host_home=settings.HOST_PROJECT_HOME,
        docker_home=settings.DOCKER_PROJECT_HOME,
    )
    docker_path = f"{base}/arl/{session_id}"
    return docker_path, resolve_host_path(docker_path)


def _prepare_host_directories(session_id: str, role: ARLRole) -> tuple[str, str]:
    """准备挂载目录，返回角色根目录的 ``(docker_path, host_path)``。

    创建 ``<project_home>/arl/<session_id>/<role>/{paseo-home,workspace}``。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        ``(docker_path, host_path)``
    """
    docker_root, host_root = _session_root(session_id)
    docker_base = Path(docker_root) / role.value
    (docker_base / "paseo-home").mkdir(parents=True, exist_ok=True)
    (docker_base / "workspace").mkdir(parents=True, exist_ok=True)
    return str(docker_base), str(Path(host_root) / role.value)


def copy_session_workspace(source_session_id: str, target_session_id: str) -> None:
    """Copy each role's ``workspace`` from one ARL session to another.

    Only ``workspace`` is copied; ``paseo-home`` (agent sessions and chat
    history) is left out, so the copy starts with fresh agents. The install
    stamp lives inside ``workspace``, which lets the copy's containers skip
    the installer. A running source may yield partially written files.

    Symlinks are copied as links: the workspace is agent-written, and
    following links could pull in files from outside it.

    Args:
        source_session_id: Session to copy from.
        target_session_id: Newly created session to copy into.

    Raises:
        OSError: When copying fails (callers treat this as best-effort).
    """
    source_root, _ = _session_root(source_session_id)
    target_root, _ = _session_root(target_session_id)
    for role in ARLRole:
        src = Path(source_root) / role.value / "workspace"
        dst = Path(target_root) / role.value / "workspace"
        if src.is_dir() and not dst.exists():
            shutil.copytree(src, dst, symlinks=True)


def _install_stamp_path(session_id: str, role: ARLRole) -> str:
    """返回安装完成标记文件在 backend 侧可见的路径。

    容器把 ARL 角色装进 ``/workspace/<role>``，而 ``/workspace`` 挂的是
    ``<role>/workspace``，故标记文件落在
    ``<role>/workspace/<role>/.aris/install.json``。因为是 bind mount，
    backend 直接 stat 文件即可，无需 ``docker exec``（省掉每轮在容器里起
    一个进程的开销）。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        install.json 的 backend 侧绝对路径
    """
    docker_root, _ = _session_root(session_id)
    return str(
        Path(docker_root) / role.value / "workspace" / role.value / ".aris" / "install.json"
    )


def _host_port(container: Container) -> int | None:
    """从容器 attrs 读回已发布的宿主机端口。

    容器网络模式下不发布端口，此时返回 None（不是错误）。

    Args:
        container: 容器对象

    Returns:
        宿主机端口，未发布时为 None
    """
    try:
        bindings = (
            container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        ).get(f"{settings.ARL_PASEO_PORT}/tcp")
        if not bindings:
            return None
        return int(bindings[0]["HostPort"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _created_at(container: Container | None) -> datetime:
    """读取容器创建时间，解析失败时回落到当前时间。

    Args:
        container: 容器对象，可为 None

    Returns:
        带时区的创建时间
    """
    if container is None:
        return datetime.now(UTC)
    raw = container.attrs.get("Created") or ""
    try:
        # Docker 返回纳秒精度 ISO8601（如 2024-01-01T00:00:00.123456789Z），
        # fromisoformat 在 3.12 下不接受 9 位小数，截到微秒。
        normalized = raw.replace("Z", "+00:00")
        if "." in normalized:
            head, _, tail = normalized.partition(".")
            frac, sign, offset = (
                tail.partition("+") if "+" in tail else tail.partition("-")
            )
            normalized = f"{head}.{frac[:6]}{sign}{offset}"
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(UTC)


def _placeholder_info(session_id: str, role: ARLRole) -> ARLContainerInfo:
    """为缺失的容器构造占位信息（状态 STOPPED）。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        占位的容器信息
    """
    return ARLContainerInfo(
        name=container_name(session_id, role),
        port=None,
        password=derive_password(session_id, role),
        status=ARLSessionStatus.STOPPED,
    )


def derive_proxy_token(session_id: str, role: ARLRole) -> str:
    """派生每会话每角色的 LLM 代理 token（HMAC-SHA256，十六进制）。

    ARL 会话长期保留，随机 token 一旦在 Redis 里过期，固化进容器环境变量的
    旧值就再也用不了。改为确定性派生后容器里的 token 永远不变，backend 每次
    连接时用同一个值重新登记（见 :func:`_register_proxy_credentials`）即可
    续期，无需重建容器。

    消息带 ``llm-proxy:`` 前缀，与 :func:`derive_password` 的结果互不相同。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        派生的代理 token（64 字符十六进制）
    """
    message = f"llm-proxy:{session_id}:{role.value}".encode()
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, message, digestmod="sha256").hexdigest()


def _upstream_credential(role: ARLRole, daemon_config: ARLDaemonConfig) -> dict[str, str]:
    """取某角色的真实上游凭据，base_url 已按对应 CLI 的要求规范化。

    Args:
        role: 容器角色
        daemon_config: 真实凭据配置

    Returns:
        含 provider_type / base_url / api_key / auth_token / model 的字典
    """
    if role == ARLRole.WORKER:
        # Claude CLI 自行拼 /v1/messages，故 base URL 不带 /v1；provider 可能
        # 只配了 api_key / auth_token 其中之一，两者都递交由代理侧按类型取用
        token = daemon_config.auth_token or ""
        return {
            "provider_type": daemon_config.provider_type,
            "base_url": _normalize_base_url(daemon_config.base_url, ensure_v1=False) or "",
            "api_key": token,
            "auth_token": token,
            "model": daemon_config.model or "",
        }
    # Codex CLI 要求 OPENAI_BASE_URL 显式带 /v1
    return {
        "provider_type": daemon_config.validator_provider_type,
        "base_url": _normalize_base_url(
            daemon_config.validator_base_url or "https://api.openai.com/v1",
            ensure_v1=True,
        )
        or "",
        "api_key": daemon_config.validator_api_key or "",
        "auth_token": "",
        "model": daemon_config.validator_model or "",
    }


def _register_proxy_credentials(
    session_id: str, user_id: str, daemon_config: ARLDaemonConfig
) -> None:
    """用确定性 token 把两个角色的真实凭据（重新）登记进 credential broker。

    ARL 容器里跑的是默认「不逐条确认」模式的 agent，可执行任意命令；凭据
    一旦以环境变量进容器，``docker inspect``、容器内任意进程、agent 自己
    都能读到。开启代理后容器只持有 :func:`derive_proxy_token` 的不透明值，
    base_url 指向 :mod:`app.api.llm4ad.llm_proxy`，真实 key 留在 backend。

    每次 :meth:`ARLRuntimeService.ensure_session` 都会调用：会话闲置到 Redis
    记录过期、或 Redis 重启丢数据后，下次连接即自动恢复；同时也用最新的
    供应商配置覆盖旧记录。token 以 ``session_id`` 归集，删除会话时批量吊销。

    Args:
        session_id: 会话 ID，作为 token 的归集键
        user_id: 归属用户 ID（审计用）
        daemon_config: 真实凭据配置
    """
    from app.services import credential_broker

    for role in ARLRole:
        credential_broker.issue_token(
            user_id=user_id,
            task_id=session_id,
            ttl=settings.LLM_PROXY_TOKEN_TTL,
            token=derive_proxy_token(session_id, role),
            **_upstream_credential(role, daemon_config),
        )


def _build_container_env(
    session: ARLSession,
    role: ARLRole,
    daemon_config: ARLDaemonConfig,
    password: str,
) -> dict[str, str]:
    """组装单个 ARL 容器的环境变量。

    ``LLM_PROXY_ENABLE`` 为真时，容器里只放确定性代理 token 并将 base_url
    指向代理端点（见 :func:`_register_proxy_credentials`）；为假时沿用直连，
    与历史行为一致。

    Args:
        session: 会话对象（取 session_id / user_id）
        role: 容器角色
        daemon_config: 真实凭据配置
        password: 该角色的 daemon 密码

    Returns:
        环境变量字典
    """
    env: dict[str, str] = {
        "PASEO_PASSWORD": password,
        "PASEO_LISTEN": f"0.0.0.0:{settings.ARL_PASEO_PORT}",
    }

    proxy_base = settings.LLM_PROXY_BASE_URL.rstrip("/") if settings.LLM_PROXY_ENABLE else ""
    upstream = _upstream_credential(role, daemon_config)
    # 代理 token 由 ensure_session 预先登记，这里只取确定性派生的值
    proxy_token = derive_proxy_token(session.session_id, role) if proxy_base else ""

    if role == ARLRole.WORKER:
        if proxy_base:
            base_url, auth_token = proxy_base, proxy_token
        else:
            base_url, auth_token = upstream["base_url"], upstream["auth_token"]
        env.update({
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "ANTHROPIC_MODEL": daemon_config.model,
            "ANTHROPIC_SMALL_FAST_MODEL": (
                daemon_config.small_fast_model or "claude-haiku-4-5-20251001"
            ),
        })
    else:
        if proxy_base:
            # 代理端点同样补 /v1：Codex 会往 {base}/responses 发请求，代理
            # 侧再与真实上游的 /v1 去重拼接（见 llm_proxy._join_upstream_url）
            base_url = _normalize_base_url(proxy_base, ensure_v1=True) or proxy_base
            api_key = proxy_token
        else:
            base_url, api_key = upstream["base_url"], upstream["api_key"]
        env.update({
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_KEY": api_key,
            "OPENAI_MODEL": daemon_config.validator_model or "gpt-5.5",
        })

    # PASEO_HOSTNAMES：让两个容器能互相通信（validator 调 worker 的 script RPC）
    env["PASEO_HOSTNAMES"] = (
        f"worker={container_name(session.session_id, ARLRole.WORKER)}:{settings.ARL_PASEO_PORT}\n"
        f"validator={container_name(session.session_id, ARLRole.VALIDATOR)}:{settings.ARL_PASEO_PORT}"
    )
    return env


class ARLRuntimeService:
    """ARL 运行时服务。

    无会话缓存：每次按 ``session_id`` 查 Docker 重建快照。所有 docker-py
    调用都经 ``asyncio.to_thread`` 下沉到线程池。
    """

    def __init__(self) -> None:
        """初始化服务。

        不做任何 I/O——Docker 客户端惰性获取，容器对账由
        :func:`reconcile_arl_containers` 在应用 lifespan 显式触发。原先把
        对账塞在这里，意味着它由「第一个 WS 连接」偶然触发，且在 event loop
        里做阻塞的 Docker list/inspect。
        """
        #: 进程内的会话编排锁。先用它挡住同进程内的并发，再去抢 Redis 锁，
        #: 避免同进程多个连接轮番 polling Redis。
        self._locks: dict[str, asyncio.Lock] = {}
        #: 每把锁的等待者计数，归零即回收，防止长期运行后字典无界增长。
        self._lock_refs: dict[str, int] = {}

    @property
    def _docker(self) -> DockerClient:
        """Docker 客户端（惰性，复用全局单例）。"""
        return get_docker_client()

    # ---- 锁 ----

    @contextlib.asynccontextmanager
    async def _session_lock(self, session_id: str) -> AsyncIterator[None]:
        """串行化同一会话的容器编排。

        两层：进程内 ``asyncio.Lock`` + 跨进程 Redis 锁。缺了后者，多
        uvicorn worker 下两个进程会同时 ``containers.run`` 同名容器，撞名
        失败的一方在清理时会强删另一方正在使用的容器。

        Redis 不可用时降级为仅进程内互斥——此时宁可承担跨进程竞争，也不该
        让用户完全连不上会话。

        Args:
            session_id: 会话 ID

        Yields:
            None（持有锁的临界区）
        """
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        self._lock_refs[session_id] = self._lock_refs.get(session_id, 0) + 1
        try:
            async with lock:
                owner = secrets.token_hex(8)
                # TTL 须覆盖最坏情况的冷启动（daemon 就绪 + 安装器），
                # 另留 60s 余量；持锁进程崩溃时靠 TTL 兜底释放。
                ttl = int(
                    settings.ARL_DAEMON_READY_TIMEOUT
                    + settings.ARL_INSTALL_READY_TIMEOUT
                    + 60
                )
                acquired = await self._try_acquire_distributed(session_id, owner, ttl)
                try:
                    yield
                finally:
                    if acquired:
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(
                                release_arl_session_lock, session_id, owner
                            )
        finally:
            self._lock_refs[session_id] -= 1
            if self._lock_refs[session_id] <= 0:
                self._lock_refs.pop(session_id, None)
                self._locks.pop(session_id, None)

    async def _try_acquire_distributed(
        self, session_id: str, owner: str, ttl: int
    ) -> bool:
        """轮询抢占 Redis 会话锁，直到成功或超时。

        Args:
            session_id: 会话 ID
            owner: 持有者标识
            ttl: 锁 TTL（秒）

        Returns:
            True 表示抢到锁（需由调用方释放）；False 表示 Redis 不可用，
            调用方继续执行（仅靠进程内互斥）。

        Raises:
            RuntimeError: Waited a full TTL while another live process kept
                holding the lock. Proceeding without the lock would let two
                processes reconcile the same containers and remove each
                other's, so the caller fails and the user retries instead.
        """
        deadline = time.monotonic() + ttl
        while time.monotonic() < deadline:
            try:
                if await asyncio.to_thread(
                    acquire_arl_session_lock, session_id, owner, ttl
                ):
                    return True
            except Exception:
                logger.opt(exception=True).warning(
                    "ARL 会话 {} 获取 Redis 编排锁失败，降级为进程内互斥", session_id
                )
                return False
            # 锁被别的进程持有：它可能正在冷启动容器，等它完成后我们能直接复用
            await asyncio.sleep(1.0)
        logger.warning("ARL session {} timed out waiting for orchestration lock ({}s)", session_id, ttl)
        raise RuntimeError(
            f"ARL session is busy (another operation held the lock for over {ttl}s), please retry later"
        )

    # ---- 查询（Docker 为权威） ----

    def _get_container_sync(self, name: str) -> Container | None:
        """按名字取容器，不存在返回 None（同步，供 to_thread 调用）。"""
        try:
            return self._docker.containers.get(name)
        except NotFound:
            return None

    def _container_info_sync(
        self, session_id: str, role: ARLRole
    ) -> tuple[Container | None, ARLContainerInfo | None]:
        """读取单个容器及其派生信息（同步）。

        Args:
            session_id: 会话 ID
            role: 容器角色

        Returns:
            ``(container, info)``；容器不存在时为 ``(None, None)``。
        """
        name = container_name(session_id, role)
        container = self._get_container_sync(name)
        if container is None:
            return None, None

        if container.status == "running":
            status = ARLSessionStatus.READY
        elif container.status in ("created", "restarting"):
            status = ARLSessionStatus.STARTING
        else:
            # exited / dead / paused：容器壳还在，可原地 start 恢复
            status = ARLSessionStatus.STOPPED

        return container, ARLContainerInfo(
            name=container.name,
            port=_host_port(container),
            password=derive_password(session_id, role),
            status=status,
        )

    def _build_session_sync(self, session_id: str) -> ARLSession | None:
        """从 Docker 重建会话快照（同步）。

        Args:
            session_id: 会话 ID

        Returns:
            ``ARLSession``；两个容器都不存在时返回 None。整体 status 取两个
            容器中「最差」的那个：都 running 才算 READY。
        """
        worker_container, worker_info = self._container_info_sync(
            session_id, ARLRole.WORKER
        )
        validator_container, validator_info = self._container_info_sync(
            session_id, ARLRole.VALIDATOR
        )
        if worker_info is None and validator_info is None:
            return None

        # 只剩单边容器：当作「未就绪」，由 ensure_session 重建成完整的一对
        if worker_info is None or validator_info is None:
            present = worker_container or validator_container
            labels = (present.labels if present else {}) or {}
            missing = "worker" if worker_info is None else "validator"
            return ARLSession(
                session_id=session_id,
                folder_id=labels.get(FOLDER_LABEL) or None,
                user_id=labels.get(USER_LABEL, ""),
                status=ARLSessionStatus.ERROR,
                worker=worker_info or _placeholder_info(session_id, ARLRole.WORKER),
                validator=validator_info
                or _placeholder_info(session_id, ARLRole.VALIDATOR),
                created_at=_created_at(present),
                error_message=f"{missing} 容器缺失",
            )

        # info 与 container 由 _container_info_sync 成对返回，故此处必然非 None；
        # 显式断言让类型检查器也能看出这一点。
        assert worker_container is not None
        labels = worker_container.labels or {}
        if (
            worker_info.status == ARLSessionStatus.READY
            and validator_info.status == ARLSessionStatus.READY
        ):
            status = ARLSessionStatus.READY
        elif ARLSessionStatus.STARTING in (worker_info.status, validator_info.status):
            status = ARLSessionStatus.STARTING
        else:
            status = ARLSessionStatus.STOPPED

        return ARLSession(
            session_id=session_id,
            folder_id=labels.get(FOLDER_LABEL) or None,
            user_id=labels.get(USER_LABEL, ""),
            status=status,
            worker=worker_info,
            validator=validator_info,
            created_at=_created_at(worker_container),
        )

    async def get_session(self, session_id: str) -> ARLSession | None:
        """查询会话当前状态（每次读 Docker，无缓存）。

        Args:
            session_id: 会话 ID

        Returns:
            ``ARLSession``；容器不存在时返回 None。
        """
        try:
            return await asyncio.to_thread(self._build_session_sync, session_id)
        except DockerException:
            logger.opt(exception=True).warning("查询 ARL 会话 {} 失败", session_id)
            return None

    async def list_sessions(self, folder_id: str | None = None) -> list[ARLSession]:
        """列出当前存在容器的 ARL 会话。

        Args:
            folder_id: 按归属分组过滤；None 返回全部。

        Returns:
            会话列表
        """

        def _list() -> list[ARLSession]:
            containers = self._docker.containers.list(
                all=True, filters={"label": SESSION_LABEL}
            )
            # 按 session 去重：一个会话有两个容器，否则会重建两次
            session_ids = {
                sid
                for c in containers
                if (sid := (c.labels or {}).get(SESSION_LABEL))
            }
            sessions = []
            for sid in sorted(session_ids):
                session = self._build_session_sync(sid)
                if session is not None:
                    sessions.append(session)
            return sessions

        try:
            sessions = await asyncio.to_thread(_list)
        except DockerException:
            logger.opt(exception=True).warning("列出 ARL 会话失败")
            return []
        if folder_id is not None:
            sessions = [s for s in sessions if s.folder_id == folder_id]
        return sessions

    # ---- 编排 ----

    async def ensure_session(
        self,
        session_id: str,
        user_id: str,
        daemon_config: ARLDaemonConfig,
        folder_id: str | None = None,
    ) -> ARLSession:
        """确保会话的两个容器就绪，返回最终快照。

        这是唯一的编排入口，幂等。按容器实际状态分四种情形处理：

        - **不存在** → 创建
        - **配置指纹或镜像已变** → 删除重建（环境变量在创建时固化，重启
          不会让新凭据/新模型生效；镜像更新同理）
        - **已停止** → 原地 ``start``（workspace 与 paseo-home 都是 bind
          mount，agent 会话不丢）
        - **在运行** → 直接复用

        两个容器中任一需要重建时，两个一起重建：它们共享 ``PASEO_HOSTNAMES``
        与同一份凭据配置，让一边新一边旧会留下难查的不一致。

        无论走哪条路径，返回前都会**按 backend 真实可达的地址**探活，因此
        调用方拿到 READY 就能直接连 daemon。

        Args:
            session_id: 会话 ID（即 ``research_session.id``）
            user_id: 归属用户 ID
            daemon_config: 真实凭据配置
            folder_id: 归属分组，仅用于打标签

        Returns:
            状态为 ``READY`` 的会话快照

        Raises:
            RuntimeError: 容器启动失败或在超时内未就绪
        """
        async with self._session_lock(session_id):
            fingerprint = _config_fingerprint(daemon_config)
            if settings.LLM_PROXY_ENABLE:
                # 每次连接都重新登记代理凭据：会话可能闲置到 Redis 记录过期，
                # 或 Redis 重启丢了数据。容器里的 token 是确定性派生的，重新
                # 登记即可恢复，复用与重建两条路径都覆盖。
                await asyncio.to_thread(
                    _register_proxy_credentials, session_id, user_id, daemon_config
                )
            session = await self.get_session(session_id)

            # 持锁期间重新判定：可能在等锁时已被别的连接/进程准备好了
            if (
                session is not None
                and session.status == ARLSessionStatus.READY
                and await self._config_matches(session_id, fingerprint)
                and await self._probe_session(
                    session, timeout=settings.ARL_DAEMON_RECHECK_TIMEOUT
                )
            ):
                logger.info("♻️ ARL 会话 {} 复用运行中的容器", session_id)
                touch_arl_session_active(session_id)
                return session

            created_fresh = await self._reconcile_containers(
                session_id=session_id,
                user_id=user_id,
                folder_id=folder_id,
                daemon_config=daemon_config,
                fingerprint=fingerprint,
                existing=session,
            )

            session = await self.get_session(session_id)
            if session is None:
                raise RuntimeError("ARL 容器启动后仍查不到，Docker 状态异常")

            try:
                await self._wait_ready(session, wait_installer=created_fresh)
            except Exception as exc:
                # 冷启动失败才清理：复用已有容器时失败不能删，那可能是别的
                # 连接正在使用的容器（探活瞬时失败不等于容器坏了）。
                if created_fresh:
                    logger.error("ARL 会话 {} 启动失败，清理容器: {}", session_id, exc)
                    await self.cleanup_session(session_id, remove_data=False)
                raise

            session.status = ARLSessionStatus.READY
            touch_arl_session_active(session_id)
            logger.info("✅ ARL 会话 {} 就绪", session_id)
            return session

    async def _config_matches(self, session_id: str, fingerprint: str) -> bool:
        """判断容器上的配置指纹与镜像是否仍与当前配置一致。

        Args:
            session_id: 会话 ID
            fingerprint: 期望的配置指纹

        Returns:
            True 表示两个容器都匹配，可直接复用
        """

        def _check() -> bool:
            for role in ARLRole:
                container = self._get_container_sync(container_name(session_id, role))
                if container is None:
                    return False
                if (container.labels or {}).get(CONFIG_FINGERPRINT_LABEL) != fingerprint:
                    logger.info(
                        "ARL 容器 {} 的配置指纹已变（provider/模型/代理开关有改动），需重建",
                        container.name,
                    )
                    return False
                try:
                    image = self._docker.images.get(_image_for(role))
                except ImageNotFound:
                    # 镜像不存在：留给创建路径去报更清晰的错，不在这里判不匹配
                    return True
                if container.attrs.get("Image") != image.id:
                    logger.info("ARL 容器 {} 运行的是已淘汰的镜像，需重建", container.name)
                    return False
            return True

        try:
            return await asyncio.to_thread(_check)
        except DockerException:
            logger.opt(exception=True).warning("校验 ARL 容器配置失败，按需重建")
            return False

    async def _reconcile_containers(
        self,
        *,
        session_id: str,
        user_id: str,
        folder_id: str | None,
        daemon_config: ARLDaemonConfig,
        fingerprint: str,
        existing: ARLSession | None,
    ) -> bool:
        """把两个容器收敛到「按当前配置创建且在运行」的状态。

        Args:
            session_id: 会话 ID
            user_id: 归属用户 ID
            folder_id: 归属分组
            daemon_config: 真实凭据配置
            fingerprint: 当前配置指纹
            existing: 当前 Docker 快照（可为 None）

        Returns:
            True 表示本次新建了容器（调用方据此决定是否等待安装器）

        Raises:
            RuntimeError: 容器创建或启动失败
        """
        config_ok = (
            existing is not None
            and await self._config_matches(session_id, fingerprint)
        )
        # 单边缺失（ERROR）或配置/镜像已变 → 整对重建
        needs_recreate = existing is None or not config_ok or (
            existing.status == ARLSessionStatus.ERROR
        )

        session = ARLSession(
            session_id=session_id,
            folder_id=folder_id,
            user_id=user_id,
            status=ARLSessionStatus.STARTING,
            worker=_placeholder_info(session_id, ARLRole.WORKER),
            validator=_placeholder_info(session_id, ARLRole.VALIDATOR),
            created_at=datetime.now(UTC),
        )

        if needs_recreate:
            logger.info("🚀 ARL 会话 {} 创建容器", session_id)
            for role in ARLRole:
                await self._remove_container(container_name(session_id, role))
            for role in ARLRole:
                await self._start_container(
                    role=role,
                    session=session,
                    daemon_config=daemon_config,
                    fingerprint=fingerprint,
                )
            return True

        # 配置没变：已停止的原地拉起，在跑的不动
        logger.info("▶️ ARL 会话 {} 配置未变，恢复已有容器", session_id)
        for role in ARLRole:
            await self._start_existing_container(session_id, role)
        return False

    async def _start_existing_container(self, session_id: str, role: ARLRole) -> None:
        """原地启动一个已存在但未运行的容器。

        Args:
            session_id: 会话 ID
            role: 容器角色

        Raises:
            RuntimeError: 启动失败
        """
        name = container_name(session_id, role)

        def _start() -> None:
            container = self._get_container_sync(name)
            if container is None:
                raise RuntimeError(f"容器 {name} 不存在")
            container.reload()
            if container.status == "running":
                return
            container.start()

        try:
            await asyncio.to_thread(_start)
            logger.info("已恢复 ARL 容器 {}", name)
        except (DockerException, RuntimeError) as exc:
            raise RuntimeError(f"恢复 {role.value} 容器失败: {exc}") from exc

    async def _remove_container(self, name: str) -> None:
        """强制删除容器（含匿名卷），不存在时静默返回。

        Args:
            name: 容器名
        """

        def _remove() -> None:
            container = self._get_container_sync(name)
            if container is None:
                return
            container.remove(force=True, v=True)

        try:
            await asyncio.to_thread(_remove)
        except DockerException as exc:
            logger.warning("删除容器 {} 失败: {}", name, exc)

    async def _start_container(
        self,
        *,
        role: ARLRole,
        session: ARLSession,
        daemon_config: ARLDaemonConfig,
        fingerprint: str,
    ) -> None:
        """创建并启动单个 ARL 容器。

        Args:
            role: 容器角色
            session: 会话对象
            daemon_config: 真实凭据配置
            fingerprint: 配置指纹，写入标签供下次比对

        Raises:
            RuntimeError: 镜像缺失或容器创建失败
        """
        name = container_name(session.session_id, role)
        password = derive_password(session.session_id, role)
        image = _image_for(role)
        _, host_root = _prepare_host_directories(session.session_id, role)
        env = _build_container_env(session, role, daemon_config, password)

        # 端口：仅宿主机模式发布。backend 在容器内时走容器名 DNS，发布端口
        # 既无必要，也会在同槽会话间引入宿主端口冲突。
        network_kwargs: dict = {"network": settings.arl_network_name}
        if not use_container_network():
            network_kwargs["ports"] = {
                f"{settings.ARL_PASEO_PORT}/tcp": (
                    "127.0.0.1",
                    _allocate_port(session.session_id, role),
                )
            }

        labels = {
            SESSION_LABEL: session.session_id,
            ROLE_LABEL: role.value,
            USER_LABEL: str(session.user_id),
            FOLDER_LABEL: str(session.folder_id or ""),
            CONFIG_FINGERPRINT_LABEL: fingerprint,
        }

        logger.info("启动 {} 容器: name={}, image={}", role.value, name, image)

        def _run() -> Container:
            try:
                self._docker.images.get(image)
            except ImageNotFound as exc:
                raise RuntimeError(
                    f"ARL {role.value} 镜像 {image} 不存在，请先构建：docker build "
                    f"-f third_party/auto-dl-loop/docker/Dockerfile --target {role.value} "
                    f"-t {image} third_party/auto-dl-loop"
                ) from exc
            return self._docker.containers.run(
                image=image,
                name=name,
                detach=True,
                environment=env,
                volumes={
                    str(Path(host_root) / "paseo-home"): {
                        "bind": "/home/paseo",
                        "mode": "rw",
                    },
                    str(Path(host_root) / "workspace"): {
                        "bind": "/workspace",
                        "mode": "rw",
                    },
                },
                labels=labels,
                mem_limit=settings.ARL_CONTAINER_MEMORY_LIMIT,
                # nano_cpus 而非 cpu_count：后者映射到 HostConfig.CpuCount，
                # 那是 Windows 专用字段，在 Linux 上被静默忽略——等于没有
                # CPU 限制。全仓其他容器都用 nano_cpus。
                nano_cpus=int(settings.ARL_CONTAINER_CPU_LIMIT * 1e9),
                # 宿主 Docker 重启后自动拉起，省掉一次冷启动。
                # 注意不能加 cap_drop / user：entrypoint 需要 root 做 chown
                # 并用 gosu 降权到 paseo 跑 daemon（见 docker/entrypoint.sh）。
                restart_policy={"Name": "unless-stopped"},
                security_opt=["no-new-privileges"],
                **network_kwargs,
            )

        try:
            container = await asyncio.to_thread(_run)
        except RuntimeError:
            raise
        except DockerException as exc:
            raise RuntimeError(f"启动 {role.value} 容器失败: {exc}") from exc
        logger.info("容器 {} 已创建: id={}", name, container.id[:12])

    # ---- 就绪探测 ----

    async def _wait_ready(self, session: ARLSession, *, wait_installer: bool) -> None:
        """等待两个容器就绪：daemon HTTP + （可选）角色安装器。

        两个角色并行等待——它们相互独立，串行等待会把最坏耗时翻倍。

        Args:
            session: 会话快照
            wait_installer: 是否等待 ``.aris/install.json``。仅新建容器需要；
                复用已有容器时安装早已完成（标记文件在 bind mount 里持久化），
                再等一遍纯属浪费。

        Raises:
            RuntimeError: 任一容器未在超时内就绪
        """
        # Always allow the cold-start budget: on the reuse path the containers
        # may have just been started from a stopped state (e.g. after idle
        # cleanup), so the daemon still has to boot. A daemon that is already
        # up answers immediately, and an exited container fails fast in
        # ``_wait_for_health``, so the larger timeout only costs time when the
        # daemon is genuinely slow.
        timeout = settings.ARL_DAEMON_READY_TIMEOUT
        results = await asyncio.gather(
            *(
                self._wait_role_ready(
                    session.session_id,
                    role,
                    session.worker if role == ARLRole.WORKER else session.validator,
                    daemon_timeout=timeout,
                    installer_timeout=(
                        settings.ARL_INSTALL_READY_TIMEOUT if wait_installer else 0.0
                    ),
                )
                for role in ARLRole
            ),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            # 多个角色同时失败时只报第一个，其余记日志——错误信息要可读
            for extra in errors[1:]:
                logger.warning("ARL 容器就绪检查另一失败: {}", extra)
            raise RuntimeError(str(errors[0])) from errors[0]

    async def _wait_role_ready(
        self,
        session_id: str,
        role: ARLRole,
        info: ARLContainerInfo,
        *,
        daemon_timeout: float,
        installer_timeout: float,
    ) -> None:
        """等待单个角色容器就绪。

        Args:
            session_id: 会话 ID
            role: 容器角色
            info: 容器信息
            daemon_timeout: 等待 daemon HTTP 就绪的超时（秒）
            installer_timeout: 等待安装器的超时（秒）；<=0 表示跳过

        Raises:
            RuntimeError: 未在超时内就绪，或容器已退出
        """
        logger.info("等待 {} daemon 就绪...", info.name)
        if not await self._wait_for_health(info, daemon_timeout):
            raise RuntimeError(
                f"{role.value} daemon 在 {daemon_timeout:.0f}s 内未就绪"
                f"{await self._crash_hint(info.name)}"
            )

        if installer_timeout > 0:
            logger.info("等待 {} 安装器完成...", info.name)
            if not await self._wait_for_installer(session_id, role, installer_timeout):
                raise RuntimeError(
                    f"{role.value} 安装器在 {installer_timeout:.0f}s 内未完成"
                    f"{await self._crash_hint(info.name)}"
                )

        logger.info("{} 容器就绪", info.name)

    async def _probe_session(self, session: ARLSession, *, timeout: float) -> bool:
        """并行探活两个容器的 daemon。

        Args:
            session: 会话快照
            timeout: 单个容器的探活超时（秒）

        Returns:
            True 表示两个 daemon 都响应
        """
        try:
            results = await asyncio.gather(
                self._wait_for_health(session.worker, timeout),
                self._wait_for_health(session.validator, timeout),
            )
        except Exception:
            logger.opt(exception=True).debug("ARL 会话探活异常")
            return False
        return all(results)

    async def _wait_for_health(self, info: ARLContainerInfo, timeout: float) -> bool:
        """轮询 daemon 的 ``/api/health`` 直到就绪或超时。

        走 **backend 实际要连的那条路径**（容器网络时按容器名 DNS，宿主机
        模式按映射端口），而不是在容器里 ``docker exec`` 探 loopback——后者
        探不出端口映射失效、网络未连通这类故障，会误报就绪，让失败延后到
        daemon WS 连接时才以裸异常暴露。

        每轮先看容器状态：已退出就立刻失败，不必耗满超时。

        Args:
            info: 容器信息
            timeout: 超时秒数

        Returns:
            True 表示就绪，False 表示超时或容器已退出
        """
        # 先判容器是否还活着，再解析地址。顺序很重要：已停止的容器在
        # NetworkSettings 里没有端口绑定，若先解析地址会报成「未发布端口」，
        # 把「容器停了」误诊成配置问题。
        if await self._container_exited(info.name):
            logger.warning("容器 {} 未在运行，终止探活", info.name)
            return False

        try:
            http_base, _ = daemon_endpoints(info)
        except RuntimeError as exc:
            logger.warning("{}", exc)
            return False

        deadline = time.monotonic() + timeout
        url = f"{http_base}/api/health"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if await self._container_exited(info.name):
                    logger.warning("容器 {} 已退出，终止探活", info.name)
                    return False
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass  # daemon 还没开始监听
                await asyncio.sleep(_PROBE_INTERVAL)
        return False

    async def _wait_for_installer(
        self, session_id: str, role: ARLRole, timeout: float
    ) -> bool:
        """等待 ARL 角色安装器完成（``.aris/install.json`` 出现）。

        安装目标是容器里的 ``/workspace/<role>``，而 ``/workspace`` 是 bind
        mount，故 backend 直接 stat 宿主侧文件即可——不必每轮 ``docker exec``
        在容器里起一个进程。

        Args:
            session_id: 会话 ID
            role: 容器角色，决定项目目录
            timeout: 超时秒数

        Returns:
            True 表示安装完成，False 表示超时或容器已退出
        """
        stamp = _install_stamp_path(session_id, role)
        name = container_name(session_id, role)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await asyncio.to_thread(os.path.isfile, stamp):
                return True
            if await self._container_exited(name):
                logger.warning("容器 {} 已退出，终止安装器等待", name)
                return False
            await asyncio.sleep(_PROBE_INTERVAL)
        return False

    async def _container_exited(self, name: str) -> bool:
        """容器是否已退出/消失（用于探活循环快速失败）。

        Args:
            name: 容器名

        Returns:
            True 表示容器已不可用；查询失败时返回 False（宁可继续等待，
            也不因一次瞬时的 Docker API 抖动误判失败）
        """

        def _check() -> bool:
            container = self._get_container_sync(name)
            if container is None:
                return True
            container.reload()
            return container.status in ("exited", "dead", "removing")

        try:
            return await asyncio.to_thread(_check)
        except DockerException:
            return False

    async def _crash_hint(self, name: str) -> str:
        """取容器尾部日志，拼成可附加到错误信息里的提示。

        启动失败最常见的原因是容器内 entrypoint 报错（镜像缺依赖、凭据格式
        不对等），只报「超时」对排查毫无帮助。

        Args:
            name: 容器名

        Returns:
            形如 ``"；容器日志尾部：..."`` 的字符串；取不到时返回空串
        """

        def _logs() -> str:
            container = self._get_container_sync(name)
            if container is None:
                return ""
            raw = container.logs(tail=_CRASH_LOG_TAIL)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace").strip()
            # stream=True 时 logs() 返回生成器；这里不用流式，仅作防御
            return str(raw).strip()

        try:
            tail = await asyncio.to_thread(_logs)
        except DockerException:
            return ""
        return f"；容器日志尾部：\n{tail}" if tail else ""

    # ---- 清理 ----

    async def cleanup_session(
        self, session_id: str, *, remove_data: bool = True
    ) -> None:
        """清理会话：删除两个容器、吊销代理 token，可选删除挂载目录。

        幂等：容器/目录不存在时静默跳过。

        Args:
            session_id: 会话 ID
            remove_data: 是否删除宿主挂载目录。会话被删除时传 True；
                启动失败清理时传 False——此时目录里可能已有安装产物，
                留着能让下次重试少跑一遍安装器。
        """
        logger.info("清理 ARL 会话 {}（remove_data={}）", session_id, remove_data)
        for role in ARLRole:
            await self._remove_container(container_name(session_id, role))

        forget_arl_session_active(session_id)

        # 容器已删，容器内持有的代理 token 没有存在价值，立即吊销
        try:
            from app.services import credential_broker

            revoked = await asyncio.to_thread(
                credential_broker.revoke_task_tokens, session_id
            )
            if revoked:
                logger.info("已吊销 ARL 会话 {} 的 {} 个代理 token", session_id, revoked)
        except Exception:
            # TTL 会兜底过期，吊销失败不该让删除流程失败
            logger.opt(exception=True).warning(
                "吊销 ARL 会话 {} 的代理 token 失败", session_id
            )

        if remove_data:
            docker_root, _ = _session_root(session_id)
            try:
                await asyncio.to_thread(shutil.rmtree, docker_root, ignore_errors=True)
            except Exception:
                logger.opt(exception=True).warning(
                    "删除 ARL 会话 {} 的挂载目录失败: {}", session_id, docker_root
                )

    def cleanup_session_sync(self, session_id: str, *, remove_data: bool = True) -> None:
        """:meth:`cleanup_session` 的同步版本。

        供同步调用方使用（如会话删除接口——FastAPI 的同步端点跑在线程池里，
        没有运行中的事件循环，``asyncio.run`` 在那里可行但很脆弱：一旦调用方
        改成 async 就会抛 RuntimeError）。底层全是同步 docker-py 调用，因此
        直接给一个同步实现比绕事件循环更稳。

        Args:
            session_id: 会话 ID
            remove_data: 是否删除宿主挂载目录
        """
        logger.info("清理 ARL 会话 {}（同步，remove_data={}）", session_id, remove_data)
        for role in ARLRole:
            name = container_name(session_id, role)
            try:
                container = self._get_container_sync(name)
                if container is not None:
                    container.remove(force=True, v=True)
            except DockerException as exc:
                logger.warning("删除容器 {} 失败: {}", name, exc)

        forget_arl_session_active(session_id)

        try:
            from app.services import credential_broker

            revoked = credential_broker.revoke_task_tokens(session_id)
            if revoked:
                logger.info("已吊销 ARL 会话 {} 的 {} 个代理 token", session_id, revoked)
        except Exception:
            logger.opt(exception=True).warning(
                "吊销 ARL 会话 {} 的代理 token 失败", session_id
            )

        if remove_data:
            docker_root, _ = _session_root(session_id)
            shutil.rmtree(docker_root, ignore_errors=True)

    async def stop_session(self, session_id: str) -> bool:
        """停止会话容器但保留容器壳与数据（空闲回收用）。

        停止而非删除：workspace 与 paseo-home 都是 bind mount，停止不丢
        数据，且 :meth:`ensure_session` 会原地 ``start`` 把 agent 会话恢复
        回来；已停止的容器不占内存与 CPU。

        Args:
            session_id: 会话 ID

        两个容器各自停止、互不影响：一个停失败不能让另一个跳过。任一失败时
        把会话以最早的时间戳放回活跃集合，下一轮扫描（而不是再等一整个空闲
        阈值）重试，避免只停掉一半、另一半一直跑着没人管。

        Returns:
            True 表示至少停了一个容器
        """

        def _stop() -> tuple[bool, bool]:
            stopped = failed = False
            for role in ARLRole:
                name = container_name(session_id, role)
                try:
                    container = self._get_container_sync(name)
                    if container is None:
                        continue
                    container.reload()
                    if container.status != "running":
                        continue
                    container.stop(timeout=10)
                    stopped = True
                    logger.debug("ARL 空闲回收 [{}] 已停止 {} 容器 {}", session_id, role.value, name)
                except DockerException:
                    failed = True
                    logger.opt(exception=True).warning(
                        "ARL 空闲回收 [{}] 停止 {} 容器 {} 失败", session_id, role.value, name
                    )
            return stopped, failed

        stopped, failed = await asyncio.to_thread(_stop)
        if failed:
            touch_arl_session_active(session_id, ts=0.0)
            logger.warning("ARL 空闲回收 [{}] 有容器没停掉，下一轮扫描重试", session_id)
        if stopped:
            logger.info("已停止空闲 ARL 会话 {} 的容器", session_id)
        return stopped


def _image_for(role: ARLRole) -> str:
    """返回角色对应的镜像名。

    Args:
        role: 容器角色

    Returns:
        镜像名
    """
    return (
        settings.ARL_WORKER_IMAGE
        if role == ARLRole.WORKER
        else settings.ARL_VALIDATOR_IMAGE
    )


# ---- 全局单例 ----

_runtime_service: ARLRuntimeService | None = None


def get_arl_runtime() -> ARLRuntimeService:
    """获取 ARL 运行时服务单例。

    单例只为复用进程内的编排锁字典（Docker 客户端本身已是全局单例）；
    服务本身不持有会话状态，故多 worker 下各进程各有一个也不会不一致。
    """
    global _runtime_service
    if _runtime_service is None:
        _runtime_service = ARLRuntimeService()
    return _runtime_service


# ---- 启动期对账与空闲回收 ----


def _live_arl_session_ids() -> set[str] | None:
    """查出数据库中仍存在的 ARL 会话 ID。

    Returns:
        会话 ID 集合；查询失败时返回 None（调用方据此跳过「孤儿」判定，
        宁可留下容器也不误删正在用的会话）。
    """
    from sqlmodel import select

    from app.core.db import get_db_session
    from app.models.research import ResearchSession

    try:
        with get_db_session() as db:
            rows = db.exec(
                select(ResearchSession.id).where(ResearchSession.session_type == "arl")
            ).all()
        return {str(row) for row in rows}
    except Exception:
        logger.opt(exception=True).warning("查询存活 ARL 会话失败")
        return None


def reconcile_arl_containers() -> int:
    """启动期对账 ARL 容器，删除孤儿并重置活跃度跟踪。

    在应用 lifespan 显式调用（而不是挂在服务构造里由第一个 WS 连接偶然
    触发）。处理两类容器：

    - **孤儿**：会话已从数据库删除，但容器还在（删除接口的清理失败、或在
      旧版本里根本没清理过）。直接删除。
    - **存活会话的容器**：保留。正在运行的补一条活跃时间戳——否则它从未
      进过活跃集合，空闲回收永远看不到它，会一直占着内存。

    已停止的容器**不删**：bind mount 里有 agent 会话状态，用户重连时原地
    start 即恢复。

    Returns:
        被删除的孤儿会话数
    """
    try:
        client = get_docker_client()
    except Exception:
        logger.opt(exception=True).warning("ARL 容器对账失败：无法连接 Docker")
        return 0

    live = _live_arl_session_ids()

    try:
        containers = client.containers.list(all=True, filters={"label": SESSION_LABEL})
    except DockerException:
        logger.opt(exception=True).warning("ARL 容器对账失败：无法列出容器")
        return 0

    # 按会话归集，避免对同一会话重复处理（一个会话两个容器）
    by_session: dict[str, list] = {}
    for container in containers:
        session_id = (container.labels or {}).get(SESSION_LABEL)
        if session_id:
            by_session.setdefault(session_id, []).append(container)

    removed = 0
    for session_id, group in by_session.items():
        if live is not None and session_id not in live:
            logger.info("清理孤儿 ARL 会话 {} 的 {} 个容器", session_id, len(group))
            for container in group:
                try:
                    container.remove(force=True, v=True)
                except DockerException as exc:
                    logger.warning("删除容器 {} 失败: {}", container.name, exc)
            forget_arl_session_active(session_id)
            removed += 1
            continue

        if any(c.status == "running" for c in group):
            # 进程重启后活跃集合可能已丢；补一条，让空闲回收能看到它
            touch_arl_session_active(session_id)

    if removed:
        logger.info("ARL 容器对账完成，清理了 {} 个孤儿会话", removed)
    return removed


async def _has_running_turn(session: ARLSession) -> bool:
    """Check whether any agent in the session is in the middle of a turn.

    Queries each daemon's agent directory. An agent waiting on a permission
    prompt is not making progress, so it counts as idle; otherwise a forgotten
    prompt would keep the containers alive forever.

    Any failure (daemon unreachable, RPC error) is treated as "not running",
    so idle cleanup keeps its original behavior when the check cannot be made.

    Args:
        session: Session snapshot with both containers' connection info.

    Returns:
        True if at least one agent has ``status == "running"`` and no
        pending permission request.
    """
    from app.services.arl.daemon_client import PaseoDaemonClient

    for role in ARLRole:
        info = session.worker if role == ARLRole.WORKER else session.validator
        if info.status != ARLSessionStatus.READY:
            continue
        client = None
        try:
            _, ws_url = daemon_endpoints(info)
            client = PaseoDaemonClient(url=ws_url, password=info.password)
            await asyncio.wait_for(client.connect(), timeout=15.0)
            entries = await asyncio.wait_for(
                client.fetch_agents(subscribe=False), timeout=15.0
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ARL 空闲回收 [{}] {} agent 状态查询失败，按不在跑回合处理: {}",
                session.session_id,
                role.value,
                exc,
            )
            continue
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.disconnect()
        statuses = []
        running = False
        for entry in entries:
            agent = entry.get("agent") if isinstance(entry.get("agent"), dict) else entry
            pending = bool(agent.get("pendingPermissions"))
            statuses.append(
                f"{agent.get('id')}={agent.get('status')}{'(待授权)' if pending else ''}"
            )
            if agent.get("status") == "running" and not pending:
                running = True
        logger.debug(
            "ARL 空闲回收 [{}] {} agent 状态: {}", session.session_id, role.value, statuses
        )
        if running:
            return True
    return False


#: 按进程名（命令行首个词的文件名，即 argv[0]）判定的常驻进程。不用 ``ps``
#: 的 comm 列：它截断到 15 字符，且 Paseo 改写的进程名带空格，会把
#: ``docker top`` 的列拆乱。也不按安装路径匹配，换安装位置不受影响。
#:
#: - 容器与 Paseo：``tini``、``Paseo``（Supervisor / Daemon 改写后的名字）、
#:   ``esbuild``（插件编译服务）；
#: - agent 本体：``claude``、``codex`` 及其辅助进程 ``codex-code-mode-host``；
#: - 只看不干活的交互工具：留在终端里没关（如 ``top``、``tail -f log``）
#:   会让容器永远回收不掉，而真正的任务另有自己的进程。
_RESIDENT_NAMES = frozenset(
    {
        "tini",
        "paseo",
        "esbuild",
        "claude",
        "codex",
        "codex-code-mode-host",
        "top",
        "htop",
        "less",
        "more",
        "man",
        "vi",
        "vim",
        "nvim",
        "nano",
        "watch",
        "tail",
    }
)
#: 进程名是 ``node`` 时看它跑的脚本名：Paseo 终端 worker、codex 的 node
#: 启动壳、validator 的验证服务（paseo.json 里的 service 脚本，起过一次就
#: 一直在，不能让它单独保活）。
_RESIDENT_NODE_SCRIPTS = frozenset(
    {"terminal-worker-process.js", "codex", "validation-cli.js"}
)
#: 不带参数时视为空闲的 shell：Paseo 终端里空着的提示符，或 ``docker exec``
#: 进来的会话。带参数（``sh -c ...``、``bash run.sh``）则是在跑东西。
_SHELL_NAMES = frozenset({"sh", "bash", "-sh", "-bash"})
#: 镜像 HEALTHCHECK 每 30s 起一次 ``/bin/sh -c node -e "...'/api/health'..."``，
#: 只活几毫秒，但扫描恰好撞上时不能把它当成后台任务。
_HEALTHCHECK_MARKER = "/api/health"


def _is_resident_process(command: str) -> bool:
    """判断一条进程是否属于容器常驻进程（不代表有任务在跑）。

    按进程名判定，只有解释器（``node``、shell）才多看一个参数。进程名与
    脚本名都不区分大小写（如 Paseo 把自己改写成 ``Paseo Daemon``），白名单
    里统一写小写。

    Args:
        command: ``docker top`` 给出的完整命令行。

    Returns:
        True 表示常驻进程或已退出的僵尸进程；False 表示 agent 起的后台任务。
    """
    command = command.strip()
    if not command or command.endswith("<defunct>"):
        return True
    argv = command.split()
    name = posixpath.basename(argv[0]).lower()
    if name in _RESIDENT_NAMES:
        return True
    if name in _SHELL_NAMES:
        return len(argv) == 1 or _HEALTHCHECK_MARKER in command
    if name == "node":
        if _HEALTHCHECK_MARKER in command:
            return True
        script = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
        return posixpath.basename(script).lower() in _RESIDENT_NODE_SCRIPTS
    return False


def _background_processes_sync(session_id: str) -> list[str]:
    """列出会话容器里 agent 起的后台进程（同步，供 to_thread 调用）。

    agent 用 ``nohup`` / ``&`` 起训练后回合就结束了，daemon 里 agent 回到
    idle，但训练还要跑很久——这是 :func:`_has_running_turn` 看不到的。
    Paseo 协议里没有后台进程信息，CPU / 网络 / 日志又都有与负载无关的底噪，
    故直接看进程表：除常驻进程外还有别的进程，就说明有任务在跑。

    Args:
        session_id: 会话 ID

    Returns:
        非常驻进程的命令行列表；容器未运行或读取失败时为空列表（保持原有
        回收行为，不因探测失败把容器永久留住）。
    """
    client = get_docker_client()
    found: list[str] = []
    for role in ARLRole:
        try:
            container = client.containers.get(container_name(session_id, role))
            container.reload()
            if container.status != "running":
                continue
            table = container.top()
        except DockerException as exc:
            logger.warning("ARL session {} process list failed: {}", session_id, exc)
            continue
        titles = table.get("Titles") or []
        column = next(
            (i for i, title in enumerate(titles) if title in ("CMD", "COMMAND")),
            len(titles) - 1,
        )
        resident: list[str] = []
        background: list[str] = []
        for row in table.get("Processes") or []:
            if column < 0 or column >= len(row):
                continue
            command = row[column]
            (resident if _is_resident_process(command) else background).append(command)
        # 常驻进程只在补白名单时有用，放 DEBUG；后台进程会出现在保活日志里
        logger.debug(
            "ARL 空闲回收 [{}] {} 常驻 {} 个: {}，后台 {} 个: {}",
            session_id,
            role.value,
            len(resident),
            [command[:120] for command in resident],
            len(background),
            [command[:200] for command in background],
        )
        found.extend(background)
    return found


def _keep_session_alive(session: ARLSession) -> None:
    """为仍在干活的会话刷新活跃时间，并给代理 token 续期（同步）。

    token 只在用户连接时由 ``ensure_session`` 重新登记；无人连接时跑满
    ``LLM_PROXY_TOKEN_TTL`` 的实验会在中途 401。这里随保活一起续期：token
    值不变，只刷新 Redis 过期时间，容器无需重启。

    Args:
        session: 会话快照
    """
    touch_arl_session_active(session.session_id)
    if not settings.LLM_PROXY_ENABLE:
        return
    from app.services import credential_broker

    extended = credential_broker.extend_task_tokens(
        session.session_id,
        settings.LLM_PROXY_TOKEN_TTL,
        user_id=session.user_id or None,
    )
    if extended:
        logger.debug(
            "ARL 空闲回收 [{}] 续期 {} 个代理 token（{}s）",
            session.session_id,
            extended,
            settings.LLM_PROXY_TOKEN_TTL,
        )
    else:
        # Redis 记录已丢（过期或 Redis 重启），续期救不回来，要等用户下次连接重新登记
        logger.warning(
            "ARL 空闲回收 [{}] 已刷新活跃时间，但没有可续期的代理 token，用户重连前调模型会 401",
            session.session_id,
        )


async def stop_idle_arl_sessions() -> int:
    """停止空闲 ARL 会话的容器。

    每个 ARL 会话常驻两个容器（默认各 4g 内存上限），只在 WS 连接期间被
    使用，而连接断开并不意味着会话结束——用户刷新页面或改天继续都要复用
    同一对容器。故不能在断连时删容器，只能靠空闲阈值回收。

    超过阈值的会话在停止前再确认两件事，任一成立就保活并续期 token：

    - agent 正在跑回合（:func:`_has_running_turn`，卡在授权上的不算）；
    - 容器里有 agent 起的后台进程（:func:`_background_processes_sync`）。

    停止而非删除的理由见 :meth:`ARLRuntimeService.stop_session`。

    Returns:
        被停止的会话数
    """
    now = time.time()
    timeout = settings.ARL_SESSION_IDLE_TIMEOUT_SECONDS
    threshold = now - timeout
    try:
        tracked = await asyncio.to_thread(list_arl_session_last_active)
        idle = await asyncio.to_thread(pop_idle_arl_sessions, threshold)
    except Exception:
        logger.opt(exception=True).warning("ARL 空闲回收失败：无法读取活跃时间")
        return 0

    # 没到阈值的会话不进入检查；排查「为什么没回收」时打开 DEBUG 看离阈值还差多久
    for session_id, last_active in sorted(tracked.items(), key=lambda kv: kv[1]):
        if session_id not in idle:
            idle_for = int(now - last_active)
            logger.debug(
                "ARL 空闲回收 [{}] 未到阈值：已空闲 {}s，还差 {}s",
                session_id,
                idle_for,
                max(timeout - idle_for, 0),
            )
    if not idle:
        logger.debug("ARL 空闲回收：跟踪 {} 个会话，没有超过 {}s 的", len(tracked), timeout)
        return 0

    runtime = get_arl_runtime()
    stopped = 0
    for session_id in idle:
        session = await runtime.get_session(session_id)
        if session is not None:
            # Still working without a browser attached: keep it and re-check
            # after another idle period.
            if await _has_running_turn(session):
                logger.info("ARL 空闲回收 [{}] 保活：agent 正在跑回合", session_id)
                await asyncio.to_thread(_keep_session_alive, session)
                continue
            background = await asyncio.to_thread(_background_processes_sync, session_id)
            if background:
                logger.info(
                    "ARL 空闲回收 [{}] 保活：有后台进程 {}",
                    session_id,
                    [command[:120] for command in background[:5]],
                )
                await asyncio.to_thread(_keep_session_alive, session)
                continue
        # 没停的原因（重新活跃 / 加锁失败）由 _stop_if_still_idle 自己记日志
        if await _stop_if_still_idle(runtime, session_id):
            stopped += 1
    logger.info("ARL 空闲回收：超时 {} 个，停止 {} 个", len(idle), stopped)
    return stopped


async def _stop_if_still_idle(runtime: ARLRuntimeService, session_id: str) -> bool:
    """Stop a session's containers unless it became active again.

    Runs under the session orchestration lock so it cannot interleave with
    :meth:`ARLRuntimeService.ensure_session`. ``pop_idle_arl_sessions`` already
    removed the session from the active set, so any score found here means a
    connect or heartbeat touched it after the pop.

    Args:
        runtime: ARL runtime service.
        session_id: Session ID popped as idle.

    Returns:
        True if at least one container was stopped.
    """
    try:
        async with runtime._session_lock(session_id):
            try:
                last_active = await asyncio.to_thread(get_arl_session_last_active, session_id)
            except Exception:
                logger.opt(exception=True).warning(
                    "ARL session {} last-active re-check failed, stopping anyway", session_id
                )
                last_active = None
            if last_active is not None:
                logger.info("ARL session {} became active again, skip idle stop", session_id)
                return False
            return await runtime.stop_session(session_id)
    except RuntimeError as exc:
        # Lock held by a long-running orchestration: the session is in use.
        logger.info("ARL session {} skip idle stop: {}", session_id, exc)
        touch_arl_session_active(session_id)
        return False


async def run_arl_idle_cleanup_loop() -> None:
    """周期性停止空闲 ARL 会话容器。

    作为应用启动时的后台任务运行、关闭时取消，与 code-server / paper
    workspace 的清理循环同构。异常只记日志不外抛，避免 Docker 或 Redis
    的一次抖动把整个循环杀掉。
    """
    interval = settings.ARL_SESSION_CLEANUP_INTERVAL_SECONDS
    logger.info(
        "启动 ARL 空闲清理循环（间隔 {}s，空闲阈值 {}s）",
        interval,
        settings.ARL_SESSION_IDLE_TIMEOUT_SECONDS,
    )
    while True:
        try:
            await stop_idle_arl_sessions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("ARL 空闲清理任务异常: {}", exc)
        await asyncio.sleep(interval)
