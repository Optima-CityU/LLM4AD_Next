"""任务服务层。

封装任务相关的业务逻辑，包括创建、运行、状态查询、数据上传等。
将路由处理器中的复杂业务逻辑提取到此处，实现关注点分离。

本包按职责拆分为多个子模块，所有公开 API 经此 ``__init__`` 统一再导出，
对外保持 ``app.services.task_service.<name>`` 的访问方式不变：

- ``auth``：任务权限校验与状态读取
- ``_helpers``：存储/文件公共助手与常量
- ``templates``：示例模板的发现与应用
- ``crud``：任务增删改查与版本树
- ``execution``：任务运行、停止与结果查询
- ``files``：任务输入数据的文件与文件夹管理
- ``logs``：任务日志读取
- ``stats``：任务统计、配置 schema 与结果渲染
- ``code_auth``：code-server 认证转发
- ``chat_tune_upload``：调参对话中的文件/目录上传
"""

from app.models import TaskStatus

from ._helpers import (
    DEFAULT_TOP_FOLDER_NAME,
    PLACEHOLDER_NAME,
)
from .auth import get_active_status, get_task_with_auth
from .chat_tune_upload import (
    _validate_chat_tune_message,
    chat_tune_upload_data,
    chat_tune_upload_file,
)
from .code_auth import verify_code_auth
from .crud import (
    copy_task,
    create_task,
    delete_task,
    get_task_storage_usage,
    get_task_tree,
    list_tasks,
    set_active_child,
    update_task,
    update_task_tag,
)
from .execution import (
    _force_stop_celery_task,
    _resolve_providers,
    get_task_result,
    run_task,
    stop_task,
)
from .files import (
    create_task_data_file,
    create_task_data_folder,
    delete_task_data_file,
    delete_task_data_folder,
    get_task_data_file,
    get_task_data_tree,
    rename_task_data_file,
    rename_task_data_folder,
    update_task_data_file,
    upload_task_data,
)
from .logs import get_task_logs, has_persisted_logs
from .memory_observability import get_task_memory_observability
from .pinned_memory import get_task_pinned_memory, set_task_pinned_memory
from .stats import generate_result_render, get_config_schema, get_task_stats
from .templates import EXAMPLES_DIR, list_example_templates
from .workspace_download import (
    download_task_workspace,
    workspace_attachment_disposition,
)

__all__ = [
    # re-exported enum
    "TaskStatus",
    # constants
    "DEFAULT_TOP_FOLDER_NAME",
    "PLACEHOLDER_NAME",
    "EXAMPLES_DIR",
    # auth
    "get_task_with_auth",
    "get_active_status",
    # crud
    "get_task_storage_usage",
    "list_tasks",
    "create_task",
    "copy_task",
    "update_task",
    "update_task_tag",
    "set_active_child",
    "get_task_tree",
    "delete_task",
    # templates
    "list_example_templates",
    # execution
    "run_task",
    "stop_task",
    "get_task_result",
    "_resolve_providers",
    "_force_stop_celery_task",
    # files
    "upload_task_data",
    "get_task_data_tree",
    "get_task_data_file",
    "update_task_data_file",
    "delete_task_data_file",
    "rename_task_data_file",
    "create_task_data_file",
    "create_task_data_folder",
    "delete_task_data_folder",
    "rename_task_data_folder",
    # logs
    "has_persisted_logs",
    "get_task_logs",
    "get_task_memory_observability",
    "get_task_pinned_memory",
    "set_task_pinned_memory",
    # stats
    "get_task_stats",
    "get_config_schema",
    "generate_result_render",
    # workspace download
    "download_task_workspace",
    "workspace_attachment_disposition",
    # code-server auth
    "verify_code_auth",
    # chat tune upload
    "chat_tune_upload_file",
    "chat_tune_upload_data",
    "_validate_chat_tune_message",
]
