"""Async client for CSPaper's Agentic Review API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import BaseModel, Field


class CSPaperReviewJob(BaseModel):
    """Normalized Agentic Review job returned by CSPaper."""

    job_id: str
    status: str
    result: str = ""
    failed_reason: str = ""
    paper_meta: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def completed(self) -> bool:
        """Return whether a usable review is available."""
        return self.status in {"COMPLETED", "COMPLETE", "SUCCEEDED", "SUCCESS"}

    @property
    def terminal(self) -> bool:
        """Return whether polling should stop."""
        return self.completed or self.status in {
            "FAILED",
            "ERROR",
            "CANCELLED",
            "CANCELED",
            "REJECTED",
        }


class CSPaperClient:
    """Submit PDFs and poll Agentic Review jobs."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://cspaper-frontend-prod.azurewebsites.net",
        request_timeout: float = 60.0,
    ) -> None:
        """Initialize the client without performing a network request."""
        if not api_key.strip():
            raise ValueError("A CSPaper API key is required")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    async def submit_review(
        self,
        paper_path: str | Path,
        *,
        agent_id: str,
        desk_rejection_enabled: bool = False,
    ) -> CSPaperReviewJob:
        """Upload a PDF and return its asynchronous job id."""
        paper = Path(paper_path).expanduser().resolve()
        if paper.suffix.lower() != ".pdf" or not paper.is_file():
            raise ValueError(f"CSPaper requires an existing PDF file: {paper}")
        with paper.open("rb") as paper_file:
            form = aiohttp.FormData()
            form.add_field("agent_id", agent_id)
            form.add_field(
                "desk_rejection_enabled",
                str(desk_rejection_enabled).lower(),
            )
            form.add_field(
                "file",
                paper_file,
                filename=paper.name,
                content_type="application/pdf",
            )
            payload = await self._request_json(
                "POST",
                "/api/platform/review",
                data=form,
                timeout=self.request_timeout,
            )
        data = _unwrap_data(payload)
        return _normalize_job(data)

    async def get_review(self, job_id: str) -> CSPaperReviewJob:
        """Fetch the latest status and result for one job."""
        payload = await self._request_json(
            "GET",
            f"/api/platform/reviews/{job_id}",
            timeout=min(self.request_timeout, 30.0),
        )
        return _normalize_job(_unwrap_data(payload), fallback_job_id=job_id)

    async def wait_for_review(
        self,
        job_id: str,
        *,
        poll_interval: float = 30.0,
        timeout: float = 1800.0,
        raise_on_failure: bool = True,
    ) -> CSPaperReviewJob:
        """Poll until CSPaper returns a terminal job status."""
        started = time.monotonic()
        while True:
            job = await self.get_review(job_id)
            if job.terminal:
                if not job.completed and raise_on_failure:
                    raise RuntimeError(
                        f"CSPaper review {job.job_id} ended with {job.status}: "
                        f"{job.failed_reason or 'no reason supplied'}"
                    )
                return job
            if time.monotonic() - started >= timeout:
                raise TimeoutError(f"Timed out waiting for CSPaper review {job_id}")
            await asyncio.sleep(poll_interval)

    async def submit_and_wait(
        self,
        paper_path: str | Path,
        *,
        agent_id: str,
        desk_rejection_enabled: bool = False,
        poll_interval: float = 30.0,
        timeout: float = 1800.0,
    ) -> CSPaperReviewJob:
        """Submit a PDF and wait for its final Markdown review."""
        submitted = await self.submit_review(
            paper_path,
            agent_id=agent_id,
            desk_rejection_enabled=desk_rejection_enabled,
        )
        return await self.wait_for_review(
            submitted.job_id,
            poll_interval=poll_interval,
            timeout=timeout,
        )

    async def submit_and_wait_cached(
        self,
        paper_path: str | Path,
        *,
        agent_id: str,
        cache_path: str | Path,
        desk_rejection_enabled: bool = False,
        poll_interval: float = 30.0,
        timeout: float = 1800.0,
    ) -> CSPaperReviewJob:
        """Reuse a matching submission record to avoid duplicate review charges."""
        paper = Path(paper_path).expanduser().resolve()
        fingerprint = _submission_fingerprint(
            paper,
            agent_id=agent_id,
            base_url=self.base_url,
            desk_rejection_enabled=desk_rejection_enabled,
        )
        record_path = Path(cache_path).expanduser().resolve()
        cached = _read_submission_record(record_path, fingerprint)
        if cached is not None:
            if cached.completed and cached.result:
                return cached
            if not cached.terminal:
                completed = await self.wait_for_review(
                    cached.job_id,
                    poll_interval=poll_interval,
                    timeout=timeout,
                    raise_on_failure=False,
                )
                _write_submission_record(record_path, fingerprint, completed)
                if not completed.completed:
                    raise RuntimeError(
                        f"CSPaper review {completed.job_id} ended with "
                        f"{completed.status}: {completed.failed_reason or 'no reason supplied'}"
                    )
                return completed

        submitted = await self.submit_review(
            paper,
            agent_id=agent_id,
            desk_rejection_enabled=desk_rejection_enabled,
        )
        _write_submission_record(record_path, fingerprint, submitted)
        completed = await self.wait_for_review(
            submitted.job_id,
            poll_interval=poll_interval,
            timeout=timeout,
            raise_on_failure=False,
        )
        _write_submission_record(record_path, fingerprint, completed)
        if not completed.completed:
            raise RuntimeError(
                f"CSPaper review {completed.job_id} ended with {completed.status}: "
                f"{completed.failed_reason or 'no reason supplied'}"
            )
        return completed

    async def _request_json(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["X-API-Key"] = self.api_key
        client_timeout = aiohttp.ClientTimeout(total=float(kwargs.pop("timeout")))
        async with aiohttp.ClientSession(
            base_url=self.base_url,
            headers=headers,
            timeout=client_timeout,
        ) as session, session.request(method, path, **kwargs) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(
                    f"CSPaper API {method} {path} returned HTTP "
                    f"{response.status}: {body[:500]}"
                )
            try:
                value = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("CSPaper API returned invalid JSON") from exc
            if not isinstance(value, dict):
                raise RuntimeError("CSPaper API response must be a JSON object")
            return value


def save_review_artifacts(
    job: CSPaperReviewJob,
    output_dir: str | Path,
    *,
    paper_name: str = "paper",
    agent_id: str = "",
) -> tuple[Path, Path]:
    """Save raw response JSON and review Markdown for reproducibility."""
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "raw-response.json"
    raw_path.write_text(
        json.dumps(job.raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review_path = output / "review.md"
    title = str(job.paper_meta.get("title") or paper_name)
    frontmatter = [
        "---",
        f"job_id: {job.job_id}",
        f"agent_id: {agent_id}",
        f"status: {job.status}",
        f"paper: {json.dumps(title, ensure_ascii=False)}",
        "---",
        "",
    ]
    review_path.write_text("\n".join(frontmatter) + job.result, encoding="utf-8")
    return raw_path, review_path


def _unwrap_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise RuntimeError("CSPaper API response.data must be an object")
    return data


def _normalize_job(data: dict[str, Any], fallback_job_id: str = "") -> CSPaperReviewJob:
    job_id = str(data.get("job_id") or data.get("id") or fallback_job_id)
    if not job_id:
        raise RuntimeError("CSPaper API response did not include a job id")
    status = str(data.get("status") or "UNKNOWN").upper()
    return CSPaperReviewJob(
        job_id=job_id,
        status=status,
        result=str(data.get("result") or ""),
        failed_reason=str(data.get("failed_reason") or ""),
        paper_meta=data.get("paper_meta") or {},
        result_summary=data.get("result_summary") or {},
        raw=data,
    )


def _submission_fingerprint(
    paper_path: Path,
    *,
    agent_id: str,
    base_url: str,
    desk_rejection_enabled: bool,
) -> str:
    digest = hashlib.sha256(paper_path.read_bytes()).hexdigest()
    material = f"{digest}:{agent_id}:{base_url}:{desk_rejection_enabled}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _read_submission_record(
    path: Path,
    fingerprint: str,
) -> CSPaperReviewJob | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("fingerprint") != fingerprint:
        return None
    try:
        return CSPaperReviewJob.model_validate(value.get("job"))
    except Exception:
        return None


def _write_submission_record(
    path: Path,
    fingerprint: str,
    job: CSPaperReviewJob,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "fingerprint": fingerprint,
        "job": job.model_dump(mode="json"),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
