"""AutoResearch pipeline 的容器运行适配层。

把 :func:`app.tasks.research_runner.task` 的 pipeline 执行从"worker 进程内直跑"
搬到隔离容器，复用通用 :class:`app.services.container_runtime.ContainerJob`：
加密配置、构造容器规格、把容器事件/输出经回调转发给 worker（再由 worker 落
Redis/DB），并读取容器写出的终态标记。容器生命周期、资源限额、网络、取消/
超时/OOM 由 ``ContainerJob`` 负责。

隔离要点：只挂载本 turn 的 ``run_dir``（``research/{uid}/{sid}/``），容器内
看不到其他用户目录；LLM 生成的代码在容器内执行，不再以 worker 身份跑。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.core.constants import (
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
from app.services.container_service import research_container_name, resolve_host_path


def _rewrite_paths(arc_config_dict: dict, run_dir: str) -> dict:
    """把 arc_config 里的宿主 run_dir 路径改写为容器内挂载路径。

    ``build_arc_config`` 会把 ``knowledge_base.root`` 等写成 run_dir 下的绝对
    路径；容器内 run_dir 挂在 :data:`RESEARCH_CONTAINER_DATA_DIR`，故整体做字符串
    替换（与 evolution_runner 同款做法）。

    分隔符归一化：run_dir 可能是 Windows 反斜杠形态（``\\data\\...``），而 config
    内的路径已统一成正斜杠（见 config_builder 的 ``as_posix()``）。若不归一化，
    反斜杠前缀匹配不到正斜杠路径，改写会静默落空，容器把整串 Windows 路径当成
    单个目录名创建（如 ``Cdataproject_home...kb``）。故把 config 文本和 run_dir
    前缀都统一成正斜杠后再替换。
    """
    host = str(run_dir).replace("\\", "/").rstrip("/")
    text = json.dumps(arc_config_dict).replace("\\\\", "/").replace(host, RESEARCH_CONTAINER_DATA_DIR)
    return json.loads(text)


def _build_spec(turn_id: str, run_dir: str, config_key: str) -> ContainerJobSpec:
    """构造研究容器规格（每-turn 一容器，只挂 run_dir）。"""
    # per-turn 事件文件：避免多轮共享 .events.jsonl 时宿主 tailer 从 offset 0
    # 全量重读造成上一轮事件重放。文件名经 env 透传给容器入口（两侧须一致）。
    events_name = research_events_filename(turn_id)
    env = {
        "PYTHONUNBUFFERED": "1",
        "NO_COLOR": "1",
        "LOGURU_COLORIZE": "false",
        "RESEARCH_CONFIG_KEY": config_key,
        RESEARCH_EVENTS_FILENAME_ENV: events_name,
    }
    return ContainerJobSpec(
        name=research_container_name(turn_id),
        image=settings.RESEARCH_RUNNER_IMAGE,
        command=["python", "app/tasks/research_container_runner.py"],
        mounts={resolve_host_path(run_dir): RESEARCH_CONTAINER_DATA_DIR},
        env=env,
        mem_limit=settings.RESEARCH_CONTAINER_MEMORY_LIMIT,
        nano_cpus=int(settings.RESEARCH_CONTAINER_CPU_LIMIT * 1e9),
        events_file=os.path.join(run_dir, events_name),
        labels={"turn_id": turn_id},
    )


def run_research_pipeline_container(
    *,
    arc_config_dict: dict,
    run_dir: str,
    turn_id: str,
    from_stage: str | None,
    to_stage: str | None,
    hitl_mode: str,
    guidance: str | None = None,
    on_event: Callable[[dict], None],
    on_stdout: Callable[[str], None] | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> ContainerJobResult:
    """在隔离容器中跑一次 ``execute_pipeline``，返回容器终态 result。

    终态 marker（gate/done/failed）由容器经 ``__result__`` 事件走 events 通道回传，
    调用方在 ``on_event`` 里捕获；本函数只负责起容器并返回 :class:`ContainerJobResult`
    （供调用方在容器崩溃/被 kill、未发 ``__result__`` 时据 ``status`` 兜底判定）。

    Args:
        arc_config_dict: ``build_arc_config`` 产出的 config dict（含 inline api_key）。
        run_dir: 宿主视角的 run_dir 绝对路径。
        turn_id: 本轮 turn ID（容器名、标签）。
        from_stage / to_stage: 起止 stage（字符串或 None）。
        hitl_mode: 前端 HITL mode（co-pilot / gate-only / step-by-step / full-auto
            / checkpoint / express / thorough / learning）。容器内经原生
            ``get_preset`` 构造 HITLSession，决定在哪些 stage 停返、给哪些
            available_actions，与 CLI ``--mode`` 一一对应。``full-auto`` 全程不停。
        guidance: 门控回复 / 本轮用户文字反馈；非空时容器入口写成
            ``stage-{from_stage}/hitl_guidance.md``，被 ARC glob 进 LLM prompt
            前言（等价 ARC CLI ``guide`` / gate INJECT 的效果）。
        on_event: 容器 NDJSON 事件回调（转发到 Redis/DB；含 ``__result__`` 终态）。
        on_stdout: 容器 stdout/stderr 行回调（可选）。
        check_cancelled: 取消检查（命中即 kill 容器，收 CANCELLED）。

    Returns:
        :class:`ContainerJobResult`：容器退出状态（COMPLETED/FAILED/CANCELLED/OOM/
        TIMED_OUT）+ exit_code。
    """
    payload: dict[str, Any] = {
        "arc_config": _rewrite_paths(arc_config_dict, run_dir),
        "from_stage": from_stage,
        "to_stage": to_stage,
        "hitl_mode": hitl_mode,
        "skip_noncritical": True,
        "run_id": turn_id,
        "guidance": guidance,
    }
    config_key = write_encrypted_config(payload, run_dir)
    spec = _build_spec(turn_id, run_dir, config_key)

    callbacks = ContainerJobCallbacks(
        on_event=on_event,
        on_stdout=on_stdout,
        check_cancelled=check_cancelled,
    )

    return ContainerJob(spec, callbacks).run()
