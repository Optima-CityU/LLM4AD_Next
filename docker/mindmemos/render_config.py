"""Render MindMemOS runtime config from the mounted template and environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


SOURCE = Path(os.getenv("MINDMEMOS_CONFIG_TEMPLATE", "/app/mindmemos/config/mindmemos/dev.yaml"))
TARGET = Path(os.getenv("MINDMEMOS_RENDERED_CONFIG", "/tmp/mindmemos-dev.yaml"))
CONFIG_DIR = Path(os.getenv("MINDMEMOS_CONFIG_DIR", "/app/mindmemos/config/mindmemos"))


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _float_env(name: str, default: float | None = None) -> float | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _normalize_model(model: str) -> str:
    if "/" in model:
        return model
    if model.startswith(("jina-embeddings-", "jina-reranker-")):
        return f"jina_ai/{model}"
    return model


def _normalize_api_base(model: str, api_base: str) -> str:
    if not model.startswith("jina_ai/"):
        return api_base
    normalized = api_base.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _model_endpoint(prefix: str) -> dict[str, Any] | None:
    raw_model = os.getenv(f"{prefix}_MODEL", "").strip()
    api_base = os.getenv(f"{prefix}_API_BASE", "").strip()
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
    if not (raw_model and api_base and api_key):
        return None

    model = _normalize_model(raw_model)
    endpoint: dict[str, Any] = {
        "model": model,
        "api_base": _normalize_api_base(model, api_base),
        "api_key": api_key,
    }
    timeout = _int_env(f"{prefix}_TIMEOUT")
    if timeout is not None:
        endpoint["timeout"] = timeout
    dimensions = _int_env(f"{prefix}_DIMENSIONS")
    if dimensions is not None:
        endpoint["dimensions"] = dimensions
    temperature = _float_env(f"{prefix}_TEMPERATURE")
    if temperature is not None:
        endpoint["temperature"] = temperature
    return endpoint


def _normalize_auth_paths(data: dict[str, Any]) -> None:
    auth = data.setdefault("auth", {})
    api_key_file = str(auth.get("api_key_file") or "").strip()
    if not api_key_file:
        return
    path = Path(api_key_file)
    auth["api_key_file"] = str(path if path.is_absolute() else CONFIG_DIR / path)


def _apply_gateway_auth(data: dict[str, Any]) -> None:
    auth = data.setdefault("auth", {})
    auth["mode"] = os.getenv("MINDMEMOS_AUTH_MODE", auth.get("mode", "gateway_jwt"))
    auth["gateway_jwt_secret"] = os.getenv(
        "MINDMEMOS_GATEWAY_JWT_SECRET",
        auth.get("gateway_jwt_secret", "demo-jwt-secret-key"),
    )
    auth["gateway_jwt_issuer"] = os.getenv(
        "MINDMEMOS_GATEWAY_JWT_ISSUER",
        auth.get("gateway_jwt_issuer", "demo-jwt-gateway"),
    )
    auth["gateway_jwt_audience"] = os.getenv(
        "MINDMEMOS_GATEWAY_JWT_AUDIENCE",
        auth.get("gateway_jwt_audience", "demo-jwt-audience"),
    )


def _apply_dynamic_binding_config(data: dict[str, Any]) -> None:
    data.setdefault("provider_binding", {})["enabled"] = _truthy(
        os.getenv("MINDMEMOS_PROVIDER_BINDING_ENABLED", "true")
    )
    qdrant = data.setdefault("database", {}).setdefault("qdrant", {})
    qdrant["project_collection_namespace_enabled"] = _truthy(
        os.getenv("MINDMEMOS_PROJECT_COLLECTION_NAMESPACE_ENABLED", "true")
    )


def main() -> None:
    with SOURCE.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    _normalize_auth_paths(data)
    _apply_gateway_auth(data)
    _apply_dynamic_binding_config(data)

    chat_endpoint = _model_endpoint("MINDMEMOS_CHAT")
    chat_router = data.setdefault("chat_model_router", {})
    chat_router["endpoints"] = [chat_endpoint] if chat_endpoint else []

    embed_endpoint = _model_endpoint("MINDMEMOS_EMBED")
    embed_router = data.setdefault("embed_model_router", {})
    embed_router["endpoints"] = [embed_endpoint] if embed_endpoint else []

    embed_dimensions = _int_env("MINDMEMOS_EMBED_DIMENSIONS")
    if embed_dimensions is not None:
        qdrant = data.setdefault("database", {}).setdefault("qdrant", {})
        qdrant["vector_size"] = embed_dimensions

    rerank_endpoint = _model_endpoint("MINDMEMOS_RERANK")
    rerank_enabled = _truthy(os.getenv("MINDMEMOS_RERANK_ENABLED"))
    rerank_router = data.setdefault("rerank_model_router", {})
    rerank_router["endpoints"] = [rerank_endpoint] if rerank_enabled and rerank_endpoint else []
    algo_rerank = data.setdefault("algo_config", {}).setdefault("search", {}).setdefault("rerank", {})
    algo_rerank["enabled"] = bool(rerank_enabled and rerank_endpoint)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
