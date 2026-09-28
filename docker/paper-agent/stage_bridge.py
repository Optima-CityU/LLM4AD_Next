"""Workspace-local MCP bridge for publishing validated research artifacts.

Exposes three tools to the research runtime's agent:

- ``check_typst``: compile the proposal Typst document in-process (official
  ``typst`` binding) and return errors/warnings so the agent can fix its own
  syntax before the user ever sees a broken preview.
- ``build_algorithm_task``: invoke the official LLM4AD builder and validation
  pipeline for an AutoDiscovery task package.
- ``publish_stage_result``: publish the finished stage artifact; the same
  compile check gates publication, so a stage result that does not compile can
  never be published.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
import typst
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm4ad-stage")

_WORKSPACE_SOURCE_ROOT = Path(os.environ.get("LLM4AD_WORKSPACE_ROOT", "/workspace/source"))
_WORKSPACE_ROOT = _WORKSPACE_SOURCE_ROOT.parent
_AUTODISCOVERY_PACKAGES_ROOT = _WORKSPACE_ROOT / ".research" / "autodiscovery" / "packages"
_PROPOSAL_ENTRY = "proposal.typ"
_SAFE_PROJECT_NAME = re.compile(r"[^a-zA-Z0-9_-]+")


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


def _source_input_path(
    raw_path: str | None,
    *,
    directory: bool,
    use_parent_for_file: bool = False,
) -> str | None:
    """Resolve one optional builder input inside the uploaded source tree."""
    if not raw_path or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = _WORKSPACE_SOURCE_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(_WORKSPACE_SOURCE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Builder inputs must stay inside /workspace/source") from exc
    if not resolved.exists():
        raise ValueError(f"Builder input does not exist: {raw_path}")
    if directory and not resolved.is_dir():
        raise ValueError(f"Dataset input must be a directory: {raw_path}")
    if not directory and not resolved.is_file() and not resolved.is_dir():
        raise ValueError(f"Code input must be a file or directory: {raw_path}")
    if use_parent_for_file and resolved.is_file():
        resolved = resolved.parent
    return str(resolved)


def _project_slug(project_name: str) -> str:
    """Create a safe package directory name without changing project meaning."""
    slug = _SAFE_PROJECT_NAME.sub("-", project_name.strip()).strip("-_").lower()
    return slug[:80] or f"algorithm-task-{uuid.uuid4().hex[:8]}"


def _package_manifest(package_dir: Path) -> list[str]:
    """List regular, symlink-free files in one generated task package."""
    manifest: list[str] = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Generated task packages cannot contain symbolic links")
        if path.is_file():
            manifest.append(path.relative_to(package_dir).as_posix())
    return manifest


@mcp.tool()
async def check_typst() -> dict[str, Any]:
    """Compile the proposal Typst document and report errors and warnings.

    Run this after every edit to ``.typ`` files. The compile stops at the
    first error, so fix the reported error and call again until ``ok`` is
    true. ``publish_stage_result`` refuses to publish while errors remain.
    """
    return await asyncio.to_thread(_compile_check)


@mcp.tool()
async def build_algorithm_task(
    description: str,
    project_name: str,
    code_path: str | None = None,
    data_path: str | None = None,
) -> dict[str, Any]:
    """Build and validate a complete runnable LLM4AD task package.

    Use this only after the AutoDiscovery conversation has resolved the
    algorithm boundary, input/output contract, metrics, validity rules, and
    evaluator data. Existing code and data paths must refer to uploaded files
    under ``/workspace/source``. The returned package has already passed the
    official LLM4AD builder validation pipeline and is ready to publish.
    """
    if not description.strip():
        raise ValueError("A complete task description is required")
    build_id = uuid.uuid4().hex
    build_root = _AUTODISCOVERY_PACKAGES_ROOT / build_id
    build_root.mkdir(parents=True, exist_ok=False)
    try:
        from llm4ad.builder import build_task

        package_path = Path(
            await build_task(
                description.strip(),
                output_dir=str(build_root),
                code_path=_source_input_path(
                    code_path,
                    directory=False,
                    use_parent_for_file=True,
                ),
                data_path=_source_input_path(data_path, directory=True),
                project_name=_project_slug(project_name),
                api_key=_required_environment("LLM4AD_BUILDER_API_KEY"),
                model=_required_environment("LLM4AD_BUILDER_MODEL"),
                base_url=_required_environment("LLM4AD_BUILDER_BASE_URL"),
                provider_type="anthropic",
                task_provider_type="anthropic",
                max_repair_attempts=3,
            )
        ).resolve()
        package_path.relative_to(build_root.resolve())
        metadata_path = package_path / "blueprint_meta.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("validation_status") != "passed" or metadata.get(
            "validation_errors"
        ):
            raise RuntimeError("The generated task package did not pass validation")
        manifest = _package_manifest(package_path)
        required_files = {"config.yaml", "debug_run.py", "test_evaluator.py", "blueprint_meta.json"}
        evaluator_file = metadata.get("evaluator_file_name")
        algorithm_dir = metadata.get("algorithm_dir_name")
        algorithm_file = metadata.get("algorithm_file_name")
        if not all(isinstance(value, str) and value for value in (evaluator_file, algorithm_dir, algorithm_file)):
            raise RuntimeError("Validated task package metadata is incomplete")
        required_files.update({evaluator_file, f"{algorithm_dir}/{algorithm_file}"})
        missing = sorted(required_files.difference(manifest))
        if missing:
            raise RuntimeError(f"Validated task package is incomplete: {', '.join(missing)}")
        return {
            "task_package_path": str(package_path),
            "project_name": metadata.get("project_name") or package_path.name,
            "manifest": manifest,
            "validation_report": {
                "status": "passed",
                "validator": "llm4ad.builder.TaskValidator",
                "repair_attempts": int(metadata.get("repair_attempts") or 0),
                "function_to_evolve": metadata.get("function_to_evolve"),
                "evaluator_file": metadata.get("evaluator_file_name"),
                "algorithm_file": "/".join(
                    part
                    for part in (
                        metadata.get("algorithm_dir_name"),
                        metadata.get("algorithm_file_name"),
                    )
                    if part
                ),
                "metrics": metadata.get("metrics") or [],
            },
        }
    except Exception:
        shutil.rmtree(build_root, ignore_errors=True)
        raise


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
