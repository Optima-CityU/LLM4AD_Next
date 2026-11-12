"""Exercise upstream game-entry adaptation without contacting a device."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

UPSTREAM = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream"
COMMIT = "3ea2d8cfb4dfbd76690ee12b25ad8afd9bcb06c6"
ORIGINAL_HASHES = {
    "selfsr/selfsr_ab.py": "d54bd4df00ec1b6241724f8e65c658ef0c14a74b19698d140c1de79bc38abeba",
    "gfrc/gfrc_ab.py": "a896de1c08c6e13570a2cc38c8b070acd40dd11f4b033d662a9744715b31d27d",
}
pytestmark = pytest.mark.unit


def test_failed_video_transfer_keeps_owned_remote_evidence(runner, args, tmp_path, monkeypatch):
    """A failed download must not destroy the only complete recording."""
    (tmp_path / "traces").mkdir()
    error = subprocess.TimeoutExpired("fixture-video-transfer", 120)
    dev = SimpleNamespace(
        shell=Mock(return_value="Physical size: 2800x1272"),
        adb=Mock(return_value="Physical size: 2800x1272"), su=Mock(),
    )
    recorder = Mock(returncode=0)
    recorder.poll.return_value = 0
    monkeypatch.setattr(runner.subprocess, "Popen", Mock(return_value=recorder))
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(runner, "download_root_file", Mock(side_effect=error), raising=False)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.pan_probe(dev, args, "A1", str(tmp_path))
    assert not any("rm -f" in call.args[0] for call in dev.su.call_args_list)


@pytest.mark.parametrize("output, expected", [
    ("Physical size: 1272x2800\n", (2800, 1272)),
    ("Physical size: 1272x2800\nOverride size: 1080x2400\n", (2400, 1080)),
    ("1272x2800\n", (2800, 1272)),
])
def test_pan_resolution_uses_only_checked_observed_dimensions(runner, tmp_path, output, expected):
    """Honor real physical/override output without a hard-coded resolution fallback."""
    dev = SimpleNamespace(adb=Mock(return_value=output))
    receipt = tmp_path / "traces/A1_wm-size.json"
    assert runner.pan_resolution(dev, receipt) == expected
    dev.adb.assert_called_once_with("shell wm size", timeout=10, check=True)
    assert json.loads(receipt.read_text())["attempts"][0]["stdout"] == output


@pytest.mark.parametrize("failure", ["", "error: device offline\n", RuntimeError("ADB: device offline"),
                                      subprocess.TimeoutExpired("wm size", 10)])
def test_pan_resolution_retries_transient_transport_without_replaying_measurement(runner, tmp_path, monkeypatch, failure):
    """Retry only the read-only probe and retain the original empty/error response."""
    dev = SimpleNamespace(adb=Mock(side_effect=[failure, "", "Physical size: 1272x2800\n"]))
    sleep = Mock()
    monkeypatch.setattr(runner.time, "sleep", sleep)
    receipt = tmp_path / "traces/A1_wm-size.json"
    assert runner.pan_resolution(dev, receipt) == (2800, 1272)
    assert [call.args[0] for call in dev.adb.call_args_list] == ["shell wm size", "wait-for-device", "shell wm size"]
    assert all(call.kwargs == {"timeout": 10, "check": True} for call in dev.adb.call_args_list)
    observations = json.loads(receipt.read_text())["attempts"]
    assert len(observations) == 2 and observations[0]["error"]
    if isinstance(failure, str):
        assert observations[0]["stdout"] == failure
    else:
        assert observations[0]["error_type"] == type(failure).__name__
    sleep.assert_called_once_with(1)


def test_pan_resolution_recovers_after_wait_transport_error(runner, tmp_path, monkeypatch):
    """A failed wait is diagnostic; a later real resolution may still recover the probe."""
    dev = SimpleNamespace(adb=Mock(side_effect=["", RuntimeError("ADB wait still offline"), "Physical size: 1272x2800\n"]))
    monkeypatch.setattr(runner.time, "sleep", Mock())
    receipt = tmp_path / "traces/A1_wm-size.json"
    assert runner.pan_resolution(dev, receipt) == (2800, 1272)
    assert json.loads(receipt.read_text())["attempts"][0]["wait_error"] == "ADB wait still offline"


@pytest.mark.parametrize("output", ["", "0x2800", "Physical size: 1272x0", "ADB error mentions 1272x2800"])
def test_unknown_pan_resolution_stops_before_recording_or_input(runner, args, tmp_path, monkeypatch, output):
    """Unavailable dimensions remain an execution failure, never invented pan evidence or a score."""
    dev = SimpleNamespace(adb=Mock(return_value=output), shell=Mock(), su=Mock())
    recorder = Mock(side_effect=AssertionError("Recording must not start without a resolution"))
    monkeypatch.setattr(runner.subprocess, "Popen", recorder)
    monkeypatch.setattr(runner.time, "sleep", Mock())
    with pytest.raises(RuntimeError, match="Pan resolution unavailable"):
        runner.pan_probe(dev, args, "A1", str(tmp_path))
    calls = dev.adb.call_args_list
    assert sum(call.args[0] == "shell wm size" for call in calls) == 3
    assert sum(call.args[0] == "wait-for-device" for call in calls) == 2
    recorder.assert_not_called()
    dev.shell.assert_not_called()
    dev.su.assert_not_called()
    receipt = json.loads((tmp_path / "traces/A1_wm-size.json").read_text())
    assert len(receipt["attempts"]) == 3
    assert not (tmp_path / "traces/A1_pan.mp4.transfer.json").exists()


@pytest.fixture
def runner(monkeypatch):
    """Load the vendored script with every device entry point blocked."""
    monkeypatch.syspath_prepend(str(UPSTREAM / "gfrc"))
    helper_spec = importlib.util.spec_from_file_location("gfrc_ab", UPSTREAM / "gfrc/gfrc_ab.py")
    helper = importlib.util.module_from_spec(helper_spec)
    monkeypatch.setitem(sys.modules, "gfrc_ab", helper)
    helper_spec.loader.exec_module(helper)
    spec = importlib.util.spec_from_file_location("hgamey_game_upstream", UPSTREAM / "selfsr/selfsr_ab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "Dev", Mock(side_effect=AssertionError("Unexpected device access")))
    monkeypatch.setattr(module.subprocess, "run", Mock(side_effect=AssertionError("Unexpected process")))
    monkeypatch.setattr(module, "lock_fds", lambda: (17,))
    return module


@pytest.fixture
def args():
    """Provide a minimal frozen plan for entry and child-process calls."""
    return SimpleNamespace(
        phonefarm="/fixture/phonefarm",
        serial="fixture-wireless-device",
        pkg="com.miHoYo.Yuanshen",
        enter_script=None,
        vlm_calls=0,
        verify_game_world=False,
        settle=20,
        window=10,
        power=False,
        pan=False,
    )


def capture_result(**changes):
    """Return the actual capture summary shape with optional failure fields."""
    report = {
        "ok": True,
        "ready_only": True,
        "frames": 1,
        "stop_reason": "target_reached",
        "recovery": {"status": "reported_complete"},
    }
    report.update(changes)
    return subprocess.CompletedProcess([], 0, json.dumps(report), "")


@pytest.mark.parametrize("cli, expected", [([], (0, False)), (["--vlm-calls", "3", "--verify-game-world"], (3, True))])
def test_cli_defaults_disable_vlm(runner, monkeypatch, cli, expected):
    """Parse the real CLI and stop before acquiring a device lease."""
    parsed = []
    original = argparse.ArgumentParser.parse_args

    def record_args(parser, *positional, **keywords):
        value = original(parser, *positional, **keywords)
        parsed.append(value)
        return value

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", record_args)
    monkeypatch.setattr(sys, "argv", ["selfsr_ab.py", "run", "--serial", "fixture", "--out", "unused", *cli])
    monkeypatch.setattr(runner, "lock_fds", Mock(side_effect=InterruptedError("stop before lease")))
    with pytest.raises(InterruptedError, match="stop before lease"):
        runner.main()
    assert (parsed[0].vlm_calls, parsed[0].verify_game_world) == expected


def test_negative_vlm_budget_rejected_before_device_access(runner, monkeypatch):
    """Reject an invalid call budget before lease or device work begins."""
    monkeypatch.setattr(sys, "argv", ["selfsr_ab.py", "run", "--serial", "fixture", "--out", "unused", "--vlm-calls", "-1"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    runner.subprocess.run.assert_not_called()


@pytest.mark.parametrize("cold, budget", [(True, 0), (False, 3)])
def test_onboard_propagates_vlm_budget_and_preserves_launch_policy(runner, args, cold, budget, tmp_path):
    """Keep upstream launch semantics while explicitly controlling VLM use."""
    args.vlm_calls = budget
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 0, "", "entry diagnostic")
    assert runner.onboard(args, str(tmp_path), "A1", cold) == (0, "entry diagnostic")
    command = runner.subprocess.run.call_args.args[0]
    assert command[1] == "onboard"
    assert command[command.index("--vlm-calls") + 1] == str(budget)
    assert ("--no-cold" in command) is (not cold)
    assert runner.subprocess.run.call_args.kwargs["pass_fds"] == (17,)
    assert runner.subprocess.run.call_count == 1


def test_onboard_verifies_world_after_success(runner, args, tmp_path):
    """Require a bounded capture after successful entry when opted in."""
    args.verify_game_world = True
    runner.subprocess.run.side_effect = [subprocess.CompletedProcess([], 0, "", ""), capture_result()]
    assert runner.onboard(args, str(tmp_path), "B2", False) == (0, "")
    command = runner.subprocess.run.call_args.args[0]
    assert command == [
        args.phonefarm,
        "capture",
        "--serial",
        args.serial,
        "--out",
        str(tmp_path / "onboard/B2_world"),
        "--ready-only",
        "--json",
        "--screenshot-timeout-ms",
        "45000",
        "--timeout-s",
        "120",
    ]
    assert runner.subprocess.run.call_args.kwargs["pass_fds"] == (17,)
    assert runner.subprocess.run.call_args.kwargs["timeout"] == 180


@pytest.mark.parametrize("scripted", [False, True])
def test_entry_failure_does_not_run_capture(runner, args, monkeypatch, scripted, tmp_path):
    """Preserve entry failures without treating a later capture as success."""
    args.verify_game_world = True
    if scripted:
        args.enter_script = "s:1"
        monkeypatch.setattr(runner, "enter_by_script", Mock(return_value=(3, "entry failed")))
    else:
        runner.subprocess.run.side_effect = None
        runner.subprocess.run.return_value = subprocess.CompletedProcess([], 3, "", "entry failed")
    assert runner.onboard(args, str(tmp_path), "A1", True) == (3, "entry failed")
    assert runner.subprocess.run.call_count == (0 if scripted else 1)


@pytest.mark.parametrize("ready", [False, True])
def test_fixed_entry_script_always_requires_world_verification(runner, args, monkeypatch, ready, tmp_path):
    """Foreground success alone cannot admit a fixed-tap arm to measurement."""
    args.enter_script = "s:1;t:0.5,0.5"
    enter = Mock(return_value=(0, ""))
    monkeypatch.setattr(runner, "enter_by_script", enter)
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = capture_result(
        ok=ready, frames=int(ready), stop_reason="target_reached" if ready else "not_ready"
    )
    code, error = runner.onboard(args, str(tmp_path), "A1", True)
    assert (code == 0) is ready
    assert bool(error) is (not ready)
    enter.assert_called_once_with(args, str(tmp_path), "A1", True)
    assert runner.subprocess.run.call_args.args[0][1] == "capture"
    assert runner.subprocess.run.call_count == 1


@pytest.mark.parametrize(
    "report",
    [
        {"frames": 0},
        {"frames": True},
        {"frames": "1"},
        {"frames": None, "kept": 1},
        {"stop_reason": "not_ready"},
        {"ok": False},
        {"ready_only": False},
    ],
)
def test_world_verification_rejects_incomplete_evidence(runner, args, report, tmp_path):
    """Require the actual frame count and terminal reason, even on exit zero."""
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = capture_result(**report)
    code, error = runner.verify_game_world(args, str(tmp_path), "A1")
    assert code != 0 and "World verification failed" in error


@pytest.mark.parametrize("stdout", ["not json", "[]", "null"])
def test_world_verification_rejects_malformed_summary(runner, args, stdout, tmp_path):
    """Malformed output cannot be mistaken for world readiness."""
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 0, stdout, "capture diagnostic")
    code, error = runner.verify_game_world(args, str(tmp_path), "A1")
    assert code != 0 and "World verification" in error


def test_terminal_capture_failure_does_not_follow_self_referencing_report(runner, args, tmp_path):
    """Preserve the actual recovery failure instead of looping over its report."""
    owned = tmp_path / "onboard/A1_world"
    owned.mkdir(parents=True)
    report_path = owned / "capture-result.json"
    report = {"ok": False, "error": "capture command/input creation is unresolved",
              "recovery": {"status": "required"}, "report_path": str(report_path)}
    report_path.write_text(json.dumps(report))
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 2, json.dumps(report), "")
    code, error = runner.verify_game_world(args, str(tmp_path), "A1")
    assert code == runner.RECOVERY_UNKNOWN
    assert "not reported_complete" in error
    assert "input creation is unresolved" in error


def test_world_verification_rejects_failed_capture_even_with_success_fields(runner, args, tmp_path):
    """A failed capture process invalidates otherwise successful summary fields."""
    result = capture_result()
    result.returncode = 2
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = result
    assert runner.verify_game_world(args, str(tmp_path), "A1")[0] == 2


@pytest.mark.parametrize("failure", [OSError("missing phonefarm"), subprocess.TimeoutExpired("capture", 120)])
def test_world_verification_reports_process_failure(runner, args, failure, tmp_path):
    """Return a failed arm diagnostic on missing tools or capture timeout."""
    runner.subprocess.run.side_effect = failure
    code, error = runner.verify_game_world(args, str(tmp_path), "A1")
    assert code != 0 and "capture failed" in error


def test_world_verification_does_not_verify_another_package(runner, args, tmp_path):
    """The Genshin-specific capture command cannot validate another game."""
    args.pkg = "com.example.othergame"
    code, error = runner.verify_game_world(args, str(tmp_path), "A1")
    assert code != 0 and "capture has no package option" in error
    runner.subprocess.run.assert_not_called()


@pytest.mark.parametrize("verify", [False, True])
def test_locked_child_inherits_entry_flags(runner, args, verify, tmp_path):
    """Carry entry policy into plain arms executed by the leased child."""
    args.vlm_calls = 2
    args.verify_game_world = verify
    args.enter_script = "s:1;t:0.5,0.5"
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 0, 'ARM_JSON {"in_game": true}\n', "")
    assert runner.make_locked(args, str(tmp_path), "standard")("plain", "A1", "native") == {"in_game": True}
    command = runner.subprocess.run.call_args.args[0]
    assert "_locked" in command
    assert command[command.index("--vlm-calls") + 1] == "2"
    assert ("--verify-game-world" in command) is verify
    assert command[command.index("--enter-script") + 1] == args.enter_script
    assert runner.subprocess.run.call_args.kwargs["pass_fds"] == (17,)


@pytest.mark.parametrize("shape", ["inline", "report_path", "worker_file"])
def test_nested_capture_needs_world_frames_and_completed_recovery(runner, args, tmp_path, shape):
    """Accept nested evidence only through the completed capture envelope."""
    session = tmp_path / "onboard/A1_world/capture-fixture"
    session.mkdir(parents=True)
    world = json.loads(capture_result().stdout)
    world.pop("recovery")
    envelope = {"ok": True, "session": str(session), "recovery": {"status": "reported_complete"}}
    if shape == "inline":
        envelope["report"] = world
    elif shape == "report_path":
        report_path = session / "report-fixture.json"
        report_path.write_text(json.dumps({"report": world, "recovery": {"status": "reported_complete"}}))
        envelope["report_path"] = str(report_path)
    else:
        (session / "worker-result.json").write_text(
            json.dumps(
                {
                    "schema": "phonefarm/capture-worker-result@1",
                    "exit_code": 0,
                    "error": None,
                    "report": world,
                }
            )
        )
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(envelope), "")
    assert runner.verify_game_world(args, str(tmp_path), "A1") == (0, "")


@pytest.mark.parametrize("recovery", [None, {}, {"status": "required"}, {"status": "verified"}])
@pytest.mark.parametrize("nested", [False, True])
def test_incomplete_capture_recovery_is_unknown(runner, args, recovery, nested, tmp_path):
    """A world frame must not override missing or unfinished recovery."""
    report = json.loads(capture_result(recovery=recovery).stdout)
    if nested:
        report = {"ok": True, "recovery": {"status": "reported_complete"}, "report": report}
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(report), "")
    code, error = runner.verify_game_world(args, str(tmp_path), "A1")
    assert code == runner.RECOVERY_UNKNOWN
    assert "recovery" in error.lower()


@pytest.mark.parametrize("failure", ["worker_failed", "not_ready", "outside_arm", "missing_recovery"])
def test_worker_artifact_alone_cannot_prove_success(runner, args, tmp_path, failure):
    """Reject failed workers, unrelated artifacts and missing recovery evidence."""
    session = tmp_path / ("unrelated" if failure == "outside_arm" else "onboard/A1_world") / "capture-fixture"
    session.mkdir(parents=True)
    world = json.loads(capture_result().stdout)
    world.pop("recovery")
    if failure == "not_ready":
        world.update(ok=False, frames=0, stop_reason="not_ready")
    (session / "worker-result.json").write_text(
        json.dumps(
            {
                "schema": "phonefarm/capture-worker-result@1",
                "exit_code": 2 if failure == "worker_failed" else 0,
                "error": None,
                "report": world,
            }
        )
    )
    envelope = {"ok": True, "session": str(session)}
    if failure != "missing_recovery":
        envelope["recovery"] = {"status": "reported_complete"}
    runner.subprocess.run.side_effect = None
    runner.subprocess.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(envelope), "")
    assert runner.verify_game_world(args, str(tmp_path), "A1")[0] != 0


@pytest.fixture
def main_without_device(runner, monkeypatch, tmp_path):
    """Run the actual arm loop against mocked entry and device transport."""
    dev = Mock()
    dev.shell.side_effect = lambda command, **kwargs: "no intervention" if "list-configs" in command else ""
    dev.prop.return_value = ""
    dev.su.return_value = ""
    monkeypatch.setattr(runner, "Dev", Mock(return_value=dev))
    monkeypatch.setattr(runner, "current_mode", lambda *args: "standard")
    monkeypatch.setattr(runner, "layer_absent", lambda *args: (True, {}))
    monkeypatch.setattr(runner.signal, "signal", Mock())
    monkeypatch.setattr(runner.time, "sleep", Mock())
    locked = Mock(return_value={"in_game": True, "display_fps": 30, "seen_fps": 30})
    monkeypatch.setattr(runner, "make_locked", Mock(return_value=locked))
    argv = ["selfsr_ab.py", "run", "--serial", "fixture", "--out", str(tmp_path), "--rounds", "2"]
    monkeypatch.setattr(sys, "argv", argv)
    return SimpleNamespace(argv=argv, locked=locked, out=tmp_path)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ([], ["shared.so"] * 4),
        (["--so-a", "base.so"], ["base.so", "shared.so", "shared.so", "base.so"]),
        (["--so-b", "candidate.so"], ["shared.so", "candidate.so", "candidate.so", "shared.so"]),
        (["--so-a", "base.so", "--so-b", "candidate.so"], ["base.so", "candidate.so", "candidate.so", "base.so"]),
    ],
)
def test_layer_selection_follows_abba_without_leaking_override(runner, main_without_device, monkeypatch, overrides, expected):
    """Choose each side independently, retaining the original shared fallback."""
    main_without_device.argv += ["--a", "layer", "--b", "layer", "--so", "shared.so", *overrides]
    seen = []

    def layer(a, *unused):
        seen.append(a.so)
        return {"layer_loaded": True, "layer_removed": True, "in_game": True, "display_fps": 30, "seen_fps": 30}

    monkeypatch.setattr(runner, "arm_layer", layer)
    assert runner.main() == 0
    assert seen == expected
    assert main_without_device.locked.call_args.args == ("restore", "end", "native")


@pytest.mark.parametrize("layered", [False, True])
def test_recovery_unknown_stops_next_arm_and_defers_cleanup(runner, main_without_device, monkeypatch, layered):
    """Do not start B after A loses recovery confirmation, even with reroll set."""
    kind = "layer" if layered else "native"
    main_without_device.argv += ["--a", kind, "--b", kind]
    failed = {"error": "World verification recovery is unknown", "recovery_unknown": True, "layer_removed": True, "reroll": True}
    layer = Mock(return_value=failed)
    monkeypatch.setattr(runner, "arm_layer", layer)
    if not layered:
        main_without_device.locked.side_effect = [failed, {}]
    runner.main()
    records = [json.loads(line) for line in (main_without_device.out / "arms.jsonl").read_text().splitlines()]
    assert len(records) == 1 and records[0]["arm"] == "A1" and records[0]["recovery_unknown"]
    assert layer.call_count == (1 if layered else 0)
    assert all(call.args[0] != "restore" for call in main_without_device.locked.call_args_list)
    assert json.loads((main_without_device.out / "restore.json").read_text())["restoration_deferred"] is True


def test_plain_arm_preserves_unknown_recovery(runner, args, monkeypatch, tmp_path):
    """Carry verification uncertainty through the leased plain-arm result."""
    monkeypatch.setattr(runner, "Dev", Mock())
    monkeypatch.setattr(runner, "prepare", Mock(return_value={}))
    monkeypatch.setattr(runner, "onboard", Mock(return_value=(75, "recovery unknown")))
    measure = Mock(side_effect=AssertionError("Unverified world must not be measured"))
    monkeypatch.setattr(runner, "measure", measure)
    rec = runner.arm_plain(args, str(tmp_path), "A1", runner.parse_kind("native"), "standard")
    assert rec["recovery_unknown"] is True
    measure.assert_not_called()


def test_layer_arm_records_library_identity_and_unknown_recovery(runner, args, monkeypatch, tmp_path):
    """Hash the chosen file and leave the native owner intact on uncertain recovery."""
    library = tmp_path / "baseline.so"
    library.write_bytes(b"fixture layer bytes")
    (tmp_path / "sessions").mkdir()
    args.so, args.session_seconds, args.keep_layer_prop = str(library), 1200, False
    monkeypatch.setattr(runner, "Dev", Mock())
    monkeypatch.setattr(runner.time, "sleep", Mock())
    monkeypatch.setattr(runner, "pf_json", Mock(return_value=(0, {"phase": "ready"})))
    monkeypatch.setattr(runner, "onboard", Mock(return_value=(75, "recovery unknown")))
    monkeypatch.setattr(runner, "layer_absent", Mock(return_value=(True, {})))
    stop = Mock(return_value={"status": "completed"})
    monkeypatch.setattr(runner, "session_action", stop)
    process = Mock(returncode=0)
    process.poll.return_value = None
    start = Mock(return_value=process)
    monkeypatch.setattr(runner.subprocess, "Popen", start)
    rec = runner.arm_layer(args, str(tmp_path), "A1", runner.parse_kind("layer"), Mock(), Mock(return_value={}))
    assert rec["layer_path"] == str(library)
    assert rec["layer_sha256"] == hashlib.sha256(library.read_bytes()).hexdigest()
    assert rec["recovery_unknown"] is True and rec["layer_removed"] is False
    assert start.call_args.args[0][-2:] == ["--so", str(library)]
    stop.assert_not_called()
    process.wait.assert_not_called()
    process.terminate.assert_not_called()
    assert start.call_args.kwargs["pass_fds"] == (17,)
    runner.Dev.return_value.shell.assert_not_called()


def test_provenance_tracks_original_and_packaged_bytes():
    """Preserve attribution while distinguishing the locally adapted runner."""
    provenance = json.loads((UPSTREAM / "provenance.json").read_text())
    assert provenance["commit"] == COMMIT
    assert provenance["modified_paths"] == ["selfsr/selfsr_ab.py", "gfrc/gfrc_ab.py"]
    assert {item["path"] for item in provenance["files"]} == set(ORIGINAL_HASHES)
    for item in provenance["files"]:
        assert item["upstream_sha256"] == ORIGINAL_HASHES[item["path"]]
        digest = hashlib.sha256((UPSTREAM / item["path"]).read_bytes()).hexdigest()
        assert item["packaged_sha256"] == digest


@pytest.mark.parametrize("failure", ["timeout", "unexpected", "unknown", "ready", "not_ready"])
def test_capture_obligation_survives_unconfirmed_exit(runner, args, tmp_path, failure):
    """Only a conclusive native recovery receipt clears the durable obligation."""
    runner.subprocess.run.side_effect = None
    if failure == "timeout":
        runner.subprocess.run.side_effect = subprocess.TimeoutExpired("capture", 180)
    elif failure == "unexpected":
        runner.subprocess.run.side_effect = RuntimeError("synthetic interruption")
    elif failure == "unknown":
        runner.subprocess.run.return_value = capture_result(recovery={"status": "required"})
    else:
        runner.subprocess.run.return_value = capture_result(ok=failure == "ready", frames=int(failure == "ready"))
    if failure == "unexpected":
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            runner.verify_game_world(args, str(tmp_path), "A1")
    else:
        runner.verify_game_world(args, str(tmp_path), "A1")
    assert runner.capture_recovery_pending(tmp_path) is (failure in ("timeout", "unexpected", "unknown"))


@pytest.mark.parametrize("layered", [False, True])
@pytest.mark.parametrize("collector", ["world", "mali"])
def test_pending_capture_blocks_finally_after_lost_arm_record(runner, main_without_device, monkeypatch, layered, collector):
    """A crash before ARM_JSON cannot hide capture ownership from outer finally."""
    kind = "layer" if layered else "native"
    main_without_device.argv += ["--a", kind, "--b", kind]
    mutation_count = []

    def interrupted(*args):
        relative = "onboard/A1_world/recovery-pending.json" if collector == "world" else "traces/A1_mali/mali-cleanup.json"
        pending = main_without_device.out / relative
        pending.parent.mkdir(parents=True)
        pending.write_text("{}")
        mutation_count.append(len(runner.Dev.return_value.mock_calls))
        raise RuntimeError("synthetic lost arm record")

    if layered:
        monkeypatch.setattr(runner, "arm_layer", interrupted)
    else:
        main_without_device.locked.side_effect = interrupted
    with pytest.raises(RuntimeError, match="synthetic lost arm record"):
        runner.main()
    assert all(call.args[0] != "restore" for call in main_without_device.locked.call_args_list)
    assert len(runner.Dev.return_value.mock_calls) == mutation_count[0]
    assert json.loads((main_without_device.out / "restore.json").read_text())["restoration_deferred"] is True


def test_layer_finally_does_not_stop_owner_after_capture_exception(runner, args, monkeypatch, tmp_path):
    """The native owner retains its FD and lifecycle when capture loses its return."""
    (tmp_path / "sessions").mkdir()
    args.so, args.session_seconds, args.keep_layer_prop = None, 1200, False
    dev = Mock()
    monkeypatch.setattr(runner, "Dev", Mock(return_value=dev))
    monkeypatch.setattr(runner.time, "sleep", Mock())
    monkeypatch.setattr(runner, "pf_json", Mock(return_value=(0, {"phase": "ready"})))
    process = Mock(returncode=None)
    process.poll.return_value = None
    monkeypatch.setattr(runner.subprocess, "Popen", Mock(return_value=process))
    stop = Mock(side_effect=AssertionError("No competing stop allowed"))
    monkeypatch.setattr(runner, "session_action", stop)

    def interrupted(*args, **kwargs):
        pending = tmp_path / "onboard/A1_world/recovery-pending.json"
        pending.parent.mkdir(parents=True)
        pending.write_text("{}")
        raise RuntimeError("synthetic capture exception")

    monkeypatch.setattr(runner, "onboard", interrupted)
    with pytest.raises(RuntimeError, match="synthetic capture exception"):
        runner.arm_layer(args, str(tmp_path), "A1", runner.parse_kind("layer"), Mock(), Mock(return_value={}))
    stop.assert_not_called()
    process.wait.assert_not_called()
    process.terminate.assert_not_called()
    dev.shell.assert_not_called()
    dev.su.assert_not_called()


@pytest.mark.parametrize("unknown", [False, True])
def test_mali_failure_stops_measurement_before_further_device_actions(runner, args, tmp_path, monkeypatch, unknown):
    """Do not take screenshots or continue KGSL sampling after Mali capture failure."""
    capture = {"metrics": {}, "error": "capture failed", "recovery_unknown": unknown,
               "cleanup_confirmed": not unknown, "timestats": ""}
    monkeypatch.setattr(runner, "collect_mali", lambda *a, **k: capture)
    dev = Mock()
    result = runner.measure(dev, args, "A1", str(tmp_path))
    assert result["error"] == "capture failed"
    assert result["recovery_unknown"] is unknown
    assert not dev.mock_calls


def test_recording_perfetto_is_not_a_failed_startup_and_is_stopped(runner, args, tmp_path, monkeypatch):
    """Enter the native window after a valid start and stop the owned recorder."""
    args.perfetto = True
    recorder = Mock()
    recorder.state = {"marker": "llm4ad_window_" + "a" * 32, "recovery_unknown": True}
    recorder.start.return_value = True

    def stopped():
        recorder.state["recovery_unknown"] = False

    recorder.stop.side_effect = stopped
    monkeypatch.setattr(runner, "PerfettoCapture", Mock(return_value=recorder))
    native = Mock(return_value={"error": "synthetic native capture failure", "recovery_unknown": False})
    monkeypatch.setattr(runner, "collect_mali", native)
    result = runner.measure(Mock(), args, "A1", str(tmp_path))
    native.assert_called_once()
    assert native.call_args.kwargs["perfetto_marker"] == recorder.state["marker"]
    recorder.stop.assert_called_once()
    assert result["error"] == "synthetic native capture failure"


@pytest.mark.parametrize("state", ["reported_unknown", "durable_unknown", "confirmed"])
def test_plain_arm_stops_only_after_confirmed_capture_cleanup(runner, args, tmp_path, monkeypatch, state):
    """Defer force-stop on either unknown channel and retain confirmed cleanup."""
    dev = Mock()
    monkeypatch.setattr(runner, "Dev", Mock(return_value=dev))
    monkeypatch.setattr(runner, "prepare", Mock(return_value={}))
    monkeypatch.setattr(runner, "onboard", Mock(return_value=(0, "")))
    monkeypatch.setattr(runner, "gfrc_after_enter", Mock(return_value=({}, "")))
    monkeypatch.setattr(runner.time, "sleep", Mock())
    measured = {"in_game": True}
    if state == "reported_unknown":
        measured.update(error="Mali cleanup unknown", recovery_unknown=True)
    else:
        receipt = tmp_path / "traces/A1_mali/mali-cleanup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"cleanup_confirmed": state == "confirmed", "recovery_unknown": state != "confirmed"}))
    monkeypatch.setattr(runner, "measure", Mock(return_value=measured))
    rec = runner.arm_plain(args, str(tmp_path), "A1", runner.parse_kind("native"), "standard")
    if state == "confirmed":
        dev.shell.assert_called_once_with(f"am force-stop {args.pkg}")
        assert not rec.get("recovery_unknown")
    else:
        assert rec["recovery_unknown"] is True
        assert not dev.mock_calls


@pytest.mark.parametrize("receipt", [
    None, "not json", "null", "[]", "{}", '{"submitted": false}',
    '{"cleanup_confirmed": true}', '{"cleanup_confirmed": true, "recovery_unknown": true}',
    '{"cleanup_confirmed": 1, "recovery_unknown": false}',
    '{"cleanup_confirmed": true, "recovery_unknown": 0}',
])
def test_incomplete_mali_receipt_keeps_recovery_pending(runner, tmp_path, receipt):
    """Treat missing, partial, malformed and ambiguous receipts as unresolved."""
    folder = tmp_path / "traces/A1_mali"
    folder.mkdir(parents=True)
    if receipt is not None:
        (folder / "mali-cleanup.json").write_text(receipt)
    assert runner.capture_recovery_pending(tmp_path)


def test_lost_mali_arm_json_stops_next_arm_and_outer_restore(runner, main_without_device):
    """An ordinary child error cannot hide a durable Mali cleanup obligation."""
    main_without_device.argv += ["--a", "native", "--b", "native"]
    calls_after_loss = []

    def lost_record(*args):
        receipt = main_without_device.out / "traces/A1_mali/mali-cleanup.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({"cleanup_confirmed": False, "recovery_unknown": True}))
        calls_after_loss.append(len(runner.Dev.return_value.mock_calls))
        return {"error": "plain exit 1: ARM_JSON missing"}

    main_without_device.locked.side_effect = lost_record
    runner.main()
    main_without_device.locked.assert_called_once_with("plain", "A1", "native")
    assert len(runner.Dev.return_value.mock_calls) == calls_after_loss[0]
    records = (main_without_device.out / "arms.jsonl").read_text().splitlines()
    assert len(records) == 1
    assert json.loads((main_without_device.out / "restore.json").read_text())["restoration_deferred"] is True
