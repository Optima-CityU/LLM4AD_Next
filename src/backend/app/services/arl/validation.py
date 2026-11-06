"""Validator 验证服务（Contrejour MCP）的连接约定与状态探测。

ARL 的使用流程是**先 Validator 后 Worker**：用户先在 Validator 侧多轮交互，由
它拉起验证服务（MCP）；之后 Worker 通过该服务拉取 ``task.md`` 并提交结果。

连接参数由 backend **事先约定**，而不是事后从 Validator 的产物里读出再同步：

- Validator 容器创建时注入 ``CONTREJOUR_HOST`` / ``CONTREJOUR_PORT`` /
  ``CONTREJOUR_URL`` / ``CONTREJOUR_TOKEN``，验证服务的 setup 直接采用这组值，
  不再自行生成地址和 token（见 :func:`validator_env`）。
- 地址用 Validator 的容器名：两个容器在同一 Docker 网络里，容器名即 DNS 名。
- token 由 ``SECRET_KEY`` 与会话 ID 经 HMAC 确定性派生，容器重建、backend
  重启都算出同一个值，无需持久化，也不会出现服务端与客户端 token 不一致。
- Worker 的 ``CLAUDE.md`` 里直接写死同一组地址与 token（见
  :func:`write_worker_guide`），所以 Worker 端不需要环境变量，也没有手动同步步骤。

状态探测打验证服务的 ``/health``（无鉴权，返回 ``{"status": "ok"}``），而不是
只看文件是否写好：文件在不代表服务在跑。

验证服务是 ``paseo.json`` 里的 service 脚本，Paseo 不会在 daemon 重启后自动拉起
它，所以容器休眠恢复后服务是停着的。:func:`start_validation_service` 负责把它
重新拉起：会话连上时自动尝试一次，前端也可以手动触发。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from app.core.config import settings
from app.core.env_detect import use_container_network
from app.services.arl.types import ARLRole

if TYPE_CHECKING:
    from app.services.arl.daemon_client import PaseoDaemonClient

#: 验证服务在 Validator 容器内的监听地址。0.0.0.0 才能让 Worker 跨容器访问
VALIDATION_HOST = "0.0.0.0"

#: 验证服务的 MCP 端点路径（相对服务根地址）
MCP_PATH = "/mcp"

#: 验证服务的健康检查路径，无鉴权
HEALTH_PATH = "/health"

#: 健康检查的超时（秒）。服务与 backend 同网络，正常响应在毫秒级
_HEALTH_TIMEOUT = 2.0

#: 写给 Worker 的说明文件。Claude Code 每轮对话都会读工作目录下的 CLAUDE.md
WORKER_GUIDE_FILENAME = "CLAUDE.md"

#: CLAUDE.md 中由系统维护的段落边界。只替换这一段，保留 agent 与 setup 写的内容
GUIDE_BEGIN_MARKER = "<!-- llm4ad:validation-guide:begin -->"
GUIDE_END_MARKER = "<!-- llm4ad:validation-guide:end -->"

#: validator-setup 写进 ``paseo.json`` 的验证服务脚本名
SERVICE_SCRIPT_NAME = "contrejour"

#: Validator 容器内的项目目录（Paseo 工作区按它查找）
VALIDATOR_PROJECT_DIR = f"/workspace/{ARLRole.VALIDATOR.value}"

#: setup apply 成功后才会写出的冻结配置，相对 Validator 项目目录
_VALIDATION_CONFIG = Path(".contrejour") / "validation" / "config.json"

#: 发起启动后的宽限期（秒）。期间视为「启动中」：不再重复启动，也不展示重启
#: 按钮。服务正常一两秒就能通过健康检查；超过宽限期仍不健康就当作启动失败，
#: 允许再试。
START_GRACE_SECONDS = 20.0

#: 一次启动流程（查工作区 + 停 + 启）的总超时
_START_TIMEOUT = 30.0

#: 手动重启时「停止后再启动」的重试：daemon 异步更新脚本状态，刚停完立刻
#: 启动可能还被判为运行中
_RESTART_RETRIES = 6
_RESTART_RETRY_DELAY = 0.5

#: 每个会话的启动锁与最近一次成功发起启动的时间（monotonic）。同一 backend
#: 进程内的多个标签页、自动恢复与手动点击共用，保证同一时刻只有一次启动。
_start_locks: dict[str, asyncio.Lock] = {}
_last_start_at: dict[str, float] = {}


@dataclass
class ValidationStatus:
    """Validator 验证服务的探测结果。

    Attributes:
        configured: 验证服务是否在线（``/health`` 返回 200）
        url: Worker 侧使用的服务根地址，形如 ``http://<validator 容器名>:8765``
        reason: 未就绪时的具体原因，直接展示给用户
        declared: Validator 是否已搭建好验证服务（见 :func:`is_validation_declared`），
            只有搭建过的服务才能重新启动
        starting: 是否处于启动宽限期内（见 :func:`is_validation_starting`）
        detail: 诊断用的补充信息，只进日志
    """

    configured: bool = False
    url: str | None = None
    reason: str | None = None
    declared: bool = False
    starting: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """转成推给前端的 payload。

        不下发 token：前端用不到它，而 WS 帧会进浏览器控制台和前端日志。

        Returns:
            前端可直接消费的状态字典
        """
        return {
            "configured": self.configured,
            "url": self.url,
            "reason": self.reason,
            "declared": self.declared,
            "starting": self.starting,
        }

    def same_state_as(self, other: ValidationStatus | None) -> bool:
        """与另一次探测结果在**前端可见维度**上是否一致。

        用于轮询去重：只有用户能看出差别时才推帧。

        Args:
            other: 上一次的状态，None 表示还没推过

        Returns:
            True 表示无需重推
        """
        if other is None:
            return False
        return (
            self.configured == other.configured
            and self.url == other.url
            and self.reason == other.reason
            and self.declared == other.declared
            and self.starting == other.starting
        )


def role_workspace(session_id: str, role: ARLRole) -> Path:
    """返回角色工作目录在 backend 侧可见的路径。

    容器里是 ``/workspace/<role>``，而 ``/workspace`` 挂的是宿主侧
    ``<role>/workspace``，故这里是 ``<role>/workspace/<role>``。

    Args:
        session_id: 会话 ID
        role: 容器角色

    Returns:
        工作目录的 backend 侧绝对路径
    """
    # 延迟导入：runtime 导入本模块组装环境变量，顶层互相导入会成环
    from app.services.arl.runtime import _session_root

    docker_root, _ = _session_root(session_id)
    return Path(docker_root) / role.value / "workspace" / role.value


def validation_url(session_id: str) -> str:
    """返回验证服务的根地址（不含 ``/mcp``），Worker 与 backend 都用它。

    Args:
        session_id: 会话 ID

    Returns:
        形如 ``http://llm4ad-arl-v-<session_id>:8765``
    """
    from app.services.arl.runtime import container_name

    host = container_name(session_id, ARLRole.VALIDATOR)
    return f"http://{host}:{settings.ARL_VALIDATION_PORT}"


def derive_validation_token(session_id: str) -> str:
    """派生每会话的验证服务 token（HMAC-SHA256，64 位十六进制）。

    确定性派生而非随机生成：容器重建后 Validator 的环境变量与 Worker 的
    ``CLAUDE.md`` 仍是同一个值，不会出现一边换了新 token、另一边还拿着旧值。
    以会话 ID（容器名由它派生）为输入，并用 ``SECRET_KEY`` 做密钥——只拿容器名
    哈希的话，知道会话 ID 的人就能算出 token。

    消息带 ``contrejour:`` 前缀，与 daemon 密码、LLM 代理 token 互不相同。

    Args:
        session_id: 会话 ID

    Returns:
        64 字符十六进制 token（满足验证服务至少 32 字符的要求）
    """
    message = f"contrejour:{session_id}".encode()
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, message, digestmod="sha256").hexdigest()


def validator_env(session_id: str) -> dict[str, str]:
    """组装 Validator 容器里验证服务所需的环境变量。

    验证服务的 setup 读到 ``CONTREJOUR_URL`` / ``CONTREJOUR_TOKEN`` 时直接采用，
    不再让用户填地址、也不新建 token。

    Args:
        session_id: 会话 ID

    Returns:
        环境变量字典
    """
    return {
        "CONTREJOUR_HOST": VALIDATION_HOST,
        "CONTREJOUR_PORT": str(settings.ARL_VALIDATION_PORT),
        "CONTREJOUR_URL": validation_url(session_id),
        "CONTREJOUR_TOKEN": derive_validation_token(session_id),
    }


def render_worker_guide(session_id: str) -> str:
    """生成写进 Worker ``CLAUDE.md`` 的验证服务连接段落。

    地址与 token 直接写死：两者都是确定性派生的，不会在会话生命周期内变化。

    Args:
        session_id: 会话 ID

    Returns:
        带首尾标记的 Markdown 段落
    """
    url = validation_url(session_id)
    token = derive_validation_token(session_id)
    return f"""{GUIDE_BEGIN_MARKER}
# 验证服务连接

Validator 提供的验证服务（MCP）连接信息如下，配置 worker 连接时直接使用：

- 服务根地址：`{url}`
- MCP 地址：`{url}{MCP_PATH}`
- Token：`{token}`

调用时把 token 放在请求头里：`Authorization: Bearer {token}`

## 注意

- 该服务运行在 Validator 容器内；连接失败说明 Validator 侧的验证服务尚未
  启动或已停止，请在左侧 Validator 对话中拉起它。
- 本段落（含首尾标记）由系统维护，请勿修改或删除。
{GUIDE_END_MARKER}
"""


def merge_worker_guide(existing: str | None, block: str) -> str:
    """把验证服务段落合并进已有的 ``CLAUDE.md`` 文本。

    - 已有系统段落：原位替换
    - 没有：追加到末尾（agent 可能整篇重写过 CLAUDE.md，把标记弄丢了）
    - 文件不存在或为空：只写该段落

    Args:
        existing: 现有 CLAUDE.md 内容；文件不存在时为 None
        block: :func:`render_worker_guide` 生成的段落

    Returns:
        合并后的完整文本
    """
    if not existing or not existing.strip():
        return block

    begin = existing.find(GUIDE_BEGIN_MARKER)
    end = existing.find(GUIDE_END_MARKER, begin + 1) if begin != -1 else -1
    if begin != -1 and end != -1:
        tail = existing[end + len(GUIDE_END_MARKER) :]
        # 吞掉旧段落末尾的换行，避免每次写入多出空行
        if tail.startswith("\r\n"):
            tail = tail[2:]
        elif tail.startswith("\n"):
            tail = tail[1:]
        return existing[:begin] + block + tail

    return existing.rstrip() + "\n\n" + block


def write_worker_guide(session_id: str) -> bool:
    """确保 Worker 的 ``CLAUDE.md`` 含最新的验证服务连接段落。

    **阻塞函数**（文件 IO），调用方需用 ``asyncio.to_thread`` 下沉。

    每次会话就绪时调用，幂等：内容未变时不写盘，不打扰 agent 的文件监听；
    段落外的内容（setup 写的角色段、agent 自己的笔记）原样保留。

    Args:
        session_id: 会话 ID

    Returns:
        True 表示本次写入了文件，False 表示内容已是最新

    Raises:
        OSError: 读写失败
    """
    workspace = role_workspace(session_id, ARLRole.WORKER)
    guide = workspace / WORKER_GUIDE_FILENAME
    existing = guide.read_text(encoding="utf-8") if guide.is_file() else None
    merged = merge_worker_guide(existing, render_worker_guide(session_id))
    if merged == existing:
        return False

    workspace.mkdir(parents=True, exist_ok=True)
    guide.write_text(merged, encoding="utf-8")
    logger.info("✅ [Session {}] 已写入 Worker {} 验证服务连接段落", session_id, guide)
    return True


def _probe_base_url(session_id: str) -> str:
    """返回 backend 探测验证服务时用的根地址。

    与 :func:`app.services.arl.runtime.daemon_endpoints` 同一思路：backend 在
    容器内时走 Docker 网络 + 容器名，与 Worker 的路径一致；裸跑在宿主机时走
    Validator 发布到 127.0.0.1 的映射端口。

    Args:
        session_id: 会话 ID

    Returns:
        不含 ``/health`` 的根地址
    """
    if use_container_network():
        return validation_url(session_id)

    from app.services.arl.runtime import validation_host_port

    return f"http://127.0.0.1:{validation_host_port(session_id)}"


def probe_validation(session_id: str) -> ValidationStatus:
    """探测 Validator 的验证服务是否在线（请求它的 ``/health``）。

    **阻塞函数**（网络 IO），调用方需用 ``asyncio.to_thread`` 下沉。

    不抛异常：这是每 5 秒跑一次的轮询，一次失败不该把整条 WS 拆掉。

    Args:
        session_id: 会话 ID

    Returns:
        探测结果。``url`` 始终是 Worker 侧地址，与探测实际走的地址无关
    """
    url = validation_url(session_id)
    probe_url = f"{_probe_base_url(session_id)}{HEALTH_PATH}"
    try:
        resp = httpx.get(probe_url, timeout=_HEALTH_TIMEOUT)
        healthy = resp.status_code == 200
        detail: dict[str, Any] = {"probe_url": probe_url, "status_code": resp.status_code}
    except httpx.HTTPError as exc:
        healthy = False
        detail = {"probe_url": probe_url, "error": f"{type(exc).__name__}: {exc}"}

    declared = is_validation_declared(session_id)
    if healthy:
        return ValidationStatus(configured=True, url=url, declared=declared, detail=detail)
    return ValidationStatus(
        url=url,
        reason="验证服务尚未启动（健康检查无响应）",
        declared=declared,
        starting=is_validation_starting(session_id),
        detail=detail,
    )


def is_validation_declared(session_id: str) -> bool:
    """判断 Validator 是否已搭建好验证服务，即「有东西可以重新启动」。

    **阻塞函数**（文件 IO），调用方需用 ``asyncio.to_thread`` 下沉。

    两个条件都满足才算：

    - ``.contrejour/validation/config.json`` 存在：validator-setup 的 apply 已
      成功（benchmark 安装、配置冻结都在它之前完成）；
    - ``paseo.json`` 的 ``scripts.contrejour`` 是带命令的 ``service`` 脚本：apply
      最后一步登记的启动命令，重启就是让 Paseo 再跑一次它。

    任何读取或解析错误都按「未搭建」处理并只记日志：宁可不启动，也不对一份
    半成品配置反复拉服务。

    Args:
        session_id: 会话 ID

    Returns:
        True 表示验证服务已搭建，可以（重新）启动
    """
    try:
        project = role_workspace(session_id, ARLRole.VALIDATOR)
        if not (project / _VALIDATION_CONFIG).is_file():
            return False
        paseo_json = project / "paseo.json"
        if not paseo_json.is_file():
            return False
        config = json.loads(paseo_json.read_text(encoding="utf-8"))
        scripts = config.get("scripts") if isinstance(config, dict) else None
        script = scripts.get(SERVICE_SCRIPT_NAME) if isinstance(scripts, dict) else None
        if not isinstance(script, dict) or script.get("type") != "service":
            return False
        command = script.get("command")
        return isinstance(command, str) and bool(command.strip())
    except Exception as exc:  # noqa: BLE001 - 检查失败只意味着不启动
        logger.warning("⚠️ [Session {}] 检查验证服务配置失败: {}", session_id, exc)
        return False


def is_validation_starting(session_id: str) -> bool:
    """验证服务是否正在启动：启动流程进行中，或刚发起启动、仍在宽限期内。

    Args:
        session_id: 会话 ID

    Returns:
        True 表示此时不应再次启动，前端显示「启动中」而非重启按钮
    """
    lock = _start_locks.get(session_id)
    if lock is not None and lock.locked():
        return True
    started_at = _last_start_at.get(session_id)
    return started_at is not None and time.monotonic() - started_at < START_GRACE_SECONDS


@dataclass
class ValidationStartResult:
    """一次（重新）启动验证服务的结果。

    Attributes:
        ok: 服务已在线、已成功发起启动或正在启动
        action: ``healthy``（本就在线）/ ``started``（已发起启动）/ ``starting``
            （启动中，本次跳过）/ ``not_declared``（还没搭建）/ ``failed``
        message: 失败或跳过的原因，可直接展示给用户
    """

    ok: bool
    action: str
    message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """转成推给前端的 payload。

        Returns:
            结果字典
        """
        return {"ok": self.ok, "action": self.action, "message": self.message}


async def start_validation_service(
    client: PaseoDaemonClient,
    session_id: str,
    *,
    restart: bool = False,
) -> ValidationStartResult:
    """让 Validator 的 Paseo daemon 把验证服务脚本（重新）启动起来。

    **不抛异常**（取消除外）：检查不通过或任何一步出错都返回失败结果，由调用
    方决定是只记日志（自动恢复）还是提示用户（手动重启）。

    幂等：同一会话同一时刻只允许一个启动流程；发起启动后的宽限期内再次调用
    直接返回 ``starting``，避免连点或多个标签页把刚起来的服务又停掉。

    流程：确认已搭建 → 健康检查已通过则什么也不做 → 用项目目录换出 Paseo
    工作区 ID → 脚本在 Paseo 里仍是 running（进程在但不健康）时，只有
    ``restart=True`` 才先停再启，否则不打扰它 → 启动。

    Args:
        client: 已连接的 Validator daemon 客户端
        session_id: 会话 ID
        restart: 脚本仍在运行但不健康时是否先停止再启动（手动重启用）

    Returns:
        启动结果
    """
    if is_validation_starting(session_id):
        return ValidationStartResult(ok=True, action="starting")

    lock = _start_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        try:
            return await asyncio.wait_for(
                _start_validation_locked(client, session_id, restart=restart),
                timeout=_START_TIMEOUT,
            )
        except TimeoutError:
            logger.warning("⏰ [Session {}] 启动验证服务超时", session_id)
            return ValidationStartResult(
                ok=False, action="failed", message="Timed out starting the validation service"
            )
        except Exception as exc:  # noqa: BLE001 - 失败只体现在结果里，不外抛
            logger.warning("⚠️ [Session {}] 启动验证服务失败: {}", session_id, exc)
            return ValidationStartResult(ok=False, action="failed", message=str(exc))


async def _start_validation_locked(
    client: PaseoDaemonClient,
    session_id: str,
    *,
    restart: bool,
) -> ValidationStartResult:
    """:func:`start_validation_service` 持锁后的实际流程，异常由调用方兜底。

    Args:
        client: Validator daemon 客户端
        session_id: 会话 ID
        restart: 脚本在运行但不健康时是否先停止

    Returns:
        启动结果
    """
    if not await asyncio.to_thread(is_validation_declared, session_id):
        return ValidationStartResult(
            ok=False,
            action="not_declared",
            message="The validation service has not been set up yet",
        )

    status = await asyncio.to_thread(probe_validation, session_id)
    if status.configured:
        return ValidationStartResult(ok=True, action="healthy")

    workspace = await client.open_workspace(VALIDATOR_PROJECT_DIR)
    workspace_id = str(workspace["id"])
    scripts = workspace.get("scripts")
    script = next(
        (
            item
            for item in (scripts if isinstance(scripts, list) else [])
            if isinstance(item, dict) and item.get("scriptName") == SERVICE_SCRIPT_NAME
        ),
        None,
    )
    running = script is not None and script.get("lifecycle") == "running"

    if running:
        if not restart:
            # 进程在但健康检查没过：可能是 agent 刚拉起、还在初始化，不打扰它
            logger.info(
                "ℹ️ [Session {}] 验证服务脚本仍在运行但未通过健康检查，跳过自动启动",
                session_id,
            )
            return ValidationStartResult(
                ok=False,
                action="failed",
                message="The validation service process is running but not healthy",
            )
        await client.stop_workspace_script(workspace_id, SERVICE_SCRIPT_NAME)

    for attempt in range(_RESTART_RETRIES):
        try:
            await client.start_workspace_script(workspace_id, SERVICE_SCRIPT_NAME)
            break
        except RuntimeError as exc:
            # 只在刚停止、daemon 还没把状态改过来时重试
            last_attempt = attempt == _RESTART_RETRIES - 1
            if not running or "already running" not in str(exc) or last_attempt:
                raise
            await asyncio.sleep(_RESTART_RETRY_DELAY)

    _last_start_at[session_id] = time.monotonic()
    logger.info("✅ [Session {}] 已发起启动验证服务（restart={}）", session_id, restart)
    return ValidationStartResult(ok=True, action="started")
