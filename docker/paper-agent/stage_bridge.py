"""Workspace-local MCP bridge for publishing validated research artifacts.

Exposes two tools to the research runtime's agent:

- ``check_typst``: compile the proposal Typst document in-process (official
  ``typst`` binding) and return errors/warnings so the agent can fix its own
  syntax before the user ever sees a broken preview.
- ``publish_stage_result``: publish the finished stage artifact; the same
  compile check gates publication, so a stage result that does not compile can
  never be published.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import typst
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm4ad-stage")

_WORKSPACE_SOURCE_ROOT = Path(os.environ.get("LLM4AD_WORKSPACE_ROOT", "/workspace/source"))
_PROPOSAL_ENTRY = "proposal.typ"


def _required_environment(name: str) -> str:
    """Read one required runtime value without exposing it to tool output."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required runtime configuration: {name}")
    return value


def _compile_check() -> dict[str, Any]:
    """Compile the current proposal and return its diagnostics.

    Hard errors raise :class:`typst.TypstError` carrying the first error's
    message plus hints, which is exactly what the agent needs to iterate:
    fix, re-check, repeat. Warnings are collected on the success path so the
    agent can also clean up deprecated constructs.
    """
    root = _WORKSPACE_SOURCE_ROOT
    entry = root / _PROPOSAL_ENTRY
    try:
        typst.compile(str(entry), root=root)
    except typst.TypstError as exc:
        return {
            "ok": False,
            "entry": _PROPOSAL_ENTRY,
            "errors": [
                {
                    "message": exc.message,
                    "hints": list(exc.hints or []),
                    "trace": [str(item) for item in (exc.trace or [])],
                }
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "entry": _PROPOSAL_ENTRY,
            "errors": [{"message": f"compile failed: {exc}"}],
        }
    try:
        _document, warnings = typst.compile_with_warnings(str(entry), root=root)
    except Exception:  # noqa: BLE001
        warnings = []
    return {
        "ok": True,
        "entry": _PROPOSAL_ENTRY,
        "warnings": [
            {
                "message": getattr(warning, "message", str(warning)),
                "hints": list(getattr(warning, "hints", None) or []),
            }
            for warning in (warnings or [])
        ],
    }


def _format_check(check: dict[str, Any]) -> str:
    """Render a compile-check result as a single human-readable string."""
    if check["ok"]:
        return "ok"
    parts = []
    for error in check.get("errors", []):
        parts.append(error.get("message", ""))
        for hint in error.get("hints", []) or []:
            parts.append(f"hint: {hint}")
    return "; ".join(part for part in parts if part)


@mcp.tool()
async def check_typst() -> dict[str, Any]:
    """Compile the proposal Typst document and report errors and warnings.

    Run this after every edit to ``.typ`` files. The compile stops at the
    first error, so fix the reported error and call again until ``ok`` is
    true. ``publish_stage_result`` refuses to publish while errors remain.
    """
    return await asyncio.to_thread(_compile_check)


@mcp.tool()
async def publish_stage_result(artifact: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """Publish the completed stage artifact after all stage work is final.

    Proposal stages refuse publication while Typst does not compile. Read-only
    workflows publish structured artifacts without a document compile gate.
    """
    if os.environ.get("LLM4AD_STAGE_REQUIRES_TYPST", "true").lower() == "true":
        check = await asyncio.to_thread(_compile_check)
        if not check["ok"]:
            raise RuntimeError(
                f"Typst compile check failed before publication: {_format_check(check)}"
            )
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
