"""Validator 验证服务的探测与同步。

ARL 的使用流程是**先 Validator 后 Worker**：用户先在 Validator 侧多轮交互，
由它拉起一个验证服务（MCP）并产出 ``task.md``；之后 Worker 才需要知道这个
服务的地址和 token，并拿到同一份 ``task.md``。

这中间没有任何可订阅的事件——服务是 agent 在容器里自行启动的，daemon 不感知。
故这里走**文件探测**：两个容器的 ``/workspace`` 都是 bind mount，backend 直接
读宿主侧文件即可，不必 ``docker exec``（省掉每轮在容器里起进程的开销，与
:func:`app.services.arl.runtime._wait_for_installer` 同一思路）。

连接地址刻意**不用** ``config.json`` 里 ``service.public_url`` 的主机部分：
那是 Validator 容器从自己视角看到的地址（多为 localhost / 容器内 IP），Worker
容器连不上。只取其端口，主机换成 Validator 的容器名——两个容器在同一 Docker
网络里，容器名即可解析。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from app.services.arl.types import ARLRole

#: 验证服务产物在容器内 ``/workspace/<role>`` 下的相对位置
VALIDATION_DIR = ".aris/validation"
TOKEN_FILENAME = "token"
CONFIG_FILENAME = "config.json"

#: Validator 产出、需要同步给 Worker 的任务说明文件
TASK_FILENAME = "task.md"

#: 写给 Worker 的说明文件。Claude Code 每轮对话都会读工作目录下的 CLAUDE.md，
#: 但 agent 运行中也会改写它，所以这里只放一段指引，凭据另存独立文件。
WORKER_GUIDE_FILENAME = "CLAUDE.md"

#: Worker 侧验证服务连接信息（url / token），相对 Worker 工作目录。
#: 与 CLAUDE.md 分开存放：CLAUDE.md 被 agent 改写后凭据不会跟着丢。
WORKER_CONNECTION_PATH = f"{VALIDATION_DIR}/connection.json"

#: CLAUDE.md 中由系统维护的段落边界。同步时只替换这一段，保留 agent 自己写的内容
GUIDE_BEGIN_MARKER = "<!-- llm4ad:validation-guide:begin -->"
GUIDE_END_MARKER = "<!-- llm4ad:validation-guide:end -->"


@dataclass
class ValidationStatus:
    """Validator 验证服务的探测结果。

    Attributes:
        configured: 四项条件（token、config.json 可解析、端口可取、task.md）
            是否都满足。只有 True 时 ``url`` / ``token`` 才有意义。
        url: Worker 侧可用的服务地址，形如 ``http://<validator 容器名>:<端口>``
        token: 调用验证服务用的 token（已去首尾空白）
        has_task: Validator 工作目录下是否已有 ``task.md``
        reason: 未配置完成时的具体原因，直接展示给用户
        detail: 诊断用的补充信息（缺哪个文件、JSON 解析失败在哪），只进日志
    """

    configured: bool = False
    url: str | None = None
    token: str | None = None
    has_task: bool = False
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """转成推给前端的 payload。

        刻意**不下发 token 明文**：前端不需要它（调用验证服务的是容器里的
        Worker agent，凭据由独立的连接信息文件落盘传递），而 WS 帧会进浏览器控制台
        和前端日志。只给一个布尔位让界面能显示「凭据已就绪」。

        Returns:
            前端可直接消费的状态字典
        """
        return {
            "configured": self.configured,
            "url": self.url,
            "has_token": bool(self.token),
            "has_task": self.has_task,
            "reason": self.reason,
        }

    def same_state_as(self, other: ValidationStatus | None) -> bool:
        """与另一次探测结果在**前端可见维度**上是否一致。

        用于轮询去重：只有用户能看出差别时才推帧，否则每 5 秒一帧会把
        前端的消息处理和日志刷满。

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
            and bool(self.token) == bool(other.token)
            and self.has_task == other.has_task
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
    # 延迟导入：runtime 导入本模块做同步，顶层互相导入会成环
    from app.services.arl.runtime import _session_root

    docker_root, _ = _session_root(session_id)
    return Path(docker_root) / role.value / "workspace" / role.value


def _extract_port(public_url: str) -> int | None:
    """从 ``service.public_url`` 里取端口。

    只取端口，主机部分丢弃——见模块文档。URL 没写端口时按 scheme 回落到
    80/443，这样 Validator 把服务挂在标准端口上也能连。

    Args:
        public_url: ``config.json`` 里的 ``service.public_url``

    Returns:
        端口号，取不到时 None
    """
    try:
        parsed = urlparse(public_url if "//" in public_url else f"//{public_url}")
    except ValueError:
        return None

    if parsed.port:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def probe_validation(session_id: str) -> ValidationStatus:
    """探测 Validator 的验证服务是否已配置好。

    **阻塞函数**（纯文件 IO），调用方需用 ``asyncio.to_thread`` 下沉。

    依次检查：

    1. ``.aris/validation/token`` 存在且去空白后非空
    2. ``.aris/validation/config.json`` 存在且是合法 JSON
    3. 其中 ``service.public_url`` 能解析出端口
    4. 工作目录下有 ``task.md``

    任一不满足都返回 ``configured=False`` 并带上可直接展示的 ``reason``。
    不抛异常：这是每 5 秒跑一次的轮询，一次读失败（文件正在被写、权限抖动）
    不该把整条 WS 拆掉，下一轮自然恢复。

    Args:
        session_id: 会话 ID

    Returns:
        探测结果
    """
    from app.services.arl.runtime import container_name

    workspace = role_workspace(session_id, ARLRole.VALIDATOR)
    validation_dir = workspace / VALIDATION_DIR
    token_path = validation_dir / TOKEN_FILENAME
    config_path = validation_dir / CONFIG_FILENAME
    task_path = workspace / TASK_FILENAME

    # task.md 独立于服务配置：服务起了但 task.md 还没写完是常见中间态，
    # 要能分别告诉用户差哪一步，故先探好它再逐项校验服务配置。
    has_task = task_path.is_file()

    try:
        raw_token = token_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ValidationStatus(
            reason="验证服务尚未启动（未找到 token 文件）",
            has_task=has_task,
            detail={"missing": str(token_path)},
        )
    except OSError as exc:
        return ValidationStatus(
            reason="验证服务 token 文件读取失败",
            has_task=has_task,
            detail={"path": str(token_path), "error": str(exc)},
        )

    token = raw_token.strip()
    if not token:
        return ValidationStatus(
            reason="验证服务 token 文件为空，可能仍在写入",
            has_task=has_task,
            detail={"path": str(token_path)},
        )

    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ValidationStatus(
            reason="验证服务尚未启动（未找到 config.json）",
            has_task=has_task,
            detail={"missing": str(config_path)},
        )
    except OSError as exc:
        return ValidationStatus(
            reason="验证服务 config.json 读取失败",
            has_task=has_task,
            detail={"path": str(config_path), "error": str(exc)},
        )

    try:
        config = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        # 文件写了一半就被读到会走这里，下一轮即恢复，故不是错误
        return ValidationStatus(
            reason="验证服务 config.json 格式不是合法 JSON，可能仍在写入",
            has_task=has_task,
            detail={"path": str(config_path), "error": str(exc)},
        )

    if not isinstance(config, dict):
        return ValidationStatus(
            reason="验证服务 config.json 顶层不是对象",
            has_task=has_task,
            detail={"path": str(config_path), "type": type(config).__name__},
        )

    service = config.get("service")
    public_url = service.get("public_url") if isinstance(service, dict) else None
    if not isinstance(public_url, str) or not public_url:
        return ValidationStatus(
            reason="验证服务 config.json 缺少 service.public_url",
            has_task=has_task,
            detail={"path": str(config_path)},
        )

    port = _extract_port(public_url)
    if port is None:
        return ValidationStatus(
            reason=f"无法从 service.public_url 解析端口: {public_url}",
            has_task=has_task,
            detail={"public_url": public_url},
        )

    # 主机用 Validator 容器名：两个容器同网络，容器名即 DNS 名。
    # public_url 里的主机是 Validator 的自视角地址，Worker 连不上。
    host = container_name(session_id, ARLRole.VALIDATOR)
    return ValidationStatus(
        configured=True,
        url=f"http://{host}:{port}",
        token=token,
        has_task=has_task,
        reason=None if has_task else "验证服务已就绪，但尚未生成 task.md",
        detail={"public_url": public_url, "port": port},
    )


def render_worker_guide() -> str:
    """生成写进 Worker ``CLAUDE.md`` 的验证服务指引段落。

    段落本身**不含**地址和 token，只告诉 Worker 去读连接信息文件。
    这样即使 agent 运行中改写了 CLAUDE.md，凭据仍在独立文件里；
    重新同步时也只会替换这一段，不会覆盖 agent 写下的其他内容。

    Returns:
        带首尾标记的 Markdown 段落
    """
    return f"""{GUIDE_BEGIN_MARKER}
# 验证服务

本工作目录的任务说明见 `{TASK_FILENAME}`（由 Validator 生成并同步过来）。

完成实现后，通过 Validator 提供的验证服务提交结果进行验证。连接信息
（服务地址 `url` 与鉴权 `token`）保存在 `{WORKER_CONNECTION_PATH}`，
每次调用前请读取该文件获取最新值，调用时把 token 放在请求头里：

```
Authorization: Bearer <token>
```

## 注意

- 该服务运行在 Validator 容器内，仅在当前会话期间可用。
- `{WORKER_CONNECTION_PATH}` 由系统自动写入，请勿修改或删除；
  本段落（含首尾标记）同样由系统维护，请保留。
- 如果连接失败，说明 Validator 侧的验证服务已停止，请在左侧 Validator
  对话中重新拉起并重新同步。
{GUIDE_END_MARKER}
"""


def render_connection(url: str, token: str) -> str:
    """生成 Worker 侧连接信息文件的内容。

    Args:
        url: 验证服务地址
        token: 验证服务 token

    Returns:
        JSON 文本
    """
    payload = {
        "url": url,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def merge_worker_guide(existing: str | None) -> str:
    """把验证服务指引合并进已有的 ``CLAUDE.md`` 文本。

    - 已有系统段落：原位替换
    - 没有：追加到末尾（agent 可能整篇重写过 CLAUDE.md，把标记弄丢了）
    - 文件不存在：只写指引段落

    Args:
        existing: 现有 CLAUDE.md 内容；文件不存在时为 None

    Returns:
        合并后的完整文本
    """
    block = render_worker_guide()
    if not existing or not existing.strip():
        return block

    begin = existing.find(GUIDE_BEGIN_MARKER)
    end = existing.find(GUIDE_END_MARKER, begin + 1) if begin != -1 else -1
    if begin != -1 and end != -1:
        tail = existing[end + len(GUIDE_END_MARKER) :]
        # 吞掉旧段落末尾的换行，避免每次同步多出空行
        if tail.startswith("\r\n"):
            tail = tail[2:]
        elif tail.startswith("\n"):
            tail = tail[1:]
        return existing[:begin] + block + tail

    return existing.rstrip() + "\n\n" + block


def sync_to_worker(session_id: str, status: ValidationStatus) -> dict[str, Any]:
    """把 ``task.md`` 与验证服务连接信息同步到 Worker 工作目录。

    **阻塞函数**（文件 IO），调用方需用 ``asyncio.to_thread`` 下沉。

    三个动作：

    1. 复制 Validator 的 ``task.md`` 到 Worker 工作目录（存在则覆盖）
    2. 写 Worker 的连接信息文件 ``.aris/validation/connection.json``（url / token）
    3. 在 Worker 的 ``CLAUDE.md`` 中写入/更新指引段落，告诉它去读连接信息文件；
       段落外的内容原样保留

    Args:
        session_id: 会话 ID
        status: :func:`probe_validation` 的结果，必须是 ``configured=True``

    Returns:
        ``{"task_synced": bool, "guide_written": bool, "connection_written": bool,
        "task_bytes": int}``

    Raises:
        ValueError: 验证服务未配置完成（调用方应先探测）
        FileNotFoundError: Validator 侧没有 ``task.md``
        OSError: 读写失败
    """
    if not status.configured or not status.url or not status.token:
        raise ValueError("验证服务尚未配置完成，无法同步")

    validator_workspace = role_workspace(session_id, ARLRole.VALIDATOR)
    worker_workspace = role_workspace(session_id, ARLRole.WORKER)
    source = validator_workspace / TASK_FILENAME
    target = worker_workspace / TASK_FILENAME

    content = source.read_text(encoding="utf-8")

    # Worker 工作目录由容器 entrypoint 创建；若轮到同步时它还不在，建出来
    # 比报错好——用户点按钮的意图是明确的
    worker_workspace.mkdir(parents=True, exist_ok=True)

    # 按文本读写而非 shutil.copy：跨平台下 task.md 的换行与编码才是确定的，
    # 而 Worker 容器里的 agent 要按 UTF-8 读它
    target.write_text(content, encoding="utf-8")

    connection = worker_workspace / WORKER_CONNECTION_PATH
    connection.parent.mkdir(parents=True, exist_ok=True)
    connection.write_text(render_connection(status.url, status.token), encoding="utf-8")

    guide = worker_workspace / WORKER_GUIDE_FILENAME
    existing = guide.read_text(encoding="utf-8") if guide.is_file() else None
    guide.write_text(merge_worker_guide(existing), encoding="utf-8")

    logger.info(
        "✅ [Session {}] 已同步 task.md（{} 字节）、{} 与 {} 到 Worker 工作目录",
        session_id,
        len(content.encode("utf-8")),
        WORKER_CONNECTION_PATH,
        WORKER_GUIDE_FILENAME,
    )
    return {
        "task_synced": True,
        "guide_written": True,
        "connection_written": True,
        "task_bytes": len(content.encode("utf-8")),
    }
