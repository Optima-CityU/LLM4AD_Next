"""Pipeline orchestrator: wire TaskAnalyzer → TaskCreator → TaskValidator → Writer.

Provides four public entry points:

* ``build_task``             – async, for use within an existing event loop.
* ``build_task_sync``        – synchronous wrapper for frontend integration.
* ``build_from_config``      – async, reads a build config YAML then delegates.
* ``build_from_config_sync`` – synchronous wrapper for config-based builds.

All functions accept an optional *on_progress* callback so the caller
(e.g. a web frontend) can report live stage updates to the user.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.builder.analyzer import TaskAnalyzer
from llm4ad.builder.creator import TaskCreator
from llm4ad.builder.validator import TaskValidator
from llm4ad.builder.writer import write_task_directory
from llm4ad.infra.provider.base import BaseProvider

# Type alias for the progress callback.
# Signature:  (stage: int, total: int, message: str) -> None
ProgressCallback = Callable[[int, int, str], None]


class BuildError(Exception):
    """Raised when the build pipeline fails."""


# ======================================================================
# Primary async API
# ======================================================================

async def build_task(
    description: str,
    output_dir: str = "./",
    *,
    code_path: str | None = None,
    data_path: str | None = None,
    project_name: str | None = None,
    multimodal: bool = False,
    visualization_hint: str | None = None,
    # Builder LLM config
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider_type: str = "openai_compatible",
    provider_name: str | None = None,
    max_repair_attempts: int = 3,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Build a complete LLM4AD application from natural language.

    Args:
        description: Natural language problem description, or path to a
            .md/.txt file containing the description.
        output_dir: Parent directory where the project folder will be created.
        code_path: Optional path to existing algorithm code (from_code mode).
        data_path: Optional path to dataset directory.
        project_name: Optional project name for the generated directory.
            If provided, overrides the LLM-generated name.
        multimodal: Whether to generate a multimodal evaluator with
            BehaviorData/BehaviorVisualization/BaseRenderer support.
        visualization_hint: Optional user-provided visualization requirements.
            When multimodal is true, the system incorporates these hints into
            the visualization design. If omitted, auto-designed by the LLM.
        api_key: API key for the builder's LLM.
        model: Model name for the builder's LLM.
        base_url: Base URL for the builder's LLM provider.
        provider_type: Provider type (openai, anthropic, openai_compatible).
        provider_name: Reference a named provider from global settings.
        max_repair_attempts: Maximum auto-repair attempts during validation.
        on_progress: Optional callback ``(stage, total, message)`` invoked
            at the start of each pipeline stage so callers can show progress.

    Returns:
        Path to the created application directory.

    Raises:
        BuildError: If the pipeline fails after all repair attempts.
    """

    def _progress(stage: int, msg: str) -> None:
        logger.info("Stage {}/4: {}", stage, msg)
        if on_progress is not None:
            on_progress(stage, 4, msg)

    # Resolve description from file if path provided
    description = _resolve_description(description)

    # Resolve provider
    provider = _resolve_provider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        provider_type=provider_type,
        provider_name=provider_name,
    )

    # Stage 1: Analyze
    _progress(1, "Analyzing problem...")
    analyzer = TaskAnalyzer(provider)
    analysis = await analyzer.analyze(
        description,
        code_path=Path(code_path) if code_path else None,
        data_path=Path(data_path) if data_path else None,
        multimodal=multimodal,
        visualization_hint=visualization_hint,
    )
    logger.info(
        "Analysis complete: project={}, function={}, metrics={}",
        analysis.project_name,
        analysis.function_name,
        [m["name"] for m in analysis.metrics],
    )

    # Override project name if user provided one
    if project_name is not None:
        analysis.project_name = project_name
        logger.info("Using user-provided project name: {}", project_name)

    # Stage 2: Create
    _progress(2, "Generating application artifacts...")
    creator = TaskCreator(provider)
    blueprint = await creator.create(analysis, description, multimodal=multimodal)
    logger.info(
        "Generation complete: evaluator={}, algorithm={}/{}",
        blueprint.evaluator_file_name,
        blueprint.algorithm_dir_name,
        blueprint.algorithm_file_name,
    )

    # Stage 3: Validate & repair
    _progress(3, "Validating generated code...")
    validator = TaskValidator(provider)
    blueprint = await validator.validate(blueprint, max_attempts=max_repair_attempts, multimodal=multimodal)

    if not blueprint.is_valid():
        errors_summary = "\n".join(f"  - {e}" for e in blueprint.validation_errors)
        raise BuildError(
            f"Validation failed after {blueprint.repair_attempts} attempts:\n{errors_summary}"
        )

    # Stage 4: Write to disk
    _progress(4, "Writing application to disk...")
    task_dir = write_task_directory(blueprint, output_dir)

    logger.info("Build complete: {}", task_dir)
    return str(task_dir)


# ======================================================================
# Synchronous frontend API
# ======================================================================

def build_task_sync(
    description: str,
    output_dir: str = "./",
    *,
    code_path: str | None = None,
    data_path: str | None = None,
    project_name: str | None = None,
    multimodal: bool = False,
    visualization_hint: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider_type: str = "openai_compatible",
    provider_name: str | None = None,
    max_repair_attempts: int = 3,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Synchronous wrapper around :func:`build_task` for frontend use.

    This is the **primary integration point for web frontends**:

    1. Frontend collects a user description (e.g. via a text box).
    2. Frontend calls ``build_task_sync(description, output_dir, ...)``.
    3. The function blocks until the application directory is generated.
    4. Returns the path to the generated directory, which the frontend
       can then load as a new template (configure YAML → start evolution).

    All parameters are identical to :func:`build_task`.

    Example (frontend integration)::

        from llm4ad.builder import build_task_sync

        # Called when user clicks "从零开始" and submits a description
        task_dir = build_task_sync(
            description=user_input_text,
            output_dir="./examples/applications/",
            api_key="sk-...",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            on_progress=lambda stage, total, msg: print(f"[{stage}/{total}] {msg}"),
        )
        # task_dir is now a ready-to-use application directory
        # Frontend can proceed to: load config.yaml → edit → run evolution
    """
    return asyncio.run(
        build_task(
            description,
            output_dir,
            code_path=code_path,
            data_path=data_path,
            project_name=project_name,
            multimodal=multimodal,
            visualization_hint=visualization_hint,
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_type=provider_type,
            provider_name=provider_name,
            max_repair_attempts=max_repair_attempts,
            on_progress=on_progress,
        )
    )


# ======================================================================
# Config-based API (multi-user platform)
# ======================================================================

async def build_from_config(
    config_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Build an LLM4AD application from a build config YAML file.

    This is the **recommended API for multi-user platforms**.  Each user
    fills in their own build config YAML (generated via
    :func:`~llm4ad.builder.builder_config.generate_build_config`) with
    their LLM credentials and problem description.

    Args:
        config_path: Path to the build config YAML file.
        on_progress: Optional callback ``(stage, total, message)`` invoked
            at the start of each pipeline stage.

    Returns:
        Path to the created application directory.

    Raises:
        BuildError: If the config is invalid or the pipeline fails.

    Example::

        from llm4ad.builder import build_from_config_sync

        task_dir = build_from_config_sync(
            "build_config.yaml",
            on_progress=lambda s, t, m: print(f"[{s}/{t}] {m}"),
        )
    """
    from llm4ad.builder.builder_config import load_build_config

    cfg = load_build_config(config_path)
    return await build_task(
        description=cfg.task.description,
        output_dir=cfg.task.output_dir,
        code_path=cfg.task.code_path,
        data_path=cfg.task.data_path,
        project_name=cfg.task.project_name,
        multimodal=cfg.task.multimodal,
        visualization_hint=cfg.task.visualization_hint,
        api_key=cfg.builder.api_key,
        model=cfg.builder.model,
        base_url=cfg.builder.base_url,
        provider_type=cfg.builder.type,
        max_repair_attempts=cfg.builder.max_repair_attempts,
        on_progress=on_progress,
    )


def build_from_config_sync(
    config_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Synchronous wrapper around :func:`build_from_config`.

    All parameters are identical to :func:`build_from_config`.
    """
    return asyncio.run(
        build_from_config(config_path, on_progress=on_progress)
    )


def _resolve_description(description: str) -> str:
    """Resolve description from a file path, or return the text as-is.

    When ``description`` is a free-form prompt (e.g. multi-line text, or a
    string longer than the OS path limit), constructing a ``Path`` and probing
    it can raise ``OSError``/``ValueError`` on some platforms rather than
    reporting "not a file". Multi-line inputs short-circuit early, and any
    filesystem error is treated as "not a file path".

    Args:
        description: Either a path to a ``.md``/``.txt`` file, or the raw
            description text.

    Returns:
        The file contents when ``description`` points to an existing
        ``.md``/``.txt`` file, otherwise ``description`` unchanged.
    """
    # Fast path: multi-line strings are descriptions, never file paths. This
    # also avoids passing them to Path(), which can raise on some platforms.
    if not description or "\n" in description or "\x00" in description:
        return description

    try:
        desc_path = Path(description)
        if desc_path.suffix in (".md", ".txt") and desc_path.is_file():
            return desc_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # Path too long, invalid characters, or unreadable — treat the input
        # as literal description text.
        return description
    return description


def _resolve_provider(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider_type: str = "openai_compatible",
    provider_name: str | None = None,
) -> BaseProvider:
    """Resolve the builder's LLM provider.

    Resolution order:
    1. Explicit CLI arguments (api_key, model, base_url)
    2. Environment variables (LLM4AD_BUILD_API_KEY, etc.)
    3. Global settings provider by name
    """
    BaseProvider.discover("llm4ad.infra.provider")

    # Try global settings first if provider_name given
    if provider_name is not None:
        return _resolve_from_global_settings(provider_name)

    # Resolve from explicit args + env vars
    resolved_api_key = api_key or os.getenv("LLM4AD_BUILD_API_KEY", "")
    resolved_model = model or os.getenv("LLM4AD_BUILD_MODEL", "gpt-4o")
    resolved_base_url = base_url or os.getenv("LLM4AD_BUILD_BASE_URL")

    if not resolved_api_key:
        raise BuildError(
            "No API key provided for the builder's LLM.\n"
            "Provide one via:\n"
            "  --api-key flag\n"
            "  LLM4AD_BUILD_API_KEY environment variable\n"
            "  --provider <name> (referencing ~/.llm4ad/settings.yaml)"
        )

    config: dict[str, Any] = {
        "api_key": resolved_api_key,
        "model": resolved_model,
        "timeout": 120.0,
        "max_retries": 3,
    }
    if resolved_base_url:
        config["base_url"] = resolved_base_url

    return BaseProvider.create(provider_type, config=config)


def _resolve_from_global_settings(provider_name: str) -> BaseProvider:
    """Resolve a provider from global settings by name."""
    from llm4ad.config.settings import load_global_settings

    global_data = load_global_settings()
    global_providers = global_data.get("providers", [])

    if not global_providers:
        raise BuildError(
            "No providers found in global settings (~/.llm4ad/settings.yaml).\n"
            "Either configure global providers or pass --api-key directly."
        )

    providers_by_name = {p.get("name", "default"): p for p in global_providers}
    if provider_name not in providers_by_name:
        available = ", ".join(providers_by_name.keys())
        raise BuildError(
            f"Provider '{provider_name}' not found in global settings.\n"
            f"Available: {available}"
        )

    cfg = providers_by_name[provider_name]
    ptype = cfg.get("type", "openai_compatible")
    return BaseProvider.create(ptype, config=cfg)
