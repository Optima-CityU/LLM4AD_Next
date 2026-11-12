"""Qualify upstream game reports without manufacturing evolutionary fitness."""

import copy
import hashlib
import importlib.util
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("hgamey_game_ab_test", ROOT / "src/llm4ad/gateway/examples/hgamey/protocol/game_ab.py")
game = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(game)
pytestmark = pytest.mark.unit


@pytest.fixture
def trial(tmp_path):
    """Provide explicitly synthetic control measurements with no device access."""
    options = game.validate_options({"rounds": 1, "a": "native", "b": "layer", "pan": False})
    raw = {
        "pkg": options["package"],
        "a": "native",
        "b": "layer",
        "rounds": 1,
        "valid_pairs": 1,
        "dropped_arms": [],
        "metrics": {"display_fps": {"a_mean": 30, "b_mean": 29, "n_pairs": 1}, "game_fps": {"status": "not_measured"}},
    }
    library = tmp_path / "layer.so"
    library.write_bytes(b"\x7fELFsynthetic-layer")
    options["layer_path"] = str(library)
    arms = [
        {"arm": "A1", "side": "A", "pair": 1, "seq": 1, "kind": "native", "in_game": True},
        {
            "arm": "B1",
            "side": "B",
            "pair": 1,
            "seq": 2,
            "kind": "layer",
            "in_game": True,
            "layer_loaded": True,
            "layer_removed": True,
            "session_exit": 0,
            "session_stop": "completed",
        },
    ]

    for arm, fps in zip(arms, (30, 29), strict=True):
        arm["display_fps"] = fps
        make_capture(tmp_path, name=arm["arm"])
    arms[1].update(layer_path=str(library), layer_sha256=hashlib.sha256(library.read_bytes()).hexdigest())
    make_session(tmp_path, "B1")

    def save():
        (tmp_path / "report.json").write_text(json.dumps(raw))
        (tmp_path / "arms.jsonl").write_text("\n".join(json.dumps(arm) for arm in arms))

    save()
    return tmp_path, options, raw, arms, save


def test_game_qualification_is_not_candidate_fitness(trial):
    """Successful controls cannot be interpreted as evaluated candidate code."""
    path, options, *_ = trial
    report = game.summarize(path, options, True, 0)
    assert report["status"] == "PASS"
    assert report["candidate_applied"] is False
    assert report["can_claim_game_improvement"] is False
    assert "score" not in report
    assert report["unsupported_metrics"] == ["game_fps"]
    assert "comparison_game_fps" not in report["metrics"]


@pytest.mark.parametrize("value", [None, 0, -1, float("nan")])
def test_missing_or_invalid_display_measurement_fails(trial, value):
    """Exit zero and complete pairs do not imply measured game performance."""
    path, options, raw, _, save = trial
    raw["metrics"]["display_fps"]["b_mean"] = value
    save()
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


def test_unapplied_sr_cannot_pass(trial):
    """Same-size/bypassed SR is not accepted as a shader comparison."""
    path, options, _, arms, save = trial
    options["b"] = arms[1]["kind"] = "sr:1:2800x1272:0"
    arms[1]["selfsr_upscaled_in_window"] = 0
    save()
    assert "did not execute" in " ".join(game.summarize(path, options, True, 0)["errors"])


def test_missing_motion_evidence_fails_when_requested(trial):
    """A failed recording cannot silently satisfy a camera-motion trial."""
    path, options, *_ = trial
    options["pan"] = True
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


@pytest.mark.parametrize("restored,exit_code", [(False, 0), (True, 2)])
def test_incomplete_cleanup_or_execution_fails(trial, restored, exit_code):
    """Keep invalid execution distinct from the algorithm's numerical quality."""
    path, options, *_ = trial
    assert game.summarize(path, options, restored, exit_code)["status"] == "ERROR"


def test_incomplete_pair_is_rejected(trial):
    """Partial runs do not reuse surviving arms as a complete comparison."""
    path, options, raw, _, save = trial
    raw["valid_pairs"] = 0
    save()
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


def test_default_command_disables_vlm_and_verifies_world(tmp_path):
    """Game entry never requires model configuration in this executor path."""
    command = game.command_for(tmp_path, "example:5555", game.validate_options({}))
    assert command[command.index("--vlm-calls") + 1] == "0"
    assert "--verify-game-world" in command


@pytest.mark.parametrize("options", [{"a": "native; command"}, {"enter_script": "sh:command"}, {"rounds": True}, {"window": -1}])
def test_invalid_operator_options_are_rejected(options):
    """Do not pass shell-like experiment input through to the phone."""
    with pytest.raises(ValueError):
        game.validate_options(options)


def test_layer_bytes_are_frozen(tmp_path):
    """A queued trial retains its original library if the operator rebuilds."""
    source = tmp_path / "original.so"
    source.write_bytes(b"\x7fELFfixture-library-not-a-device-binary")
    folder = tmp_path / "job"
    folder.mkdir()
    frozen = {"submission": {"options": {"layer_path": str(source)}}}
    game.freeze_inputs(folder, frozen)
    before = copy.deepcopy(frozen)
    source.write_bytes(b"changed")
    assert Path(frozen["submission"]["options"]["layer_path"]).read_bytes().startswith(b"\x7fELF")
    assert frozen == before


@pytest.fixture
def candidate_trial(trial):
    """Provide distinct staged libraries and explicit per-arm source identities."""
    path, options, raw, arms, save = trial
    baseline, candidate = path / "baseline.so", path / "candidate.so"
    baseline.write_bytes(b"\x7fELFsynthetic-baseline")
    candidate.write_bytes(b"\x7fELFsynthetic-candidate")
    options.update(
        game.validate_options(
            {
                "rounds": 1,
                "a": "sr:1:2800x1272:0",
                "b": "sr:1:2800x1272:0",
                "pan": False,
                "baseline_layer_path": str(baseline),
                "layer_path": str(candidate),
                "candidate_id": "c" * 64,
                "baseline_source_sha256": "a" * 64,
                "candidate_source_sha256": "b" * 64,
            }
        )
    )
    raw.update(a=options["a"], b=options["b"])
    make_session(path, "A1")
    for arm, library, source in zip(arms, (baseline, candidate), ("a" * 64, "b" * 64), strict=True):
        arm.update(
            kind=options["a"],
            layer_loaded=True,
            layer_removed=True,
            session_exit=0,
            session_stop="completed",
            selfsr_upscaled_in_window=1200,
            layer_path=str(library),
            layer_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
            selfsr={"game_extent": [2136, 971], "swap_extent": [2800, 1272], "candidate_source_sha256": source},
        )
    save()
    return trial


def test_candidate_validation_requires_source_and_dimensions(candidate_trial):
    """A valid fixture applies the candidate without claiming performance improvement."""
    path, options, _, _, _ = candidate_trial
    report = game.summarize(path, options, True, 0)
    assert report["status"] == "PASS" and report["candidate_applied"] is True
    assert report["candidate_id"] == options["candidate_id"]
    assert report["can_claim_game_improvement"] is False


@pytest.mark.parametrize("failure", ["source", "extent", "recovery", "missing_motion"])
def test_candidate_known_invalid_evidence_fails(candidate_trial, failure):
    """Retain working rejection gates for the composite validation path."""
    path, options, _, arms, save = candidate_trial
    if failure == "source":
        arms[1]["selfsr"]["candidate_source_sha256"] = "0" * 64
    elif failure == "extent":
        arms[1]["selfsr"]["swap_extent"] = [2136, 971]
    elif failure == "recovery":
        arms[1]["recovery_unknown"] = True
    else:
        options["pan"] = True
    save()
    result = game.summarize(path, options, True, 0)
    assert result["status"] == "ERROR" and result["candidate_applied"] is False


def test_candidate_loaded_library_hash_must_match_frozen_input(candidate_trial):
    """Source metadata alone cannot establish which complete library was loaded."""
    path, options, _, arms, save = candidate_trial
    arms[1]["layer_sha256"] = "0" * 64
    save()
    report = game.summarize(path, options, True, 0)
    assert report["status"] == "ERROR" and report["candidate_applied"] is False


def test_failed_layer_session_cannot_pass(candidate_trial):
    """Layer removal and marker counters do not override a failed session exit."""
    path, options, _, arms, save = candidate_trial
    arms[1].update(session_exit=2, session_stop="failed")
    save()
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


def test_duplicate_candidate_arm_cannot_replace_baseline(candidate_trial):
    """Two B records do not prove a measured baseline/candidate pair."""
    path, options, _, arms, save = candidate_trial
    arms[0] = copy.deepcopy(arms[1])
    save()
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


@pytest.mark.parametrize("failure", ["boolean_fps", "nan_sr_count", "negative_motion"])
def test_nonmeasurement_values_cannot_pass(candidate_trial, failure):
    """Require real finite measurements rather than Python truthiness."""
    path, options, raw, arms, save = candidate_trial
    if failure == "boolean_fps":
        raw["metrics"]["display_fps"]["b_mean"] = True
    elif failure == "nan_sr_count":
        arms[1]["selfsr_upscaled_in_window"] = float("nan")
    else:
        options["pan"] = True
        for arm in arms:
            arm["pan_moving_frames"] = -1
    save()
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


def make_session(evidence, name):
    """Retain the actual layer owner's terminal control fields."""
    folder = evidence / "sessions" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "session-control.json").write_text(
        json.dumps(
            {
                "phase": "closed",
                "accepting": False,
                "stop_reason": "requested",
                "recovery": {
                    "status": "reported_complete",
                    "error": None,
                    "game_state_restored": False,
                    "job_recovery_complete": False,
                },
            }
        )
    )


def make_capture(evidence, *, name="A1", complete=True):
    """Create synthetic state, verified disk checks and the final completion report.

    Args:
        evidence: Root directory for this trial's evidence.
        name: Arm owning the capture.
        complete: Whether native recovery completion was reported.

    Returns:
        The capture session directory.
    """
    session = evidence / "onboard" / f"{name}_world" / "capture-fixture"
    session.mkdir(parents=True)
    (session / "session.json").write_text(
        json.dumps(
            {
                "schema": "phonefarm/capture-session@1",
                "stage": "active",
                "serial": "fixture:5555",
                "transport_uncertain": False,
                "write_pending": None,
            }
        )
    )
    recovery = {
        "schema": "phonefarm/capture-recovery@1",
        "status": "verified",
        "error": None,
        "serial": "fixture:5555",
        "session": str(session),
        "evidence": str(session / "recovery-100-1000.json"),
        "checks": {
            "scope": "capture_input_stayon_and_exit_policy",
            "game_state_restored": False,
            "stayon_restored": True,
            "input_used": False,
            "input_absent": None,
            "exit_policy": {"requested": "keep_running", "app_stopped": None, "screen_asleep": None},
        },
    }
    (session / "recovery-100-1000.json").write_text(json.dumps(recovery))
    (session / "report-100-1001.json").write_text(
        json.dumps(
            {
                "ok": complete,
                "ready_only": True,
                "frames": 1,
                "stop_reason": "target_reached",
                "serial": "fixture:5555",
                "session": str(session),
                "recovery": recovery | {"status": "reported_complete" if complete else "required"},
            }
        )
    )
    return session


def test_completed_capture_is_confirmed(tmp_path):
    """Disk recovery stays verified while the final report reports completion."""
    make_capture(tmp_path)
    assert game.captures_confirmed(tmp_path) is True


@pytest.mark.parametrize("field, value", [("transport_uncertain", True), ("write_pending", {"operation": "capture"})])
def test_capture_pending_transport_is_not_confirmed(tmp_path, field, value):
    """Do not erase explicit transport uncertainty with a verified receipt."""
    session = make_capture(tmp_path)
    state = json.loads((session / "session.json").read_text())
    state[field] = value
    (session / "session.json").write_text(json.dumps(state))
    assert game.captures_confirmed(tmp_path) is False


def test_verified_receipt_does_not_override_failed_completion(tmp_path):
    """A failed complete_recovery call leaves verified disk checks but no completion."""
    make_capture(tmp_path, complete=False)
    assert game.captures_confirmed(tmp_path) is False


def test_latest_failed_recovery_cannot_be_hidden_by_pid_sort(tmp_path):
    """A later recovery from PID 10 must override older success from PID 9."""
    session = make_capture(tmp_path, complete=False)
    for path in session.glob("recovery-*.json"):
        path.unlink()
    (session / "recovery-9-100.json").write_text(json.dumps({"status": "verified", "at": "2026-11-06T01:00:00Z"}))
    (session / "recovery-10-200.json").write_text(json.dumps({"status": "required", "at": "2026-11-06T02:00:00Z"}))
    assert game.captures_confirmed(tmp_path) is False


def test_orphaned_capture_artifacts_are_not_confirmed(tmp_path):
    """Existing capture evidence with missing ownership state remains uncertain."""
    session = make_capture(tmp_path, complete=False)
    (session / "session.json").unlink()
    assert game.captures_confirmed(tmp_path) is False


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    """Exercise wrapper lifecycle using host-only files and fake processes."""
    options = game.validate_options({"a": "native", "b": "native", "rounds": 1, "pan": False})
    frozen = {"submission": {"options": options}, "device": {"endpoint": "fixture:5555", "expected_serial": "fixture-serial"}}
    (tmp_path / "input.json").write_text(json.dumps(frozen))
    (tmp_path / "job.json").write_text(json.dumps({"state": "running"}))
    values = {"boot_id": "fixture-boot", "screen_off_timeout": "60000", "refresh_rate_mode": "null"}

    def shell(command):
        if command == "getprop":
            return "[ro.serialno]: [fixture-serial]"
        if command == "getprop ro.serialno":
            return "fixture-serial"
        if command == "cat /proc/sys/kernel/random/boot_id":
            return values["boot_id"]
        if command.startswith("settings get system "):
            return values[command.rsplit(" ", 1)[1]]
        if command.startswith("cmd game list-configs "):
            return "no intervention"
        if command == "settings get global game_gfrc_mode":
            return "null"
        return ""

    dev = Mock()
    dev.shell.side_effect = shell
    upstream = SimpleNamespace(
        Dev=Mock(return_value=dev),
        SR_PROP="debug.knobs.selfsr",
        UNLIMIT_PROP="debug.knobs.unlimit",
        FP_PROP="vendor.perf.framepacing.enable",
        SR_OPTS=(),
        IP_PROPS=(),
        IP_TUNE=(),
        PANEL_KEYS=("refresh_rate_mode",),
        current_mode=Mock(return_value="standard"),
        layer_absent=Mock(return_value=(True, {})),
    )
    monkeypatch.setattr(game, "load_upstream", lambda: upstream)

    monkeypatch.setenv("PF_LOCK_FDS", "17")
    monkeypatch.setenv("PF_ROOT", "/fixture/phonefarm")
    monkeypatch.setattr(game.os, "fstat", Mock())
    monkeypatch.setattr(game.time, "sleep", Mock())
    child = Mock(returncode=0)
    child.poll.return_value = 0

    def launch(*unused, **kwargs):
        evidence = tmp_path / "evidence"
        (evidence / "restore.json").write_text(json.dumps({"layer_absent": True, "layer_info": {}}))
        (evidence / "report.json").write_text(
            json.dumps(
                {
                    "pkg": options["package"],
                    "a": "native",
                    "b": "native",
                    "rounds": 1,
                    "valid_pairs": 1,
                    "dropped_arms": [],
                    "metrics": {"display_fps": {"a_mean": 30, "b_mean": 30, "n_pairs": 1}},
                }
            )
        )
        (evidence / "arms.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {
                        "arm": f"{side}1",
                        "kind": "native",
                        "side": side,
                        "pair": 1,
                        "seq": index,
                        "in_game": True,
                        "display_fps": 30,
                    }
                )
                for index, side in enumerate(("A", "B"), 1)
            )
        )
        for side in ("A", "B"):
            make_capture(evidence, name=f"{side}1")
        if values.get("cancel_on_launch"):
            (tmp_path / "job.json").write_text(json.dumps({"state": "cancel_requested"}))
        return child

    monkeypatch.setattr(game.subprocess, "Popen", launch)
    return SimpleNamespace(folder=tmp_path, child=child, dev=dev, values=values, upstream=upstream)


def test_cancelled_running_child_never_returns_pass(lifecycle, monkeypatch):
    """Wait for the direct child and retain ABORT after a cooperative stop."""
    lifecycle.values["cancel_on_launch"] = True
    lifecycle.child.poll.side_effect = [None, 0, 0]
    assert game.run(lifecycle.folder) == 2
    lifecycle.child.send_signal.assert_called_once_with(signal.SIGTERM)
    assert json.loads((lifecycle.folder / "result.json").read_text())["status"] == "ABORT"


def test_cleanup_grace_expiry_does_not_restore_over_live_child(lifecycle, monkeypatch):
    """Keep restoration uncertain when the direct child outlives cancellation grace."""
    lifecycle.values["cancel_on_launch"] = True
    lifecycle.child.poll.return_value = None
    monkeypatch.setattr(game.time, "monotonic", Mock(side_effect=[0, 1, 2, 302]))
    assert game.run(lifecycle.folder) == 2
    report = json.loads((lifecycle.folder / "result.json").read_text())
    assert report["status"] == "ABORT" and report["conditions_observed"]["restored"] is False
    assert any("cleanup still running" in error for error in report["errors"])
    assert not any(
        call.args[0].startswith(("settings put", "settings delete", "setprop")) for call in lifecycle.dev.shell.call_args_list
    )


def test_cancellation_at_child_exit_cannot_report_pass(lifecycle):
    """A queued cancellation remains visible when the child exits before polling."""
    lifecycle.values["cancel_on_launch"] = True
    assert game.run(lifecycle.folder) != 0
    assert json.loads((lifecycle.folder / "result.json").read_text())["status"] == "ABORT"


def test_unreadable_boot_identity_cannot_certify_restoration(lifecycle):
    """A failed boot-id read must not be treated as same-boot evidence."""
    lifecycle.values["boot_id"] = ""
    game.run(lifecycle.folder)
    report = json.loads((lifecycle.folder / "result.json").read_text())
    assert report["conditions_observed"]["restored"] is False
    assert report["status"] != "PASS"


def test_unresolved_capture_prevents_wrapper_restoration_writes(lifecycle, monkeypatch):
    """Do not restore settings while a capture worker may still own them."""
    monkeypatch.setattr(game, "captures_confirmed", lambda unused: False)
    assert game.run(lifecycle.folder) == 2
    assert not any(
        call.args[0].startswith(("settings put", "settings delete", "setprop")) for call in lifecycle.dev.shell.call_args_list
    )


def test_completed_native_run_returns_pass(lifecycle):
    """A complete run remains valid with real-shaped capture and restoration data."""
    assert game.run(lifecycle.folder) == 0
    report = json.loads((lifecycle.folder / "result.json").read_text())
    assert report["status"] == "PASS" and report["conditions_observed"]["restored"] is True


def test_prelaunch_cancellation_does_not_start_child(lifecycle, monkeypatch):
    """Retain queued cancellation before the upstream runner mutates the phone."""
    launch = Mock(side_effect=AssertionError("Cancelled job must not launch"))
    monkeypatch.setattr(game.subprocess, "Popen", launch)
    (lifecycle.folder / "job.json").write_text(json.dumps({"state": "cancel_requested"}))
    assert game.run(lifecycle.folder) == 2
    launch.assert_not_called()
    assert json.loads((lifecycle.folder / "result.json").read_text())["status"] == "ABORT"


@pytest.mark.parametrize("stage", ["snapshot", "cleanup", "write", "readback"])
def test_transport_exception_retains_error_result(lifecycle, stage):
    """Neither snapshot nor finally transport failures may lose result.json."""
    original = lifecycle.dev.shell.side_effect
    calls = 0

    def shell(command):
        nonlocal calls
        if command == "cat /proc/sys/kernel/random/boot_id":
            calls += 1
            if (stage == "snapshot" and calls == 1) or (stage == "cleanup" and calls == 2):
                raise TimeoutError("synthetic transport lost")
        if stage == "write" and command.startswith("settings put"):
            raise TimeoutError("synthetic transport lost")
        if stage == "readback" and command == "getprop" and calls >= 2:
            return ""
        return original(command)

    lifecycle.dev.shell.side_effect = shell
    assert game.run(lifecycle.folder) == 2
    report = json.loads((lifecycle.folder / "result.json").read_text())
    assert report["status"] == "ERROR" and report["conditions_observed"]["restored"] is False
    assert report["errors"] and (lifecycle.folder / "game-restoration.json").exists()


def test_cancellation_during_restoration_remains_abort(lifecycle):
    """Cancellation after child exit still prevents a published PASS."""
    original = lifecycle.dev.shell.side_effect

    def shell(command):
        if command.startswith("settings put"):
            (lifecycle.folder / "job.json").write_text(json.dumps({"state": "cancel_requested"}))
        return original(command)

    lifecycle.dev.shell.side_effect = shell
    assert game.run(lifecycle.folder) == 2
    assert json.loads((lifecycle.folder / "result.json").read_text())["status"] == "ABORT"


@pytest.mark.parametrize("failure", ["boot_changed", "live_layer", "pending_capture"])
def test_unconfirmed_owner_blocks_outer_writes(lifecycle, monkeypatch, failure):
    """Do not compete with another boot, native session or a capture worker."""
    original = game.subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        if failure == "boot_changed":
            lifecycle.values["boot_id"] = "different-boot"
        elif failure == "live_layer":
            folder = lifecycle.folder / "evidence/sessions/A1"
            folder.mkdir(parents=True)
            (folder / "session-control.json").write_text(json.dumps({"phase": "ready", "accepting": True}))
        else:
            (lifecycle.folder / "evidence/onboard/A1_world/recovery-pending.json").write_text("{}")
        return child

    monkeypatch.setattr(game.subprocess, "Popen", launch)
    assert game.run(lifecycle.folder) == 2
    report = json.loads((lifecycle.folder / "result.json").read_text())
    assert report["conditions_observed"]["restored"] is False
    assert not any(
        call.args[0].startswith(("settings put", "settings delete", "setprop")) for call in lifecycle.dev.shell.call_args_list
    )


@pytest.mark.parametrize("failure", ["serial", "evidence", "missing_report", "pending", "stayon", "input", "json"])
def test_capture_receipts_cannot_hide_invalid_state(tmp_path, failure):
    """Require matching completion evidence and successful owned-resource checks."""
    session = make_capture(tmp_path)
    report_path = session / "report-100-1001.json"
    report = json.loads(report_path.read_text())
    if failure == "serial":
        report["serial"] = "another-device"
    elif failure == "evidence":
        report["recovery"]["evidence"] = str(session / "recovery-9-999.json")
    elif failure in ("stayon", "input"):
        recovery_path = session / "recovery-100-1000.json"
        recovery = json.loads(recovery_path.read_text())
        recovery["checks"].update(
            {"stayon_restored": False} if failure == "stayon" else {"input_used": True, "input_absent": False}
        )
        recovery_path.write_text(json.dumps(recovery))
        report["recovery"] = recovery | {"status": "reported_complete"}
    report_path.write_text(json.dumps(report))
    if failure == "missing_report":
        report_path.unlink()
    elif failure == "pending":
        (session.parent / "recovery-pending.json").write_text("{}")
    elif failure == "json":
        (session / "session.json").write_text("truncated")
    assert game.captures_confirmed(tmp_path) is False


@pytest.mark.parametrize("failure", ["in_game", "kind", "pair", "seq", "layer_loaded", "layer_removed", "path", "display_fps"])
def test_required_arm_evidence_cannot_be_missing(candidate_trial, failure):
    """Aggregate success never substitutes for a specific arm's required evidence."""
    path, options, _, arms, save = candidate_trial
    arms[1].pop("layer_path" if failure == "path" else failure)
    save()
    assert game.summarize(path, options, True, 0)["status"] == "ERROR"


@pytest.mark.parametrize("failure", ["report", "arms", "metrics", "missing_library", "capture_frame", "control"])
def test_damaged_artifacts_produce_serializable_error(candidate_trial, failure):
    """Reject partial artifacts while retaining an inspectable result object."""
    path, options, raw, _, save = candidate_trial
    if failure == "report":
        (path / "report.json").write_text("truncated")
    elif failure == "arms":
        (path / "arms.jsonl").write_text("null\n")
    elif failure == "metrics":
        raw["metrics"]["display_fps"] = []
        save()
    elif failure == "missing_library":
        Path(options["layer_path"]).unlink()
    elif failure == "capture_frame":
        report_path = next(path.glob("onboard/B1_world/capture-*/report-*.json"))
        report = json.loads(report_path.read_text())
        report.update(ok=False, frames=0, stop_reason="not_ready")
        report_path.write_text(json.dumps(report))
    else:
        (path / "sessions/B1/session-control.json").unlink()
    result = game.summarize(path, options, True, 0)
    assert result["status"] == "ERROR" and result["candidate_applied"] is False
    json.dumps(result, allow_nan=False)


def test_nonfinite_report_still_serializes(candidate_trial):
    """Invalid upstream floats remain in the raw artifact, not invalid result JSON."""
    path, options, raw, _, save = candidate_trial
    raw["metrics"]["display_fps"]["b_mean"] = float("nan")
    save()
    result = game.summarize(path, options, True, 0)
    assert result["status"] == "ERROR"
    json.dumps(result, allow_nan=False)


def test_deadline_at_child_exit_cannot_report_pass(lifecycle, monkeypatch):
    """A child finishing after its deadline cannot bypass the polling deadline."""
    monkeypatch.setattr(game.time, "monotonic", Mock(side_effect=[0, 1801]))
    assert game.run(lifecycle.folder) == 2
    assert json.loads((lifecycle.folder / "result.json").read_text())["status"] == "ABORT"


@pytest.mark.parametrize(
    "proof",
    [
        "confirmed",
        "unknown",
        "intent",
        "missing",
        "truncated",
        "lost_arm",
        "nested_unknown",
        "restore_missing",
        "restore_unknown",
        "restore_deferred",
        "restore_truncated",
        "restore_unconfirmed",
    ],
)
def test_mali_cleanup_proof_gates_native_outer_writes(lifecycle, monkeypatch, proof):
    """A finished native arm cannot release unconfirmed collector ownership."""
    original = game.subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        evidence = lifecycle.folder / "evidence"
        # Confirmed capture receipts and no layer sessions isolate the Mali guard.
        assert not (evidence / "sessions").exists()
        for name in ("A1", "B1"):
            folder = evidence / "traces" / f"{name}_mali"
            folder.mkdir(parents=True)
            (folder / "mali-cleanup.json").write_text(
                json.dumps(
                    {
                        "cleanup_confirmed": True,
                        "recovery_unknown": False,
                        "metrics": {},
                        "provenance": {},
                    }
                )
            )
        receipt = folder / "mali-cleanup.json"
        if proof in ("unknown", "lost_arm"):
            receipt.write_text(json.dumps({"cleanup_confirmed": False, "recovery_unknown": True}))
        elif proof == "intent":
            receipt.write_text(json.dumps({"submitted": False, "instance": "synthetic-private-trace"}))
        elif proof == "missing":
            receipt.unlink()
        elif proof == "truncated":
            receipt.write_text('{"cleanup_confirmed":')
        if proof == "lost_arm":
            (evidence / "arms.jsonl").unlink()
        elif proof == "nested_unknown":
            path = evidence / "arms.jsonl"
            arms = [json.loads(line) for line in path.read_text().splitlines()]
            arms[1]["mali_telemetry"] = {"cleanup_confirmed": False, "recovery_unknown": True}
            path.write_text("\n".join(json.dumps(arm) for arm in arms))
        restore = evidence / "restore.json"
        if proof == "restore_missing":
            restore.unlink()
        elif proof == "restore_unknown":
            restore.write_text(json.dumps({"recovery_unknown": True, "restoration_deferred": True}))
        elif proof == "restore_deferred":
            restore.write_text(json.dumps({"layer_absent": True, "restoration_deferred": True}))
        elif proof == "restore_truncated":
            restore.write_text('{"layer_absent":')
        elif proof == "restore_unconfirmed":
            restore.write_text("{}")
        return child

    monkeypatch.setattr(game.subprocess, "Popen", launch)
    confirmed = proof == "confirmed"
    assert game.run(lifecycle.folder) == (0 if confirmed else 2)
    report = json.loads((lifecycle.folder / "result.json").read_text())
    assert report["conditions_observed"]["restored"] is confirmed
    writes = [
        call
        for call in lifecycle.dev.shell.call_args_list
        if call.args[0].startswith(("settings put", "settings delete", "setprop"))
    ]
    assert bool(writes) is confirmed
