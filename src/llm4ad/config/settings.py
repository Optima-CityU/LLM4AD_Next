"""Global settings management for LLM4AD.

Loads user-level settings from ``~/.llm4ad/settings.yaml`` and merges
them with per-task configuration.  Provider definitions in the global
file can be referenced by name in task configs, reducing boilerplate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from loguru import logger


def get_default_settings_path() -> Path:
    """Return the default global settings file path.

    Checks the ``LLM4AD_SETTINGS_FILE`` environment variable first,
    then falls back to ``~/.llm4ad/settings.yaml``.

    Returns:
        Path to the global settings file.
    """
    env_path = os.getenv("LLM4AD_SETTINGS_FILE")
    if env_path:
        return Path(env_path)
    return Path.home() / ".llm4ad" / "settings.yaml"


def load_yaml_with_env_expansion(path: str | Path) -> dict[str, Any]:
    """Load a YAML file with ``${VAR_NAME}`` environment variable expansion.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed YAML data as a dictionary.

    Raises:
        ImportError: If PyYAML is not installed.
        KeyError: If a referenced environment variable is not set.
        ValueError: If the YAML file cannot be parsed.
    """
    try:
        import yaml
        from yaml import SafeLoader
    except ImportError:
        raise ImportError(
            "PyYAML is required to load YAML config. Install with: pip install pyyaml"
        ) from None

    path_str = str(path)

    def env_var_constructor(loader: SafeLoader, node: yaml.Node) -> str:
        value = loader.construct_scalar(node)

        def replace_match(match: re.Match) -> str:
            var_name = match.group(1)
            env_val = os.getenv(var_name)
            if env_val is None:
                raise KeyError(
                    f"Environment variable '${{{var_name}}}' is not set"
                    f" in YAML file '{path_str}'"
                )
            return env_val

        return re.sub(r"\$\{(\w+)\}", replace_match, value)

    class EnvVarLoader(SafeLoader):
        pass

    EnvVarLoader.add_constructor("tag:yaml.org,2002:str", env_var_constructor)

    with open(path, encoding="UTF-8") as f:
        data = yaml.load(f, Loader=EnvVarLoader)

    return data if data is not None else {}


def load_global_settings(path: Path | None = None) -> dict[str, Any]:
    """Load global settings from the user's settings file.

    If the file does not exist, returns an empty dictionary silently.

    Args:
        path: Override path to the settings file.  When ``None``,
              uses :func:`get_default_settings_path`.

    Returns:
        Raw dictionary parsed from the settings YAML, or ``{}`` if
        the file is absent.
    """
    settings_path = path or get_default_settings_path()
    if not settings_path.exists():
        logger.debug("Global settings file not found at {}", settings_path)
        return {}

    logger.debug("Loading global settings from {}", settings_path)
    try:
        data = load_yaml_with_env_expansion(settings_path)
    except Exception as e:
        logger.warning("Failed to load global settings from {}: {}", settings_path, e)
        raise
    providers = data.get("providers", [])
    logger.info(
        "Loaded global settings from {} with {} provider(s)",
        settings_path,
        len(providers),
    )
    return data


def merge_with_global_settings(
    global_data: dict[str, Any],
    task_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge global settings into task configuration.

    If task configuration omits ``providers``, the global providers are
    used directly.  Otherwise, for each provider entry in
    ``task_data["providers"]``, if a provider with the same name exists in
    ``global_data["providers"]``, the global definition is used as the base
    and task-level fields are overlaid on top (task takes precedence).

    Args:
        global_data: Raw dict from the global settings file.
        task_data: Raw dict from the task configuration file.

    Returns:
        A new dict with providers merged.  Non-provider fields in
        ``task_data`` are left untouched.
    """
    global_providers = global_data.get("providers", [])
    if not global_providers:
        return task_data

    # Build lookup by provider name
    global_by_name: dict[str, dict[str, Any]] = {}
    for p in global_providers:
        name = p.get("name", "default")
        if name in global_by_name:
            logger.warning(
                "Duplicate provider name '{}' in global settings, using last definition",
                name,
            )
        global_by_name[name] = p

    task_providers = task_data.get("providers")
    if task_providers is None:
        result = dict(task_data)
        result["providers"] = list(global_providers)
        return result

    merged_providers = []
    for task_provider in task_providers:
        name = task_provider.get("name", "default")
        if name in global_by_name:
            # Global as base, task overrides
            merged = dict(global_by_name[name])
            override_keys = [k for k in task_provider if k != "name"]
            if override_keys:
                logger.debug(
                    "Merging provider '{}': overriding {} from task config",
                    name,
                    override_keys,
                )
            merged.update(task_provider)
            merged_providers.append(merged)
        else:
            merged_providers.append(task_provider)

    result = dict(task_data)
    result["providers"] = merged_providers
    return result
