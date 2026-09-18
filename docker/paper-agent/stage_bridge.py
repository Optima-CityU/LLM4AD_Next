"""Workspace-local MCP bridge for publishing validated research artifacts."""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm4ad-stage")


def _required_environment(name: str) -> str:
    """Read one required runtime value without exposing it to tool output."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required runtime configuration: {name}")
    return value


@mcp.tool()
async def publish_stage_result(artifact: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """Publish the completed stage artifact after all project edits are final."""
    payload = {
        "workspace_id": _required_environment("LLM4AD_STAGE_WORKSPACE_ID"),
        "run_id": _required_environment("LLM4AD_STAGE_RUN_ID"),
        "idempotency_key": idempotency_key,
        "artifact": artifact,
    }
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        response = await client.post(
            _required_environment("LLM4AD_STAGE_PUBLISH_URL"),
            headers={
                "x-llm4ad-runtime-token": _required_environment("LLM4AD_STAGE_RUNTIME_TOKEN")
            },
            json=payload,
        )
    if not response.is_success:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(detail or f"Stage publication failed with HTTP {response.status_code}")
    return response.json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
