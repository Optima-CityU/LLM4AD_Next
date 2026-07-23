"""Collaborate Agent 的容器运行适配层。

把 :func:`app.tasks.research_runner.collab` 的协作一轮从「worker 进程内直跑」搬到
隔离容器，复用通用 :class:`app.services.container_runtime.ContainerJob`：加密配置、
构造容器规格、把容器事件/输出经回调转发给 worker（再由 worker 落 Redis/DB），并读取
容器写出的终态标记。与 :mod:`app.services.research_pipeline_runner` 同构，只是容器
入口指向 ``research_collab_container_runner.py``、传入的 payload 是协作专属字段。

隔离要点：只挂载本会话的 ``run_dir``；容器有网络（agent 需调 LLM）；每条协作消息一个
短命容器，跑完即退。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.core.constants import (
    RESEARCH_COLLAB_CONFIG_KEY_ENV,
    RESEARCH_CONTAINER_DATA_DIR,
    RESEARCH_EVENTS_FILENAME_ENV,
    research_events_filename,
)
from app.services._research_container_common import write_encrypted_config
from app.services.container_runtime import (
    ContainerJob,
    ContainerJobCallbacks,
    ContainerJobResult,
    ContainerJobSpec,
)
from app.services.container_service import (
    research_collab_container_name,
    resolve_host_path,
)


def _build_spec(turn_id: str, run_dir: str, config_key: str) -> ContainerJobSpec:
    """构造协作 agent 容器规格（每条协作消息一容器，只挂 run_dir）。"""
    # per-turn 事件文件：collab 每轮一容器但复用 session 级 run_dir，共享
    # .events.jsonl 会让宿主 tailer 从 offset 0 把上一轮 __collab_text__ 重读，
    # 前端拼进新卡片（新卡片重放上一轮回复）。每轮独立文件根治。
    events_name = research_events_filename(turn_id)
    env = {
        "PYTHONUNBUFFERED": "1",
        "NO_COLOR": "1",
        "LOGURU_COLORIZE": "false",
        RESEARCH_COLLAB_CONFIG_KEY_ENV: config_key,
        RESEARCH_EVENTS_FILENAME_ENV: events_name,
    }
    return ContainerJobSpec(
        name=research_collab_container_name(turn_id),
        image=settings.RESEARCH_RUNNER_IMAGE,
        command=["python", "app/tasks/research_collab_container_runner.py"],
        mounts={resolve_host_path(run_dir): RESEARCH_CONTAINER_DATA_DIR},
        env=env,
        mem_limit=settings.RESEARCH_CONTAINER_MEMORY_LIMIT,
        nano_cpus=int(settings.RESEARCH_CONTAINER_CPU_LIMIT * 1e9),
        events_file=os.path.join(run_dir, events_name),
        labels={"turn_id": turn_id, "kind": "collab"},
    )


def run_collab_turn_container(
    *,
    run_dir: str,
    turn_id: str,
    stage_num: int,
    stage_name: str,
    topic: str,
    user_message: str,
    provider_config: dict,
    is_gate: bool = False,
    pipeline_context: str = "",
    on_event: Callable[[dict], None],
    on_stdout: Callable[[str], None] | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> ContainerJobResult:
    """在隔离容器中跑一轮协作（AgentScope ReAct），返回容器终态 result。

    终态 marker（``outcome="collab_done"``，含 agent 最终文本）由容器经 ``__result__``
    事件走 events 通道回传，调用方在 ``on_event`` 里捕获；本函数只负责起容器并返回
    :class:`ContainerJobResult`（供调用方在容器崩溃/被 kill 时据 ``status`` 兜底）。

    Args:
        run_dir: 宿主视角的 run_dir 绝对路径（协作会话态落在其下 hitl/collab/）。
        turn_id: 本条协作 turn ID（容器名、标签）。
        stage_num / stage_name: 命中门控、正在协作的 stage。
        topic: 研究主题（注入 agent system prompt）。
        user_message: 本轮用户协作消息。
        provider_config: ``resolve_provider_for_arc`` 产出的凭证字典（含 api_key/
            base_url/primary_model），agent 用它构造 OpenAI 兼容 model。
        is_gate: 会话是否正处于门控暂停。决定 ``run_pipeline`` 工具的 system prompt
            措辞（门控中 next=通过/current=驳回重跑；非门控则从对应 stage 起跑）。
        pipeline_context: 宿主拼好的流水线上下文块（ARC 总览 + 上一步/本步/下一步
            用途，见 ``streaming.build_stage_context``），嵌入 agent system prompt
            让其懂本步目标与上下游依赖。空串则容器侧退回只报 stage 名。
        on_event: 容器 NDJSON 事件回调（转发到 Redis/DB；含 ``__collab_*__`` 与
            ``__result__``）。
        on_stdout: 容器 stdout/stderr 行回调（可选）。
        check_cancelled: 取消检查（命中即 kill 容器）。

    Returns:
        :class:`ContainerJobResult`：容器退出状态 + exit_code。
    """
    payload: dict[str, Any] = {
        "stage_num": stage_num,
        "stage_name": stage_name,
        "topic": topic,
        "user_message": user_message,
        "provider_config": provider_config,
        "is_gate": is_gate,
        "pipeline_context": pipeline_context,
    }
    config_key = write_encrypted_config(payload, run_dir)
    spec = _build_spec(turn_id, run_dir, config_key)

    callbacks = ContainerJobCallbacks(
        on_event=on_event,
        on_stdout=on_stdout,
        check_cancelled=check_cancelled,
    )

    return ContainerJob(spec, callbacks).run()
