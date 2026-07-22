"""Configuration management for CLI memory settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# JWT issuer/audience match the standard MindMemOS gateway deployment
# (backend defaults: LLM4AD_MINDMEMOS_JWT_ISSUER / _AUDIENCE). These are
# deployment-level values that rarely change, so they are not exposed in the
# config form; advanced users can override them directly in settings.yaml.
# jwt_secret defaults to the standard MindMemOS deployment secret so the local
# default setup works out of the box; override it if the deployment sets a
# custom LLM4AD_MINDMEMOS_JWT_SECRET.
DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "http://127.0.0.1:18000",
    "jwt_secret": "demo-jwt-secret-key",
    "jwt_issuer": "demo-jwt-gateway",
    "jwt_audience": "demo-jwt-audience",
    "timeout": 60,
    "add_timeout": 300,  # 5 minutes for add operations
}

# Prefilled with OpenAI defaults so users only need to add an API key.
DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "chat": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
    },
    "embedding": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "text-embedding-3-small",
        "dimensions": 1536,
    },
    "rerank": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
}


def settings_path() -> Path:
    """Get the path to CLI settings file."""
    return Path.home() / ".llm4ad" / "settings.yaml"


def _as_mapping(value: Any) -> dict[str, Any]:
    """Return a string-keyed mapping, or an empty mapping for invalid YAML values."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def load_settings() -> dict[str, Any]:
    """Load CLI settings from file."""
    path = settings_path()
    if not path.exists():
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            data = _as_mapping(yaml.safe_load(f))
            return _as_mapping(data.get("memory"))
    except Exception:
        return {}


def save_settings(config: dict[str, Any]) -> None:
    """Save CLI settings to file."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing settings
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = _as_mapping(yaml.safe_load(f))
    else:
        data = {}

    # Update memory section
    if not isinstance(data.get("memory"), dict):
        data["memory"] = {}

    # Deep merge config into memory section
    for key, value in config.items():
        if isinstance(value, dict) and key in data["memory"] and isinstance(data["memory"][key], dict):
            data["memory"][key].update(value)
        else:
            data["memory"][key] = value

    # Write back
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

    # Secure permissions
    os.chmod(path, 0o600)


def get_config() -> dict[str, Any]:
    """Get merged configuration with defaults."""
    settings = load_settings()

    config = DEFAULT_CONFIG.copy()
    if "mindmemos" in settings:
        config.update(settings["mindmemos"])

    return config


def get_providers() -> dict[str, Any]:
    """Get provider configuration (chat, embedding, optional rerank)."""
    settings = load_settings()

    providers = {
        "chat": DEFAULT_PROVIDERS["chat"].copy(),
        "embedding": DEFAULT_PROVIDERS["embedding"].copy(),
        "rerank": DEFAULT_PROVIDERS["rerank"].copy(),
    }

    if "providers" in settings:
        for key in ("chat", "embedding", "rerank"):
            if key in settings["providers"]:
                providers[key].update(settings["providers"][key])

    return providers


def is_connection_configured() -> bool:
    """Check whether the MindMemOS connection is configured."""
    cfg = get_config()
    return bool(cfg.get("base_url") and cfg.get("jwt_secret"))


def is_binding_configured() -> bool:
    """Check whether chat and embedding providers are configured."""
    providers = get_providers()
    chat = providers["chat"]
    embedding = providers["embedding"]
    return bool(
        chat.get("base_url")
        and chat.get("api_key")
        and chat.get("model")
        and embedding.get("base_url")
        and embedding.get("api_key")
        and embedding.get("model")
    )


def get_embedding_lock() -> dict[str, Any] | None:
    """Return the locked embedding identity (model, dimensions), or None.

    Set once on the first successful bind. Mirrors the backend rule where
    embedding model/dimensions are immutable for a memory space (changing them
    would invalidate stored vectors).
    """
    settings = load_settings()
    lock = settings.get("embedding_lock")
    if isinstance(lock, dict) and lock.get("model"):
        return lock
    return None


def set_embedding_lock(model: str, dimensions: int) -> None:
    """Persist the locked embedding identity after the first successful bind."""
    save_settings({"embedding_lock": {"model": model, "dimensions": dimensions}})


def resolve_task_id_from_config(config_path: str | Path | None) -> str | None:
    """Resolve a task/session id from a pipeline config.yaml.

    Reads ``memory.mindmemos_session_id`` / ``memory.task_id`` /
    ``memory.session_id``, falling back to top-level ``project_name``.
    Returns None if the file has no task identifier. When ``config_path`` is
    None, returns None (global scope).
    """
    if config_path is None:
        return None
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        return None
    memory = data.get("memory")
    if isinstance(memory, dict):
        for key in ("mindmemos_session_id", "task_id", "session_id"):
            value = memory.get(key)
            if value and str(value).strip():
                return str(value).strip()
    project_name = data.get("project_name")
    if project_name and str(project_name).strip():
        return str(project_name).strip()
    return None
