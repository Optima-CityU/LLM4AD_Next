"""Synthetic composite-adapter tests; no timings, images or ELF bytes are device evidence."""

import asyncio
import copy
import importlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from llm4ad.gateway.sdk.lifecycle import run_operation
from llm4ad.remote.contracts import RemoteJob, load_evaluator

EXAMPLE = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey"
pytestmark = pytest.mark.unit


async def run_adapter(adapter, job, reporter, *, operation="evaluate"):
    """Exercise preparation and finalization through the SDK operation wrapper."""
    return await run_operation(adapter, operation, job, reporter, lambda: getattr(adapter, operation)(job, reporter))


class Reporter:
    """Keep isolated snapshots of the synthetic accepted attempt."""

    def __init__(self):
        """Start with no durable intent."""
        self.saved_state = {}
        self.messages = []

    def save_state(self, state):
        """Simulate the SDK's durable copy before a network operation."""
        self.saved_state = copy.deepcopy(state)

    def progress(self, message):
        """Record progress without supplying measured percentages."""
        self.messages.append(message)


@pytest.fixture
def local(tmp_path, monkeypatch):
    """Provide fake native inputs and a real subprocess running a synthetic builder."""
    monkeypatch.syspath_prepend(str(EXAMPLE))
    module = importlib.import_module("llm4ad.gateway.examples.hgamey.benchmark")
    monkeypatch.setattr(module.Benchmark, "tool_version", lambda *args: "synthetic-compiler-version")

    def compile_shader(self, source, output):
        output.write_bytes(b"synthetic-spirv:" + source.read_bytes())

    monkeypatch.setattr(module.Benchmark, "compile", compile_shader)
    tools = tmp_path / "synthetic tools"
    ndk, phonefarm = tools / "ndk", tools / "phonefarm"
    native = phonefarm / "knobs/gray/layer/selfsr.cpp"
    cxx = ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang++"
    glslc = ndk / "shader-tools/linux-x86_64/glslc"
    for path in (native, cxx, glslc, ndk / "source.properties"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"synthetic-only {path.name}")
    executable = phonefarm / "phonefarm"
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(phonefarm) + os.pathsep + os.environ["PATH"])
    builder = tools / "build_present_candidate.py"
    builder.write_text(
        '"""Synthetic CLI only: never compiles shaders or contacts a phone."""\n'
        "import argparse, json, sys\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "for name in ('source', 'ndk', 'phonefarm', 'phonefarm-root', 'out'):\n"
        "    parser.add_argument('--' + name, required=True)\n"
        "parser.add_argument('--kind', choices=('seed', 'candidate'), required=True)\n"
        "parser.add_argument('--mode', choices=('resize',), required=True)\n"
        "args = parser.parse_args()\n"
        "out = Path(args.out)\n"
        "out.mkdir(exist_ok=False)\n"
        "(out / 'argv.json').write_text(json.dumps(dict(vars(args), python=sys.executable)))\n"
        "(out / 'libVkLayer_refknobs.so').write_bytes(\n"
        "    b'\\x7fELFsynthetic-only:' + args.kind.encode() + Path(args.source).read_bytes())\n"
    )
    settings = json.loads((EXAMPLE / "config/settings.json").read_text())
    # Keep the original composite-protocol regressions on legacy flat settings.
    settings.pop("evaluation_profiles", None)
    settings.pop("score_mode", None)
    settings.pop("min_operator_improvement_pct", None)
    settings.update(
        device_id="synthetic-phone", expected_serial="synthetic-serial", device_profile="synthetic-gpu", poll_interval=0.001
    )
    settings["game_check"] = {"ndk_path": str(ndk), "phonefarm_root": str(phonefarm), "builder_path": str(builder), "options": {}}
    references = tmp_path / "inputs/genshin"
    references.mkdir(parents=True)
    (references / "frame.png").write_bytes(b"synthetic-reference-not-a-device-image")
    filename = tmp_path / "settings.json"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    shutil.copyfile(EXAMPLE / "assets/baseline.comp", candidate / "shader.comp")
    with (candidate / "shader.comp").open("a") as stream:
        stream.write("\n// Synthetic candidate identity.\n")

    def adapter():
        filename.write_text(json.dumps(settings))
        monkeypatch.setenv("HGAMEY_GATEWAY_CONFIG", str(filename))
        return load_evaluator(f"{EXAMPLE / 'evaluator.py'}:HGameyShaderEvaluator")

    job = RemoteJob(
        id=uuid.uuid4().hex, attempt_id=uuid.uuid4().hex, task_id=uuid.uuid4().hex, project_root=str(candidate), timeout=120
    )
    return SimpleNamespace(
        adapter=adapter,
        job=job,
        settings=settings,
        filename=filename,
        candidate=candidate,
        builder=builder,
        native=native,
        ndk=ndk,
        cxx=cxx,
        glslc=glslc,
        executable=executable,
        module=module,
    )


class SyntheticExecutor:
    """Simulate idempotent jobs, lost responses and cleanup without a device."""

    def __init__(self, adapter, reporter):
        """Bind one attempt's saved intent to a deterministic fake transport."""
        self.adapter, self.reporter = adapter, reporter
        self.jobs, self.posts, self.cancelled, self.lookups = {}, [], [], []
        self.features = ["idempotent_submit", "job_lookup", "cancel", "game_ab"]
        self.headless_status = "PASS"
        self.game_changes = {}
        self.lost_response = False
        self.crash_after_accept = False
        self.unsupported = False
        self.game_state = "completed"
        self.game_cleanup = "completed"
        self.cancel_finishes = False
        self.cancel_error = None
        self.missing_report = False

    async def rpc(self, session, method, path, body=None, missing=False):
        """Answer synthetic executor RPCs and verify intent precedes every HTTP call."""
        state = self.reporter.saved_state
        assert state, "Intent must be durable before HTTP"
        if state.get("game_required", self.adapter.benchmark.game_check is not None):
            assert state["game"]["request"]["operation"] == "game-ab"
            for name in ("baseline-layer/libVkLayer_refknobs.so", "candidate-layer/libVkLayer_refknobs.so"):
                assert (Path(state["evidence_directory"]) / name).exists()
        if path == "/v1/health":
            return {"features": self.features}
        if path == "/v1/devices":
            return {"devices": [{"id": "synthetic-phone", "enabled": True, "expected_serial": "synthetic-serial"}]}
        if path.startswith("/v1/jobs/by-key/"):
            key = path.rsplit("/", 1)[1]
            self.lookups.append(key)
            return copy.deepcopy(self.jobs.get(key))
        if method == "POST" and path == "/v1/jobs":
            self.posts.append(copy.deepcopy(body))
            is_game = body["operation"] == "game-ab"
            if is_game:
                assert state["game"]["submission_started"] is True
                assert body == state["game"]["request"]
                assert state["idempotency_key"] in self.jobs
                if self.unsupported:
                    raise aiohttp.ClientResponseError(None, (), status=400)
            key = body["idempotency_key"]
            self.jobs.setdefault(
                key,
                {
                    "id": f"synthetic-{len(self.jobs)}",
                    "operation": body["operation"],
                    "state": self.game_state if is_game else "completed",
                    "cleanup_status": self.game_cleanup if is_game else "completed",
                },
            )
            if is_game and self.crash_after_accept:
                self.crash_after_accept = False
                raise asyncio.CancelledError("Synthetic worker exit before receiving the game receipt")
            if is_game and self.lost_response:
                self.lost_response = False
                raise aiohttp.ClientConnectionError("Synthetic dropped submit response")
            return copy.deepcopy(self.jobs[key])
        item = next(item for item in self.jobs.values() if item["id"] == path.split("/")[3])
        if path.endswith("/cancel"):
            self.cancelled.append(item["operation"])
            if self.cancel_error == item["operation"]:
                raise aiohttp.ClientConnectionError("Synthetic cancellation transport outage")
            if item["state"] not in {"completed", "failed", "cancelled"}:
                item.update(
                    state="cancelled" if self.cancel_finishes else "cancel_requested",
                    cleanup_status="completed" if self.cancel_finishes else None,
                )
            return copy.deepcopy(item)
        if not path.endswith("/result"):
            return copy.deepcopy(item)
        if item["operation"] == "eval":
            return {
                "source": "phonefarm",
                "kind": "shader",
                "candidate_id": state["idempotency_key"],
                "status": self.headless_status,
                "conditions_observed": {"restored": True},
                "metrics": {"operator_latency_ms": 2.5, "psnr_db": 35},
                "baseline_metrics": {"operator_latency_ms": 5, "psnr_db": 35},
                "verdict": {"is_pareto_improvement": True, "p_value": 0.001},
                "synthetic_only": True,
            }
        if self.missing_report:
            return None
        return {
            "schema": "llm4ad/hgamey-game-ab@1",
            "status": "PASS",
            "errors": [],
            "candidate_applied": True,
            "conditions_observed": {"restored": True},
            "candidate_id": state["idempotency_key"],
            "candidate_layer_sha256": state["files"]["candidate-layer/libVkLayer_refknobs.so"],
            "baseline_layer_sha256": state["files"]["baseline-layer/libVkLayer_refknobs.so"],
            "metrics": {"baseline_display_fps": 30, "comparison_display_fps": 30, "comparison_power_watt": None},
            "synthetic_only": True,
            **self.game_changes,
        }


def connect(adapter, reporter, monkeypatch):
    """Attach the synthetic transport without replacing adapter control flow."""
    executor = SyntheticExecutor(adapter, reporter)
    monkeypatch.setattr(adapter, "rpc", executor.rpc)
    return executor


async def test_composite_builds_before_http_and_keeps_latency_score(local, monkeypatch):
    """Two synthetic arms preserve headless fitness and monitor capped game FPS."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    result = await run_adapter(adapter, local.job, reporter)
    assert result.success and result.score == -2.5
    assert result.metrics["operator_latency_ms"] == 2.5 and result.metrics["psnr_db"] == 35
    assert result.monitor_metrics["psnr_db"] == 35
    assert result.monitor_metrics["game_comparison_display_fps"] == 30
    assert "game_comparison_power_watt" not in result.metrics
    assert result.metadata["can_claim_game_improvement"] is False
    assert result.metadata["report"]["synthetic_only"] and result.metadata["game_report"]["synthetic_only"]
    assert [body["operation"] for body in executor.posts] == ["eval", "game-ab"]
    state = reporter.saved_state
    options = executor.posts[1]["options"]
    assert options["candidate_id"] == state["idempotency_key"] != state["game"]["idempotency_key"]
    for name in ("baseline", "candidate"):
        assert options[f"{name}_source_sha256"] == state["files"][f"{name}.comp"]
        assert options["baseline_layer_path" if name == "baseline" else "layer_path"].startswith("/inputs/")
        folder = Path(state["evidence_directory"])
        args = json.loads((folder / f"{name}-layer/argv.json").read_text())
        assert args["source"] == str(folder / f"{name}.comp")
        assert args["kind"] == ("seed" if name == "baseline" else "candidate")
        assert args["mode"] == "resize" and args["python"] == sys.executable
        assert args["phonefarm"] == str(local.executable) and args["phonefarm_root"] == str(local.executable.parent)
        assert args["ndk"] == str(local.ndk)
    assert options["a"] == options["b"] == "sr:1:2800x1272:0"
    assert options["rounds"] == 1 and options["window"] == 8 and options["settle"] == 5
    assert options["enter_script"] == "s:35;t:0.5,0.93;s:20" and options["pan"] is True
    assert options["timeout_seconds"] == 900


async def test_absent_game_check_keeps_original_contract_and_single_job(local, monkeypatch):
    """Opting out requires neither native tools nor game request state."""
    del local.settings["game_check"]
    local.builder.unlink()
    local.executable.unlink()
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    result = await run_adapter(adapter, local.job, reporter)
    assert result.success and result.score == -2.5
    assert "game_check" not in adapter.benchmark.manifest
    assert "game" not in reporter.saved_state and "game_report" not in result.metadata
    assert len(executor.posts) == 1
    assert result.metadata["measurement_scope"] == "operator_benchmark_only"


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("advertised", [True, False])
async def test_game_capability_is_required_only_when_configured(local, monkeypatch, configured, advertised):
    """Reject unsupported composite dispatch while retaining headless compatibility."""
    if not configured:
        del local.settings["game_check"]
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    if not advertised:
        executor.features.remove("game_ab")
    if configured and not advertised:
        with pytest.raises(RuntimeError, match="game_ab"):
            await run_adapter(adapter, local.job, reporter)
        assert not executor.posts and not executor.jobs
    else:
        assert (await run_adapter(adapter, local.job, reporter)).success
        assert len(executor.posts) == (2 if configured else 1)


@pytest.mark.parametrize("status", ["ABORT", "ERROR", "SLOW", "POOR_QUALITY", "CRASH"])
async def test_unsuccessful_headless_never_dispatches_game(local, monkeypatch, status):
    """A failed or invalid headless sample cannot qualify the game arm."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.headless_status = status
    result = await run_adapter(adapter, local.job, reporter)
    assert result is None if status in {"ABORT", "ERROR"} else not result.success
    assert [body["operation"] for body in executor.posts] == ["eval"]
    assert reporter.saved_state["game"]["submission_started"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unsupported"},
        {"status": "ERROR"},
        {"status": "ABORT"},
        {"status": "REJECT"},
        {"errors": ["synthetic missing telemetry"]},
        {"errors": None},
        {"candidate_applied": False},
        {"candidate_applied": 1},
        {"conditions_observed": {"restored": False}},
        {"conditions_observed": None},
        {"candidate_id": "another-candidate"},
        {"candidate_layer_sha256": "wrong"},
        {"baseline_layer_sha256": "wrong"},
        {"metrics": {}},
        {"metrics": None},
        {"metrics": {"baseline_display_fps": 30, "comparison_display_fps": 0}},
        {"metrics": {"baseline_display_fps": True, "comparison_display_fps": 30}},
        {"metrics": {"baseline_display_fps": 30, "comparison_display_fps": float("nan")}},
    ],
)
async def test_invalid_game_sample_is_infrastructure_not_candidate_success(local, monkeypatch, changes):
    """Synthetic invalid evidence never leaks a successful headless-only result."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.game_changes = changes
    assert await run_adapter(adapter, local.job, reporter) is None
    assert len(executor.jobs) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("unsupported", True),
        ("missing_report", True),
        ("game_cleanup", None),
        ("game_state", "interrupted"),
        ("game_state", "uncertain"),
        ("game_state", "cancelled"),
        ("game_state", "failed"),
    ],
)
async def test_unavailable_game_execution_is_unscored(local, monkeypatch, field, value):
    """Unsupported operations and uncertain execution remain infrastructure cases."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    setattr(executor, field, value)
    assert await run_adapter(adapter, local.job, reporter) is None


async def test_game_metrics_cannot_replace_headless_objective(local, monkeypatch):
    """Even colliding names are isolated as game monitors."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.game_changes = {
        "metrics": {"baseline_display_fps": 30, "comparison_display_fps": 30, "operator_latency_ms": 99, "psnr_db": 1}
    }
    result = await run_adapter(adapter, local.job, reporter)
    assert result.score == -2.5 and result.metrics["psnr_db"] == 35
    assert result.monitor_metrics["game_operator_latency_ms"] == 99


async def test_lost_response_and_restart_follow_both_original_jobs(local, monkeypatch):
    """Recovery reuses frozen binaries and identities even after source mutation."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.lost_response = True
    assert (await run_adapter(adapter, local.job, reporter)).success
    assert len(executor.jobs) == 2
    count = len(executor.posts)
    (local.candidate / "shader.comp").write_text("Changed after acceptance")
    restarted = local.adapter()
    monkeypatch.setattr(restarted, "rpc", executor.rpc)
    reporter.saved_state["game"].pop("external_job_id")
    assert (await run_adapter(restarted, local.job, reporter, operation="recover")).success
    assert (await run_adapter(restarted, local.job, reporter)).success
    assert len(executor.posts) == count and len(executor.jobs) == 2


async def test_crash_after_game_acceptance_recovers_without_resubmitting(local, monkeypatch):
    """A replacement worker discovers the accepted game job using its saved key."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.crash_after_accept = True
    with pytest.raises(asyncio.CancelledError):
        await run_adapter(adapter, local.job, reporter)
    assert reporter.saved_state["game"]["submission_started"] is True
    assert "external_job_id" not in reporter.saved_state["game"]
    restarted = local.adapter()
    monkeypatch.setattr(restarted, "rpc", executor.rpc)
    assert (await run_adapter(restarted, local.job, reporter, operation="recover")).success
    assert len(executor.posts) == 2 and len(executor.jobs) == 2


async def test_crash_between_headless_and_game_recovers_frozen_request(local, monkeypatch):
    """A crash before game dispatch neither repeats headless nor changes the game body."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    original_follow = adapter.follow_job

    async def crash_before_game(session, reporter, state, game=False):
        if game:
            raise asyncio.CancelledError("Synthetic exit between the two jobs")
        return await original_follow(session, reporter, state)

    monkeypatch.setattr(adapter, "follow_job", crash_before_game)
    with pytest.raises(asyncio.CancelledError):
        await run_adapter(adapter, local.job, reporter)
    assert reporter.saved_state["game"]["submission_started"] is False
    body = copy.deepcopy(reporter.saved_state["game"]["request"])
    restarted = local.adapter()
    monkeypatch.setattr(restarted, "rpc", executor.rpc)
    assert (await run_adapter(restarted, local.job, reporter, operation="recover")).success
    assert executor.posts[1] == body and len(executor.posts) == 2


@pytest.mark.parametrize("operation", ["eval", "game-ab"])
async def test_accepted_job_missing_from_executor_is_never_replayed(local, monkeypatch, operation):
    """A known external identity cannot turn into a fresh experiment."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    assert (await run_adapter(adapter, local.job, reporter)).success
    key = next(key for key, item in executor.jobs.items() if item["operation"] == operation)
    del executor.jobs[key]
    assert await run_adapter(adapter, local.job, reporter, operation="recover") is None
    assert len(executor.posts) == 2


async def test_resume_rejects_another_sdk_attempt(local, monkeypatch):
    """The game arm remains within its original accepted SDK attempt."""
    adapter, reporter = local.adapter(), Reporter()
    connect(adapter, reporter, monkeypatch)
    reporter.save_state(adapter.prepare(local.job))
    other = local.job.model_copy(update={"attempt_id": uuid.uuid4().hex})
    with pytest.raises(RuntimeError, match="different accepted attempt"):
        await run_adapter(adapter, other, reporter, operation="recover")


@pytest.mark.parametrize(
    "target", ["baseline-layer/libVkLayer_refknobs.so", "candidate-layer/libVkLayer_refknobs.so", "game-body"]
)
async def test_frozen_layer_or_game_body_tampering_blocks_recovery(local, monkeypatch, target):
    """Persisted identities cover both binaries and the exact game submission."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    reporter.save_state(adapter.prepare(local.job))
    if target == "game-body":
        reporter.saved_state["game"]["request"]["options"]["window"] += 1
    else:
        (Path(reporter.saved_state["evidence_directory"]) / target).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="changed"):
        await run_adapter(adapter, local.job, reporter, operation="recover")
    assert not executor.posts


@pytest.mark.parametrize("identity", ["options", "builder", "native", "cxx", "glslc", "ndk", "executable"])
def test_game_inputs_are_part_of_immutable_benchmark(local, identity):
    """Options, builder, native sources and toolchain content pin the contract."""
    local.adapter()
    if identity == "options":
        local.settings["game_check"]["options"]["window"] = 9
    else:
        path = local.ndk / "source.properties" if identity == "ndk" else getattr(local, identity)
        path.write_bytes(path.read_bytes() + b"\nchanged")
    with pytest.raises(ValueError, match="Benchmark changed"):
        local.adapter()


def test_changed_build_input_is_rejected_before_preparation(local):
    """An already loaded worker cannot build a new native version silently."""
    adapter = local.adapter()
    local.native.write_text("changed-native-source")
    with pytest.raises(RuntimeError, match="build inputs changed"):
        adapter.prepare(local.job)


async def test_missing_native_inputs_are_not_candidate_compilation_failures(local, monkeypatch):
    """Operator tool removal remains an infrastructure error before any dispatch."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    local.native.unlink()
    with pytest.raises(RuntimeError, match="build inputs are unavailable"):
        await run_adapter(adapter, local.job, reporter)
    assert not executor.posts and not reporter.saved_state


def test_actual_compiler_behind_ndk_wrapper_is_pinned(local):
    """An unchanged target wrapper cannot hide a replaced native compiler."""
    compiler = local.cxx.with_name("clang++")
    compiler.write_bytes(b"synthetic-compiler-version-one")
    local.adapter()
    compiler.write_bytes(b"synthetic-compiler-version-two")
    with pytest.raises(ValueError, match="Benchmark changed"):
        local.adapter()


@pytest.mark.parametrize(
    "key", ["candidate_id", "candidate_source_sha256", "baseline_source_sha256", "layer_path", "baseline_layer_path"]
)
def test_game_identities_and_paths_are_adapter_owned(local, key):
    """User options cannot override the frozen candidate or fixed baseline."""
    local.settings["game_check"]["options"][key] = "user-override"
    with pytest.raises(ValueError, match="adapter-owned"):
        local.adapter()


@pytest.mark.parametrize("kind", ["seed", "candidate"])
async def test_layer_build_failure_never_dispatches_either_job(local, monkeypatch, kind):
    """Synthetic native build failures cannot produce phone or candidate success."""
    local.builder.write_text(
        local.builder.read_text().replace(
            "out = Path(args.out)", f"assert args.kind != {kind!r}, 'synthetic build failure'\nout = Path(args.out)"
        )
    )
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    with pytest.raises(RuntimeError, match=f"Game {kind} layer build failed"):
        await run_adapter(adapter, local.job, reporter)
    assert not executor.posts and not reporter.saved_state


async def test_cancellation_checks_both_jobs_and_waits_for_cleanup(local, monkeypatch):
    """The completed headless arm cannot hide a still-running game arm."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.game_state, executor.game_cleanup = "running", None
    work = asyncio.create_task(run_adapter(adapter, local.job, reporter))
    try:
        for _ in range(1000):
            if reporter.saved_state.get("game", {}).get("external_job_id"):
                break
            await asyncio.sleep(0.001)
        assert reporter.saved_state["game"]["external_job_id"]
        assert await adapter.cancel(local.job, reporter.saved_state) is False
        assert executor.cancelled == ["eval", "game-ab"]
        executor.cancel_finishes = True
        assert await adapter.cancel(local.job, reporter.saved_state) is True
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def test_cancellation_checks_game_after_headless_transport_error(local, monkeypatch):
    """A failure querying one arm does not skip cancellation of the other."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    assert (await run_adapter(adapter, local.job, reporter)).success
    executor.cancel_error = "eval"
    assert await adapter.cancel(local.job, reporter.saved_state) is False
    assert executor.cancelled == ["eval", "game-ab"]


@pytest.mark.parametrize("started", [False, True])
async def test_absent_game_job_is_safe_only_before_submission_started(local, monkeypatch, started):
    """A lost game POST response remains uncertain until the executor confirms it."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    executor.headless_status = "POOR_QUALITY"
    assert not (await run_adapter(adapter, local.job, reporter)).success
    reporter.saved_state["game"]["submission_started"] = started
    assert await adapter.cancel(local.job, reporter.saved_state) is (not started)
    assert reporter.saved_state["game"]["idempotency_key"] in executor.lookups


async def test_cancellation_queries_game_even_when_headless_job_is_missing(local, monkeypatch):
    """Missing headless receipts must not leave a known game job uncancelled."""
    adapter, reporter = local.adapter(), Reporter()
    executor = connect(adapter, reporter, monkeypatch)
    assert (await run_adapter(adapter, local.job, reporter)).success
    del executor.jobs[reporter.saved_state["idempotency_key"]]
    assert await adapter.cancel(local.job, reporter.saved_state) is False
    assert executor.cancelled == ["game-ab"]


def test_game_wrapper_accepts_frozen_request_and_checks_synthetic_source_markers(local, tmp_path):
    """Exercise the parent's wrapper with synthetic artifacts, never a phone."""
    import hashlib

    spec = importlib.util.spec_from_file_location("synthetic_game_wrapper_for_adapter", EXAMPLE / "protocol/game_ab.py")
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    adapter = local.adapter()
    state = adapter.prepare(local.job)
    options = wrapper.validate_options(state["game"]["request"]["options"])
    folder = Path(state["evidence_directory"])
    for key, name in (("baseline_layer_path", "baseline"), ("layer_path", "candidate")):
        options[key] = str(folder / f"{name}-layer/libVkLayer_refknobs.so")
    frozen = {"submission": {"options": options}}
    wrapper.freeze_inputs(tmp_path / "synthetic-executor", frozen)
    options = frozen["submission"]["options"]
    evidence = tmp_path / "synthetic-game-evidence"
    evidence.mkdir()
    (evidence / "report.json").write_text(
        json.dumps(
            {
                "pkg": options["package"],
                "a": options["a"],
                "b": options["b"],
                "rounds": 1,
                "valid_pairs": 1,
                "dropped_arms": [],
                "metrics": {"display_fps": {"a_mean": 30, "b_mean": 30, "n_pairs": 1}},
                "synthetic_only": True,
            }
        )
    )
    arms = [
        {
            "arm": f"{side}1",
            "side": side,
            "pair": 1,
            "seq": index,
            "kind": options[side.lower()],
            "in_game": True,
            "display_fps": 30,
            "layer_loaded": True,
            "layer_removed": True,
            "session_exit": 0,
            "session_stop": "completed",
            "layer_path": options["baseline_layer_path" if side == "A" else "layer_path"],
            "layer_sha256": hashlib.sha256(
                Path(options["baseline_layer_path" if side == "A" else "layer_path"]).read_bytes()
            ).hexdigest(),
            "selfsr_upscaled_in_window": 8,
            "pan_moving_frames": 3,
            "selfsr": {"candidate_source_sha256": options[f"{name}_source_sha256"], "swap_extent": [2800, 1272]},
            "synthetic_only": True,
        }
        for index, (side, name) in enumerate((("A", "baseline"), ("B", "candidate")), 1)
    ]
    for arm in arms:
        session = evidence / "onboard" / f"{arm['arm']}_world" / "capture-fixture"
        session.mkdir(parents=True)
        (session / "session.json").write_text(json.dumps({
            "schema": "phonefarm/capture-session@1", "stage": "active", "serial": "fixture:5555",
            "transport_uncertain": False, "write_pending": None,
        }))
        recovery = {
            "schema": "phonefarm/capture-recovery@1", "status": "verified", "error": None,
            "serial": "fixture:5555", "session": str(session), "evidence": str(session / "recovery-100-1000.json"),
            "checks": {"scope": "capture_input_stayon_and_exit_policy", "stayon_restored": True,
                       "input_used": False, "input_absent": None, "game_state_restored": False},
        }
        (session / "recovery-100-1000.json").write_text(json.dumps(recovery))
        (session / "report-100-1001.json").write_text(json.dumps({
            "ok": True, "ready_only": True, "frames": 1, "stop_reason": "target_reached",
            "serial": "fixture:5555", "session": str(session),
            "recovery": recovery | {"status": "reported_complete"},
        }))
        control = evidence / "sessions" / arm["arm"] / "session-control.json"
        control.parent.mkdir(parents=True)
        control.write_text(json.dumps({
            "phase": "closed", "accepting": False, "stop_reason": "requested",
            "recovery": {"status": "reported_complete", "error": None, "game_state_restored": False,
                         "job_recovery_complete": False},
        }))
    (evidence / "arms.jsonl").write_text("\n".join(json.dumps(arm) for arm in arms))
    report = wrapper.summarize(evidence, options, True, 0)
    assert report["status"] == "PASS", report["errors"]
    headless = adapter.convert_report(
        {
            "source": "phonefarm",
            "kind": "shader",
            "candidate_id": state["idempotency_key"],
            "conditions_observed": {"restored": True},
            "status": "PASS",
            "metrics": {"operator_latency_ms": 2.5, "psnr_db": 35},
            "synthetic_only": True,
        },
        state,
    )
    result = adapter.combine_game_report(headless, report, state)
    assert result.success and result.score == -2.5
    assert report["candidate_applied"] is True and report["can_claim_game_improvement"] is False
    arms[1]["selfsr"]["candidate_source_sha256"] = "wrong-synthetic-source"
    (evidence / "arms.jsonl").write_text("\n".join(json.dumps(arm) for arm in arms))
    assert adapter.combine_game_report(headless, wrapper.summarize(evidence, options, True, 0), state) is None


@pytest.fixture
def fidelity(local):
    """Enable versioned two-stage settings while retaining synthetic native tools."""
    local.settings['evaluation_profiles'] = {
        'standard': {'seconds': 5, 'rounds': 8},
        'elite': {'seconds': 5, 'rounds': 16},
    }
    local.settings['score_mode'] = 'relative_latency'
    return local


@pytest.mark.parametrize('candidate,p_value,native_improvement,confirmed', [
    (99.5, 0.001, True, False),
    (99.00001, 0.001, True, False),
    (99, 0.001, True, True),
    (98, 0.01, True, True),
    (98, 0.010001, True, False),
    (98, 0.02, True, False),
    (98, 0.001, False, False),
    (100, 0.001, True, False),
    (101, 0.001, True, False),
])
def test_confirmed_improvement_requires_minimum_magnitude(
    fidelity, candidate, p_value, native_improvement, confirmed,
):
    """A tiny but significant gain stays valid evidence, without confirmation."""
    adapter = fidelity.adapter()
    for profile in ('standard', 'elite'):
        state = adapter.prepare(fidelity.job.model_copy(update={'evaluation_profile': profile}))
        result = adapter.convert_report({
            'source': 'phonefarm', 'kind': 'shader', 'candidate_id': state['idempotency_key'],
            'status': 'PASS', 'conditions_observed': {'restored': True},
            'metrics': {'operator_latency_ms': candidate, 'psnr_db': 35},
            'baseline_metrics': {'operator_latency_ms': 100},
            'verdict': {'is_pareto_improvement': native_improvement, 'p_value': p_value},
        }, state)
        assert result.success
        summary = result.metadata['evaluation_summary']
        assert summary['operator_improvement_confirmed'] is confirmed
        assert summary['min_operator_improvement_pct'] == 1
        assert summary['operator_improvement_threshold_met'] is (candidate <= 99)
        assert result.metrics['operator_improvement_pct'] == pytest.approx(100 - candidate)
        assert result.score == result.metrics['operator_improvement_pct']
        assert result.evolution_feedback['assessment'] == summary


def test_improvement_threshold_is_frozen_in_contract(fidelity):
    """Changing acceptance policy cannot silently reuse old physical results."""
    adapter = fidelity.adapter()
    fidelity.settings['min_operator_improvement_pct'] = 2
    with pytest.raises(ValueError, match='Benchmark changed'):
        fidelity.adapter()
    filename = fidelity.filename.with_name('stricter-improvement.json')
    filename.write_text(json.dumps(fidelity.settings))
    stricter = fidelity.module.Benchmark(filename)
    assert stricter.min_operator_improvement_pct == 2
    assert stricter.contract.data_version != adapter.contract.data_version
    strict_adapter = type(adapter)(filename)
    state = strict_adapter.prepare(fidelity.job)
    for candidate, confirmed in ((99, False), (98, True)):
        result = strict_adapter.convert_report({
            'source': 'phonefarm', 'kind': 'shader', 'candidate_id': state['idempotency_key'],
            'status': 'PASS', 'conditions_observed': {'restored': True},
            'metrics': {'operator_latency_ms': candidate, 'psnr_db': 35},
            'baseline_metrics': {'operator_latency_ms': 100},
            'verdict': {'is_pareto_improvement': True, 'p_value': 0.001},
        }, state)
        assert result.metadata['evaluation_summary']['operator_improvement_confirmed'] is confirmed


@pytest.mark.parametrize('threshold', [True, None, '1', 0.5, -1, 100, float('nan'), float('inf')])
def test_invalid_improvement_threshold_fails_before_dispatch(fidelity, threshold):
    """Reject invalid operator-owned acceptance limits before phone work."""
    fidelity.settings['min_operator_improvement_pct'] = threshold
    with pytest.raises(ValueError, match='min_operator_improvement_pct'):
        fidelity.adapter()


@pytest.mark.parametrize('profile,rounds,operations', [
    ('standard', 8, ['eval']),
    ('elite', 16, ['eval', 'game-ab']),
])
async def test_fidelity_selects_budget_and_game_execution(fidelity, monkeypatch, profile, rounds, operations):
    """Real adapter flow selects fidelity and never builds game layers for standard."""
    adapter, reporter = fidelity.adapter(), Reporter()
    job = fidelity.job.model_copy(update={'evaluation_profile': profile})
    executor = connect(adapter, reporter, monkeypatch)
    result = await run_adapter(adapter, job, reporter)
    assert result.success and result.score == 50
    assert result.metrics['operator_latency_ms'] == 2.5
    assert result.metrics['baseline_operator_latency_ms'] == 5
    assert result.metrics['operator_improvement_pct'] == 50
    assert [item['operation'] for item in executor.posts] == operations
    assert executor.posts[0]['request']['protocol']['rounds'] == rounds
    assert executor.posts[0]['request']['workload']['seconds'] == 5
    assert adapter.benchmark.request['protocol']['rounds'] == 8
    summary = result.metadata['evaluation_summary']
    assert summary['operator_improvement_confirmed'] is True
    assert summary['game_validation'] == ('passed' if profile == 'elite' else 'not_requested')
    assert result.evolution_feedback['assessment'] == summary
    assert result.metadata['can_claim_game_improvement'] is False
    folder = Path(reporter.saved_state['evidence_directory'])
    assert (folder / 'candidate-layer').exists() == (profile == 'elite')
    assert adapter.contract.metrics[0].name == 'operator_improvement_pct'
    assert await adapter.cancel(job, reporter.saved_state) is True


@pytest.mark.parametrize('baseline', [None, 0, -1, float('nan'), float('inf'), True])
def test_relative_fitness_requires_real_positive_baseline(fidelity, baseline):
    """Absent or invalid same-run timing never becomes a fabricated score."""
    adapter = fidelity.adapter()
    state = adapter.prepare(fidelity.job)
    report = {
        'source': 'phonefarm', 'kind': 'shader', 'candidate_id': state['idempotency_key'],
        'status': 'PASS', 'conditions_observed': {'restored': True},
        'metrics': {'operator_latency_ms': 2, 'psnr_db': 35},
        'baseline_metrics': {'operator_latency_ms': baseline},
    }
    assert adapter.convert_report(report, state) is None


async def test_elite_restart_keeps_profile_and_reuses_both_external_jobs(fidelity, monkeypatch):
    """A lost game reply reconnects the same frozen elite jobs without replay."""
    adapter, reporter = fidelity.adapter(), Reporter()
    job = fidelity.job.model_copy(update={'evaluation_profile': 'elite'})
    executor = connect(adapter, reporter, monkeypatch)
    executor.crash_after_accept = True
    with pytest.raises(asyncio.CancelledError):
        await run_adapter(adapter, job, reporter)
    assert [item['operation'] for item in executor.posts] == ['eval', 'game-ab']
    restarted = fidelity.adapter()
    executor.adapter = restarted
    monkeypatch.setattr(restarted, 'rpc', executor.rpc)
    result = await run_adapter(restarted, job, reporter, operation="recover")
    assert result.success and result.score == 50
    assert result.metadata['evaluation_profile'] == 'elite'
    assert len(executor.posts) == 2
    with pytest.raises(RuntimeError, match='attempt or profile'):
        await run_adapter(restarted, job.model_copy(update={'evaluation_profile': 'standard'}), reporter, operation="recover")


def test_fidelity_budgets_are_part_of_the_registered_contract(fidelity):
    """Changing elite alone cannot silently reuse an accepted contract."""
    first = fidelity.adapter().contract.data_version
    fidelity.settings['evaluation_profiles']['elite']['rounds'] = 20
    with pytest.raises(ValueError, match='Benchmark changed'):
        fidelity.adapter()
    changed = fidelity.filename.with_name('changed-settings.json')
    changed.write_text(json.dumps(fidelity.settings))
    benchmark = fidelity.module.Benchmark(changed)
    assert benchmark.contract.data_version != first


@pytest.mark.parametrize('profiles', [
    {}, {'standard': {'seconds': 5, 'rounds': 8}},
    {'standard': {'seconds': 5, 'rounds': 8}, 'elite': {'seconds': 5, 'rounds': 4}},
    {'standard': {'seconds': True, 'rounds': 8}, 'elite': {'seconds': 5, 'rounds': 16}},
])
def test_invalid_fidelity_configuration_is_rejected(fidelity, profiles):
    """Malformed budgets fail before any device access or registration."""
    fidelity.settings['evaluation_profiles'] = profiles
    with pytest.raises(ValueError, match='profile|Profile|Elite'):
        fidelity.adapter()


@pytest.mark.parametrize('reject_elite', [False, True])
async def test_orchestrator_dispatches_two_fidelities_over_real_http(fidelity, monkeypatch, tmp_path, reject_elite):
    """Simulate the real dispatcher/client/adapter chain; every measurement is synthetic."""
    import base64
    from unittest.mock import MagicMock

    from aiohttp import web

    from llm4ad.config.evaluator import RemoteEvaluatorConfig
    from llm4ad.evaluator.dispatcher import EvaluationDispatcher
    from llm4ad.infra.version_control.base import WorktreeInfo
    from llm4ad.orchestrator.island_ga import DiverseIslandGAOrchestrator, Island
    from llm4ad.planner.base import Algorithm, InsightType
    from llm4ad.remote.bundles import extract_candidate

    executor_jobs, requests, platform_calls = {}, [], []
    adapters = []

    async def health(request):
        return web.json_response({'features': ['idempotent_submit', 'job_lookup', 'cancel', 'game_ab']})

    async def devices(request):
        return web.json_response({'devices': [{'id': 'synthetic-phone', 'enabled': True, 'expected_serial': 'synthetic-serial'}]})

    async def submit_executor(request):
        body = await request.json()
        key = body['idempotency_key']
        requests.append(copy.deepcopy(body))
        executor_jobs.setdefault(key, {'id': uuid.uuid4().hex, 'body': body, 'state': 'completed', 'cleanup_status': 'completed'})
        return web.json_response({k: v for k, v in executor_jobs[key].items() if k != 'body'})

    async def lookup(request):
        item = executor_jobs.get(request.match_info['key'])
        if item is None:
            return web.json_response({}, status=404)
        return web.json_response({k: v for k, v in item.items() if k != 'body'})

    async def executor_result(request):
        item = next(row for row in executor_jobs.values() if row['id'] == request.match_info['id'])
        body = item['body']
        if body['operation'] == 'eval':
            operator = body['request']
            benchmark = adapters[0].benchmark
            relative = Path(operator['payload']['shader_path']).relative_to(benchmark.prefix)
            source = (benchmark.inputs / relative).read_text()
            fast = 'synthetic-fast' in source
            rejected = reject_elite and fast and operator['protocol']['rounds'] == 16
            return web.json_response({
                'source': 'phonefarm', 'kind': 'shader', 'candidate_id': operator['candidate_id'],
                'status': 'POOR_QUALITY' if rejected else 'PASS',
                'conditions_observed': {'restored': True},
                'metrics': {'operator_latency_ms': 2.5 if fast else 4, 'psnr_db': 10 if rejected else 35},
                'baseline_metrics': {'operator_latency_ms': 5, 'psnr_db': 35},
                'verdict': {'is_pareto_improvement': not rejected, 'p_value': 0.001},
                'synthetic_only': True,
            })
        options = body['options']
        return web.json_response({
            'schema': 'llm4ad/hgamey-game-ab@1', 'status': 'PASS', 'errors': [],
            'candidate_applied': True, 'candidate_id': options['candidate_id'],
            'conditions_observed': {'restored': True},
            'baseline_layer_sha256': next(
                state['files']['baseline-layer/libVkLayer_refknobs.so']
                for state in platform_calls if state['idempotency_key'] == options['candidate_id']
            ),
            'candidate_layer_sha256': next(
                state['files']['candidate-layer/libVkLayer_refknobs.so']
                for state in platform_calls if state['idempotency_key'] == options['candidate_id']
            ),
            'metrics': {'baseline_display_fps': 30, 'comparison_display_fps': 30},
            'synthetic_only': True,
        })

    class ObservedReporter(Reporter):
        """Expose frozen submissions to the simulated executor, without changing them."""
        def save_state(self, state):
            super().save_state(state)
            platform_calls.append(copy.deepcopy(state))

    requested_profiles = []

    async def submit_platform(request):
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        body = await request.json()
        requested_profiles.append(body['evaluation_profile'])
        root = tmp_path / 'gateway-work' / body['id']
        extract_candidate(base64.b64decode(body['bundle']), root)
        job = RemoteJob(
            id=body['id'], attempt_id=uuid.uuid4().hex, task_id=request.match_info['task'],
            project_root=str(root), timeout=body['timeout'], evaluation_profile=body['evaluation_profile'],
        )
        adapter = fidelity.adapter()
        adapters.append(adapter)
        assert body['contract'] == adapter.contract.model_dump(mode='json')
        result = await run_adapter(adapter, job, ObservedReporter())
        assert result is not None
        return web.json_response({'state': 'completed', 'attempt_id': job.attempt_id, 'report': result.model_dump(mode='json')})

    app = web.Application()
    app.router.add_get('/v1/health', health)
    app.router.add_get('/v1/devices', devices)
    app.router.add_post('/v1/jobs', submit_executor)
    app.router.add_get('/v1/jobs/by-key/{key}', lookup)
    app.router.add_get('/v1/jobs/{id}/result', executor_result)
    app.router.add_post('/api/v1/llm4ad/evaluation-gateways/tasks/{task}/jobs', submit_platform)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    address = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    fidelity.settings['executor_url'] = address
    monkeypatch.setenv('LLM4AD_REMOTE_URL', address + '/api/v1/llm4ad/evaluation-gateways')
    monkeypatch.setenv('LLM4AD_REMOTE_TASK_ID', str(uuid.uuid4()))
    monkeypatch.setenv('LLM4AD_REMOTE_TOKEN', 'synthetic-token')
    try:
        contract = fidelity.adapter().contract.model_dump(mode='json')
        dispatcher = EvaluationDispatcher(RemoteEvaluatorConfig(
            gateway_ids=[str(uuid.uuid4())], contract=contract, parallel=False,
        ))
        orchestrator = object.__new__(DiverseIslandGAOrchestrator)
        orchestrator.dispatcher = dispatcher
        orchestrator.config = SimpleNamespace(elite_reevaluation_count=1)
        orchestrator.version_control = MagicMock()
        candidates = []
        for name in ('slow', 'fast'):
            root = tmp_path / ('candidate-' + name)
            root.mkdir()
            (root / 'shader.comp').write_text((fidelity.candidate / 'shader.comp').read_text() + f'\n// synthetic-{name}\n')
            algorithm = Algorithm(
                id=name, insight_type=InsightType.MUTATION, name=name, description='Synthetic pipeline fixture',
                worktree=WorktreeInfo(name=name, path=str(root), branch=name, commit_hash='synthetic', created_at=0, last_used_at=0),
            )
            algorithm.add_code_artifact('shader.comp', (root / 'shader.comp').read_text(), language='glsl')
            await orchestrator._evaluate_algorithm(algorithm)
            orchestrator._record_evaluation_outcome(algorithm)
            candidates.append(algorithm)
        await orchestrator._reevaluate_elite_offspring(candidates)
        assert requested_profiles == ['standard', 'standard', 'elite']
        slow, fast = candidates
        assert slow.score == 20
        assert fast.custom_metadata['evaluation_profiles']['standard']['score'] == 50
        if reject_elite:
            assert not fast.is_evaluated()
            assert fast.custom_metadata['evaluation_profiles']['elite']['status'] == 'failed'
        else:
            assert fast.score == 50 and orchestrator._elite_evaluation_passed(fast)
        orchestrator.islands = [Island(island_id=0, population=candidates)]
        orchestrator.global_best_individual = max((a for a in candidates if a.is_evaluated()), key=lambda a: a.score)
        await orchestrator._finalize_elite_evaluation()
        best = orchestrator.global_best_individual
        assert best.name == ('slow' if reject_elite else 'fast')
        assert orchestrator._elite_evaluation_passed(best)
        summary = best.custom_metadata['evaluation_profiles']['elite']
        assert summary['assessment']['game_validation'] == 'passed'
        assert summary['evidence']['remote_job_id']
        restored = Algorithm.model_validate_json(best.model_dump_json())
        assert restored.custom_metadata['evaluation_profiles'] == best.custom_metadata['evaluation_profiles']
        assert orchestrator._elite_evaluation_passed(restored)
        assert len([r for r in requests if r['operation'] == 'game-ab']) == 1
        assert [r['request']['protocol']['rounds'] for r in requests if r['operation'] == 'eval'] == ([8, 8, 16, 16] if reject_elite else [8, 8, 16])
        assert orchestrator._total_evaluation_attempts == (4 if reject_elite else 3)
        assert orchestrator._total_successful_evaluations == 3
        (tmp_path / 'simulation.json').write_text(json.dumps({
            'synthetic_only': True, 'requested_profiles': requested_profiles,
            'operator_rounds': [r['request']['protocol']['rounds'] for r in requests if r['operation'] == 'eval'],
            'best': best.name, 'best_score': best.score,
            'game_jobs': len([r for r in requests if r['operation'] == 'game-ab']),
            'candidate_profiles': {a.name: a.custom_metadata['evaluation_profiles'] for a in candidates},
        }, indent=2))
    finally:
        await runner.cleanup()


async def test_elite_cancel_waits_for_game_cleanup(fidelity, monkeypatch):
    """New fidelity settings retain both cleanup obligations after interruption."""
    adapter, reporter = fidelity.adapter(), Reporter()
    job = fidelity.job.model_copy(update={'evaluation_profile': 'elite'})
    executor = connect(adapter, reporter, monkeypatch)
    executor.game_state = 'running'
    executor.crash_after_accept = True
    with pytest.raises(asyncio.CancelledError):
        await run_adapter(adapter, job, reporter)
    assert await adapter.cancel(job, reporter.saved_state) is False
    executor.cancel_finishes = True
    assert await adapter.cancel(job, reporter.saved_state) is True
    assert 'game-ab' in executor.cancelled


def test_frozen_profile_request_cannot_change_after_acceptance(fidelity):
    """Recovery rejects changed budgets and missing game obligations before HTTP."""
    adapter = fidelity.adapter()
    state = adapter.prepare(fidelity.job)
    state['request']['request']['protocol']['rounds'] = 1
    with pytest.raises(RuntimeError, match='operator submission changed'):
        asyncio.run(adapter.resume(state, Reporter()))
    state = adapter.prepare(fidelity.job.model_copy(update={'evaluation_profile': 'elite'}))
    state['game_required'] = False
    with pytest.raises(RuntimeError, match='game obligation'):
        asyncio.run(adapter.resume(state, Reporter()))


@pytest.mark.parametrize('baseline,candidate,expected', [(5, 4, 20), (50, 40, 20), (5, 6, -20)])
def test_relative_score_scale_is_shared_by_both_fidelities(fidelity, baseline, candidate, expected):
    """Positive means faster; clock scale and requested fidelity do not change the formula."""
    adapter = fidelity.adapter()
    for profile in ('standard', 'elite'):
        state = adapter.prepare(fidelity.job.model_copy(update={'evaluation_profile': profile}))
        result = adapter.convert_report({
            'source': 'phonefarm', 'kind': 'shader', 'candidate_id': state['idempotency_key'],
            'status': 'PASS', 'conditions_observed': {'restored': True},
            'metrics': {'operator_latency_ms': candidate, 'psnr_db': 35},
            'baseline_metrics': {'operator_latency_ms': baseline},
            'verdict': {'is_pareto_improvement': False, 'p_value': 0.5},
        }, state)
        assert result.success and result.score == expected
        assert result.metadata['evaluation_summary']['operator_improvement_confirmed'] is False
