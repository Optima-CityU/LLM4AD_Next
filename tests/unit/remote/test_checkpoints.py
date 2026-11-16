"""Exercise SDK-owned preparation recovery without hardware or external requests."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm4ad.gateway.sdk import TaskCheckpoints

pytestmark = pytest.mark.unit


@pytest.fixture
def prepared(tmp_path):
    """Provide one attempt and a small original input outside its workspace."""
    source = tmp_path / "input.txt"
    source.write_text("Accepted input")
    job = SimpleNamespace(id="job", attempt_id="attempt", task_id="task")
    root = tmp_path / "preparation"
    return job, source, root


def store(prepared, **kwargs):
    """Open the same SDK workspace after a simulated gateway restart."""
    job, source, root = prepared
    return TaskCheckpoints(job, root, inputs={"input.txt": source}, context=kwargs or {"version": "1"})


def test_completed_step_and_inputs_survive_restart(prepared):
    """Mutable original files cannot silently change a frozen input or rerun a completed step."""
    first = store(prepared)
    output = first.path("build/result.txt")

    def compile_input():
        output.parent.mkdir()
        output.write_bytes(first.path("input.txt").read_bytes())

    first.run("compile", compile_input, outputs=["build"], required=["build/result.txt"])
    prepared[1].write_text("Edited original")
    reopened = store(prepared)
    action = Mock(side_effect=AssertionError("Completed build must not repeat"))
    reopened.run("compile", action, outputs=["build"], required=["build/result.txt"])
    assert output.read_text() == "Accepted input"
    action.assert_not_called()


def test_interrupted_step_is_rebuilt_and_its_files_are_retained(prepared):
    """Only unfinished output is moved aside; the SDK publishes no premature checkpoint."""
    first = store(prepared)
    output = first.path("build")

    def interrupt():
        output.mkdir()
        (output / "partial.txt").write_text("Partial diagnostics")
        raise KeyboardInterrupt("Synthetic interruption")

    with pytest.raises(KeyboardInterrupt):
        first.run("compile", interrupt, outputs=["build"], required=["build/result.txt"])
    reopened = store(prepared)

    def finish():
        output.mkdir()
        (output / "result.txt").write_text("Completed")

    reopened.run("compile", finish, outputs=["build"], required=["build/result.txt"])
    retained = list((prepared[2] / ".sdk-interrupted").glob("*/build/partial.txt"))
    assert len(retained) == 1 and retained[0].read_text() == "Partial diagnostics"
    assert "compile" in json.loads(reopened.receipt.read_text())["steps"]


@pytest.mark.parametrize("target", ["input.txt", "result.txt"])
def test_tampering_cannot_be_reused(prepared, target):
    """Hash mismatches fail explicitly rather than being silently rewritten."""
    checkpoints = store(prepared)
    checkpoints.run(
        "compile", lambda: checkpoints.path("result.txt").write_text("Built"),
        outputs=["result.txt"], required=["result.txt"],
    )
    checkpoints.path(target).write_text("Tampered")
    with pytest.raises(RuntimeError, match="changed"):
        store(prepared)


def test_attempt_and_context_cannot_reuse_another_checkpoint(prepared):
    """Version changes and new attempts cannot inherit a previous build's completion."""
    store(prepared)
    with pytest.raises(RuntimeError, match="another attempt"):
        store(prepared, version="2")
    prepared[0].attempt_id = "replacement"
    with pytest.raises(RuntimeError, match="another attempt"):
        store(prepared)


@pytest.mark.parametrize("name", ["../outside", "/outside", "input.txt", ".sdk-checkpoints.json"])
def test_step_cannot_overwrite_inputs_or_escape_workspace(prepared, name):
    """Invalid output declarations are rejected before user code runs."""
    checkpoints = store(prepared)
    action = Mock()
    with pytest.raises(ValueError):
        checkpoints.run("unsafe", action, outputs=[name], required=[name])
    action.assert_not_called()


def test_step_cannot_mutate_frozen_input(prepared):
    """A completed output cannot conceal input mutation during compilation."""
    checkpoints = store(prepared)

    def change_input():
        checkpoints.path("input.txt").write_text("Mutated during build")
        checkpoints.path("result.txt").write_text("Result")

    with pytest.raises(RuntimeError, match="changed"):
        checkpoints.run("compile", change_input, outputs=["result.txt"], required=["result.txt"])
    assert not json.loads(checkpoints.receipt.read_text())["steps"]
