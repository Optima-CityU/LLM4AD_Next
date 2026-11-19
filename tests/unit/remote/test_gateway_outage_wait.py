"""Verify that a gateway outage does not turn queue waiting into execution failure."""

import asyncio
import json
import uuid

import pytest
from aiohttp import web

from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
from llm4ad.evaluator.models import EvaluationResult, Metric, MetricType
from llm4ad.gateway.sdk import client
from llm4ad.gateway.sdk.contracts import EvaluationContract

pytestmark = pytest.mark.unit


async def test_gateway_offline_queue_wait_exceeds_execution_timeout_without_resubmission(tmp_path, monkeypatch):
    """Keep one submitted job alive beyond its execution budget until the gateway returns."""
    task_id, gateway_id, attempt_id = (str(uuid.uuid4()) for _ in range(3))
    contract = EvaluationContract(
        evaluator_id="hgamey-genshin-sr-game", version="1", data_version="synthetic-outage-fixture",
        metrics=[Metric(name="game_score", type=MetricType.MAXIMIZE)],
    )
    config = RemoteEvaluatorConfig(
        gateway_ids=[gateway_id], contract=contract.model_dump(mode="json"),
        candidate_paths=["shader.comp"], parameters={}, timeout=0.05,
    )
    context = EvalContext(
        project_root=str(tmp_path), timeout=config.timeout, evaluation_profile="standard", behavior_storage="none",
        algorithm_id="synthetic-candidate", algorithm_name="Outage fixture", source_fingerprint="a" * 64,
        execution_id=str(uuid.uuid4()), instance_id="whole-candidate",
    )
    (tmp_path / "shader.comp").write_text("#version 450\nvoid main() {}\n")
    expected = EvaluationResult(score=1.25, metrics={"game_score": 1.25}, success=True)
    gateway_online, queued_polled = asyncio.Event(), asyncio.Event()
    requests, submissions, poll_ids = [], [], []

    @web.middleware
    async def record_requests(request, handler):
        """Record every request so unexpected cancellation or resubmission is visible."""
        requests.append((request.method, request.path))
        return await handler(request)

    async def submit(request):
        """Accept one platform job while its selected gateway is offline."""
        submissions.append(await request.json())
        return web.json_response({"id": submissions[-1]["id"], "attempt_id": attempt_id, "state": "queued"})

    async def status(request):
        """Leave the job queued until the fake gateway supplies its completed result."""
        poll_ids.append(request.match_info["job_id"])
        payload = {"id": poll_ids[-1], "attempt_id": attempt_id, "state": "queued"}
        if gateway_online.is_set():
            payload.update(state="completed", report=expected.model_dump(mode="json"))
        else:
            queued_polled.set()
        return web.json_response(payload)

    app = web.Application(middlewares=[record_requests])
    submission_path = f"/tasks/{task_id}/jobs"
    app.router.add_post(submission_path, submit)
    app.router.add_get(submission_path + "/{job_id}", status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("LLM4AD_REMOTE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", "synthetic-task-token")
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", task_id)
    real_sleep = asyncio.sleep

    async def short_poll(delay, result=None):
        """Shorten polling intervals while retaining real elapsed time and execution budgets."""
        return await real_sleep(min(delay, 0.005), result)

    monkeypatch.setattr(client.asyncio, "sleep", short_poll)
    started = asyncio.get_running_loop().time()
    evaluation = asyncio.create_task(client.evaluate_remote(config, context))
    try:
        await asyncio.wait_for(queued_polled.wait(), timeout=2)
        await real_sleep(context.timeout * 3)
        assert asyncio.get_running_loop().time() - started > context.timeout
        assert not evaluation.done(), "Gateway queue waiting must not consume the execution timeout"
        assert not evaluation.cancelled()
        assert len(submissions) == 1
        request_id = submissions[0]["id"]
        uuid.UUID(request_id)
        assert len(poll_ids) >= 2
        assert set(poll_ids) == {request_id}
        receipts = list((tmp_path / ".remote-evaluation").glob("*.json"))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text()) == {"id": request_id}

        gateway_online.set()
        result = await asyncio.wait_for(evaluation, timeout=2)
        assert result.success is True and result.error_message is None
        assert result.score == expected.score and result.metrics == expected.metrics
        assert result.metadata["remote_job_id"] == request_id
        assert result.metadata["remote_attempt_id"] == attempt_id
        assert len(submissions) == 1 and set(poll_ids) == {request_id}
        assert requests == [("POST", submission_path)] + [("GET", f"{submission_path}/{request_id}")] * len(poll_ids)
        assert submissions[0]["timeout"] == context.timeout
        assert submissions[0]["gateway_id"] == config.gateway_id
        assert submissions[0]["contract"] == config.contract
        assert submissions[0]["parameters"] == config.parameters
        for field in ("evaluation_profile", "behavior_storage", "algorithm_id", "algorithm_name",
                      "source_fingerprint", "execution_id", "instance_id"):
            assert submissions[0][field] == getattr(context, field)
        assert not list((tmp_path / ".remote-evaluation").iterdir())
    finally:
        if not evaluation.done():
            evaluation.cancel()
        await asyncio.gather(evaluation, return_exceptions=True)
        await runner.cleanup()
