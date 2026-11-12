"""Typed universal gateway settings and private applied/pending configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .configuration import user_ids, validate_server
from .sdk.storage import persist

DEFAULT_SERVER = "https://llm4ad-next.cn"
ADAPTERS = {
    "example": "llm4ad.gateway.examples.basic.evaluator:Evaluator",
    "hgamey": "llm4ad.gateway.examples.hgamey.evaluator:HGameyShaderEvaluator",
}
ENVIRONMENT_KEYS = {
    "server": "LLM4AD_SERVER_URL",
    "name": "LLM4AD_GATEWAY_NAME",
    "allowed_users": "LLM4AD_ALLOWED_USER_IDS",
    "adapter": "LLM4AD_GATEWAY_ADAPTER",
    "evaluator": "LLM4AD_EVALUATOR",
    "state_dir": "LLM4AD_GATEWAY_STATE_DIR",
    "concurrency": "LLM4AD_GATEWAY_CONCURRENCY",
    "cleanup_enabled": "LLM4AD_GATEWAY_CLEANUP_ENABLED",
    "cleanup_interval": "LLM4AD_GATEWAY_CLEANUP_INTERVAL_SECONDS",
    "cleanup_keep_seconds": "LLM4AD_GATEWAY_EVIDENCE_KEEP_SECONDS",
    "local_log_keep_seconds": "LLM4AD_GATEWAY_LOG_KEEP_SECONDS",
    "ui_host": "LLM4AD_GATEWAY_UI_HOST",
    "ui_port": "LLM4AD_GATEWAY_UI_PORT",
    "open_browser": "LLM4AD_GATEWAY_OPEN_BROWSER",
    "language": "LLM4AD_GATEWAY_UI_LANGUAGE",
    "theme": "LLM4AD_GATEWAY_UI_THEME",
}
COLD_SETTINGS = frozenset({"server", "adapter", "evaluator", "state_dir"})
LISTENER_SETTINGS = frozenset({"ui_host", "ui_port"})


class GatewaySettings(BaseModel):
    """Configure the SDK without importing an optional UI or hardware backend."""

    model_config = ConfigDict(extra="forbid", validate_default=True)
    server: str = DEFAULT_SERVER
    name: str = "local-evaluator"
    allowed_users: list[str] = Field(default_factory=list)
    adapter: Literal["custom", "example", "hgamey"] = "custom"
    evaluator: str | None = None
    state_dir: str = ".llm4ad-gateway"
    concurrency: int = Field(default=1, ge=1)
    cleanup_enabled: bool = True
    cleanup_interval: float = Field(default=30, ge=1, allow_inf_nan=False)
    cleanup_keep_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    local_log_keep_seconds: float = Field(default=604800, ge=0, allow_inf_nan=False)
    ui_host: str = "127.0.0.1"
    ui_port: int = Field(default=8768, ge=0, le=65535)
    open_browser: bool | None = None
    language: Literal["auto", "zh", "en"] = "auto"
    theme: Literal["auto", "dark", "light"] = "auto"

    @field_validator("server")
    @classmethod
    def platform_url(cls, value: str) -> str:
        """Use the existing platform URL policy."""
        return validate_server(value.strip())

    @field_validator("allowed_users", mode="before")
    @classmethod
    def authorized_users(cls, value) -> list[str]:
        """Accept both form lists and existing comma-separated environment values."""
        return user_ids([value] if isinstance(value, str) else (value or []))

    @field_validator("state_dir")
    @classmethod
    def state_directory(cls, value: str) -> str:
        """Pin relative paths before a worker changes its working directory."""
        return str(Path(value).expanduser().resolve())

    @field_validator("name", "ui_host")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        """Reject accidental empty names or listener addresses."""
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Value must be a nonempty single line")
        return value.strip()

    @field_validator("open_browser", mode="before")
    @classmethod
    def browser_mode(cls, value):
        """Keep automatic detection distinct from explicit true/false settings."""
        return None if value in (None, "", "auto") else value

    @property
    def ready(self) -> bool:
        """Report whether startup can proceed beyond the setup form."""
        return bool(self.allowed_users and (self.adapter != "custom" or self.evaluator))

    @property
    def evaluator_spec(self) -> str:
        """Resolve the same built-in adapters used by the CLI."""
        if self.adapter != "custom":
            return ADAPTERS[self.adapter]
        if not self.evaluator:
            raise ValueError("Provide an evaluator file or module followed by :Class")
        from .sdk.contracts import normalize_spec

        return normalize_spec(self.evaluator)

    def require_ready(self) -> None:
        """Retain clear fail-fast behavior for a headless launcher."""
        if not self.allowed_users:
            raise ValueError("Provide --allow-user UUID or LLM4AD_ALLOWED_USER_IDS")
        _ = self.evaluator_spec
        if self.adapter == "hgamey" and self.concurrency != 1:
            raise ValueError(
                "The HGamey device executor has one serialized evaluation slot per device"
            )

    def environment(self, base: dict | None = None) -> dict:
        """Overlay supported settings while retaining private adapter configuration."""
        environment = dict(base or {})
        for key, name in ENVIRONMENT_KEYS.items():
            value = getattr(self, key)
            if value is None:
                environment.pop(name, None)
            elif isinstance(value, list):
                environment[name] = ",".join(value)
            elif isinstance(value, bool):
                environment[name] = str(value).lower()
            else:
                environment[name] = str(value)
        return environment


def environment_values(environment: dict) -> dict:
    """Read only declared universal configuration, never an entire process environment."""
    return {
        key: environment[name]
        for key, name in ENVIRONMENT_KEYS.items()
        if environment.get(name) not in (None, "")
    }


class SettingsStore:
    """Keep effective and deferred settings separate across launcher restarts."""

    def __init__(self, root: Path):
        """Keep UI configuration at the launcher's stable control directory."""
        self.root = root.expanduser().resolve()
        self.path = self.root / "dashboard-settings.json"

    def read(self) -> dict:
        """Read saved public settings without creating a gateway or identity."""
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Saved gateway settings must be an object")
        return value

    def write(
        self,
        settings: GatewaySettings,
        pending: GatewaySettings | None,
        paused: bool,
        pending_error=None,
    ) -> None:
        """Atomically persist only the declared public setting fields."""
        persist(
            self.path,
            {
                "applied": settings.model_dump(mode="json"),
                "pending": pending.model_dump(mode="json") if pending else None,
                "paused": paused,
                "pending_error": pending_error,
            },
        )


def load_settings(
    root: Path, environment: dict, explicit: dict | None = None, config: dict | None = None
) -> GatewaySettings:
    """Merge defaults, environment, saved UI, explicit JSON and CLI in that order."""
    saved = SettingsStore(root).read()
    values = {
        "state_dir": str(root),
        **environment_values(environment),
        **saved.get("applied", {}),
        **environment_values(config or {}),
        **{key: value for key, value in (explicit or {}).items() if value is not None},
    }
    if values.get("evaluator") and "adapter" not in values:
        values["adapter"] = "custom"
    return GatewaySettings.model_validate(values)
