"""Reconcile each native owner without replaying measurements or changing results."""

import json
from types import SimpleNamespace

import pytest

from llm4ad.gateway.examples.hgamey.execution import recovery

pytestmark = pytest.mark.unit


def write_json(path, value):
    """Write a synthetic receipt to the test directory."""
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def restoration(tmp_path, monkeypatch):
    """Provide one leased device with deterministic identity and native commands."""
    folder = tmp_path / "native-job"
    folder.mkdir()
    endpoint = "fixture-phone:5555"
    write_json(folder / "input.json", {
        "device": {"id": "fixture", "endpoint": endpoint, "expected_serial": "physical-fixture"},
        "submission": {"operation": "game-ab"},
    })
    write_json(folder / "connection.json", {"boot_id": "original-boot"})
    write_json(folder / "foreground.json", {"restore_required": False})
    (folder / "evidence").mkdir()
    calls = []
    replies = []

    def adb(*args, **kwargs):
        if args == ("-s", endpoint, "shell", "getprop", "ro.serialno"):
            return "physical-fixture"
        if args == ("-s", endpoint, "shell", "cat", "/proc/sys/kernel/random/boot_id"):
            return "original-boot"
        assert args in {("connect", endpoint), ("-s", endpoint, "wait-for-device")}
        return ""

    def command(argv, **kwargs):
        calls.append(argv)
        assert kwargs["pf"] is True
        if replies:
            return replies.pop(0)
        if argv[1] == "gpu-op":
            value = {"restored": True, "serial": endpoint, "scope": "gpu_op_owned_frequency_charging"}
        else:
            value = {"recovery": {"status": "reported_complete"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    monkeypatch.setattr(recovery, "inherited_fds", lambda: (7,))
    monkeypatch.setattr(recovery, "adb", adb)
    monkeypatch.setattr(recovery, "command", command)
    monkeypatch.setattr(recovery, "restore_collectors", lambda folder, endpoint: [])
    return SimpleNamespace(folder=folder, endpoint=endpoint, calls=calls, replies=replies)


def capture_receipt(restoration, completed):
    """Create an ordinary capture with optional matching native completion proof."""
    folder = restoration.folder / "evidence/onboard/A1_world/capture-fixture"
    folder.mkdir(parents=True)
    write_json(folder / "session.json", {
        "schema": "phonefarm/capture-session@1", "serial": restoration.endpoint,
        "transport_uncertain": False, "write_pending": None,
    })
    if completed:
        proof = {
            "schema": "phonefarm/capture-recovery@1", "status": "verified", "error": None,
            "serial": restoration.endpoint, "session": str(folder),
            "evidence": str(folder / "recovery-1-2.json"), "checks": {"scope": "no_device_mutations"},
        }
        write_json(folder / "recovery-1-2.json", proof)
        write_json(folder / "report-1-3.json", {
            "serial": restoration.endpoint, "session": str(folder),
            "recovery": {**proof, "status": "reported_complete"},
        })
    return folder


def test_completed_capture_uses_existing_completion_without_wrong_owner(restoration):
    """A closed onboarding capture must not be handed to gray-layer recovery."""
    folder = capture_receipt(restoration, completed=True)
    proof = recovery.restore_on_device(restoration.folder)
    assert proof["restored"] is True
    assert proof["checks"][0]["session"] == str(folder)
    assert proof["checks"][0]["recovery"]["status"] == "reported_complete"
    assert restoration.calls == []


@pytest.mark.parametrize("stale_receipt", [False, True])
def test_incomplete_capture_uses_its_own_native_recovery(restoration, stale_receipt):
    """Missing or inconsistent capture proof requires the capture CLI, not frame-audit."""
    folder = capture_receipt(restoration, completed=stale_receipt)
    if stale_receipt:
        path = folder / "report-1-3.json"
        report = json.loads(path.read_text())
        report["recovery"]["status"] = "required"
        write_json(path, report)
    proof = recovery.restore_on_device(restoration.folder)
    assert proof["restored"] is True
    assert restoration.calls == [[
        "phonefarm", "capture", "--recover", str(folder), "--serial", restoration.endpoint, "--json",
    ]]


@pytest.mark.parametrize("operation", ["eval", "game-ab"])
def test_game_quality_replay_restores_its_static_gpu_lock(restoration, operation):
    """A game job's nested same-input replay has independent frequency cleanup."""
    path = restoration.folder / "input.json"
    frozen = json.loads(path.read_text())
    frozen["submission"]["operation"] = operation
    write_json(path, frozen)
    if operation == "game-ab":
        intent = restoration.folder / "evidence/quality/replay/frame-01/replay-intent.json"
        intent.parent.mkdir(parents=True)
        write_json(intent, {"submitted": True})
    proof = recovery.restore_on_device(restoration.folder)
    assert proof["restored"] is True
    assert proof["checks"][-1]["scope"] == "gpu_op_owned_frequency_charging"
    assert restoration.calls == [[
        "phonefarm", "gpu-op", "--serial", restoration.endpoint, "--unlock",
        "--out", str(restoration.folder / "evidence"), "--json",
    ]]


@pytest.mark.parametrize("failure", ["capture", "gpu-op"])
def test_native_recovery_failure_cannot_be_acknowledged(restoration, failure):
    """Neither an incorrect owner nor a failed unlock can release the resource."""
    if failure == "capture":
        capture_receipt(restoration, completed=False)
        body = {"recovery": {"status": "required", "error": "capture is not restored"}}
    else:
        intent = restoration.folder / "evidence/quality/replay/frame-01/replay-intent.json"
        intent.parent.mkdir(parents=True)
        write_json(intent, {"submitted": True})
        body = {"restored": False}
    restoration.replies.append(SimpleNamespace(returncode=2, stdout=json.dumps(body), stderr=""))
    with pytest.raises(RuntimeError, match="restoration"):
        recovery.restore_on_device(restoration.folder)


def test_foreign_capture_is_rejected_before_native_commands(restoration):
    """A recovery command must never mutate a second phone's capture session."""
    folder = capture_receipt(restoration, completed=False)
    state = json.loads((folder / "session.json").read_text())
    state["serial"] = "different-phone:5555"
    write_json(folder / "session.json", state)
    with pytest.raises(ValueError, match="another device"):
        recovery.restore_on_device(restoration.folder)
    assert not restoration.calls


def test_game_without_gpu_replay_does_not_unlock_unrelated_state(restoration):
    """A game-only attempt has no static GPU lock restoration obligation."""
    assert recovery.restore_on_device(restoration.folder)["restored"] is True
    assert not restoration.calls


def test_whitebox_refbench_uses_its_own_recovery_owner(restoration):
    """A native whitebox session is not a generic frame-audit session."""
    folder = restoration.folder / "evidence/whitebox-session"
    folder.mkdir()
    write_json(folder / "session.json", {
        "schema": "phonefarm/gray-capture-session@2", "serial": restoration.endpoint,
        "completion_owner": "whitebox_refbench_native",
    })
    assert recovery.restore_on_device(restoration.folder)["restored"] is True
    assert restoration.calls == [[
        "phonefarm", "whitebox-refbench-recover", "--session", str(folder),
        "--serial", restoration.endpoint, "--json",
    ]]


def test_fresh_native_cleanup_releases_settings_without_rewriting_failure(restoration, monkeypatch):
    """Stale wrapper flags cannot overrule a verified native completion."""
    from llm4ad.gateway.examples.hgamey.protocol import game_ab

    capture_receipt(restoration, completed=False)
    write_json(restoration.folder / "game-original.json", {"boot_id": "original-boot"})
    path = restoration.folder / "evidence/restore.json"
    write_json(path, {"recovery_unknown": True})
    original = path.read_bytes()
    order = []

    def collectors(folder, endpoint):
        assert len(restoration.calls) == 1
        order.append("collectors")
        return [{"schema": "hgamey/collector-restoration@1", "cleanup_confirmed": True}]

    def settings(folder):
        assert order == ["collectors"]
        order.append("settings")
        return True

    monkeypatch.setattr(recovery, "restore_collectors", collectors)
    monkeypatch.setattr(game_ab, "restore_settings", settings)
    proof = recovery.restore_on_device(restoration.folder)
    assert proof["restored"] is True
    assert order == ["collectors", "settings"]
    assert proof["checks"][-1]["cleanup_confirmed"] is True
    assert path.read_bytes() == original


def test_collector_failure_prevents_settings_restoration(restoration, monkeypatch):
    """Settings cannot resume a workload while an owned recorder is unresolved."""
    from llm4ad.gateway.examples.hgamey.protocol import game_ab

    write_json(restoration.folder / "game-original.json", {"boot_id": "original-boot"})
    calls = []

    def unresolved(folder, endpoint):
        raise RuntimeError("Collector restoration remains unconfirmed")

    monkeypatch.setattr(recovery, "restore_collectors", unresolved)
    monkeypatch.setattr(game_ab, "restore_settings", lambda folder: calls.append(folder))
    with pytest.raises(RuntimeError, match="Collector restoration"):
        recovery.restore_on_device(restoration.folder)
    assert not calls


@pytest.mark.parametrize("kind,scripts", [
    ("sysparam", ["knob_sysparam", "charge_suspend"]),
    ("whitebox", ["charge_suspend"]),
])
def test_native_script_controls_are_restored_before_gpu_unlock(restoration, monkeypatch, kind, scripts):
    """An empty GPU lock is not proof that sysparam or charging controls ended."""
    path = restoration.folder / "input.json"
    frozen = json.loads(path.read_text())
    frozen["submission"] = {"operation": "eval", "request": {"kind": kind}}
    write_json(path, frozen)
    original_adb = recovery.adb
    commands = []

    def adb(*args, **kwargs):
        if args[:3] == ("-s", restoration.endpoint, "shell") and args[3].startswith("su -c "):
            assert not restoration.calls
            commands.append(args[3])
            return "HGAMEY_NATIVE_SCRIPTS_RESTORED\n"
        return original_adb(*args, **kwargs)

    monkeypatch.setattr(recovery, "adb", adb)
    proof = recovery.restore_on_device(restoration.folder)
    assert proof["restored"] is True
    assert len(commands) == 1
    for script in scripts:
        assert f"/data/local/tmp/{script}.sh restore" in commands[0]
    assert proof["checks"][0]["scripts"] == scripts
    assert proof["checks"][1]["scope"] == "gpu_op_owned_frequency_charging"


def test_missing_script_readback_cannot_release_device(restoration, monkeypatch):
    """Exit status without the native script completion readback is insufficient."""
    path = restoration.folder / "input.json"
    frozen = json.loads(path.read_text())
    frozen["submission"] = {"operation": "eval", "request": {"kind": "sysparam"}}
    write_json(path, frozen)
    original_adb = recovery.adb

    def adb(*args, **kwargs):
        if args[:3] == ("-s", restoration.endpoint, "shell") and args[3].startswith("su -c "):
            return "KNOB_RESTORE_FAIL\n"
        return original_adb(*args, **kwargs)

    monkeypatch.setattr(recovery, "adb", adb)
    with pytest.raises(RuntimeError, match="script restoration"):
        recovery.restore_on_device(restoration.folder)
    assert not restoration.calls
