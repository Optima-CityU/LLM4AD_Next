"""Bind a local Codex bridge to the existing provider and experiment settings."""

import uuid
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.services.user_default_model_service import update_user_default_model


def bridge_base_url() -> str:
    """Return the configured inference endpoint."""
    return settings.LOCAL_CODEX_BRIDGE_URL.rstrip("/") + "/v1"


def is_local_codex_provider(provider) -> bool:
    """Identify bindings without adding credentials or a new database enum."""
    return bool(settings.LOCAL_CODEX_BRIDGE_URL) and provider.base_url == bridge_base_url()


def _allowed(user) -> bool:
    """Limit access to the owner of an explicitly configured local deployment."""
    return settings.ENVIRONMENT == "local" and user.is_superuser


async def get_status(db: Session, user) -> dict:
    """Report bridge/login state and the current user's existing binding."""
    result = {
        "enabled": bool(
            _allowed(user) and settings.LOCAL_CODEX_BRIDGE_URL and settings.LOCAL_CODEX_BRIDGE_TOKEN
        ),
        "connected": False,
        "installed": False,
        "authenticated": False,
        "version": "",
        "model": "",
        "provider_id": None,
        "default_for_experiments": False,
        "reason": "not_configured",
        "models": [],
        "default_model": "",
        "models_available": False,
    }
    if not result["enabled"]:
        return result
    provider = db.exec(
        select(models.LLMProvider).where(
            models.LLMProvider.user_id == user.id,
            models.LLMProvider.base_url == bridge_base_url(),
        )
    ).first()
    if provider:
        result["provider_id"] = str(provider.id)
        selected_model = provider.model.split(";")[0]
        result["model"] = "" if selected_model == "codex-default" else selected_model
        defaults = db.exec(
            select(models.UserDefaultModel).where(models.UserDefaultModel.user_id == user.id)
        ).first()
        result["default_for_experiments"] = bool(
            defaults
            and defaults.planner_provider_id == provider.id
            and defaults.coder_provider_id == provider.id
            and defaults.planner_model_name == selected_model
            and defaults.coder_model_name == selected_model
        )
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.get(
                settings.LOCAL_CODEX_BRIDGE_URL.rstrip("/") + "/status",
                headers={"Authorization": f"Bearer {settings.LOCAL_CODEX_BRIDGE_TOKEN}"},
            )
            response.raise_for_status()
            data = response.json()
        result.update(
            {
                key: data.get(key, result[key])
                for key in (
                    "installed",
                    "authenticated",
                    "version",
                    "models",
                    "default_model",
                    "models_available",
                )
            }
        )
        result["connected"] = bool(result["installed"] and result["authenticated"])
        result["reason"] = (
            "ready"
            if result["connected"]
            else ("not_logged_in" if result["installed"] else "not_installed")
        )
    except (httpx.HTTPError, ValueError):
        result["reason"] = "unreachable"
    return result


async def bind(db: Session, user, name: str, model: str, set_defaults: bool):
    """Save a bridge provider and optionally bind the planning/coding defaults."""
    state = await get_status(db, user)
    if not state["enabled"]:
        raise HTTPException(403, "本机 Codex 连接尚未启用，或当前用户不是本地管理员")
    if not state["connected"]:
        raise HTTPException(409, "本机 Codex 尚未就绪，请检测连接并完成 codex login")
    provider = (
        db.get(models.LLMProvider, uuid.UUID(state["provider_id"]))
        if state["provider_id"]
        else None
    )
    previous_models = (provider.model or "").split(";") if provider else []
    selected_model = model.strip() or "codex-default"
    # Keep earlier explicit task/default selections valid when the binding changes.
    available_models = list(
        dict.fromkeys(item for item in [selected_model, *previous_models] if item)
    )
    if len(";".join(available_models)) > 255:
        raise HTTPException(400, "模型名称列表超过长度限制，请使用较短的模型 ID")
    for candidate in [*[item["id"] for item in state.get("models", [])], "codex-default"]:
        if (
            candidate not in available_models
            and len(";".join([*available_models, candidate])) <= 255
        ):
            available_models.append(candidate)
    if provider is None:
        provider = models.LLMProvider(user_id=user.id)
    provider.name = name.strip() or "本机 Codex CLI"
    provider.type = models.ProviderType.OPENAI_COMPATIBLE
    provider.base_url = bridge_base_url()
    provider.api_key = settings.LOCAL_CODEX_BRIDGE_TOKEN
    provider.auth_token = ""
    provider.model = ";".join(available_models)
    provider.timeout = 600
    provider.max_retries = 0
    provider.updated_time = datetime.now(UTC)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    if set_defaults:
        update_user_default_model(
            db,
            user.id,
            {
                "planner_provider_id": provider.id,
                "planner_model_name": selected_model,
                "coder_provider_id": provider.id,
                "coder_model_name": selected_model,
            },
        )
    return provider


def configure_experiment(input_args: dict, provider_configs: dict[str, dict]) -> None:
    """Route container coding through the host bridge and the plain-text coder."""
    coder = input_args.get("coder", {})
    planner = input_args.get("planner", {})
    selected = [provider_configs.get(part.get("provider"), {}) for part in (planner, coder)]
    local = [
        bool(settings.LOCAL_CODEX_BRIDGE_URL) and config.get("base_url") == bridge_base_url()
        for config in selected
    ]
    if any(local) and (input_args.get("multimodal") or {}).get("enabled"):
        raise HTTPException(400, "本机 Codex 暂不支持多模态采样，请关闭任务的多模态选项")
    if local[1]:
        coder["type"] = "custom"
        coder["timeout"] = max(coder.get("timeout") or 0, 600)
