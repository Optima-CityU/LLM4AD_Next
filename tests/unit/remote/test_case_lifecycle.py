"""Case lifecycle regressions using synthetic compilers, local files and fake RPCs."""

import copy
import json
import py_compile
import subprocess
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from test_hgamey_game_adapter import connect
from test_hgamey_game_adapter import local as hgamey_fixture

from llm4ad.gateway.examples.basic.evaluator import Evaluator as BasicEvaluator
from llm4ad.gateway.examples.hgamey import runtime as runtime_module
from llm4ad.gateway.examples.hgamey.compilation.shader import compile_shader
from llm4ad.gateway.examples.hgamey.runtime import HGameyRuntime
from llm4ad.gateway.sdk.contracts import RemoteJob
from llm4ad.gateway.sdk.errors import EvaluationError
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import run_operation

local = hgamey_fixture


@pytest.fixture
def attempt(local, tmp_path, monkeypatch):
    """Use a real durable journal with private log paths and synthetic compiler output."""
    adapter = local.adapter()
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(local.job.model_dump(mode="json"))
    reporter = Reporter(journal, local.job.id, local.job.attempt_id)

    def local_log(name, *, description=""):
        path = tmp_path / "private-logs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(reporter, "local_log", local_log, raising=False)
    executor = connect(adapter, reporter, monkeypatch)
    return adapter, reporter, executor


async def invoke(adapter, operation, job, reporter):
    """Run the complete SDK lifecycle rather than bypassing preparation hooks."""
    return await run_operation(adapter, operation, job, reporter, lambda: getattr(adapter, operation)(job, reporter))


async def test_fresh_prepares_once_and_recovery_reuses_frozen_measurements(attempt, local, monkeypatch):
    """Retries reuse accepted binaries and external IDs despite changed candidate sources."""
    adapter, reporter, executor = attempt
    prepare = Mock(wraps=adapter.prepare)
    monkeypatch.setattr(adapter, "prepare", prepare)
    result = await invoke(adapter, "evaluate", local.job, reporter)
    assert result.success and len(executor.posts) == 2
    frozen = copy.deepcopy(reporter.saved_state)
    (local.candidate / "shader.comp").write_text("invalid changed source")
    result = await invoke(adapter, "recover", local.job, reporter)
    assert result.success and len(executor.posts) == 2
    result = await invoke(adapter, "evaluate", local.job, reporter)
    assert result.success and len(executor.posts) == 2
    prepare.assert_called_once_with(local.job)
    assert reporter.saved_state == frozen
    folder = Path(frozen["evidence_directory"])
    assert all((folder / name).is_file() for name in frozen["files"])


async def test_unprepared_evaluate_cannot_silently_compile(attempt, local, monkeypatch):
    """Direct callers must opt into lifecycle preparation before measurement."""
    adapter, reporter, executor = attempt
    prepare = Mock(side_effect=AssertionError("Unexpected preparation"))
    monkeypatch.setattr(adapter, "prepare", prepare)
    with pytest.raises(RuntimeError, match="before_task"):
        await adapter.evaluate(local.job, reporter)
    assert await invoke(adapter, "recover", local.job, reporter) is None
    prepare.assert_not_called()
    assert executor.posts == []


async def test_recovery_adopts_completed_preparation_without_rebuilding(attempt, local, monkeypatch):
    """A crash between frozen intent and journal save recovers from the owned receipt."""
    adapter, reporter, executor = attempt
    state = adapter.prepare(local.job)
    monkeypatch.setattr(adapter, "prepare", Mock(side_effect=AssertionError("Recovery compiled")))
    assert not reporter.saved_state
    result = await invoke(adapter, "recover", local.job, reporter)
    assert result.success and len(executor.posts) == 2
    assert reporter.saved_state["idempotency_key"] == state["idempotency_key"]


@pytest.mark.parametrize("name", ["candidate.spv", "candidate-layer/libVkLayer_refknobs.so"])
async def test_recovery_rejects_missing_frozen_file_identity_before_http(attempt, local, name):
    """A partial file manifest cannot bypass validation of accepted binaries."""
    adapter, reporter, executor = attempt
    state = adapter.prepare(local.job)
    state["files"].pop(name)
    reporter.save_state(state)
    with pytest.raises(RuntimeError, match="file identities are missing"):
        await invoke(adapter, "recover", local.job, reporter)
    assert executor.posts == []


async def test_candidate_preparation_failure_finalizes_private_logs_and_feedback(attempt, local, monkeypatch, tmp_path):
    """Candidate compile errors retain their cause, feedback and captured diagnostics."""
    adapter, reporter, executor = attempt
    original = adapter.benchmark.compile
    failure = ValueError("synthetic candidate syntax error")

    def compile_with_error(source, output):
        (output.parent / f"{output.stem}.tool-0.log").write_text("captured stdout\ncaptured stderr\n")
        if source.name == "candidate.comp":
            raise failure
        return original(source, output)

    monkeypatch.setattr(adapter.benchmark, "compile", compile_with_error)
    with pytest.raises(EvaluationError) as caught:
        await invoke(adapter, "evaluate", local.job, reporter)
    assert type(caught.value).__name__ == "CandidateCompilationError"
    assert caught.value.__cause__ is failure
    assert caught.value.metadata["candidate_failure"] is True
    assert caught.value.evolution_feedback == {"stage": "compile", "diagnostics": str(failure)}
    assert (tmp_path / "private-logs/compiler/candidate.tool-0.log").read_text() == "captured stdout\ncaptured stderr\n"
    folder = adapter.benchmark.profile / "jobs" / adapter.benchmark.attempt_key(local.job)
    assert (folder / "candidate.comp").exists() and (folder / "baseline.comp").exists()
    assert not reporter.saved_state and executor.posts == []
    assert reporter.journal.artifacts(local.job.id, local.job.attempt_id) == []


async def test_baseline_error_is_not_candidate_feedback(attempt, local, monkeypatch):
    """An operator baseline failure remains an infrastructure error with its original cause."""
    adapter, reporter, executor = attempt
    failure = ValueError("synthetic broken baseline tool")
    monkeypatch.setattr(adapter.benchmark, "compile", Mock(side_effect=failure))
    with pytest.raises(RuntimeError, match="operator-owned baseline") as caught:
        await invoke(adapter, "evaluate", local.job, reporter)
    assert caught.value.__cause__ is failure
    assert not isinstance(caught.value, EvaluationError)
    assert executor.posts == []


async def test_diagnostic_failure_does_not_replace_preparation_error(attempt, local, monkeypatch):
    """The SDK preserves the primary compiler error if diagnostic finalization also fails."""
    adapter, reporter, _ = attempt
    original = adapter.benchmark.compile

    def compile_with_error(source, output):
        if source.name == "candidate.comp":
            (output.parent / "candidate.tool-0.log").write_text("synthetic compiler output")
            raise ValueError("primary candidate error")
        return original(source, output)

    monkeypatch.setattr(adapter.benchmark, "compile", compile_with_error)
    monkeypatch.setattr(reporter, "local_log", Mock(side_effect=OSError("log unavailable")))
    with pytest.raises(EvaluationError, match="primary candidate error"):
        await invoke(adapter, "evaluate", local.job, reporter)
    assert not reporter.saved_state


@pytest.mark.parametrize("captured", [b"captured output", "captured output"])
def test_compiler_timeout_keeps_captured_output_and_original_exception(tmp_path, monkeypatch, captured):
    """Captured stdout and stderr survive a compiler timeout without replacing its exception."""
    failure = subprocess.TimeoutExpired(["synthetic-compiler"], 1, output=captured, stderr=captured)
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        compile_shader(tmp_path / "candidate.comp", tmp_path / "candidate.spv", compiler="fake", validator="fake", timeout=1)
    assert caught.value is failure
    assert (tmp_path / "candidate.tool-0.log").read_bytes() == b"captured outputcaptured output"


def test_compiler_timeout_is_preserved_when_diagnostic_storage_fails(tmp_path, monkeypatch):
    """A log write failure cannot hide the compiler's actual timeout exception."""
    failure = subprocess.TimeoutExpired(["synthetic-compiler"], 1, output=b"partial output")
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    monkeypatch.setattr(Path, "write_bytes", Mock(side_effect=OSError("diagnostic storage unavailable")))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        compile_shader(tmp_path / "candidate.comp", tmp_path / "candidate.spv", compiler="fake", validator="fake", timeout=1)
    assert caught.value is failure


async def test_completed_game_evidence_is_retrieved_only_in_after_hook(attempt, local, monkeypatch):
    """Measurement follows accepted jobs; finalization handles completed, restored evidence."""
    adapter, reporter, _ = attempt
    retain = AsyncMock()
    monkeypatch.setattr(adapter, "retain_game_artifacts", retain)
    await adapter.before_task(local.job, reporter, "evaluate")
    result = await adapter.evaluate(local.job, reporter)
    retain.assert_not_awaited()
    await adapter.after_task(local.job, reporter, "evaluate", result=result)
    retain.assert_awaited_once()
    folder = Path(reporter.saved_state["evidence_directory"])
    receipt = json.loads((folder / "game-executor-job.json").read_text())
    receipt["cleanup_status"] = "uncertain"
    (folder / "game-executor-job.json").write_text(json.dumps(receipt))
    retain.reset_mock()
    await adapter.after_task(local.job, reporter, "recover", result=result)
    retain.assert_not_awaited()
    assert all((folder / name).exists() for name in reporter.saved_state["files"])


async def test_cancel_never_compiles_or_removes_frozen_files(attempt, local, monkeypatch):
    """Cancellation hooks preserve compiler outputs and restoration evidence for SDK retention."""
    adapter, reporter, executor = attempt
    await invoke(adapter, "evaluate", local.job, reporter)
    folder = Path(reporter.saved_state["evidence_directory"])
    files = {name: (folder / name).read_bytes() for name in reporter.saved_state["files"]}
    monkeypatch.setattr(adapter, "prepare", Mock(side_effect=AssertionError("Cancellation prepared")))
    confirmed = await run_operation(
        adapter, "cancel", local.job, reporter, lambda: adapter.cancel(local.job, reporter.saved_state)
    )
    assert confirmed and executor.cancelled == ["eval", "game-ab"]
    assert all((folder / name).read_bytes() == content for name, content in files.items())


async def test_startup_uses_fresh_and_recovery_hooks_with_short_frozen_budget(local, monkeypatch, tmp_path):
    """Startup keeps separate provenance, wraps both operations and never replays accepted jobs."""
    adapter = local.adapter()
    runtime = HGameyRuntime.__new__(HGameyRuntime)
    runtime.options = {"startup_check": True, "startup_timeout": 60, "startup_seconds": 1, "startup_rounds": 2}
    directory = tmp_path / "startup-state"
    directory.mkdir()
    observed, executors = [], []
    before, after = adapter.before_task, adapter.after_task

    async def observe_before(job, reporter, operation):
        observed.append("before:" + operation)
        await before(job, reporter, operation)

    async def observe_after(job, reporter, operation, **kwargs):
        observed.append("after:" + operation)
        await after(job, reporter, operation, **kwargs)

    def reporter_factory(journal, job_id, attempt_id):
        reporter = Reporter(journal, job_id, attempt_id)
        if executors:
            executors[0].reporter = reporter
        else:
            executors.append(connect(adapter, reporter, monkeypatch))
        return reporter

    monkeypatch.setattr(runtime_module, "Reporter", reporter_factory)
    monkeypatch.setattr(adapter, "before_task", observe_before)
    monkeypatch.setattr(adapter, "after_task", observe_after)
    await runtime.startup_check(adapter, directory)
    assert observed == ["before:evaluate", "after:evaluate"]
    journal = Journal(directory / "startup.sqlite3")
    row = journal.rows()[0]
    state = json.loads(row["saved"])
    assert state["purpose"] == "startup_readiness_only"
    assert state["request"]["request"]["workload"]["seconds"] == 1
    assert state["request"]["request"]["protocol"]["rounds"] == 2
    assert json.loads(row["job"])["evaluation_profile"] == "elite"
    assert len(executors[0].posts) == 2
    journal.update(row["id"], "recovery_required", {})
    monkeypatch.setattr(adapter, "prepare", Mock(side_effect=AssertionError("Startup recovery compiled")))
    observed.clear()
    runtime.options["startup_seconds"] = 30
    await runtime.startup_check(adapter, directory)
    assert observed == ["before:recover", "after:recover"]
    assert len(executors[0].posts) == 2
    assert json.loads(journal.rows()[0]["saved"])["request"] == state["request"]
    assert Journal(directory / "jobs.sqlite3").rows() == []


@pytest.fixture
def external_logs(attempt, local, tmp_path):
    """Create receipt-bound compiler and executor logs without starting a backend."""
    adapter, reporter, _ = attempt
    state = adapter.prepare(local.job)
    stage = Path(state["evidence_directory"])
    (stage / "candidate.tool-0.log").write_text("synthetic compiler diagnostics")
    executor_root = tmp_path / "executor"
    for arm, intent in (("eval", state), ("game-ab", state["game"])):
        key = uuid.uuid4().hex
        intent["external_job_id"] = key
        folder = executor_root / "jobs" / key
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            json.dumps(
                {
                    "id": key,
                    "idempotency_key": intent["idempotency_key"],
                    "operation": arm,
                    "device_id": intent["request"]["device_id"],
                }
            )
        )
        for name in ("worker.stdout", "worker.stderr", "backend.stdout", "backend.stderr", "game.stdout", "game.stderr"):
            (folder / name).write_text(f"synthetic {arm} {name}")
        (folder / "ignored.txt").write_text("not an allowed log")
    reporter.save_state(state)
    runtime = HGameyRuntime.__new__(HGameyRuntime)
    runtime.options = {
        "profiles": [{"settings_path": local.filename, "settings": local.settings}],
        "executor_data": executor_root,
    }
    runtime.engine = Mock(side_effect=AssertionError("Log discovery touched the device engine"))
    return runtime, state, stage


def test_runtime_local_logs_reads_only_owned_whitelisted_files(external_logs, local, monkeypatch):
    """Runtime log discovery never constructs tools or probes executor/device state."""
    runtime, state, stage = external_logs
    monkeypatch.setattr(local.module.Benchmark, "__init__", Mock(side_effect=AssertionError("Constructed benchmark")))
    logs = runtime.local_logs(local.job, state)
    assert len(logs) == 15
    assert logs["compiler/candidate.tool-0.log"] == stage / "candidate.tool-0.log"
    assert logs["executor/eval/worker.stdout"].read_text() == "synthetic eval worker.stdout"
    assert logs["executor/game-ab/game.stderr"].read_text() == "synthetic game-ab game.stderr"
    assert not any(path.name == "ignored.txt" for path in logs.values())
    assert runtime.local_logs(local.job, {}) == {name: path for name, path in logs.items() if name.startswith("compiler/")}
    assert all((stage / name).is_file() for name in state["files"])
    assert runtime.engine.mock_calls == []


@pytest.mark.parametrize("tampering", ["attempt", "owner", "native_receipt", "native_path", "symlink", "compiler_symlink"])
def test_runtime_local_logs_rejects_unowned_or_symlink_paths(external_logs, local, tmp_path, tampering):
    """Manipulated recovery state never grants access to unrelated local logs."""
    runtime, state, stage = external_logs
    if tampering == "attempt":
        state["remote_attempt_id"] = "another-attempt"
    elif tampering == "owner":
        record = json.loads((stage / "owner.json").read_text())
        record["job_id"] = "another-job"
        (stage / "owner.json").write_text(json.dumps(record))
    elif tampering == "native_receipt":
        receipt = runtime.options["executor_data"] / "jobs" / state["external_job_id"] / "job.json"
        record = json.loads(receipt.read_text())
        record["idempotency_key"] = "0" * 64
        receipt.write_text(json.dumps(record))
    elif tampering == "native_path":
        state["external_job_id"] = "../other"
    else:
        target = (
            stage / "candidate.tool-0.log"
            if tampering == "compiler_symlink"
            else (runtime.options["executor_data"] / "jobs" / state["external_job_id"] / "worker.stdout")
        )
        other = tmp_path / "outside.log"
        other.write_text("unrelated file")
        target.unlink()
        target.symlink_to(other)
    with pytest.raises(ValueError):
        runtime.local_logs(local.job, state)


@pytest.fixture
def basic_job(tmp_path):
    """Create a local candidate and real reporter for the runnable basic example."""
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "solution.py").write_text("print('candidate diagnostic')\ndef solve(x): return x*x\n")
    job = RemoteJob(
        id=uuid.uuid4().hex, attempt_id=uuid.uuid4().hex, task_id=uuid.uuid4().hex, project_root=str(candidate), timeout=10
    )
    journal = Journal(tmp_path / "basic.sqlite3")
    journal.accept(job.model_dump(mode="json"))
    return job, Reporter(journal, job.id, job.attempt_id)


async def test_basic_finalizes_outputs_after_json_runner_execution(basic_job):
    """Candidate stdout stays diagnostic while measurements remain valid JSON."""
    job, reporter = basic_job
    adapter = BasicEvaluator()
    await adapter.before_task(job, reporter, "evaluate")
    result = await adapter.evaluate(job, reporter)
    assert result.score == 0 and result.metadata["cases"] == 5
    assert not (Path(job.artifact_dir) / "measurements.json").exists()
    await adapter.after_task(job, reporter, "evaluate", result=result)
    assert {item.name for item in result.artifacts} == {"measurements.json", "report.md"}
    assert json.loads((Path(job.artifact_dir) / "measurements.json").read_text())["mse"] == 0
    assert "candidate diagnostic" in adapter.diagnostic_path(job, reporter, "runner.stderr").read_text()


async def test_basic_preparation_error_finalizes_without_running_candidate(basic_job, monkeypatch):
    """The after hook records syntax failures and the runner never starts."""
    job, reporter = basic_job
    (Path(job.project_root) / "solution.py").write_text("def broken syntax")
    adapter = BasicEvaluator()
    evaluate = AsyncMock(side_effect=AssertionError("Ran candidate after syntax failure"))
    monkeypatch.setattr(adapter, "evaluate", evaluate)
    with pytest.raises(py_compile.PyCompileError):
        await invoke(adapter, "evaluate", job, reporter)
    evaluate.assert_not_awaited()
    assert "SyntaxError" in adapter.diagnostic_path(job, reporter, "operation-error.log").read_text()
    assert (Path(job.project_root) / "solution.py").exists()
