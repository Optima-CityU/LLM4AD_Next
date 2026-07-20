"""
任务（Task）请求/响应 Schema。

定义任务 CRUD、运行、结果查询等操作所需的数据验证和序列化模型。
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import TaskStatus

# ---- 文件管理 Schema ----


class FileTreeNode(BaseModel):
    """文件树节点。"""

    name: str = Field(description="文件或目录名称")
    path: str = Field(description="相对路径")
    type: str = Field(description="节点类型: file 或 directory")
    children: list["FileTreeNode"] | None = None


class FileTreeResponse(BaseModel):
    """文件目录树响应。"""

    tree: list[FileTreeNode]


class FileContentResponse(BaseModel):
    """文件内容响应。"""

    file_path: str
    content: str


class FileUpdateRequest(BaseModel):
    """文件修改请求。"""

    file_path: str = Field(description="文件相对路径")
    content: str = Field(description="文件新内容")


class FileCreateRequest(BaseModel):
    """文件创建请求。"""

    path: str | None = Field(
        default=None,
        description="用户已选择的路径：文件路径(如 'a/b/c.txt')、目录路径(如 'a/b/c/')、或为空/null表示根目录",
    )


class FileCreateResponse(BaseModel):
    """文件创建响应。"""

    file_path: str = Field(description="已创建文件的相对路径")


class FileRenameRequest(BaseModel):
    """文件重命名请求。"""

    old_path: str = Field(description="原文件相对路径")
    new_path: str = Field(description="新文件相对路径")


class FolderCreateRequest(BaseModel):
    """文件夹创建请求。"""

    path: str | None = Field(
        default=None,
        description="父目录相对路径，必须指向已有目录（不支持创建顶级文件夹）",
    )
    name: str | None = Field(
        default=None,
        description="新文件夹名称，为空时自动生成（new_folder、new_folder_1...）",
    )


class FolderCreateResponse(BaseModel):
    """文件夹创建响应。"""

    folder_path: str = Field(description="已创建文件夹的相对路径")


class FolderRenameRequest(BaseModel):
    """文件夹重命名请求。"""

    old_path: str = Field(description="原文件夹相对路径")
    new_path: str = Field(description="新文件夹相对路径")


# ---- Chat Tune 上传响应 ----


class ChatTuneUploadFileResponse(BaseModel):
    """调参对话上传文件响应。"""

    payload: dict[str, Any] = Field(description="更新后的消息 payload")


class ChatTuneUploadDataResponse(BaseModel):
    """调参对话上传目录响应。"""

    payload: dict[str, Any] = Field(description="更新后的消息 payload")


# ---- 存储用量 Schema ----


class StorageUsage(BaseModel):
    """任务输入数据存储用量。"""

    used_bytes: int = Field(description="已使用空间（字节）")
    limit_bytes: int = Field(description="空间上限（字节）")
    used_mb: float = Field(description="已使用空间（MB，保留两位小数）")
    limit_mb: int = Field(description="空间上限（MB）")


# ---- 请求 Schema ----


class TaskCreate(BaseModel):
    """任务创建请求。input_args 为空时自动填充 AppConfig 默认值。"""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    project_id: uuid.UUID
    input_args: dict | None = None
    template_name: str | None = Field(default=None, description="示例模板名称，选择后自动填充数据和参数")
    config_name: str | None = Field(default=None, description="模板中的配置文件名，默认为 config.yaml")
    language: str = Field(default="zh", description="语言参数（zh/en），影响初始调参消息语言")
    ai_built: bool = Field(default=False, description="是否由 AI 构建")


class TaskUpdate(BaseModel):
    """任务更新请求（所有字段均可选）。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    input_args: dict | None = None


class TaskTagUpdate(BaseModel):
    """任务标签更新请求。"""

    tag: str | None = Field(default=None, max_length=255, description="任务标签")


class TaskCopyRequest(BaseModel):
    """任务复制请求。"""

    is_child: bool = Field(default=False, description="是否作为子任务复制")


class SetActiveChildRequest(BaseModel):
    """设置根任务当前选中的子任务版本。"""

    child_id: uuid.UUID | None = Field(
        default=None, description="子任务 ID；传 null 清除选中，读取时默认回退为指向自身"
    )


# ---- 响应 Schema ----


class TaskResponse(BaseModel):
    """任务基础响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_time: datetime
    updated_time: datetime
    project_id: uuid.UUID
    name: str
    description: str | None
    status: TaskStatus
    input_args: dict
    celery_task_id: str | None = None
    input_data_path: str | None = None
    reports: dict | None = None
    group_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    tag: str | None = None
    active_child_id: uuid.UUID | None = None
    ai_built: bool = False
    ai_build_started: bool = False
    active_status: TaskStatus | None = Field(
        default=None,
        description="active_child_id 指向任务的状态；为空或指向自身时等于本任务自身的 status",
    )
    storage_usage: StorageUsage | None = None
    children_count: int | None = None


class PaginatedTaskResponse(BaseModel):
    """任务分页响应。"""

    items: list[TaskResponse]
    total: int
    skip: int
    limit: int


class TaskRunResponse(BaseModel):
    """任务运行提交响应。"""

    task_id: uuid.UUID
    celery_task_id: str
    status: TaskStatus


class TaskResultResponse(BaseModel):
    """任务 Celery 执行结果响应。"""

    task_id: uuid.UUID
    celery_task_id: str | None = None
    celery_status: str
    status: TaskStatus
    result: dict | None = None
    error: str | None = None


class TaskTreeItem(BaseModel):
    """任务树节点，包含常用信息，不包含 logs 和 result 等大字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    status: TaskStatus
    created_time: datetime
    updated_time: datetime
    project_id: uuid.UUID
    group_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    tag: str | None = None
    active_child_id: uuid.UUID | None = None
    celery_task_id: str | None = None
    input_data_path: str | None = None


class TaskTreeResponse(BaseModel):
    """任务树响应。"""

    root: TaskTreeItem
    children: list[TaskTreeItem]


# ---- 日志 Schema ----


class TaskLogsResponse(BaseModel):
    """任务日志游标分页响应。"""

    task_id: uuid.UUID
    source: str = Field(description="日志来源: db 或 redis")
    entries: list[dict] = Field(default_factory=list, description="日志条目列表（按时间正序）")
    next_cursor: str | None = Field(default=None, description="下一页游标，null 表示没有更多数据")
    has_more: bool = Field(default=False, description="是否还有更早的日志")


class MemoryInjectionSummary(BaseModel):
    """单次 MindMemOS 注入事件摘要。"""

    sampler: str = "unknown"
    strategy: str = ""
    scope_hits: dict[str, int] = Field(default_factory=dict)
    deduped_hits: int = 0
    injected_chars: int = 0
    elapsed_ms: int = 0
    timestamp: datetime | None = None


class MemoryContributionScopeSummary(BaseModel):
    """单个 scope 的记忆贡献度估算。"""

    calls: int = 0
    positive_results: int = 0
    best_delta: float | None = None
    average_delta: float | None = None


def _default_memory_contribution_scopes() -> dict[str, MemoryContributionScopeSummary]:
    return {
        "task": MemoryContributionScopeSummary(),
        "project": MemoryContributionScopeSummary(),
        "user": MemoryContributionScopeSummary(),
    }


class MemoryContributionSummary(BaseModel):
    """MindMemOS 注入后候选算法评分变化的聚合估算。"""

    associated_generations: int = 0
    scored_generations: int = 0
    positive_results: int = 0
    best_delta: float | None = None
    average_delta: float | None = None
    by_scope: dict[str, MemoryContributionScopeSummary] = Field(
        default_factory=_default_memory_contribution_scopes
    )


class TaskMemoryObservabilityResponse(BaseModel):
    """任务级 MindMemOS 记忆使用统计。"""

    task_id: uuid.UUID
    enabled: bool
    injection_calls: int = 0
    scope_hits_total: dict[str, int] = Field(default_factory=lambda: {"task": 0, "project": 0, "user": 0})
    deduped_hits_total: int = 0
    injected_chars_total: int = 0
    elapsed_ms_total: int = 0
    elapsed_ms_avg: int = 0
    sampler_counts: dict[str, int] = Field(default_factory=dict)
    created_task_memory_count: int = 0
    latest_injection: MemoryInjectionSummary | None = None
    contribution: MemoryContributionSummary = Field(default_factory=MemoryContributionSummary)


# ---- 辅助函数 ----


def generate_default_input_args() -> dict:
    """生成 AppConfig 的默认参数字典，按照类型填充默认值。"""
    from llm4ad.config.schema import AppConfig, CustomEvaluatorConfig

    config = AppConfig(evaluator=CustomEvaluatorConfig(module=""))
    return config.model_dump()



# ---- app参数配置schema ----
class AppConfigSchemaResponse(BaseModel):
    """参数配置 JSON Schema 响应。"""

    config_schema: dict = Field(default_factory=dict, description="AppConfig 的 JSON Schema")


class ExampleTemplateConfigItem(BaseModel):
    """单个示例模板下的配置文件。"""

    name: str = Field(description="配置文件名")
    description_en: str | None = Field(default=None, description="配置文件英文简介，用于前端提示")
    description_zh: str | None = Field(default=None, description="配置文件中文简介，用于前端提示")


class ExampleTemplateItem(BaseModel):
    """单个示例模板。"""

    name: str = Field(description="模板文件夹名称")
    configs: list[ExampleTemplateConfigItem] = Field(
        default_factory=list, description="配置文件列表，每项包含文件名与可选的中英文简介"
    )


class ExampleTemplateListResponse(BaseModel):
    """示例模板列表响应。"""

    templates: list[ExampleTemplateItem] = Field(default_factory=list, description="可用的示例模板列表")


# ---- 统计 Schema ----


class TaskStatsResponse(BaseModel):
    """任务基本统计信息响应。"""

    task_id: uuid.UUID
    solution_count: int = Field(description="解的个数")
    avg_score: float | None = Field(default=None, description="解的平均分（无解时为 None）")
    max_score: float | None = Field(default=None, description="解的最高分（无解时为 None）")
    input_args: dict = Field(default_factory=dict, description="任务参数 JSON")
    created_time: datetime = Field(description="任务创建时间")
    updated_time: datetime = Field(description="任务最后修改时间")
