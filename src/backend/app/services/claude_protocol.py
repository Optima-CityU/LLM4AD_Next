"""Shared Claude runtime protocol routing rules."""

from __future__ import annotations

from typing import Any, Literal

from app import models

ClaudeUpstreamFormat = Literal["anthropic", "openai_chat"]


def provider_api_format(provider_type: Any) -> ClaudeUpstreamFormat:
    """Map a platform provider type to the cc-switch upstream wire format."""
    value = provider_type.value if hasattr(provider_type, "value") else str(provider_type)
    if value == models.ProviderType.ANTHROPIC.value:
        return "anthropic"
    return "openai_chat"
