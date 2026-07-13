"""自动科研桥接层：Celery 任务 + 每-turn 隔离容器跑 ARC pipeline + 事件汇流。

**架构分层**：

- :mod:`.snapshots` — 会话 / 轮次的 frozen dataclass 快照 + run_dir 解析
- :mod:`.lifecycle` — 原子终态 :func:`finalize_turn` + 磁盘清理
  :func:`cleanup_run_dir` + worker 启动孤儿扫 :func:`sweep_orphan_running_turns`
- :mod:`.streaming` — ``ResearchEventSink``（Redis 同步 + DB 批 flusher）
  + stage 进度翻译 + ``persist_gate_pause``
- :mod:`.config_builder` — build_arc_config + provider 解析 + HITL mode 映射
- :mod:`.task` — Celery ``run_research_turn``（宿主只做编排：起隔离容器跑
  pipeline、经 ``ContainerJob`` 转发容器事件、写终态）+ signal hooks
  + ``enqueue_research_turn``

取消 / 终态守卫模型见 :mod:`.lifecycle` 模块 docstring（各处只留指针）。
pipeline 执行本身在隔离容器内（见 :mod:`app.tasks.research_container_runner`
与 :mod:`app.services.research_pipeline_runner`）。Celery worker 通过
``include=["app.tasks.research_runner"]`` 加载本包，下面 ``from .task import ...``
触发任务注册与 signal 挂载。
"""

from __future__ import annotations

# 对外公开 API：research_service.py 与 core.celery.py 引用
from .collab import enqueue_collab_turn, run_collab_turn
from .config_builder import write_stage_guidance
from .lifecycle import cleanup_run_dir, finalize_turn
from .streaming import is_valid_stage, stage_display_name
from .task import enqueue_research_turn, run_research_turn

__all__ = [
    "cleanup_run_dir",
    "enqueue_collab_turn",
    "enqueue_research_turn",
    "finalize_turn",
    "is_valid_stage",
    "run_collab_turn",
    "run_research_turn",
    "stage_display_name",
    "write_stage_guidance",
]
