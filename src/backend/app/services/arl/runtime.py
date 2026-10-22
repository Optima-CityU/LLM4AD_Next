"""
ARL 运行时服务：容器生命周期管理。

负责：
- 创建会话时启动两个容器（worker + validator）
- 等待 daemon 就绪（/api/health）与安装器完成（.aris/install.json）
- 维护会话到容器的映射
- 清理会话时删除容器
"""

import asyncio
import hmac
from datetime import UTC, datetime
from pathlib import Path

from docker import DockerClient
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from loguru import logger

from app.core.config import settings
from app.core.docker import get_docker_client
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

#: Anthropic 与 OpenAI 对 base URL 的 /v1 后缀要求相反
V1_SUFFIX = "/v1"


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


class ARLRuntimeService:
    """ARL 运行时服务单例。

    管理所有活跃会话的容器生命周期与状态。
    """

    def __init__(self):
        """初始化服务，连接 Docker daemon。"""
        self._docker: DockerClient = get_docker_client()
        # 活跃会话映射：session_id -> ARLSession
        self._sessions: dict[str, ARLSession] = {}
        # 启动时恢复或清理容器
        self._restore_or_cleanup_containers()

    def _restore_or_cleanup_containers(self) -> None:
        """恢复运行中的容器到会话映射，或清理停止的容器。"""
        try:
            containers = self._docker.containers.list(
                all=True, filters={"label": SESSION_LABEL}
            )
            for container in containers:
                session_id = container.labels.get(SESSION_LABEL)
                if not session_id:
                    continue

                # 如果容器正在运行，尝试恢复到会话映射
                if container.status == "running":
                    try:
                        self._restore_session_from_containers(session_id)
                        logger.info(
                            "恢复 ARL 会话 {} 从运行中的容器",
                            session_id,
                        )
                    except Exception as e:
                        logger.warning(
                            "无法恢复会话 {}: {}, 将清理容器",
                            session_id,
                            e,
                        )
                        try:
                            container.remove(force=True)
                        except Exception as cleanup_err:
                            logger.warning("删除容器失败: {}", cleanup_err)
                else:
                    # 容器已停止，清理
                    logger.info(
                        "清理停止的 ARL 容器 {} (session={})",
                        container.name,
                        session_id,
                    )
                    try:
                        container.remove(force=True)
                    except Exception as e:
                        logger.warning("删除容器 {} 失败: {}", container.name, e)
        except DockerException as e:
            logger.exception("恢复/清理 ARL 容器失败: {}", e)

    def _restore_session_from_containers(self, session_id: str) -> None:
        """从运行中的容器恢复会话到内存映射。

        Args:
            session_id: Runtime session ID

        Raises:
            Exception: 容器不完整或信息无法恢复
        """
        # 查找两个容器
        worker_container = self._docker.containers.get(
            self._container_name(session_id, ARLRole.WORKER)
        )
        validator_container = self._docker.containers.get(
            self._container_name(session_id, ARLRole.VALIDATOR)
        )

        # 从标签恢复信息
        worker_labels = worker_container.labels
        project_id = int(worker_labels.get("llm4ad.arl.project_id", 0))
        user_id = worker_labels.get("llm4ad.arl.user_id", "")

        # 重新派生密码（密码是确定性的，基于 session_id + role）
        worker_password = self._derive_password(session_id, ARLRole.WORKER)
        validator_password = self._derive_password(session_id, ARLRole.VALIDATOR)

        # 获取端口（从容器的端口映射中提取）
        worker_port = int(
            worker_container.attrs["NetworkSettings"]["Ports"]
            [f"{settings.ARL_PASEO_PORT}/tcp"][0]["HostPort"]
        )
        validator_port = int(
            validator_container.attrs["NetworkSettings"]["Ports"]
            [f"{settings.ARL_PASEO_PORT}/tcp"][0]["HostPort"]
        )

        # 重建会话对象
        session = ARLSession(
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
            status=ARLSessionStatus.READY,
            worker=ARLContainerInfo(
                name=worker_container.name,
                port=worker_port,
                password=worker_password,
                status=ARLSessionStatus.READY,
            ),
            validator=ARLContainerInfo(
                name=validator_container.name,
                port=validator_port,
                password=validator_password,
                status=ARLSessionStatus.READY,
            ),
            created_at=datetime.now(UTC),
        )

        self._sessions[session_id] = session
        logger.info("成功恢复会话 {}", session_id)

    def _derive_password(self, session_id: str, role: ARLRole) -> str:
        """派生每会话每角色的 Paseo daemon 密码（HMAC-SHA256）。

        Args:
            session_id: 会话 ID（32 字节十六进制）
            role: 容器角色

        Returns:
            派生的密码（Base64 编码）
        """
        message = f"{session_id}:{role.value}".encode()
        key = settings.SECRET_KEY.encode("utf-8")
        digest = hmac.new(key, message, digestmod="sha256").digest()
        # 取前 32 字节编码为 base64（移除末尾 = padding）
        return digest[:32].hex()

    def _container_name(self, session_id: str, role: ARLRole) -> str:
        """生成容器名称。

        格式: llm4ad-arl-{role_abbrev}-{session_id}
        """
        abbrev = "w" if role == ARLRole.WORKER else "v"
        return f"llm4ad-arl-{abbrev}-{session_id}"

    def _allocate_port(self, session_id: str, role: ARLRole) -> int:
        """为容器分配宿主机端口。

        临时实现：基于 session_id 哈希 + 角色偏移。
        生产环境应维护端口池或使用动态分配。
        """
        # 简单哈希：取 session_id 后 4 位转整数
        base = int(session_id[-4:], 16) % 10000 + 16000  # 16000-25999
        offset = 0 if role == ARLRole.WORKER else 1
        return base + offset

    async def _wait_for_health(self, container_name: str, timeout: float) -> bool:
        """等待容器内 daemon 的 /api/health 就绪。

        Args:
            container_name: 容器名称
            timeout: 超时秒数

        Returns:
            True 表示就绪，False 表示超时
        """
        start = asyncio.get_event_loop().time()
        check_cmd = (
            "node -e \""
            "require('http').get({host:'127.0.0.1',port:6767,path:'/api/health'},"
            "r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))\""
        )

        while (asyncio.get_event_loop().time() - start) < timeout:
            try:
                result = self._docker.containers.get(container_name).exec_run(
                    ["sh", "-c", check_cmd], demux=False
                )
                if result.exit_code == 0:
                    return True
            except Exception:
                pass  # 容器还没起来或 daemon 还没监听
            await asyncio.sleep(2)
        return False

    async def _wait_for_installer(
        self, container_name: str, role: ARLRole, timeout: float
    ) -> bool:
        """等待 ARL 角色安装器完成（.aris/install.json 出现）。

        安装目标是容器里的 ``ARL_PROJECT_DIR``，即 ``/workspace/<role>``，
        不是 ``/workspace`` 本身。

        Args:
            container_name: 容器名称
            role: 容器角色，决定项目目录
            timeout: 超时秒数

        Returns:
            True 表示安装完成，False 表示超时
        """
        start = asyncio.get_event_loop().time()
        check_cmd = f"test -f /workspace/{role.value}/.aris/install.json"

        while (asyncio.get_event_loop().time() - start) < timeout:
            try:
                result = self._docker.containers.get(container_name).exec_run(
                    ["sh", "-c", check_cmd], demux=False
                )
                if result.exit_code == 0:
                    return True
            except Exception:
                pass
            await asyncio.sleep(3)
        return False

    def _prepare_host_directories(self, session_id: str, role: ARLRole) -> str:
        """准备宿主机挂载目录。

        创建 <HOST_PROJECT_HOME>/arl/<session_id>/<role>/{paseo-home,workspace}

        Args:
            session_id: 会话 ID
            role: 容器角色

        Returns:
            角色根目录的宿主机路径
        """
        base = Path(settings.HOST_PROJECT_HOME) / "arl" / session_id / role.value
        (base / "paseo-home").mkdir(parents=True, exist_ok=True)
        (base / "workspace").mkdir(parents=True, exist_ok=True)
        return str(base)

    async def create_session(
        self,
        session_id: str,
        project_id: int,
        user_id: str,
        daemon_config: ARLDaemonConfig,
    ) -> ARLSession:
        """创建 ARL 会话，启动 worker 和 validator 容器。

        Args:
            session_id: 数据库会话 ID（用作容器名的一部分）
            project_id: 项目 ID
            user_id: 用户 ID（UUID 字符串）
            daemon_config: Daemon 配置（API 凭据与模型）

        Returns:
            ARLSession 对象

        Raises:
            RuntimeError: 容器启动或就绪检查失败
        """
        # 检查是否已存在容器（防止重复启动）
        existing_session = self.get_session(session_id)
        if existing_session:
            logger.info("ARL 会话 {} 已存在，复用现有容器", session_id)
            return existing_session

        logger.info(
            "创建 ARL 会话: session={}, project={}, user={}",
            session_id,
            project_id,
            user_id,
        )

        # 准备容器信息
        containers_info = {}
        for role in [ARLRole.WORKER, ARLRole.VALIDATOR]:
            name = self._container_name(session_id, role)
            password = self._derive_password(session_id, role)
            logger.debug("派生容器密码: role={}, name={}", role.value, name)
            port = self._allocate_port(session_id, role)
            containers_info[role] = ARLContainerInfo(
                name=name,
                port=port,
                password=password,
                status=ARLSessionStatus.STARTING,
            )

        # 创建会话对象（初始状态 STARTING）
        session = ARLSession(
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
            status=ARLSessionStatus.STARTING,
            worker=containers_info[ARLRole.WORKER],
            validator=containers_info[ARLRole.VALIDATOR],
            created_at=datetime.now(UTC),
        )
        self._sessions[session_id] = session

        try:
            # 启动两个容器
            await self._start_container(
                ARLRole.WORKER, session, daemon_config
            )
            await self._start_container(
                ARLRole.VALIDATOR, session, daemon_config
            )

            # 等待两个容器都就绪
            await self._wait_both_ready(session)

            # 更新状态为 READY
            session.status = ARLSessionStatus.READY
            session.worker.status = ARLSessionStatus.READY
            session.validator.status = ARLSessionStatus.READY
            logger.info("ARL 会话 {} 启动完成", session_id)
            return session

        except Exception as e:
            logger.exception("ARL 会话 {} 启动失败: {}", session_id, e)
            session.status = ARLSessionStatus.ERROR
            session.error_message = str(e)
            # 失败时清理已创建的容器
            await self.cleanup_session(session_id)
            raise RuntimeError(f"ARL 会话启动失败: {e}") from e

    async def _start_container(
        self,
        role: ARLRole,
        session: ARLSession,
        daemon_config: ARLDaemonConfig,
    ) -> None:
        """启动单个 ARL 容器。

        Args:
            role: 容器角色
            session: 会话对象
            daemon_config: Daemon 配置
        """
        container_info = session.worker if role == ARLRole.WORKER else session.validator
        image = (
            settings.ARL_WORKER_IMAGE
            if role == ARLRole.WORKER
            else settings.ARL_VALIDATOR_IMAGE
        )

        # 准备宿主机目录
        host_root = self._prepare_host_directories(session.session_id, role)

        # 准备环境变量（Worker 和 Validator 使用不同的 API）
        env = {
            "PASEO_PASSWORD": container_info.password,
            "PASEO_LISTEN": f"0.0.0.0:{settings.ARL_PASEO_PORT}",
        }

        if role == ARLRole.WORKER:
            # Worker 使用 Claude API（Claude CLI 自带 /v1 前缀，base URL 不应带 /v1）
            env.update({
                "ANTHROPIC_BASE_URL": _normalize_base_url(
                    daemon_config.base_url, ensure_v1=False
                ),
                "ANTHROPIC_AUTH_TOKEN": daemon_config.auth_token,
                "ANTHROPIC_MODEL": daemon_config.model,
                "ANTHROPIC_SMALL_FAST_MODEL": daemon_config.small_fast_model or "claude-haiku-4-5-20251001",
            })
        else:
            # Validator 使用 OpenAI/Codex API（Codex CLI 要求显式带 /v1）
            env.update({
                "OPENAI_BASE_URL": _normalize_base_url(
                    daemon_config.validator_base_url or "https://api.openai.com/v1",
                    ensure_v1=True,
                ),
                "OPENAI_API_KEY": daemon_config.validator_api_key,
                "OPENAI_MODEL": daemon_config.validator_model or "gpt-5.5",
            })

        # PASEO_HOSTNAMES：让两个容器能互相通信（validator 调 worker 的 script RPC）
        worker_name = self._container_name(session.session_id, ARLRole.WORKER)
        validator_name = self._container_name(session.session_id, ARLRole.VALIDATOR)
        env["PASEO_HOSTNAMES"] = (
            f"worker={worker_name}:{settings.ARL_PASEO_PORT}\n"
            f"validator={validator_name}:{settings.ARL_PASEO_PORT}"
        )

        # 挂载卷
        volumes = {
            str(Path(host_root) / "paseo-home"): {
                "bind": "/home/paseo",
                "mode": "rw",
            },
            str(Path(host_root) / "workspace"): {
                "bind": "/workspace",
                "mode": "rw",
            },
        }

        # 端口映射（daemon WS 端口）
        ports = {f"{settings.ARL_PASEO_PORT}/tcp": ("127.0.0.1", container_info.port)}

        # 标签（用于过滤与清理）
        labels = {
            SESSION_LABEL: session.session_id,
            ROLE_LABEL: role.value,
            "llm4ad.arl.project_id": str(session.project_id),
            "llm4ad.arl.user_id": str(session.user_id),
        }

        logger.info(
            "启动 {} 容器: name={}, image={}, port={}",
            role.value,
            container_info.name,
            image,
            container_info.port,
        )

        try:
            container: Container = self._docker.containers.run(
                image=image,
                name=container_info.name,
                detach=True,
                network=settings.arl_network_name,
                environment=env,
                volumes=volumes,
                ports=ports,
                labels=labels,
                mem_limit=settings.ARL_CONTAINER_MEMORY_LIMIT,
                cpu_count=int(settings.ARL_CONTAINER_CPU_LIMIT),
                remove=False,  # 不自动删除，便于排错
            )
            logger.info("容器 {} 已创建: id={}", container_info.name, container.id[:12])
        except DockerException as e:
            logger.exception("启动容器 {} 失败: {}", container_info.name, e)
            raise RuntimeError(f"启动 {role.value} 容器失败: {e}") from e

    async def _wait_both_ready(self, session: ARLSession) -> None:
        """等待两个容器都就绪（daemon + 安装器）。

        Args:
            session: 会话对象

        Raises:
            RuntimeError: 任一容器超时未就绪
        """
        for role in [ARLRole.WORKER, ARLRole.VALIDATOR]:
            container_info = session.worker if role == ARLRole.WORKER else session.validator
            logger.info("等待 {} daemon 就绪...", container_info.name)
            health_ok = await self._wait_for_health(
                container_info.name, settings.ARL_DAEMON_READY_TIMEOUT
            )
            if not health_ok:
                raise RuntimeError(
                    f"{role.value} daemon 在 {settings.ARL_DAEMON_READY_TIMEOUT}s 内未就绪"
                )

            logger.info("等待 {} 安装器完成...", container_info.name)
            installer_ok = await self._wait_for_installer(
                container_info.name, role, settings.ARL_INSTALL_READY_TIMEOUT
            )
            if not installer_ok:
                raise RuntimeError(
                    f"{role.value} 安装器在 {settings.ARL_INSTALL_READY_TIMEOUT}s 内未完成"
                )

            logger.info("{} 容器就绪", container_info.name)

    async def cleanup_session(self, session_id: str) -> None:
        """清理会话，删除关联的容器。

        Args:
            session_id: 会话 ID
        """
        session = self._sessions.pop(session_id, None)
        if not session:
            logger.warning("清理会话 {} 失败: 会话不存在", session_id)
            return

        logger.info("清理 ARL 会话 {}", session_id)
        for role in [ARLRole.WORKER, ARLRole.VALIDATOR]:
            container_info = session.worker if role == ARLRole.WORKER else session.validator
            try:
                container = self._docker.containers.get(container_info.name)
                container.remove(force=True)
                logger.info("已删除容器 {}", container_info.name)
            except NotFound:
                logger.debug("容器 {} 不存在，跳过删除", container_info.name)
            except DockerException as e:
                logger.warning("删除容器 {} 失败: {}", container_info.name, e)

    def get_session(self, session_id: str) -> ARLSession | None:
        """获取会话对象。

        Args:
            session_id: 会话 ID

        Returns:
            ARLSession 或 None（会话不存在）
        """
        return self._sessions.get(session_id)

    def list_sessions(self, project_id: int | None = None) -> list[ARLSession]:
        """列出活跃会话。

        Args:
            project_id: 项目 ID 过滤（None 则返回全部）

        Returns:
            会话列表
        """
        sessions = list(self._sessions.values())
        if project_id is not None:
            sessions = [s for s in sessions if s.project_id == project_id]
        return sessions


# 全局单例
_runtime_service: ARLRuntimeService | None = None


def get_arl_runtime() -> ARLRuntimeService:
    """获取 ARL 运行时服务单例。"""
    global _runtime_service
    if _runtime_service is None:
        _runtime_service = ARLRuntimeService()
    return _runtime_service
