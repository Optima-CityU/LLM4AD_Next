"""Evaluation adapter used inside a platform task container."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

import aiohttp
from loguru import logger

from llm4ad.evaluator.base import EvaluationResult
from llm4ad.remote.bundles import pack_candidate


async def evaluate_remote(config, cfg) -> EvaluationResult:
    """Submit once, then wait without treating network loss as candidate failure."""
    url = os.environ.get("LLM4AD_REMOTE_URL", "")
    token = os.environ.get("LLM4AD_REMOTE_TOKEN", "")
    task_id = os.environ.get("LLM4AD_REMOTE_TASK_ID", "")
    if not url or not token or not task_id:
        raise RuntimeError("Remote evaluation requires a platform task runtime")
    bundle = pack_candidate(cfg.project_root, config.candidate_paths)
    key = hashlib.sha256(
        bundle
        + json.dumps(
            {"task_id": task_id, "config": config.model_dump(mode="json"), "context": cfg.model_dump(mode="json")}, sort_keys=True
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
        "gateway_ids": config.gateway_ids,
        "contract": config.contract,
        "timeout": cfg.timeout,
        "evaluation_profile": cfg.evaluation_profile,
        "behavior_storage": cfg.behavior_storage,
        "parameters": config.parameters,
        "bundle": base64.b64encode(bundle).decode(),
    }
    headers = {"Authorization": f"Bearer {token}"}
    last_state = None
    submitted = False
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
        while True:
            try:
                async with session.request(
                    "GET" if submitted else "POST",
                    f"{url}/tasks/{task_id}/jobs" + (f"/{request_id}" if submitted else ""),
                    **({} if submitted else {"json": body}),
                ) as response:
                    if response.status == 409:
                        raise asyncio.CancelledError("Platform task is no longer active")
                    if response.status >= 400 and response.status < 500:
                        raise RuntimeError(f"Remote evaluation configuration/channel error: HTTP {response.status}")
                    response.raise_for_status()
                    job = await response.json()
                submitted = True
                if job["state"] != last_state:
                    logger.info("Remote evaluation {}: {}", request_id, job["state"])
                    last_state = job["state"]
                if job["state"] == "completed":
                    result = EvaluationResult.model_validate(job["report"])
                    pending.unlink(missing_ok=True)
                    return result
                if job["state"] == "failed":
                    pending.unlink(missing_ok=True)
                    return EvaluationResult(
                        success=False,
                        metrics={},
                        error_message=job["report"].get("message", "Remote evaluation failed"),
                        metadata={"failure_type": job["report"].get("error_type", "RemoteExecutionError")},
                    )
                if job["state"] in {"cancelled", "cancel_requested"}:
                    raise asyncio.CancelledError("Remote evaluation cancelled")
            except (TimeoutError, aiohttp.ClientError):
                logger.debug("Remote evaluation transport unavailable; waiting for reconnection")
            await asyncio.sleep(2)
