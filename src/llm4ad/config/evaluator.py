"""Evaluator configuration schemas.

Defines configuration classes for dataset discovery and evaluator setup,
including custom (external module) and executable (built-in) evaluators.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm4ad.config.ui import ui


class DatasetConfig(BaseModel):
    """Dataset configuration with multiple discovery modes.

    Supports three modes:
    - files: Explicit list of specific dataset files
    - directory: Automatically traverse all files in a directory
    - glob: Glob pattern matching for dataset files
    """

    mode: Literal["files", "directory", "glob"] = Field(
        default="files", description="Dataset discovery mode",
        json_schema_extra=ui(
            label_zh="发现模式", label_en="Discovery Mode",
            desc_zh="数据集文件的发现方式：files（显式列表）、directory（目录遍历）或 glob（模式匹配）",
            desc_en="How dataset files are discovered: files (explicit list), directory (traversal), or glob (pattern matching)",
        ),
    )
    files: list[str] | None = Field(
        default=None, description="Explicit list of files (for files mode)",
        json_schema_extra=ui(
            label_zh="文件列表", label_en="Files",
            desc_zh="在 files 模式下使用的数据集文件路径列表",
            desc_en="List of dataset file paths used in files mode",
        ),
    )
    path: str | None = Field(
        default=None, description="Directory path (for directory mode)",
        json_schema_extra=ui(
            label_zh="目录路径", label_en="Directory Path",
            desc_zh="在 directory 模式下要遍历的目录路径",
            desc_en="Directory path to traverse in directory mode",
        ),
    )
    recursive: bool = Field(
        default=True, description="Search recursively in subdirectories (for directory mode)",
        json_schema_extra=ui(
            label_zh="递归搜索", label_en="Recursive",
            desc_zh="在 directory 模式下是否递归搜索子目录",
            desc_en="Whether to recursively search subdirectories in directory mode",
        ),
    )
    pattern: str | None = Field(
        default=None, description="Glob pattern (for glob mode)",
        json_schema_extra=ui(
            label_zh="Glob 模式", label_en="Glob Pattern",
            desc_zh="在 glob 模式下用于匹配数据集文件的通配符表达式，例如 'data/**/*.csv'",
            desc_en="Wildcard expression for matching dataset files in glob mode, e.g. 'data/**/*.csv'",
        ),
    )


class MetricPatternConfig(BaseModel):
    """Configuration for a single metric extraction pattern.

    Each pattern defines how to extract one metric value from the
    executable's stdout/stderr output using a regex with one capture group.
    """

    name: str = Field(
        ..., description="Metric name (e.g. 'execution_time_ms')",
        json_schema_extra=ui(
            label_zh="指标名称", label_en="Metric Name",
            desc_zh="指标的唯一标识名称，例如 'execution_time_ms'",
            desc_en="Unique identifier for the metric, e.g. 'execution_time_ms'",
        ),
    )
    pattern: str = Field(
        ..., description="Regex with one capture group for the numeric value",
        json_schema_extra=ui(
            label_zh="正则表达式", label_en="Regex Pattern",
            desc_zh="包含一个捕获组的正则表达式，用于从输出中提取数值型指标",
            desc_en="Regex with one capture group to extract a numeric metric value from output",
        ),
    )
    type: Literal["minimize", "maximize"] = Field(
        default="maximize", description="Optimization direction",
        json_schema_extra=ui(
            label_zh="优化方向", label_en="Optimization Direction",
            desc_zh="指标的优化方向：maximize（越大越好）或 minimize（越小越好）",
            desc_en="Optimization direction: maximize (higher is better) or minimize (lower is better)",
        ),
    )
    weight: float = Field(
        default=1.0, ge=0, description="Weight for multi-objective scoring",
        json_schema_extra=ui(
            label_zh="权重", label_en="Weight",
            desc_zh="多目标评分时该指标的权重系数，值越大影响越大",
            desc_en="Weight coefficient for this metric in multi-objective scoring; higher values increase its influence",
        ),
    )


class EvaluatorConfig(BaseModel):
    """Base evaluator configuration with shared fields.

    Uses Pydantic's discriminator feature to automatically instantiate
    the correct concrete config subclass based on the ``type`` field.
    """

    type: Literal["executable", "custom"] = Field(
        default="custom", description="Evaluator type",
        json_schema_extra=ui(
            label_zh="评估器类型", label_en="Evaluator Type",
            desc_zh="评估器类型：custom（自定义 Python 模块）或 executable（外部可执行文件）",
            desc_en="Evaluator type: custom (Python module) or executable (external binary)",
        ),
    )
    dataset: DatasetConfig = Field(
        default_factory=DatasetConfig, description="Dataset configuration",
        json_schema_extra=ui(
            label_zh="数据集配置", label_en="Dataset",
            desc_zh="数据集发现与加载的配置，包括文件列表、目录遍历或 glob 匹配",
            desc_en="Dataset discovery and loading configuration, including file lists, directory traversal, or glob matching",
        ),
    )
    timeout: float = Field(
        default=60.0, gt=0, description="Evaluation timeout per instance in seconds",
        json_schema_extra=ui(
            label_zh="超时时间（秒）", label_en="Timeout (seconds)",
            desc_zh="单个评估实例的最大执行时间（秒），超时后将被终止",
            desc_en="Maximum execution time in seconds for a single evaluation instance; terminated if exceeded",
        ),
    )
    max_retries: int = Field(
        default=1, ge=0, description="Maximum retries for failed evaluations",
        json_schema_extra=ui(
            label_zh="最大重试次数", label_en="Max Retries",
            desc_zh="评估失败后的最大重试次数，设为 0 表示不重试",
            desc_en="Maximum number of retries after a failed evaluation; set to 0 to disable retries",
        ),
    )
    parallel: bool = Field(
        default=True, description="Enable parallel evaluation across multiple CPU cores",
        json_schema_extra=ui(
            label_zh="并行评估", label_en="Parallel",
            desc_zh="是否启用多核并行评估以加速批量评估任务",
            desc_en="Whether to enable multi-core parallel evaluation to speed up batch evaluation tasks",
        ),
    )
    batch_size: int = Field(
        default=10, gt=0, description="Batch size for parallel evaluation",
        json_schema_extra=ui(
            label_zh="批次大小", label_en="Batch Size",
            desc_zh="并行评估时每批处理的实例数量",
            desc_en="Number of instances processed per batch during parallel evaluation",
        ),
    )


class CustomEvaluatorConfig(EvaluatorConfig):
    """Custom evaluator loaded from an external module.

    The ``module`` field specifies the import path in the format
    ``module.path:ClassName`` or ``path/to/file.py:ClassName``.

    Extra fields (e.g. ``api_config``) are preserved and passed to
    the evaluator constructor via ``model_extra``.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["custom"] = Field(
        default="custom", description="Evaluator type",
        json_schema_extra=ui(
            hidden=True,
            label_zh="评估器类型", label_en="Evaluator Type",
            desc_zh="评估器类型标识，固定为 custom",
            desc_en="Evaluator type identifier, fixed to custom",
        ),
    )
    provider: str = Field(
        default="default",
        description="Name of the LLM provider to use for evaluation",
        json_schema_extra=ui(
            hidden=True,
            label_zh="LLM 提供者", label_en="Provider",
            desc_zh="用于评估阶段的 LLM 提供者名称，需在 providers 列表中预先配置",
            desc_en="Name of the LLM provider for evaluation; must be defined in the providers list",
        ),
    )
    module: str = Field(
        default="", description="Module path for custom evaluator (format: module.path:ClassName)",
        json_schema_extra=ui(
            label_zh="模块路径", label_en="Module Path",
            desc_zh="自定义评估器的导入路径，格式为 'module.path:ClassName' 或 'path/to/file.py:ClassName'",
            desc_en="Import path for the custom evaluator, format: 'module.path:ClassName' or 'path/to/file.py:ClassName'",
        ),
    )
    metrics: list[str] = Field(
        default_factory=list, description="Metrics to compute",
        json_schema_extra=ui(
            hidden=True,
            label_zh="计算指标", label_en="Metrics",
            desc_zh="需要计算的指标名称列表，由自定义评估器实现",
            desc_en="List of metric names to compute, implemented by the custom evaluator",
        ),
    )


class ExecutableEvaluatorConfig(EvaluatorConfig):
    """Built-in executable evaluator, fully config-driven.

    Evaluates algorithms by running a compiled executable and parsing
    metrics from its output using configurable regex patterns.

    The ``command`` field supports placeholders:
    ``{executable}``, ``{data_file}``, ``{timeout}``, ``{project_root}``.
    """

    type: Literal["executable"] = Field(
        default="executable", description="Evaluator type",
        json_schema_extra=ui(
            hidden=True,
            label_zh="评估器类型", label_en="Evaluator Type",
            desc_zh="评估器类型标识，固定为 executable",
            desc_en="Evaluator type identifier, fixed to executable",
        ),
    )
    executable: str = Field(
        ..., description="Relative path to executable in project_root (e.g. 'build/sorting_benchmark')",
        json_schema_extra=ui(
            label_zh="可执行文件路径", label_en="Executable Path",
            desc_zh="相对于项目根目录的可执行文件路径，例如 'build/sorting_benchmark'",
            desc_en="Path to the executable relative to project root, e.g. 'build/sorting_benchmark'",
        ),
    )
    command: str = Field(
        default="{executable} {data_file}",
        description="Command template with placeholders: {executable}, {data_file}, {timeout}, {project_root}",
        json_schema_extra=ui(
            label_zh="命令模板", label_en="Command Template",
            desc_zh="运行评估的命令模板，支持占位符：{executable}、{data_file}、{timeout}、{project_root}",
            desc_en="Command template for running evaluation, supports placeholders: "
            "{executable}, {data_file}, {timeout}, {project_root}",
        ),
    )
    metric_patterns: list[MetricPatternConfig] = Field(
        ..., description="Regex patterns for extracting metrics from output",
        json_schema_extra=ui(
            label_zh="指标提取模式", label_en="Metric Patterns",
            desc_zh="从可执行文件的标准输出中提取指标值的正则表达式模式列表",
            desc_en="List of regex patterns for extracting metric values from the executable's stdout",
        ),
    )


class EvalContext(BaseModel):
    """Runtime context passed to an evaluator for a single evaluation."""

    data_path: str = Field(default="", description="Data path")
    project_root: str = Field(description="Project root path, e.g. worktree path")
    timeout: float = Field(
        default=60.0, gt=0, description="Evaluation timeout per instance in seconds"
    )
    behavior_storage: Literal["rendered", "raw", "none"] = Field(
        default="none",
        description="How evaluators should store behavior data: "
        "'rendered' (pre-rendered images), 'raw' (compact JSON for deferred rendering), "
        "or 'none' (no behavior data).",
    )
    candidate_id: str = Field(
        default="", description="Identifier of the candidate being evaluated"
    )
    generation: int = Field(
        default=0, ge=0, description="Evolution generation of the candidate"
    )
    parent_ids: list[str] = Field(
        default_factory=list, description="Parent candidate identifiers"
    )
