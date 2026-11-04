"""Expose workspace-scoped AutoDesign operations over the official MCP server."""

from __future__ import annotations

import math
import os
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm4ad-autodesign")


def _required_environment(name: str) -> str:
    """Read a required runtime setting without printing its value.

    Args:
        name: Environment variable supplied by the workspace runtime.

    Returns:
        Nonempty runtime setting.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required runtime configuration: {name}")
    return value


def _positive_timeout(value: str | int | float) -> float:
    """Parse a finite, positive timeout in seconds.

    Args:
        value: Transport or validation timeout.

    Returns:
        Valid timeout in seconds.
    """
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Timeout must be a finite positive number of seconds") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be a finite positive number of seconds")
    return timeout


def _safe_detail(value: Any, token: str) -> Any:
    """Remove credentials from backend error details.

    Args:
        value: Structured error detail returned by the backend.
        token: Workspace runtime token that must never enter tool output.

    Returns:
        Error detail with credential fields and reflected tokens redacted.
    """
    if isinstance(value, dict):
        secret_fields = {"api_key", "authorization", "password", "secret", "token", "x-llm4ad-runtime-token"}
        return {key: "[redacted]" if key.lower() in secret_fields else _safe_detail(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_detail(item, token) for item in value]
    if isinstance(value, str):
        return value.replace(token, "[redacted]")
    return value


async def _runtime_call(
    operation: str,
    task_id: str | None = None,
    arguments: dict[str, Any] | None = None,
    *,
    validation_timeout: float | None = None,
) -> dict[str, Any]:
    """Forward one operation without local filesystem access or mutation retries.

    Args:
        operation: Runtime operation name.
        task_id: Optional task in the workspace's authorized family.
        arguments: Operation-specific JSON arguments.
        validation_timeout: Worker timeout, allowing transport time for its result.

    Returns:
        Backend response object, including its authoritative paths and state.
    """
    url = _required_environment("LLM4AD_AUTODESIGN_URL")
    workspace_id = _required_environment("LLM4AD_AUTODESIGN_WORKSPACE_ID")
    token = _required_environment("LLM4AD_AUTODESIGN_TOKEN")
    timeout = _positive_timeout(os.environ.get("LLM4AD_AUTODESIGN_TIMEOUT_SECONDS", "120"))
    if validation_timeout is not None:
        dependency_timeout = _positive_timeout(os.environ.get("TASK_DEP_INSTALL_TIMEOUT", "600"))
        timeout = max(timeout, validation_timeout + dependency_timeout + 30)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                url,
                headers={"x-llm4ad-runtime-token": token},
                json={
                    "workspace_id": workspace_id,
                    "operation": operation,
                    "task_id": task_id,
                    "arguments": arguments if arguments is not None else {},
                },
            )
    except httpx.TimeoutException:
        raise RuntimeError(
            f"AutoDesign {operation} timed out after {timeout:g}s; inspect task context before retrying. "
            "For validation, adjust timeout_seconds or LLM4AD_AUTODESIGN_TIMEOUT_SECONDS as needed."
        ) from None
    except httpx.RequestError:
        raise RuntimeError(f"AutoDesign {operation} could not reach the runtime service") from None
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(f"AutoDesign {operation} returned a non-JSON response (HTTP {response.status_code})") from None
    if not response.is_success:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        safe_detail = _safe_detail(detail, token) if detail else "Runtime operation rejected"
        raise RuntimeError(f"AutoDesign {operation} failed (HTTP {response.status_code}): {safe_detail}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"AutoDesign {operation} returned an invalid response object")
    return payload


@mcp.tool()
async def get_task_context(task_id: str | None = None) -> dict[str, Any]:
    """Read live task configuration, status, family, and project verification state.

    Args:
        task_id: Task UUID, or the task associated with this workspace.

    Returns:
        Sanitized context; an independent session can have no task yet.
    """
    return await _runtime_call("context", task_id)


@mcp.tool()
async def get_memory_context(task_id: str | None = None) -> dict[str, Any]:
    """Inspect memory availability, configured scopes and evolution settings.

    Args:
        task_id: Selected family member, or this conversation's associated task.

    Returns:
        Redacted memory configuration and the scopes available to this design.
    """
    return await _runtime_call("memory_context", task_id)


@mcp.tool()
async def list_memory_cards(
    scope: Literal["user", "project", "task"] = "user",
    task_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Read existing cards from the same memory store used by the platform UI.

    Args:
        scope: User memory, this design's project, or selected task-family memory.
        task_id: Selected family member, or this conversation's associated task.
        page: One-based result page.
        page_size: Number of cards requested from the existing memory service.

    Returns:
        Full card contents, identities, status and pagination metadata.
    """
    return await _runtime_call("memory_list", task_id, {"scope": scope, "page": page, "page_size": page_size})


@mcp.tool()
async def save_memory_card(
    content: str,
    title: str = "",
    memory_type: Literal["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"] = "general_insight",
    scope: Literal["user", "project", "task"] = "user",
    task_id: str | None = None,
    memory_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save a user-requested experience card through the platform memory service.

    Args:
        content: Verified experience or knowledge, with its supporting evidence.
        title: Short descriptive title.
        memory_type: Existing platform category for the card.
        scope: User, associated project or selected task-family memory.
        task_id: Selected family member, or this conversation's associated task.
        memory_id: Existing card ID to update; omit to create a card.
        tags: Optional labels for the experience.
        metadata: Evidence such as source task ID, file paths and metric fields.

    Returns:
        The saved card and its actual persistent identity.
    """
    return await _runtime_call("memory_save", task_id, {"scope": scope, "card": {
        "id": memory_id, "type": memory_type, "title": title, "content": content,
        "tags": tags or [], "metadata": {"llm4ad_evidence": metadata} if metadata else {},
    }})


@mcp.tool()
async def update_project_name(name: str) -> dict[str, Any]:
    """Name the current design from the goal established in the conversation.

    Args:
        name: Concise project name in the user's preferred language.

    Returns:
        Saved design title, also used when preparing its task.
    """
    return await _runtime_call("rename", arguments={"name": name})


@mcp.tool()
async def list_task_files(
    area: Literal["input", "run", "draft", "references", "analysis"] = "run",
    task_id: str | None = None,
    directory: str = "",
) -> dict[str, Any]:
    """List scoped task files, references, or read-only design records.

    Args:
        area: File area interpreted by the backend, never a host directory.
        task_id: Task UUID, or the task associated with this workspace.
        directory: Relative directory within the selected area.

    Returns:
        Scoped paths and file metadata supplied by the backend.
    """
    return await _runtime_call("list_files", task_id, {"area": area, "directory": directory})


@mcp.tool()
async def read_task_file(
    path: str,
    area: Literal["input", "run", "draft", "references", "analysis"] = "run",
    task_id: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read full file text by default, or an explicitly requested line range.

    Args:
        path: Relative file path returned by the scoped file listing.
        area: Input, run, draft, references, or read-only analysis file area.
        task_id: Task UUID, or the task associated with this workspace.
        start_line: Optional first line, using the backend's one-based numbering.
        end_line: Optional last line, inclusive.

    Returns:
        File content and line metadata without silent truncation.
    """
    arguments: dict[str, Any] = {"path": path, "area": area}
    if start_line is not None:
        arguments["start_line"] = start_line
    if end_line is not None:
        arguments["end_line"] = end_line
    return await _runtime_call("read_file", task_id, arguments)


@mcp.tool()
async def create_child_task(
    parent_task_id: str,
    config_patch: dict[str, Any] | None = None,
    seed: Literal["input", "best"] = "input",
    name: str | None = None,
) -> dict[str, Any]:
    """Create a child with canonical inputs copied by the server.

    Args:
        parent_task_id: Explicit source task UUID.
        config_patch: Nested AppConfig fields to merge into the child.
        seed: Copy original inputs, or replace candidate inputs with exported best code.
        name: Optional child task name.

    Returns:
        Created child context; missing best code is an error, never a silent fallback.
    """
    return await _runtime_call("create_child", parent_task_id, {"config_patch": config_patch, "seed": seed, "name": name})


@mcp.tool()
async def patch_child_parameters(task_id: str, config_patch: dict[str, Any]) -> dict[str, Any]:
    """Compatibility alias for editing an explicit unstarted task.

    Args:
        task_id: Target child UUID.
        config_patch: Nested AppConfig patch, including planner, coder, or evaluator fields.

    Returns:
        Server-validated child state; credentials and task eligibility stay server-owned.
    """
    return await _runtime_call("patch_child", task_id, {"config_patch": config_patch})


@mcp.tool()
async def patch_task_parameters(task_id: str, config_patch: dict[str, Any]) -> dict[str, Any]:
    """Apply stored parameters to an explicit unstarted root or child.

    Args:
        task_id: Unstarted task selected by the user or created for the change.
        config_patch: Nested AppConfig fields to change.

    Returns:
        Actual before/after changes without starting or publishing the task.
    """
    return await _runtime_call("patch_child", task_id, {"config_patch": config_patch})


@mcp.tool()
async def sync_task_draft(
    task_id: str,
    resolution: Literal["keep_draft", "use_task"] | None = None,
) -> dict[str, Any]:
    """Merge manual task changes into a draft and resolve actual conflicts.

    Args:
        task_id: Exact task whose draft is being edited.
        resolution: For conflicting fields/files, keep Agent draft changes or use
            stored task values; omit to preserve both and report conflicts.

    Returns:
        Scoped draft path and conflicts still requiring resolution. This does
        not publish the draft or replace the task's canonical inputs.
    """
    return await _runtime_call("sync_draft", task_id, {"resolution": resolution})


@mcp.tool()
async def validate_project(project_path: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Verify the authored project directory in an isolated task runner.

    Args:
        project_path: Current project under the workspace's draft directory.
        timeout_seconds: Positive worker timeout for actual baseline and evaluator tests.

    Returns:
        Actual check results and the verified project revision.
    """
    timeout = _positive_timeout(timeout_seconds)
    return await _runtime_call(
        "validate",
        arguments={"project_path": project_path, "timeout_seconds": timeout_seconds},
        validation_timeout=timeout,
    )


@mcp.tool()
async def prepare_task(
    project_id: str | None = None,
    task_id: str | None = None,
    name: str | None = None,
    enable_memory: bool | None = None,
) -> dict[str, Any]:
    """Prepare an unstarted task directly from the verified project files.

    Args:
        project_id: Optional destination project UUID.
        task_id: Unstarted child UUID; omission creates a root or a new child for an existing task.
        name: Optional task name.
        enable_memory: Optional task memory choice; omission preserves server defaults.

    Returns:
        Task and session for the existing configuration steps; execution has not started.
    """
    return await _runtime_call("publish", task_id, {"project_id": project_id, "name": name, "enable_memory": enable_memory})


@mcp.tool()
async def start_task(task_id: str) -> dict[str, Any]:
    """Start the selected task when the user requests execution.

    Args:
        task_id: Explicit task UUID from current context or a created child.

    Returns:
        Server execution state; inspect it before claiming the task is running.
    """
    return await _runtime_call("start", task_id)


@mcp.tool()
async def stop_task(task_id: str) -> dict[str, Any]:
    """Request that the server stop the selected running task.

    Args:
        task_id: Explicit task UUID from current context or a created child.

    Returns:
        Stop result; request acceptance alone does not prove execution has ended.
    """
    return await _runtime_call("stop", task_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
