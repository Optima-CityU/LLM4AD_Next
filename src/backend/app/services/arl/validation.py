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
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.core.config import settings
from app.core.env_detect import use_container_network
from app.services.arl.types import ARLRole

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


@dataclass
class ValidationStatus:
    """Validator 验证服务的探测结果。

    Attributes:
        configured: 验证服务是否在线（``/health`` 返回 200）
        url: Worker 侧使用的服务根地址，形如 ``http://<validator 容器名>:8765``
        reason: 未就绪时的具体原因，直接展示给用户
        detail: 诊断用的补充信息，只进日志
    """

    configured: bool = False
    url: str | None = None
    reason: str | None = None
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

    if healthy:
        return ValidationStatus(configured=True, url=url, detail=detail)
    return ValidationStatus(
        url=url,
        reason="验证服务尚未启动（健康检查无响应）",
        detail=detail,
    )
