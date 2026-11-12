"""Genshin gateway protocol tests; fixture timings are not device measurements."""

import asyncio
import copy
import importlib
import json
import os
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from llm4ad.coder.custom_naive_coder import CustomNaiveCoder
from llm4ad.config.app import AppConfig
from llm4ad.config.coder import CustomCoderConfig
from llm4ad.gateway.sdk.errors import EvaluationError
from llm4ad.gateway.sdk.lifecycle import run_operation
from llm4ad.infra.provider.base import GenerationResult
from llm4ad.remote.contracts import RemoteJob, load_evaluator
from llm4ad.remote.gateway import Gateway, stop_process

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "src/llm4ad/gateway/examples/hgamey"
TEMPLATE = ROOT / "examples/applications/hgamey_shader"


async def operate(adapter, job, reporter, operation="evaluate"):
    """Exercise preparation, measurement and finalization through the SDK boundary."""
    return await run_operation(adapter, operation, job, reporter, lambda: getattr(adapter, operation)(job, reporter))


class Reporter:
    """Retain submission intent and observed progress for restart tests."""

    def __init__(self):
        """Create an empty in-memory recovery journal."""
        self.saved_state = {}
        self.messages = []

    def save_state(self, value):
        """Keep a durable-intent-shaped copy for the next adapter instance."""
        self.saved_state = copy.deepcopy(value)

    def progress(self, message, **kwargs):
        """Record observed fixture progress without inventing percentages."""
        self.messages.append(message)


@pytest.fixture
def local(tmp_path, monkeypatch):
    """Build a private benchmark without phone access or compiler dependencies."""
    monkeypatch.syspath_prepend(str(EXAMPLE))
    benchmark_module = importlib.import_module("llm4ad.gateway.examples.hgamey.benchmark")
    real_version, real_compile = benchmark_module.Benchmark.tool_version, benchmark_module.Benchmark.compile
    monkeypatch.setattr(benchmark_module.Benchmark, "tool_version", lambda self, executable: "fixture-tool-version")

    def compile_shader(self, source, output):
        if "invalid_shader" in source.read_text():
            raise ValueError("fixture GLSL syntax error")
        output.write_bytes(b"\x03\x02\x23\x07" + source.read_bytes())

    monkeypatch.setattr(benchmark_module.Benchmark, "compile", compile_shader)
    settings = json.loads((EXAMPLE / "config/settings.json").read_text())
    # Exercise legacy flat settings independently of the new fidelity defaults.
    settings.pop("evaluation_profiles", None)
    settings.pop("score_mode", None)
    settings.pop("min_operator_improvement_pct", None)
    settings.update(
        device_id="fixture-phone", expected_serial="fixture-serial", device_profile="fixture-gpu-v1", poll_interval=0.001
    )
    references = tmp_path / "inputs/genshin"
    references.mkdir(parents=True)
    (references / "frame.png").write_bytes(b"fixture-reference-not-a-real-device-frame")
    filename = tmp_path / "settings.json"
    filename.write_text(json.dumps(settings))
    monkeypatch.setenv("HGAMEY_GATEWAY_CONFIG", str(filename))
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    shutil.copyfile(TEMPLATE / "algorithm/shader.comp", candidate / "shader.comp")

    def adapter():
        return load_evaluator(f"{EXAMPLE / 'evaluator.py'}:HGameyShaderEvaluator")

    def job():
        return RemoteJob(
            id=str(uuid.uuid4()),
            attempt_id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            project_root=str(candidate),
            timeout=120,
        )

    return SimpleNamespace(
        adapter=adapter,
        job=job,
        settings=settings,
        filename=filename,
        candidate=candidate,
        references=references,
        benchmark_module=benchmark_module,
        real_version=real_version,
        real_compile=real_compile,
    )


@pytest.fixture
async def executor(local):
    """Run a loopback-only HTTP fixture with explicit synthetic measurements."""
    state = SimpleNamespace(jobs={}, posts=0, lost_response=False, phases=["completed"], status="PASS", cancel_finishes=False)

    async def health(request):
        return web.json_response({"features": ["idempotent_submit", "job_lookup", "cancel"]})

    async def devices(request):
        return web.json_response({"devices": [{"id": "fixture-phone", "enabled": True, "expected_serial": "fixture-serial"}]})

    async def submit(request):
        body = await request.json()
        state.posts += 1
        key = body["idempotency_key"]
        if key not in state.jobs:
            phase = state.phases.pop(0) if len(state.phases) > 1 else state.phases[0]
            state.jobs[key] = {
                "id": uuid.uuid4().hex,
                "state": phase,
                "cleanup_status": "completed" if phase == "completed" else None,
            }
        if state.lost_response:
            state.lost_response = False
            request.transport.close()
        return web.json_response(state.jobs[key], status=202)

    async def lookup(request):
        item = state.jobs.get(request.match_info["key"])
        return web.json_response(item) if item else web.json_response({}, status=404)

    async def status(request):
        item = next(item for item in state.jobs.values() if item["id"] == request.match_info["id"])
        phase = state.phases.pop(0) if len(state.phases) > 1 else state.phases[0]
        item.update(state=phase, cleanup_status="completed" if phase == "completed" else None)
        return web.json_response(item)

    async def result(request):
        key = next(key for key, item in state.jobs.items() if item["id"] == request.match_info["id"])
        return web.json_response(
            {
                "version": 2,
                "kind": "shader",
                "candidate_id": key,
                "source": "phonefarm",
                "status": state.status,
                "metrics": {"operator_latency_ms": 2.5, "psnr_db": 35, "power_watt": None, "fps_p95_ms": None},
                "verdict": {"is_pareto_improvement": False, "p_value": 0.5},
                "conditions_observed": {"restored": True},
                "qualification": {"can_claim_improvement": False},
                "fixture_only": True,
            }
        )

    async def cancel(request):
        item = next(item for item in state.jobs.values() if item["id"] == request.match_info["id"])
        if item["state"] in {"completed", "failed", "cancelled"}:
            return web.json_response(item)
        if item["state"] == "queued" or state.cancel_finishes:
            item.update(state="cancelled", cleanup_status="completed")
        else:
            item.update(state="cancel_requested", cleanup_status=None)
        return web.json_response(item)

    app = web.Application()
    app.add_routes(
        [
            web.get("/v1/health", health),
            web.get("/v1/devices", devices),
            web.post("/v1/jobs", submit),
            web.get("/v1/jobs/by-key/{key}", lookup),
            web.get("/v1/jobs/{id}", status),
            web.get("/v1/jobs/{id}/result", result),
            web.post("/v1/jobs/{id}/cancel", cancel),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    local.settings["executor_url"] = f"http://127.0.0.1:{port}"
    local.filename.write_text(json.dumps(local.settings))
    try:
        yield state
    finally:
        await runner.cleanup()


async def test_sdk_evaluation_uses_fixed_inputs_and_keeps_measurement_provenance(local, executor):
    """One whole-candidate job follows compilation, submit and measured result."""
    adapter, reporter = local.adapter(), Reporter()
    result = await operate(adapter, local.job(), reporter)
    assert result.success and result.score == -2.5
    assert "power_watt" not in result.metrics and "fps_p95_ms" not in result.metrics
    assert result.metadata["can_claim_game_improvement"] is False
    assert result.metadata["report"]["verdict"]["is_pareto_improvement"] is False
    assert result.metadata["report"]["fixture_only"] is True
    body = reporter.saved_state["request"]
    assert body["request"]["payload"]["quality_reference"].startswith("/inputs/llm4ad-hgamey/")
    assert (Path(reporter.saved_state["evidence_directory"]) / "baseline.spv").is_file()
    assert executor.posts == 1


async def test_lost_submit_response_and_restart_reuse_the_existing_job(local, executor):
    """Response loss does not create a second phone experiment on recovery."""
    executor.lost_response = True
    adapter, reporter = local.adapter(), Reporter()
    job = local.job()
    first = await operate(adapter, job, reporter)
    assert first.success and len(executor.jobs) == 1
    posts = executor.posts
    reporter.saved_state.pop("external_job_id")
    result = await operate(local.adapter(), job, reporter, "recover")
    assert result.success and executor.posts == posts and len(executor.jobs) == 1


async def test_compile_failure_never_submits_a_phone_job(local, executor):
    """Invalid candidate syntax is actionable code feedback, not transport work."""
    (local.candidate / "shader.comp").write_text("invalid_shader")
    reporter = Reporter()
    with pytest.raises(EvaluationError, match="syntax error") as failure:
        await operate(local.adapter(), local.job(), reporter)
    assert failure.value.metadata["candidate_failure"]
    assert failure.value.evolution_feedback["stage"] == "compile"
    assert executor.posts == 0 and not reporter.saved_state


async def test_aborted_measurement_remains_unscored(local, executor):
    """An aborted experiment is not a negative algorithm training sample."""
    executor.status = "ABORT"
    reporter = Reporter()
    assert await operate(local.adapter(), local.job(), reporter) is None
    assert reporter.saved_state["external_job_id"]


@pytest.mark.parametrize("status", ["SLOW", "POOR_QUALITY", "CRASH"])
async def test_real_candidate_rejection_retains_feedback(local, executor, status):
    """Valid negative outcomes preserve the backend's rejection reasons."""
    executor.status = status
    result = await operate(local.adapter(), local.job(), Reporter())
    assert not result.success and result.evolution_feedback["status"] == status


async def test_cancellation_waits_for_external_cleanup(local, executor):
    """Stopping an HTTP client is never used as a phone stop receipt."""
    adapter, reporter = local.adapter(), Reporter()
    job = local.job()
    executor.phases = ["running"]
    work = asyncio.create_task(operate(adapter, job, reporter))
    for _ in range(1000):
        if reporter.saved_state.get("external_job_id"):
            break
        await asyncio.sleep(0.001)
    assert reporter.saved_state.get("external_job_id")
    assert await adapter.cancel(job, reporter.saved_state) is False
    executor.cancel_finishes = True
    assert await adapter.cancel(job, reporter.saved_state) is True
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work


async def test_late_cancellation_accepts_already_completed_cleanup(local, executor):
    """A stop racing with completion does not leave a clean finished job stuck."""
    adapter, reporter, job = local.adapter(), Reporter(), local.job()
    assert (await operate(adapter, job, reporter)).success
    assert await adapter.cancel(job, reporter.saved_state) is True


async def test_missing_job_does_not_confirm_cancellation_after_a_lost_submission(local, executor):
    """An in-flight POST can still create work after an absent-job lookup."""
    adapter, job = local.adapter(), local.job()
    state = adapter.prepare(job)
    assert await adapter.cancel(job, state) is False


def test_frame_changes_cannot_silently_change_a_registered_contract(local):
    """Children and restarted workers honor the parent's pinned benchmark."""
    initial = local.adapter().contract.data_version
    (local.references / "frame.png").write_bytes(b"changed-reference")
    with pytest.raises(ValueError, match="Benchmark changed"):
        local.adapter()
    new_settings = local.filename.with_name("new-profile.json")
    new_settings.write_text(json.dumps(local.settings))
    os.environ["HGAMEY_GATEWAY_CONFIG"] = str(new_settings)
    assert local.adapter().contract.data_version != initial


def test_reference_sampling_matches_upstream_and_covers_the_full_sequence(local):
    """More than 32 frames use upstream rounded, evenly-spaced indices."""
    (local.references / "frame.png").unlink()
    for index in range(65):
        (local.references / f"frame-{index:03d}.png").write_bytes(f"fixture-{index}".encode())
    frames = local.adapter().benchmark.manifest["frames"]
    assert len(frames) == 32
    assert frames[0]["name"] == "frame-000.png" and frames[-1]["name"] == "frame-064.png"
    assert [item["name"] for item in frames] == [f"frame-{(index * 64 + 15) // 31:03d}.png" for index in range(32)]


def test_tampered_staging_cannot_be_recovered_as_the_original_job(local):
    """Idempotent recovery checks the exact sources and binaries originally sent."""
    adapter = local.adapter()
    job = local.job()
    state = adapter.prepare(job)
    (Path(state["evidence_directory"]) / "candidate.spv").write_bytes(b"tampered")
    reporter = Reporter()
    reporter.save_state(state)
    with pytest.raises(RuntimeError, match="Staged candidate/baseline changed"):
        asyncio.run(operate(adapter, job, reporter, "recover"))


def test_example_is_a_draft_until_an_authorized_gateway_is_selected(local):
    """The shipped template contains no invented gateway or account identity."""
    import yaml
    from pydantic import ValidationError

    data = yaml.safe_load((TEMPLATE / "config.yaml").read_text())
    assert data["evaluator"]["gateway_ids"] == []
    assert (TEMPLATE / data["version_control"]["local_path"] / "shader.comp").is_file()
    with pytest.raises(ValidationError) as error:
        AppConfig.model_validate(data)
    assert [item["loc"] for item in error.value.errors()] == [("evaluator", "remote", "gateway_ids")]
    data["evaluator"].update(gateway_ids=[str(uuid.uuid4())], contract=local.adapter().contract.model_dump(mode="json"))
    config = AppConfig.model_validate(data)
    assert config.evaluator.type == "remote" and config.coder.context_max_tokens >= 24000


def test_real_glsl_compilation_and_spirv_validation(tmp_path, monkeypatch):
    """Run real local tools when available; this is not a GPU performance test."""
    tools = os.environ.get("HGAMEY_TEST_TOOL_DIR")
    compiler = str(Path(tools) / "glslangValidator") if tools else shutil.which("glslangValidator")
    validator = str(Path(tools) / "spirv-val") if tools else shutil.which("spirv-val")
    if not compiler or not validator:
        pytest.skip("Install glslang-tools and spirv-tools for real Shader compiler checks")
    monkeypatch.syspath_prepend(str(EXAMPLE))
    benchmark_module = importlib.import_module("llm4ad.gateway.examples.hgamey.benchmark")
    settings = json.loads((EXAMPLE / "config/settings.json").read_text())
    settings.update(compiler=compiler, spirv_validator=validator)
    (tmp_path / "inputs/genshin").mkdir(parents=True)
    (tmp_path / "inputs/genshin/frame.png").write_bytes(b"offline-compiler-fixture")
    filename = tmp_path / "settings.json"
    filename.write_text(json.dumps(settings))
    benchmark = benchmark_module.Benchmark(filename)
    for name, source in (("baseline", benchmark.baseline), ("seed", TEMPLATE / "algorithm/shader.comp")):
        output = tmp_path / f"{name}.spv"
        benchmark.compile(source, output)
        assert output.read_bytes().startswith(b"\x03\x02\x23\x07")
    invalid_interface = tmp_path / "wrong-binding.comp"
    invalid_interface.write_text((TEMPLATE / "algorithm/shader.comp").read_text().replace("binding = 1", "binding = 2"))
    with pytest.raises(ValueError, match="bindings 0 and 1"):
        benchmark.compile(invalid_interface, tmp_path / "wrong-binding.spv")


async def test_coder_and_real_sdk_worker_roundtrip(local, executor, monkeypatch, tmp_path):
    """Run two real SDK workers and a coder mutation against fixture-only timing."""
    tools = os.environ.get("HGAMEY_TEST_TOOL_DIR")
    compiler = str(Path(tools) / "glslangValidator") if tools else shutil.which("glslangValidator")
    validator = str(Path(tools) / "spirv-val") if tools else shutil.which("spirv-val")
    if not compiler or not validator:
        pytest.skip("Real SDK Shader workers need glslang-tools and spirv-tools")
    monkeypatch.setattr(local.benchmark_module.Benchmark, "tool_version", local.real_version)
    monkeypatch.setattr(local.benchmark_module.Benchmark, "compile", local.real_compile)
    local.settings.update(compiler=compiler, spirv_validator=validator)
    local.filename.write_text(json.dumps(local.settings))
    gateway = Gateway(
        "http://unused-platform.invalid",
        "offline-fixture",
        [str(uuid.uuid4())],
        f"{EXAMPLE / 'evaluator.py'}:HGameyShaderEvaluator",
        tmp_path / "sdk-state",
        1,
    )
    provider = MagicMock(model="fixture-no-model-network")
    original = (local.candidate / "shader.comp").read_text()
    body = original.split("// EVOLVE_START", 1)[1].split("// EVOLVE_END", 1)[0]
    replacement = body.replace("mix(top_left, top_right, fraction.x)", "(top_left * (1.0 - fraction.x) + top_right * fraction.x)")
    assert replacement != body
    provider.generate = AsyncMock(return_value=GenerationResult(text=f"```glsl:shader.comp\n{replacement}\n```"))
    coder = CustomNaiveCoder(CustomCoderConfig(default_extension="comp"), provider)
    for generation in range(2):
        job = local.job().model_dump(mode="json")
        gateway.journal.accept(job)
        gateway.start_process(job)
        process = gateway.processes[job["id"]]
        try:
            for _ in range(3000):
                if not process.is_alive():
                    break
                await asyncio.sleep(0.01)
            assert not process.is_alive(), "SDK fixture worker did not finish"
            process.join(timeout=1)
            row = gateway.journal.get(job["id"])
            assert row["state"] == "completed", row["report"]
            result = json.loads(row["report"])
            assert result["success"] and result["metadata"]["report"]["fixture_only"]
            assert result["metadata"]["can_claim_game_improvement"] is False
        finally:
            stop_process(process)
            gateway.processes.pop(job["id"], None)
        if generation == 0:
            generated = await coder.generate("Reuse the bilinear interpolation calculation", {}, str(local.candidate))
            assert generated.is_success, generated.error_message
            assert (local.candidate / "shader.comp").read_text() != original
    assert len(executor.jobs) == 2
    provider.generate.assert_awaited_once()
