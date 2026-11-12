"""Verify real file retention, instance identity and non-scoring failures."""

import asyncio
import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm4ad.config.evaluator import CustomEvaluatorConfig
from llm4ad.evaluator.behavior import BehaviorData, BehaviorVisualization
from llm4ad.evaluator.dispatcher import EvaluationDispatcher
from llm4ad.evaluator.models import EvaluationArtifact, EvaluationResult


def dispatcher(monkeypatch, tmp_path, evaluator, files=None):
    """Build a real dispatcher with an isolated in-process evaluator."""
    monkeypatch.setattr(EvaluationDispatcher, "_resolve_evaluator_class", lambda _: type(evaluator))
    monkeypatch.setattr(EvaluationDispatcher, "_get_data_files", lambda _: files or [])
    instance = EvaluationDispatcher(
        CustomEvaluatorConfig(module="fixture:Evaluator", parallel=True),
        records_dir=tmp_path / "records",
    )
    monkeypatch.setattr(instance, "_create_evaluator", lambda: evaluator)
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    algorithm = SimpleNamespace(
        id="abcd1234ef00",
        name="Candidate",
        worktree=SimpleNamespace(path=str(worktree)),
        custom_metadata={},
        code_fingerprint=lambda: "source-a",
    )
    return instance, algorithm


class FilesEvaluator:
    """Produce a declared report plus an undeclared temporary file."""

    async def evaluate(self, cfg):
        """Return measured data and exactly one retained report."""
        output = Path(cfg.artifact_dir)
        (output / "report.json").write_text(json.dumps({"instance": cfg.instance_id, "value": 42}))
        (output / "temporary.txt").write_text("do not retain")
        return EvaluationResult(
            score=3,
            metrics={"accuracy": 3},
            monitor_metrics={"latency": 12},
            metadata={"explanation": "measured"},
            artifacts=[EvaluationArtifact(name="reports/报告.json", path="report.json", media_type="application/json")],
        )


def test_declared_outputs_survive_worktree_removal(monkeypatch, tmp_path):
    """Retain exact declared bytes and stable candidate links outside the worktree."""
    runner, algorithm = dispatcher(monkeypatch, tmp_path, FilesEvaluator())
    result = asyncio.run(runner.dispatch_batch([algorithm]))[0]
    shutil.rmtree(algorithm.worktree.path)
    reference = result.metadata["evaluation_records"][0]
    root = tmp_path / "records" / reference["id"]
    record = json.loads((root / "record.json").read_text())
    assert record["algorithm_id"] == algorithm.id
    assert record["source_fingerprint"] == "source-a"
    assert record["result"]["metadata"]["explanation"] == "measured"
    assert len(record["artifacts"]) == 1
    saved = record["artifacts"][0]
    assert json.loads((root / "files" / saved["sha256"]).read_text())["value"] == 42
    assert not (root / "output").exists()
    assert algorithm.custom_metadata["evaluation_records"] == [reference]


def test_instances_and_profiles_retain_all_metadata(monkeypatch, tmp_path):
    """Never flatten distinct instances or overwrite standard data during elite evaluation."""
    runner, algorithm = dispatcher(monkeypatch, tmp_path, FilesEvaluator(), ["data/a.json", "other/a.json"])
    standard = asyncio.run(runner.dispatch_batch([algorithm]))[0]
    elite = asyncio.run(runner.dispatch_batch([algorithm], evaluation_profile="elite"))[0]
    assert standard.score == elite.score == 3
    assert standard.monitor_metrics == {"latency": 12}
    assert len(standard.metadata["per_instance"]) == 2
    assert all(item["metadata"]["explanation"] == "measured" for item in standard.metadata["per_instance"])
    assert {ref["instance_id"] for ref in standard.metadata["evaluation_records"]} == {"0:a.json", "1:a.json"}
    assert len(algorithm.custom_metadata["evaluation_records"]) == 4
    assert len(list((tmp_path / "records").glob("*/record.json"))) == 4


@pytest.mark.parametrize("kind", ["missing", "escape", "symlink", "duplicate"])
def test_invalid_artifact_never_changes_score(monkeypatch, tmp_path, kind):
    """Reject invalid file declarations while retaining the original evaluation outcome."""
    outside = tmp_path / "private.txt"
    outside.write_text("outside")

    class InvalidEvaluator:
        async def evaluate(self, cfg):
            root = Path(cfg.artifact_dir)
            if kind == "symlink":
                try:
                    (root / "bad.txt").symlink_to(outside)
                except OSError:
                    pytest.skip("Symlinks unavailable")
            path = str(outside) if kind == "escape" else "bad.txt"
            artifacts = [EvaluationArtifact(name="bad.txt", path=path)]
            if kind == "duplicate":
                (root / "bad.txt").write_text("first")
                artifacts *= 2
            return EvaluationResult(score=7, metrics={"value": 7}, artifacts=artifacts)

    runner, algorithm = dispatcher(monkeypatch, tmp_path, InvalidEvaluator())
    result = asyncio.run(runner.dispatch_batch([algorithm]))[0]
    assert result.success and result.score == 7
    record = json.loads(next((tmp_path / "records").glob("*/record.json")).read_text())
    assert record["storage_state"] == "error" and record["storage_errors"]
    assert outside.read_text() == "outside"


def test_failed_evaluation_retains_declared_files(monkeypatch, tmp_path):
    """Failures still retain diagnostics without converting them into success."""

    class Failed(FilesEvaluator):
        async def evaluate(self, cfg):
            result = await super().evaluate(cfg)
            result.success, result.error_message = False, "candidate failed"
            return result

    runner, algorithm = dispatcher(monkeypatch, tmp_path, Failed())
    result = asyncio.run(runner.dispatch_batch([algorithm]))[0]
    record = json.loads(next((tmp_path / "records").glob("*/record.json")).read_text())
    assert not result.success and record["state"] == "failed"
    assert record["artifacts"]


def test_behavior_data_keeps_existing_payload_and_saves_files(monkeypatch, tmp_path):
    """Expose returned images and renderer data without changing multimodal input."""
    behavior = BehaviorData(
        visualizations=[
            BehaviorVisualization(
                label="curve",
                data_base64=base64.b64encode(b"image fixture").decode(),
                raw_data={"points": [1, 2], "visited": {1, 2}},
                renderer="curve",
            )
        ]
    )

    class Visual:
        async def evaluate(self, cfg):
            return EvaluationResult(metrics={}, behavior=behavior)

    runner, algorithm = dispatcher(monkeypatch, tmp_path, Visual())
    result = asyncio.run(runner.dispatch_batch([algorithm]))[0]
    record = json.loads(next((tmp_path / "records").glob("*/record.json")).read_text())
    assert result.behavior is behavior
    assert len(record["artifacts"]) == 2
    raw = next(item for item in record["artifacts"] if item["name"].endswith(".json"))
    directory = next((tmp_path / "records").iterdir())
    assert sorted(json.loads((directory / "files" / raw["sha256"]).read_text())["data"]["visited"]) == [1, 2]
    assert "behavior" not in record["result"]


def test_cancelled_evaluation_records_terminal_state(monkeypatch, tmp_path):
    """Preserve cancellation rather than treating an interrupted evaluation as passed."""

    class Cancelled:
        async def evaluate(self, cfg):
            raise asyncio.CancelledError()

    runner, algorithm = dispatcher(monkeypatch, tmp_path, Cancelled())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.dispatch_batch([algorithm]))
    record = json.loads(next((tmp_path / "records").glob("*/record.json")).read_text())
    assert record["state"] == "cancelled" and not record["result"]["success"]


def test_child_does_not_inherit_or_mutate_parent_evaluation_links(monkeypatch, tmp_path):
    """Shallow-copied algorithm metadata must not cross candidate boundaries."""
    runner, algorithm = dispatcher(monkeypatch, tmp_path, FilesEvaluator())
    parent_metadata = {"evaluation_records": [{"id": "parent-record", "algorithm_id": "parent"}]}
    algorithm.custom_metadata = parent_metadata
    asyncio.run(runner.dispatch_batch([algorithm]))
    assert parent_metadata["evaluation_records"] == [{"id": "parent-record", "algorithm_id": "parent"}]
    assert len(algorithm.custom_metadata["evaluation_records"]) == 1
    assert algorithm.custom_metadata["evaluation_records"][0]["algorithm_id"] == algorithm.id


def test_owned_reader_rejects_parent_symlink_and_fifo(tmp_path):
    """Pinned directory reads cannot follow evaluator-controlled filesystem links."""
    import os

    from llm4ad.evaluator.records import open_owned_file

    root, outside = tmp_path / "owned", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises((OSError, ValueError)), open_owned_file(root / "linked" / "private.txt", root):
        pytest.fail("A symlinked parent must not be readable")
    if hasattr(os, "mkfifo"):
        fifo = root / "pipe"
        os.mkfifo(fifo)
        with pytest.raises(ValueError), open_owned_file(fifo, root):
            pytest.fail("A pipe must not be read as evidence")


def test_missing_monitor_data_is_not_averaged_as_zero():
    """Only measured monitoring values contribute to their own aggregate mean."""
    runner = EvaluationDispatcher.__new__(EvaluationDispatcher)
    aggregate = runner._aggregate_results(
        [
            EvaluationResult(score=1, metrics={}, monitor_metrics={"latency": 12}),
            EvaluationResult(score=3, metrics={}),
        ]
    )
    assert aggregate.score == 2
    assert aggregate.monitor_metrics == {"latency": 12}


def test_candidate_logs_keep_only_references_to_retained_details():
    """Detailed arrays belong in records rather than every generated-node log."""
    result = EvaluationResult(
        score=2,
        metrics={"quality": 2},
        metadata={
            "evaluation_records": [{"id": "record"}],
            "measurements": [1, 2, 3],
        },
    )
    summary = result.candidate_summary()
    assert summary["metadata"] == {"evaluation_records": [{"id": "record"}]}
    assert result.metadata["measurements"] == [1, 2, 3]
    legacy = EvaluationResult(metrics={}, metadata={"detail": "legacy"})
    assert legacy.candidate_summary()["metadata"]["detail"] == "legacy"


def test_unsupported_extra_metadata_does_not_leave_running_records(monkeypatch, tmp_path):
    """Bad optional details are visible as storage errors without changing scores."""

    class Opaque:
        async def evaluate(self, cfg):
            return EvaluationResult(score=2, metrics={"quality": 2}, metadata={"opaque": object()})

    runner, algorithm = dispatcher(monkeypatch, tmp_path, Opaque(), ["first.json", "second.json"])
    result = asyncio.run(runner.dispatch_batch([algorithm]))[0]
    assert result.success and result.score == 2
    records = [json.loads(path.read_text()) for path in (tmp_path / "records").glob("*/record.json")]
    assert len(records) == 2
    assert all(record["state"] == "completed" and record["storage_state"] == "error" for record in records)
    assert len(algorithm.custom_metadata["evaluation_records"]) == 2
