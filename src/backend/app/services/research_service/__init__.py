"""自动科研（Research）Service 层。

按子域拆分为 _common / folders / sessions / turns / collab / artifacts / messages，
本包 ``__init__`` 重新导出全部公开函数，拆包对调用方无感。各子域职责见其模块 docstring。
"""

from __future__ import annotations

from .analysis import (
    generate_analysis_report,
    get_analysis,
    get_analysis_data,
    stop_analysis_report,
)
from .artifacts import (
    create_artifacts_archive,
    get_artifact_tree,
    list_artifacts,
    list_generated_solutions,
    resolve_artifact_path,
    write_artifact,
)
from .collab import start_collab_turn
from .folders import (
    create_folder,
    delete_folder,
    get_folder_tree,
    list_folders,
    reorder_folders,
    update_folder,
)
from .messages import (
    inject_stage_guidance,
    list_session_messages,
    list_turn_messages,
)
from .sessions import (
    create_session,
    delete_session,
    get_session_detail,
    get_state,
    list_sessions,
    update_session,
)
from .translate import (
    get_translate_stream_type,
    stop_translation,
    translate_artifact,
)
from .turns import (
    get_stream_context,
    get_turn,
    list_session_turns,
    retry_turn,
    start_turn,
    stop_turn,
)

__all__ = [
    "create_artifacts_archive",
    "create_folder",
    "create_session",
    "delete_folder",
    "delete_session",
    "generate_analysis_report",
    "get_analysis",
    "get_analysis_data",
    "get_artifact_tree",
    "get_folder_tree",
    "get_session_detail",
    "get_state",
    "get_stream_context",
    "get_translate_stream_type",
    "get_turn",
    "inject_stage_guidance",
    "list_artifacts",
    "list_folders",
    "list_generated_solutions",
    "list_session_messages",
    "list_session_turns",
    "list_sessions",
    "list_turn_messages",
    "reorder_folders",
    "resolve_artifact_path",
    "retry_turn",
    "start_collab_turn",
    "start_turn",
    "stop_analysis_report",
    "stop_translation",
    "stop_turn",
    "translate_artifact",
    "update_folder",
    "update_session",
    "write_artifact",
]
