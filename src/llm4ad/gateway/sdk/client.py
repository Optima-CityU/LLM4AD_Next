"""Evaluation adapter used inside a platform task container."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import TypedDict

import aiohttp
from loguru import logger

from llm4ad.evaluator.models import EvaluationResult
from llm4ad.gateway.examples.filament_fsr.source import (
    CONTRACT_VERSION,
    DATA_VERSION,
    EVALUATOR_ID,
    PERFORMANCE_EVALUATOR_ID,
    SOURCE_NAME,
    CandidateShaderError,
    reference_source,
    validate_candidate,
)
from llm4ad.gateway.sdk.bundles import pack_candidate


class _RequestBody(TypedDict, total=False):
    """Carry the optional JSON argument for submitting a remote job."""

    json: dict


async def configuration_error_detail(response: aiohttp.ClientResponse) -> str:
    """Read an actionable API error without echoing request inputs or archives.

    Args:
        response: Rejected platform HTTP response.

    Returns:
        A bounded reason containing only the server's diagnostic fields.
    """
    try:
        payload = await response.json(content_type=None)
    except (ValueError, aiohttp.ClientError):
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail[:1000]
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', []))}: {item.get('msg', '')}"
            for item in detail[:3] if isinstance(item, dict)
        )[:1000]
    return ""


async def evaluate_remote(config, cfg) -> EvaluationResult:
    """Submit once, then wait without treating network loss as candidate failure."""
    url = os.environ.get("LLM4AD_REMOTE_URL", "")
    token = os.environ.get("LLM4AD_REMOTE_TOKEN", "")
    task_id = os.environ.get("LLM4AD_REMOTE_TASK_ID", "")
    if not url or not token or not task_id:
        raise RuntimeError("Remote evaluation requires a platform task runtime")
    if not config.gateway_id:
        raise ValueError("Choose one gateway and evaluator before running this task")
    # The bundled benchmark uses the same source policy in the task and on the gateway.
    contract = config.contract
    if (
        contract.get("evaluator_id") in (EVALUATOR_ID, PERFORMANCE_EVALUATOR_ID)
        and contract.get("version") == CONTRACT_VERSION
        and contract.get("data_version") == DATA_VERSION
    ):
        try:
            validate_candidate(Path(cfg.project_root) / SOURCE_NAME, reference_source())
        except CandidateShaderError as error:
            return EvaluationResult(
                success=False,
                metrics={},
                error_message=str(error),
                metadata={**error.metadata, "failure_type": type(error).__name__},
            )
    bundle = pack_candidate(cfg.project_root, config.candidate_paths)
    key = hashlib.sha256(
        bundle
        + json.dumps(
            {
                "task_id": task_id,
                "config": config.model_dump(mode="json"),
                "context": cfg.model_dump(
                    mode="json",
                    include={
                        "project_root",
                        "data_path",
                        "timeout",
                        "behavior_storage",
                        "evaluation_profile",
                    },
                ),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    pending_dir = Path(cfg.project_root) / ".remote-evaluation"
    pending_dir.mkdir(exist_ok=True)
    pending = pending_dir / f"{key}.json"
    request_id = json.loads(pending.read_text())["id"] if pending.exists() else str(uuid.uuid4())
    with tempfile.NamedTemporaryFile(mode="w", dir=pending_dir, delete=False) as stream:
        json.dump({"id": request_id}, stream)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    temporary.replace(pending)
    body = {
        "id": request_id,
        "gateway_id": config.gateway_id,
        "dispatch_version": 2,
        "contract": config.contract,
        "timeout": cfg.timeout,
        "evaluation_profile": cfg.evaluation_profile,
        "behavior_storage": cfg.behavior_storage,
        "parameters": config.parameters,
        "bundle": base64.b64encode(bundle).decode(),
        **{
            name: getattr(cfg, name)
            for name in ("algorithm_id", "algorithm_name", "source_fingerprint", "execution_id", "instance_id")
        },
    }
    headers = {"Authorization": f"Bearer {token}"}
    last_state = None
    submitted = False
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
        while True:
            try:
                request_body: _RequestBody = {} if submitted else {"json": body}
                async with session.request(
                    "GET" if submitted else "POST",
                    f"{url}/tasks/{task_id}/jobs" + (f"/{request_id}" if submitted else ""),
                    **request_body,
                ) as response:
                    if response.status == 409:
                        raise asyncio.CancelledError("Platform task is no longer active")
                    if response.status >= 400 and response.status < 500:
                        detail = await configuration_error_detail(response)
                        raise RuntimeError(
                            f"Remote evaluation configuration/channel error: HTTP {response.status}"
                            + (f": {detail}" if detail else "")
                        )
                    response.raise_for_status()
                    job = await response.json()
                submitted = True
                if job["state"] != last_state:
                    logger.info("Remote evaluation {}: {}", request_id, job["state"])
                    last_state = job["state"]
                if job["state"] == "completed":
                    result = EvaluationResult.model_validate(job["report"])
                    result.metadata["remote_job_id"] = request_id
                    if job.get("attempt_id"):
                        result.metadata["remote_attempt_id"] = job["attempt_id"]
                    pending.unlink(missing_ok=True)
                    return result
                if job["state"] == "failed":
                    pending.unlink(missing_ok=True)
                    report = job["report"]
                    result = EvaluationResult.model_validate({
                        "metrics": {}, **report, "success": False,
                        "error_message": report.get("error_message") or report.get("message", "Remote evaluation failed"),
                    })
                    result.metadata.update(remote_job_id=request_id, remote_attempt_id=job.get("attempt_id", ""))
                    result.metadata.setdefault("failure_type", report.get("error_type", "RemoteExecutionError"))
                    category = result.metadata.get("failure_category")
                    if isinstance(category, str) and category in {"infrastructure", "evaluator", "configuration"}:
                        result.metrics = {}
                        result.score = 0.0
                    return result
                if job["state"] in {"cancelled", "cancel_requested"}:
                    raise asyncio.CancelledError("Remote evaluation cancelled")
            except (TimeoutError, aiohttp.ClientError):
                logger.debug("Remote evaluation transport unavailable; waiting for reconnection")
            await asyncio.sleep(2)
