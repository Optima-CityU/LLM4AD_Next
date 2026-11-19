"""Exercise gateway SSE framing and recovery over real HTTP connections."""

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from llm4ad.remote.gateway import Gateway

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/remote_evaluation/evaluator.py"


@asynccontextmanager
async def event_server(handler, routes=()):
    """Serve notifications on an ephemeral local HTTP port."""
    app = web.Application()
    app.router.add_get("/api/v1/llm4ad/evaluation-gateways/{gateway_id}/events", handler)
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


def gateway(tmp_path, url):
    """Build a local gateway without starting evaluation processes."""
    return Gateway(url, "sse-test", [str(uuid.uuid4())], f"{EXAMPLE}:Evaluator", tmp_path)


async def test_sse_frames_authentication_and_long_lived_timeout(tmp_path):
    """Only complete poll events wake dispatch, beyond the normal HTTP timeout."""
    ready, send_data, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = []

    async def events(request):
        requests.append((dict(request.headers), dict(request.query)))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b": keep-alive\r\n\r\nevent: poll\r\n")
        ready.set()
        await send_data.wait()
        await response.write(b'data: {"type":')
        await response.write(b' "poll"}\r\n\r\n')
        await finish.wait()
        return response

    async with event_server(events) as url:
        worker = gateway(tmp_path, url)
        async with aiohttp.ClientSession(
            headers={"Authorization": "Bearer " + worker.identity["secret"]},
            timeout=aiohttp.ClientTimeout(total=0.01),
        ) as session:
            task = asyncio.create_task(worker.notifications(session))
            try:
                await asyncio.wait_for(ready.wait(), 2)
                await asyncio.sleep(0.04)
                assert not worker.wakeup.is_set()
                send_data.set()
                await asyncio.wait_for(worker.wakeup.wait(), 2)
                assert len(requests) == 1
                headers, query = requests[0]
                assert headers["Authorization"] == "Bearer " + worker.identity["secret"]
                assert headers["Accept"] == "text/event-stream"
                assert "Upgrade" not in headers
                assert query == {}
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                send_data.set()
                finish.set()


async def test_http_heartbeats_continue_when_sse_is_unavailable(tmp_path):
    """An SSE outage cannot stop heartbeat reconciliation or durable result upload."""
    prefix = "/api/v1/llm4ad/evaluation-gateways"
    reconciled = asyncio.Event()
    heartbeats, notifications, reports = 0, 0, []

    async def events(request):
        nonlocal notifications
        notifications += 1
        return web.Response(status=503)

    async def register(request):
        return web.json_response({})

    async def heartbeat(request):
        nonlocal heartbeats
        heartbeats += 1
        if heartbeats >= 2:
            reconciled.set()
        return web.json_response({"jobs": []})

    async def report(request):
        payload = await request.json()
        reports.append(payload)
        return web.json_response({"ack": payload["sequence"], "state": payload["state"]})

    async def claim(request):
        return web.json_response(None)

    routes = [
        web.post(prefix + "/register", register),
        web.post(prefix + "/{gateway_id}/heartbeat", heartbeat),
        web.post(prefix + "/{gateway_id}/claim", claim),
        web.post(prefix + "/{gateway_id}/jobs/{job_id}/report", report),
    ]
    async with event_server(events, routes) as url:
        worker = gateway(tmp_path, url)
        job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())}
        worker.journal.accept(job)
        worker.journal.update(job["id"], "completed", {"score": 7})
        task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(reconciled.wait(), 12)
            assert notifications > 0
            assert len(reports) == 1
            assert reports[0]["report"] == {"score": 7}
            assert not worker.journal.rows(pending_only=True)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@pytest.mark.parametrize("interruption", ["eof", "http_error", "wrong_content_type", "stalled"])
async def test_sse_reconnects_without_losing_local_results(tmp_path, interruption, monkeypatch):
    """EOF, stalled streams and proxy errors reconnect without replaying jobs."""
    requests = 0
    finish = asyncio.Event()
    if interruption == "stalled":
        timeout = aiohttp.ClientTimeout

        def short_read_timeout(**kwargs):
            kwargs["sock_read"] = 0.05
            return timeout(**kwargs)

        monkeypatch.setattr("llm4ad.remote.gateway.aiohttp.ClientTimeout", short_read_timeout)

    async def events(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            if interruption == "http_error":
                return web.Response(status=503)
            if interruption == "wrong_content_type":
                return web.Response(text="maintenance", content_type="text/html")
            if interruption == "stalled":
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                await finish.wait()
                return response
            return web.Response(body=b"event: poll\ndata: {}\n", content_type="text/event-stream")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b"event: ignored\ndata: {}\n\n: keep-alive\n\ndata: {}\n\n")
        await finish.wait()
        return response

    async with event_server(events) as url:
        worker = gateway(tmp_path, url)
        job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())}
        worker.journal.accept(job)
        worker.journal.update(job["id"], "completed", {"score": 1})
        before = worker.journal.get(job["id"])
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(worker.notifications(session))
            try:
                await asyncio.wait_for(worker.wakeup.wait(), 4)
                assert requests == 2
                assert worker.journal.get(job["id"]) == before
                assert not worker.processes
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                finish.set()
