"""Independent evaluator dispatch with synthetic native files and no device access."""

import json
import uuid
from pathlib import Path

import pytest
from test_hgamey_game_adapter import Reporter, connect
from test_hgamey_game_adapter import local as hgamey_fixture

from llm4ad.gateway.examples.hgamey.evaluators import HGameyGameEvaluator, HGameyStaticEvaluator
from llm4ad.gateway.examples.hgamey.runtime import HGameyRuntime
from llm4ad.gateway.sdk.lifecycle import run_operation

pytestmark = pytest.mark.unit

local = hgamey_fixture


async def run(adapter, job, reporter, operation="evaluate"):
    """Exercise the shared SDK hooks and the selected evaluator's actual control flow."""
    return await run_operation(adapter, operation, job, reporter, lambda: getattr(adapter, operation)(job, reporter))


async def test_static_never_compiles_or_dispatches_game(local, monkeypatch):
    """A configured game capability cannot turn static evaluation into game validation."""
    local.adapter()
    adapter, reporter = HGameyStaticEvaluator(local.filename), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    result = await run(adapter, local.job, reporter)
    assert result.success and result.score == -2.5
    assert result.metadata["evaluation_mode"] == "static"
    assert adapter.contract.evaluator_id == "hgamey-genshin-sr-static"
    assert len(executor.posts) == 1 and executor.posts[0]["operation"] == "eval"
    assert adapter.benchmark.game_check is None and adapter.benchmark.evaluation_profiles is None
    assert not list(Path(reporter.saved_state["evidence_directory"]).glob("*-layer"))


async def test_game_dispatches_only_game_and_scores_game_observations(local, monkeypatch):
    """The game evaluator's fitness cannot accidentally come from headless latency."""
    local.adapter()
    local.settings["game_scoring"] = {"weights": {"fps": 1, "frame_p95": 0, "gpu_per_frame": 0, "quality": 0}}
    local.settings["reference_path"] = str(local.filename.parent / "not-required-game-references")
    local.filename.write_text(json.dumps(local.settings))
    adapter, reporter = HGameyGameEvaluator(local.filename), Reporter()
    posts = []

    async def rpc(session, method, path, body=None, missing=False):
        if path == "/v1/health":
            return {"features": ["idempotent_submit", "job_lookup", "cancel", "game_ab"]}
        if path == "/v1/devices":
            return {"devices": [{"id": "synthetic-phone", "enabled": True, "expected_serial": "synthetic-serial"}]}
        if path.startswith("/v1/jobs/by-key/"):
            return None
        if method == "POST":
            posts.append(body)
            return {"id": "a" * 32, "state": "completed", "cleanup_status": "completed"}
        if path.endswith("/result"):
            state = reporter.saved_state
            return {
                "schema": "llm4ad/hgamey-game-ab@1", "status": "PASS", "errors": [],
                "candidate_applied": True, "conditions_observed": {"restored": True},
                "candidate_id": state["idempotency_key"],
                "candidate_layer_sha256": state["files"]["candidate-layer/libVkLayer_refknobs.so"],
                "baseline_layer_sha256": state["files"]["baseline-layer/libVkLayer_refknobs.so"],
                "metrics": {"baseline_display_fps": 30, "comparison_display_fps": 33},
            }
        raise AssertionError((method, path))

    monkeypatch.setattr(adapter, "rpc", rpc)
    result = await run(adapter, local.job, reporter)
    assert result.success and result.score == pytest.approx(1)
    assert adapter.contract.evaluator_id == "hgamey-genshin-sr-game"
    assert len(posts) == 1 and posts[0]["operation"] == "game-ab"
    assert posts[0]["idempotency_key"] == reporter.saved_state["idempotency_key"]
    assert "game" not in reporter.saved_state and "operator_latency_ms" not in result.metrics
    assert result.metadata["measurement_scope"] == "game_candidate_evaluation"


def test_capabilities_have_separate_state_and_contracts(local, tmp_path):
    """One physical device advertises two independently selectable evaluator bindings."""
    local.adapter()
    game_config = tmp_path / "game.json"
    game_config.write_text(json.dumps(local.settings["game_check"]))
    environment = {
        "LLM4AD_SERVER_URL": "http://fixture-platform.invalid", "LLM4AD_GATEWAY_NAME": "fixture",
        "LLM4AD_ALLOWED_USER_IDS": str(uuid.uuid4()), "HGAMEY_RUNTIME_DIR": str(tmp_path / "runtime"),
        "HGAMEY_DEVICE_HOST": "192.0.2.9", "HGAMEY_DEVICE_SERIAL": "fixture-serial",
        "HGAMEY_DEVICE_PROFILE": "fixture-driver", "HGAMEY_REFERENCE_PATH": str(local.filename.parent / "inputs/genshin"),
        "HGAMEY_GAME_CHECK_CONFIG": str(game_config), "HGAMEY_EVALUATOR_MODES": "static,game",
    }
    runtime = HGameyRuntime(environment, "llm4ad.gateway.examples.hgamey.evaluator:HGameyShaderEvaluator")
    bindings = runtime.bindings()
    assert len(bindings) == 2
    assert {binding.metadata["evaluation_mode"] for binding in bindings} == {"static", "game"}
    assert len({binding.directory for binding in bindings}) == 2
    assert len({binding.metadata["device_id"] for binding in bindings}) == 1
    for profile in runtime.options["profiles"]:
        assert "evaluation_profiles" not in profile["settings"]


def test_comparison_names_match_uploaded_namespace(local):
    """Reference links resolve to the SDK artifact names shown in node information."""
    local.adapter()
    adapter = HGameyStaticEvaluator(local.filename)
    report = {"evidence_comparisons": [{"id": "frame-00", "kind": "static", "images": [
        {"role": "baseline", "artifact": "images/frame-00/baseline.png"},
        {"role": "candidate", "artifact": "images/frame-00/candidate.png"},
    ]}]}
    images = adapter.comparisons(report)[0]["images"]
    assert [image["artifact"] for image in images] == [
        "static/images/frame-00/baseline.png", "static/images/frame-00/candidate.png",
    ]
    assert report["evidence_comparisons"][0]["images"][0]["artifact"].startswith("images/")


def game_report(state, fps=30):
    """Build a synthetic completed game report bound to compiled input identities."""
    return {
        "schema": "llm4ad/hgamey-game-ab@1", "status": "PASS", "errors": [],
        "candidate_applied": True, "conditions_observed": {"restored": True},
        "candidate_id": state["idempotency_key"],
        "candidate_layer_sha256": state["files"]["candidate-layer/libVkLayer_refknobs.so"],
        "baseline_layer_sha256": state["files"]["baseline-layer/libVkLayer_refknobs.so"],
        "metrics": {"baseline_display_fps": 30, "comparison_display_fps": fps},
    }


@pytest.mark.parametrize("fps,expected", [(30, 0), (27, -1), (None, None)])
def test_completed_game_no_advantage_and_missing_observations(local, fps, expected):
    """Retain neutral/lower fitness, and finish missing measurements without recovery loops."""
    local.adapter()
    local.settings["game_scoring"] = {"weights": {"fps": 1, "frame_p95": 0, "gpu_per_frame": 0, "quality": 0}}
    local.filename.write_text(json.dumps(local.settings))
    adapter = HGameyGameEvaluator(local.filename)
    state = adapter.prepare(local.job)
    result = adapter.convert_report(game_report(state, fps), state)
    assert result is not None
    if expected is None:
        assert not result.success and result.metadata["failure_type"] == "GameMeasurementUnavailable"
        assert result.metadata["missing_scored_metrics"]
    else:
        assert result.success and result.score == pytest.approx(expected)
        assert result.metadata["evaluation_summary"]["comparison_outcome"] == "no_advantage"
    assert all(type(value) in (int, float) for value in result.metrics.values())
    assert all(type(value) in (int, float) for value in result.monitor_metrics.values())
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_game_scoring_change_creates_a_different_contract(local):
    """Selecting different metric weights cannot silently mix scores in one gateway pool."""
    local.adapter()
    first = HGameyGameEvaluator(local.filename).contract
    settings = {**local.settings, "game_scoring": {"weights": {"fps": 1}}}
    other = local.filename.with_name("different-policy.json")
    other.write_text(json.dumps(settings))
    second = HGameyGameEvaluator(other).contract
    assert first.evaluator_id == second.evaluator_id
    assert first.data_version != second.data_version


@pytest.mark.parametrize("kind", [HGameyStaticEvaluator, HGameyGameEvaluator])
async def test_missing_accepted_job_is_not_silently_replayed(local, monkeypatch, kind):
    """An executor losing a known job must not schedule another phone experiment."""
    local.adapter()
    adapter, reporter = kind(local.filename), Reporter()
    state = adapter.prepare(local.job)
    state.update(external_job_id="a" * 32, submission_started=True)
    reporter.save_state(state)
    calls = []

    async def rpc(session, method, path, body=None, missing=False):
        calls.append((method, path))
        assert method == "GET" and path.startswith("/v1/jobs/by-key/")
        return None

    monkeypatch.setattr(adapter, "rpc", rpc)
    assert await adapter.follow_job(None, reporter, state) is None
    assert len(calls) == 1


@pytest.mark.parametrize("kind", [HGameyStaticEvaluator, HGameyGameEvaluator])
async def test_cancel_before_submission_needs_no_extra_job(local, monkeypatch, kind):
    """Cancel a frozen unsubmitted independent job without inventing a second stage."""
    local.adapter()
    adapter = kind(local.filename)
    state = adapter.prepare(local.job)

    async def rpc(session, method, path, body=None, missing=False):
        assert method == "GET" and path.startswith("/v1/jobs/by-key/")
        return None

    monkeypatch.setattr(adapter, "rpc", rpc)
    assert await adapter.cancel(local.job, state)


@pytest.mark.parametrize("kind,scope", [(HGameyStaticEvaluator, "static"), (HGameyGameEvaluator, "game")])
async def test_root_operation_retains_images_in_matching_namespace(local, monkeypatch, kind, scope):
    """Independent root receipts deliver artifacts through the existing SDK reporter."""
    local.adapter()
    adapter, reporter = kind(local.filename), Reporter()
    state = adapter.prepare(local.job)
    state["external_job_id"] = "a" * 32
    reporter.save_state(state)
    folder = Path(state["evidence_directory"])
    (folder / "executor-job.json").write_text(json.dumps({
        "id": state["external_job_id"], "state": "completed", "cleanup_status": "completed",
    }))
    image = {"name": "images/candidate.png", "media_type": "image/png"}
    (folder / "report.json").write_text(json.dumps({"artifacts": [image]}))
    uploaded = []

    async def retain(session, owner, saved, item, *, optional):
        assert owner is reporter and saved["external_job_id"] == state["external_job_id"]
        assert optional is True
        uploaded.append(f"{saved['artifact_scope']}/{item['name']}")

    monkeypatch.setattr(adapter, "retain_game_artifact", retain)
    await adapter.after_task(local.job, reporter, "evaluate")
    assert uploaded == [f"{scope}/images/candidate.png"]


def serial_native_fixture(folder):
    """Run an importable synthetic backend inside the actual SDK loky slot."""
    import time

    from llm4ad.gateway.examples.hgamey.execution.storage import read_json, write_json

    folder = Path(folder)
    data = folder.parent.parent
    body = read_json(folder / "input.json")["submission"]
    with (data / "fixture-calls").open("a") as stream:
        stream.write(body["operation"] + "\n")
    if body["operation"] == "eval":
        (data / "fixture-started").touch()
        until = time.monotonic() + 15
        while not (data / "fixture-release").exists():
            if time.monotonic() >= until:
                raise RuntimeError("Synthetic experiment was not released")
            time.sleep(0.01)
    write_json(folder / "result.json", {"conditions_observed": {"restored": True}})
    write_json(folder / "foreground.json", {"restore_required": False, "headless": True, "paused": False})
    return 0


async def test_static_and_game_share_one_serial_device_queue(local, tmp_path):
    """Concurrent capability submissions use one existing executor without device overlap."""
    import asyncio
    import time

    from llm4ad.gateway.examples.hgamey.execution import Engine

    local.adapter()
    adapters = [HGameyStaticEvaluator(local.filename), HGameyGameEvaluator(local.filename)]
    # In-process executor inputs refer to the same local paths, as in the combined container.
    for adapter in adapters:
        adapter.benchmark.prefix = adapter.benchmark.inputs
    prepared = [adapter.prepare(local.job) for adapter in adapters]
    devices = tmp_path / "devices.json"
    devices.write_text(json.dumps({"version": 1, "devices": [{
        "id": "synthetic-phone", "label": "fixture", "host": "192.0.2.2", "port": 5555,
        "expected_serial": "synthetic-serial", "enabled": True,
    }]}))
    data = tmp_path / "executor"
    engine = Engine(data, devices, runner=serial_native_fixture)
    try:
        first = engine.submit(prepared[0]["request"])
        end = time.monotonic() + 10
        while not (data / "fixture-started").exists() and time.monotonic() < end:
            await asyncio.sleep(0.01)
        assert (data / "fixture-started").is_file()
        second = engine.submit(prepared[1]["request"])
        assert engine.get(first["id"])["state"] == "running"
        assert engine.get(second["id"])["state"] == "queued"
        assert (data / "fixture-calls").read_text().splitlines() == ["eval"]
        (data / "fixture-release").touch()
        end = time.monotonic() + 10
        while engine.get(second["id"])["state"] in {"queued", "running"} and time.monotonic() < end:
            await asyncio.sleep(0.01)
        assert (data / "fixture-calls").read_text().splitlines() == ["eval", "game-ab"]
        assert engine.get(second["id"])["state"] == "completed"
        assert engine.health()["blocked_jobs"] == []
    finally:
        (data / "fixture-release").touch()
        await asyncio.to_thread(engine.close)


def test_unverifiable_game_quality_is_a_terminal_low_score(local):
    """A completed experiment with missing IQ is automatically ranked low, never recovered."""
    local.adapter()
    adapter = HGameyGameEvaluator(local.filename)
    state = adapter.prepare(local.job)
    report = game_report(state)
    report['metrics'].update(
        baseline_display_frame_p95_upper_ms=34, comparison_display_frame_p95_upper_ms=34,
        baseline_gpu_uid_active_pct=60, comparison_gpu_uid_active_pct=60,
    )
    report['quality'] = {'status': 'not_measured', 'metrics': [], 'not_computed': ['Insufficient stable tiles']}
    result = adapter.convert_report(report, state)
    assert result is not None and result.success
    assert result.score == adapter.benchmark.game_scoring['quality_gap_score']
    assert result.metadata['quality_assessment']['penalized']
    assert result.metadata['evaluation_summary']['comparison_outcome'] == 'quality_penalty'
    assert 'infrastructure_failure' not in result.metadata
    assert result.evolution_feedback['quality_assessment']['status'] == 'unverified'
