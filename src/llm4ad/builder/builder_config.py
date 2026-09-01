"""Build configuration for the automated task builder.

Defines the per-build config YAML schema used by the multi-user platform.
When a user clicks "从零开始" (start from scratch) on the frontend, the
system generates a build config YAML template via :func:`generate_build_config`.
The user fills in their LLM credentials and problem description, then the
backend loads it via :func:`load_build_config` and runs the build pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BuilderLLMConfig:
    """LLM credentials for the builder agents.

    These credentials are used by the builder pipeline (TaskAnalyzer,
    TaskCreator, TaskValidator) to call the LLM.  They are separate
    from the generated application's provider credentials.
    """

    type: str = "openai_compatible"
    """Provider type: openai, anthropic, or openai_compatible."""

    api_key: str = ""
    """API key for the builder's LLM."""

    base_url: str | None = None
    """Base URL for OpenAI-compatible providers."""

    model: str = "gpt-4o"
    """Model name."""

    max_repair_attempts: int = 10
    """Maximum auto-repair attempts during validation."""


@dataclass
class BuildTaskConfig:
    """Problem specification for the build."""

    description: str = ""
    """Natural language problem description."""

    output_dir: str = "./"
    """Parent directory where the project folder will be created."""

    project_name: str | None = None
    """Optional project name for the generated directory. If not provided,
    the LLM will generate one based on the problem description."""

    code_path: str | None = None
    """Optional path to existing algorithm code (from_code mode)."""

    data_path: str | None = None
    """Optional path to dataset directory."""

    multimodal: bool = False
    """Whether to generate a multimodal evaluator with visualization support."""

    visualization_hint: str | None = None
    """Optional user-provided visualization requirements. When multimodal is true,
    the system will incorporate these hints into the visualization design.
    If not provided, the system auto-designs an appropriate visualization."""


@dataclass
class BuilderConfig:
    """Complete per-build configuration.

    Combines builder LLM credentials with the task specification.
    Loaded from a YAML file via :func:`load_build_config`.
    """

    builder: BuilderLLMConfig = field(default_factory=BuilderLLMConfig)
    """LLM configuration for the builder agents."""

    task: BuildTaskConfig = field(default_factory=BuildTaskConfig)
    """Problem specification."""


BUILD_CONFIG_TEMPLATE = """\
# ============================================================
# LLM4AD Auto Build Configuration
# ============================================================
# Fill in the sections below, then run:
#   llm4ad build --config this_file.yaml
#
# You can use ${{ENV_VAR}} syntax to reference environment variables
# instead of writing credentials directly in this file.
# ============================================================

# --- Builder LLM Configuration ---
# These credentials are used by the builder pipeline to generate
# your application. They are NOT written into the generated config.
builder:
  type: "openai_compatible"          # openai | anthropic | openai_compatible
  api_key: "${{LLM4AD_BUILD_API_KEY}}"  # Your API key (or ${{ENV_VAR}})
  base_url: "${{LLM4AD_BUILD_BASE_URL}}"  # e.g. https://api.openai.com/v1
  model: "gpt-4o"                    # Model to use for building
  max_repair_attempts: 10            # Auto-repair attempts on validation failure

# --- Task Specification ---
task:
  description: |
{description_block}
  output_dir: "./"                   # Where to create the application directory
  # project_name: "my_task"          # Optional: directory name (auto-generated if omitted)
  # multimodal: true                 # Generate evaluator with visualization support
  # visualization_hint: |            # Optional: describe your visualization needs
  #   Render a bar chart showing the solution quality over iterations.
  #   Include key metrics as text annotations on the chart.
  # code_path: "./existing_code/"    # Uncomment for from_code mode
  # data_path: "./data/"             # Uncomment to include dataset
"""


def generate_build_config(
    output_path: str | Path,
    *,
    description: str = "",
) -> Path:
    """Generate a build config YAML template for the user to fill in.

    Args:
        output_path: Path where the YAML file will be written.
        description: Optional pre-filled task description.

    Returns:
        Path to the written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if description.strip():
        lines = description.strip().splitlines()
        description_block = "\n".join(f"    {line}" for line in lines)
    else:
        description_block = (
            "    Describe your problem here.\n"
            "    For example: Evolve efficient sorting algorithms\n"
            "    that minimize the number of comparisons."
        )

    content = BUILD_CONFIG_TEMPLATE.format(description_block=description_block)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def load_build_config(config_path: str | Path) -> BuilderConfig:
    """Load and validate a build config YAML file.

    Supports ``${ENV_VAR}`` expansion in all string fields, using the
    same mechanism as LLM4AD's global settings loader.

    Args:
        config_path: Path to the build config YAML file.

    Returns:
        A populated BuilderConfig instance.

    Raises:
        BuildError: If the file is missing, unparseable, or has invalid fields.
    """
    from llm4ad.builder.pipeline import BuildError
    from llm4ad.config.settings import load_yaml_with_env_expansion

    config_path = Path(config_path)
    if not config_path.exists():
        raise BuildError(f"Build config file not found: {config_path}")

    try:
        data: dict[str, Any] = load_yaml_with_env_expansion(config_path)
    except KeyError as e:
        raise BuildError(f"Environment variable expansion failed in {config_path}: {e}") from e
    except Exception as e:
        raise BuildError(f"Failed to parse build config {config_path}: {e}") from e

    builder_data = data.get("builder", {})
    task_data = data.get("task", {})

    builder_llm = BuilderLLMConfig(
        type=builder_data.get("type", "openai_compatible"),
        api_key=builder_data.get("api_key", ""),
        base_url=builder_data.get("base_url"),
        model=builder_data.get("model", "gpt-4o"),
        max_repair_attempts=builder_data.get("max_repair_attempts", 10),
    )

    task_cfg = BuildTaskConfig(
        description=task_data.get("description", ""),
        output_dir=task_data.get("output_dir", "./"),
        project_name=task_data.get("project_name"),
        code_path=task_data.get("code_path"),
        data_path=task_data.get("data_path"),
        multimodal=bool(task_data.get("multimodal", False)),
        visualization_hint=task_data.get("visualization_hint"),
    )

    if not builder_llm.api_key:
        raise BuildError(
            "No API key provided in build config.\n"
            "Set builder.api_key in the YAML file, or use ${ENV_VAR} syntax."
        )

    if not task_cfg.description.strip():
        raise BuildError(
            "No task description provided in build config.\n"
            "Set task.description in the YAML file."
        )

    return BuilderConfig(builder=builder_llm, task=task_cfg)
