from __future__ import annotations

from pathlib import Path

from llm4ad.integrations.cspaper.client import (
    CSPaperClient,
    CSPaperReviewJob,
    save_review_artifacts,
)


async def test_submit_and_poll_normalizes_cspaper_contract(tmp_path: Path) -> None:
    """The client accepts the official submit and result response envelopes."""
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 test")
    client = CSPaperClient("csp_live_test")
    responses = [
        {"data": {"job_id": "job-1", "status": "PENDING"}},
        {
            "data": {
                "id": "job-1",
                "status": "COMPLETED",
                "result": "# Review\n- Improve runtime.",
                "paper_meta": {"title": "Test Paper"},
            }
        },
    ]

    async def fake_request(method: str, path: str, **kwargs):
        assert kwargs
        if method == "POST":
            assert path == "/api/platform/review"
        else:
            assert path == "/api/platform/reviews/job-1"
        return responses.pop(0)

    client._request_json = fake_request  # type: ignore[method-assign]

    job = await client.submit_and_wait(
        paper,
        agent_id="ICML_main_2026_1",
        poll_interval=0.001,
        timeout=1,
    )

    assert job.completed
    assert job.job_id == "job-1"
    raw, review = save_review_artifacts(
        job,
        tmp_path / "output",
        paper_name=paper.name,
        agent_id="ICML_main_2026_1",
    )
    assert raw.is_file()
    assert "job_id: job-1" in review.read_text(encoding="utf-8")


async def test_cached_submission_reuses_completed_review(tmp_path: Path) -> None:
    """A matching PDF and agent reuse a completed result without submitting again."""
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 cached")
    cache = tmp_path / "submission.json"
    first = CSPaperClient("csp_live_test")
    calls = 0

    async def first_submit(*args, **kwargs):
        nonlocal calls
        calls += 1
        return CSPaperReviewJob(job_id="job-2", status="PENDING")

    async def first_wait(*args, **kwargs):
        return CSPaperReviewJob(
            job_id="job-2",
            status="COMPLETED",
            result="# Cached review",
            raw={"id": "job-2", "status": "COMPLETED", "result": "# Cached review"},
        )

    first.submit_review = first_submit  # type: ignore[method-assign]
    first.wait_for_review = first_wait  # type: ignore[method-assign]
    await first.submit_and_wait_cached(
        paper,
        agent_id="ICML_main_2026_1",
        cache_path=cache,
    )

    second = CSPaperClient("csp_live_test")

    async def forbidden_submit(*args, **kwargs):
        raise AssertionError("completed cached review should not be resubmitted")

    second.submit_review = forbidden_submit  # type: ignore[method-assign]
    reused = await second.submit_and_wait_cached(
        paper,
        agent_id="ICML_main_2026_1",
        cache_path=cache,
    )

    assert calls == 1
    assert reused.result == "# Cached review"


async def test_failed_cached_submission_is_retried_on_next_run(tmp_path: Path) -> None:
    """A terminal failed job is recorded and replaced by a new submission."""
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 retry")
    cache = tmp_path / "submission.json"
    first = CSPaperClient("csp_live_test")

    async def failed_submit(*args, **kwargs):
        return CSPaperReviewJob(job_id="failed-job", status="PENDING")

    async def failed_wait(*args, **kwargs):
        return CSPaperReviewJob(
            job_id="failed-job",
            status="FAILED",
            failed_reason="provider exhausted",
        )

    first.submit_review = failed_submit  # type: ignore[method-assign]
    first.wait_for_review = failed_wait  # type: ignore[method-assign]
    try:
        await first.submit_and_wait_cached(
            paper,
            agent_id="ICML_main_2026_1",
            cache_path=cache,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed CSPaper jobs must raise")

    second = CSPaperClient("csp_live_test")

    async def successful_submit(*args, **kwargs):
        return CSPaperReviewJob(job_id="retry-job", status="PENDING")

    async def successful_wait(*args, **kwargs):
        return CSPaperReviewJob(
            job_id="retry-job",
            status="COMPLETED",
            result="# Retried review",
        )

    second.submit_review = successful_submit  # type: ignore[method-assign]
    second.wait_for_review = successful_wait  # type: ignore[method-assign]
    completed = await second.submit_and_wait_cached(
        paper,
        agent_id="ICML_main_2026_1",
        cache_path=cache,
    )

    assert completed.job_id == "retry-job"
