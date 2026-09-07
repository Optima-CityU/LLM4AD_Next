"""
全局常量定义。

集中管理项目中的硬编码值，避免在多处散落魔法字符串和魔法数字。
"""

import os

from app.models import TaskStatus

# ---- Docker / Code-Server 相关 ----

"""Code-Server Docker 镜像名称。"""
CODE_SERVER_IMAGE = "codercom/code-server:4.117.0-39"

"""Code-Server 容器内存限制。"""
CODE_SERVER_MEMORY_LIMIT = "1g"
CODE_SERVER_CPU_LIMIT: float = 0.5

"""Docker 网络名称，用于容器间通信。"""
DOCKER_NETWORK_NAME = os.getenv("DOCKER_NETWORK_NAME", f"{os.getenv('PROJECT_NAME', 'llm4ad-web')}_default")

# Code-Server VS Code 默认配置（不含主题，主题由接口参数决定）
_CODE_SERVER_VSCODE_BASE_SETTINGS = {
    "window.commandCenter": False,
    "security.workspace.trust.enabled": False,
    "workbench.layoutControl.enabled": False,
    "workbench.activityBar.location": "hidden",
    "workbench.startupEditor": "none",
    "workbench.statusBar.visible": False,
    "chat.disableAIFeatures": True,
}

CODE_SERVER_THEME_DARK = "Tomorrow Night Blue"
CODE_SERVER_THEME_LIGHT = "Visual Studio Light"


def get_code_server_vscode_settings(dark: bool = True) -> dict:
    """根据指定主题返回 Code-Server 的 VS Code 配置。

    Args:
        dark: True 返回深色主题配置，False 返回浅色主题配置。

    Returns:
        合并主题后的完整 VS Code 配置字典。
    """
    theme = CODE_SERVER_THEME_DARK if dark else CODE_SERVER_THEME_LIGHT
    return {**_CODE_SERVER_VSCODE_BASE_SETTINGS, "workbench.colorTheme": theme}


# ---- 任务容器隔离 ----

"""任务容器名称前缀。"""
TASK_CONTAINER_NAME_PREFIX = "llm4ad-task-"

"""任务容器内的数据挂载路径。"""
TASK_CONTAINER_DATA_DIR = "/task/data"

"""AppConfig JSON 文件名（由宿主写入，容器内由 AppConfig.from_json 读取）。"""
APP_CONFIG_FILENAME = ".app_config.json"

"""容器写入、宿主侧 tail 的 NDJSON 事件流文件名（旧的 session 级共享文件）。

保留作兜底默认：容器入口未从 env 收到 per-turn 文件名时回退到它。研究类容器
（pipeline / collab）改用 per-turn 文件（见 :func:`research_events_filename`），
避免多轮共享同一文件时宿主 tailer 从 offset 0 全量重读造成上一轮事件重放。
"""
EVENTS_FILENAME = ".events.jsonl"

"""把 per-turn 事件文件名传给容器入口的环境变量名。"""
RESEARCH_EVENTS_FILENAME_ENV = "RESEARCH_EVENTS_FILENAME"


def research_events_filename(turn_id: str) -> str:
    """本 turn 专属的 NDJSON 事件文件名（``.events-<turn_id>.jsonl``）。

    per-turn 隔离：run_dir 是 session 级、被多轮复用，若所有轮共享一个
    ``.events.jsonl``，宿主 :class:`ContainerJob` 每轮从 offset 0 全量 tail 会把
    历史轮的事件重新读一遍（collab 表现为新卡片重放上一轮文本）。每轮一个独立
    文件，从头读也只有本轮内容。
    """
    return f".events-{turn_id}.jsonl"

# ---- 调参（chat-tune）隔离容器 ----

"""调参隔离容器名称前缀（容器名同时作为 docker 网络内的 DNS 主机名）。"""
CHAT_TUNE_CONTAINER_NAME_PREFIX = "llm4ad-chat-tune-"

"""调参容器内的数据挂载路径（file_reader 沙箱根目录）。"""
CHAT_TUNE_CONTAINER_DATA_DIR = "/task/data"

# ---- AutoResearch（researchclaw pipeline）隔离容器 ----

"""研究运行容器名称前缀（容器名同时作为 docker 网络内的 DNS 主机名）。"""
RESEARCH_CONTAINER_NAME_PREFIX = "llm4ad-research-"

"""研究容器内 run_dir 的挂载路径（= researchclaw 的 project_root）。"""
RESEARCH_CONTAINER_DATA_DIR = "/research/run"

# ---- Collaborate Agent（AgentScope ReAct 协作子会话）隔离容器 ----

"""协作 agent 容器名称前缀（每条协作消息一个短命容器）。"""
RESEARCH_COLLAB_CONTAINER_NAME_PREFIX = "llm4ad-research-collab-"

"""协作会话态在 run_dir 下的相对目录，按 stage 隔离到 ``{run_dir}/hitl/collab/stage-NN/``。"""
RESEARCH_COLLAB_STATE_SUBDIR = "hitl/collab"

"""协作容器配置里的一次性加密密钥环境变量名（同 pipeline 容器约定，纯占位不落 env）。"""
RESEARCH_COLLAB_CONFIG_KEY_ENV = "RESEARCH_COLLAB_CONFIG_KEY"

# ---- Celery 状态映射 ----
"""Celery 任务状态到业务 TaskStatus 的映射表。"""
CELERY_STATUS_MAP: dict[str, TaskStatus] = {
    "PENDING": TaskStatus.PENDING,
    "STARTED": TaskStatus.RUNNING,
    "SUCCESS": TaskStatus.COMPLETED,
    "FAILURE": TaskStatus.FAILED,
    "REVOKED": TaskStatus.FAILED,
    "RETRY": TaskStatus.RUNNING,
}
