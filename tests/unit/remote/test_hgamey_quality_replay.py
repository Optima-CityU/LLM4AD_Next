"""Exercise native-format replay receipts with synthetic files and mocked processes only."""

import hashlib
import json
import signal
import struct
import subprocess
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm4ad.gateway.examples.hgamey.protocol import quality_replay as replay

pytestmark = pytest.mark.unit


def png(width=1920, height=1080):
    """Encode flat synthetic RGB pixels without an image library or device."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixels = (b"\x00" + b"\x20\x40\x60" * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def digest(data):
    """Bind fixture declarations to their exact synthetic bytes."""
    return hashlib.sha256(data).hexdigest()


def native_report(folder, serial):
    """Produce the actual native export schema, including six separately named invocation artifacts."""
    reference = folder / "source.png"
    source_sha = digest(reference.read_bytes())
    rgba_sha = digest(b"\x20\x40\x60\xff" * (1920 * 1080))
    artifacts, provenance, comparisons = [], [], []
    prefix = "evidence-123-abcdef-0/images/frame-00/"
    image = png()
    names = {"reference": prefix + "reference.png"}
    for role in ("reference", "baseline", "candidate"):
        for round_id in ([0] if role == "reference" else range(3)):
            name = prefix + (f"round-{round_id:02}/" if round_id else "") + role + ".png"
            path = folder / "native" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
            artifacts.append({"name": name, "sha256": digest(image), "size": len(image), "media_type": "image/png"})
            item = {"artifact": name, "frame_id": "frame-00", "role": role, "rgba_sha256": rgba_sha,
                    "width": 1920, "height": 1080}
            if role == "reference":
                item["source"] = {"kind": "prepared_reference", "path": str(reference), "sha256": source_sha,
                                  "width": 2000, "height": 1200, "transform": "center_crop_without_resampling",
                                  "crop_x": 40, "crop_y": 60}
            else:
                item.update(round=round_id, reference_source=str(reference), reference_source_sha256=source_sha,
                            reference_source_width=2000, reference_source_height=1200,
                            reference_rgba_sha256=rgba_sha, psnr_db=99.0)
                item["source"] = {"kind": "vulkan_gpu_readback", "runner_exit_status": 0, "outside_gpu_timing": True,
                                  "remote_path": f"/data/local/tmp/phonefarm_gpuop/frame-00-round-{round_id}-{role}.rgba",
                                  "shader": f"/data/local/tmp/phonefarm_gpuop/{role}.spv", "size": 1920 * 1080 * 4,
                                  "sha256": rgba_sha, "buffer": "produced (same bytes used by runner PSNR)"}
                names[(role, round_id)] = name
            provenance.append(item)
    for round_id in range(3):
        comparisons.append({"id": f"frame-00-round-{round_id:02}", "kind": "static", "images": [
            {"role": role, "artifact": names["reference"] if role == "reference" else names[(role, round_id)]}
            for role in ("reference", "baseline", "candidate")
        ]})
    return {
        "version": 2, "kind": "shader", "source": "phonefarm", "status": "PASS",
        "measurement_scope": "operator_benchmark_only", "artifact_errors": [], "artifacts": artifacts,
        "evidence_provenance": provenance, "evidence_comparisons": comparisons,
        "resolved_config": {"serial": serial, "carrier": "headless", "seconds": 1, "gpu_level": 35},
        "conditions_observed": {"root_verified": True, "restored": True, "arms": [
            {"arm": "A" if i % 2 == 0 else "B", "index": i, "restored": True, "runner_exit_status": 0}
            for i in range(6)
        ]},
    }


@pytest.fixture
def case(tmp_path, monkeypatch):
    """Install a process-only native simulation while retaining an actual local fixture lease descriptor."""
    source = tmp_path / "actual-fixture-source.png"
    source.write_bytes(png(2000, 1200))
    inputs = tmp_path / "inputs/replay"
    inputs.mkdir(parents=True)
    shaders = {"protocol": "same-input-gpu-replay@1"}
    for role in ("candidate_comp", "baseline_comp", "candidate_spv", "baseline_spv"):
        path = inputs / role.replace("_", ".")
        data = ("explicit synthetic fixture " + role).encode()
        path.write_bytes(data)
        shaders[role + "_path"], shaders[role + "_sha256"] = str(path), digest(data)
    state = SimpleNamespace(folder=tmp_path / "evidence/quality/replay/frame-01", source=source, shaders=shaders,
                            serial="fixture-device", mutate=lambda report: None, returncode=0,
                            polls=0, never_finishes=False, wait_hook=lambda: None, written=False,
                            encode=json.dumps)

    def launch(command, **kwargs):
        assert command[:2] == ["phonefarm", "eval"]
        state.command, state.kwargs = command, kwargs
        state.native = native_report(state.folder, state.serial)
        state.mutate(state.native)
        process = Mock(pid=424242, returncode=None)
        process.poll.side_effect = lambda: process.returncode

        def wait(timeout):
            state.wait_hook()
            state.wait_hook = lambda: None
            if state.never_finishes or state.polls:
                state.polls = max(0, state.polls - 1)
                raise subprocess.TimeoutExpired("synthetic native replay", timeout)
            kwargs["stdout"].write(state.encode(state.native))
            kwargs["stdout"].flush()
            process.returncode = state.returncode
            state.written = True
            return process.returncode

        process.wait.side_effect = wait
        state.process = process
        return process

    spawn = Mock(side_effect=launch)
    monkeypatch.setattr(replay.subprocess, "Popen", spawn)
    monkeypatch.setenv("HGAMEY_GPU_LEVEL", "35")
    with (tmp_path / "lease").open("w") as lease:
        state.fd = lease.fileno()
        monkeypatch.setenv("PF_LOCK_FDS", str(state.fd))
        state.spawn = spawn
        yield state


def run(case, cancelled=lambda: False, timeout=30):
    """Call only the orchestrator under the process fixture."""
    return replay.replay_frame(case.folder, case.source, case.shaders, case.serial, cancelled, timeout)


@pytest.mark.parametrize("status,code", [("PASS", 0), ("SLOW", 1), ("POOR_QUALITY", 1)])
def test_exports_six_native_outputs_and_keeps_nonpass_measurements(case, status, code):
    """Equal deterministic pixels remain six real-format invocation receipts, including poor quality outcomes."""
    case.returncode = code
    case.mutate = lambda r: r.update(status=status)
    result = run(case)
    assert result["status"] == "completed" and result["cleanup_confirmed"] is True, result
    assert result["report"] == case.native and result["report"]["status"] == status
    assert result["frame_id"] == "frame-01" and result["source"]["sha256"] == digest(case.source.read_bytes())
    assert (case.folder / result["source"]["artifact"]).read_bytes() == case.source.read_bytes()
    assert len(result["baseline"]) == len(result["candidate"]) == 3
    outputs = result["baseline"] + result["candidate"]
    assert len({item["artifact"] for item in outputs}) == 6 and len({item["sha256"] for item in outputs}) == 1
    assert all(item["frame_id"] == "frame-01" and item["source_sha256"] == result["source"]["sha256"] for item in outputs)
    assert [item["invocation"]["round"] for item in result["baseline"]] == [0, 1, 2]
    assert len(result["evidence_comparisons"]) == 3
    names = {item["name"] for item in result["artifacts"]}
    assert {"request.json", "replay-intent.json", "native-report.json", "native.stdout"} <= names
    assert "replay-result.json" not in names
    for item in result["artifacts"]:
        assert not Path(item["name"]).is_absolute()
        assert digest((case.folder / item["name"]).read_bytes()) == item["sha256"]
    request = json.loads((case.folder / "request.json").read_text())
    assert request["version"] == 2 and request["workload"] == {"carrier": "headless", "seconds": 1}
    assert request["protocol"]["rounds"] == 3 and request["protocol"]["alternating"] is True
    assert request["conditions"]["quality_reference"] == str(case.folder / "source.png")
    assert request["conditions"]["require_root"] is True and request["conditions"]["allow_missing_power"] is True
    assert request["conditions"]["gpu_level"] == 35 and "gpu_frequency_lock" not in request["conditions"]
    assert case.kwargs["pass_fds"] == (case.fd,)
    case.spawn.assert_called_once()


def test_cancel_before_launch_does_no_native_work(case):
    """A stopped admission flag cannot start a GPU frame."""
    result = run(case, lambda: True)
    assert result["status"] == "cancelled" and result["cleanup_confirmed"] is True
    case.spawn.assert_not_called()


@pytest.mark.parametrize("via_signal", [False, True])
def test_cancel_during_native_frame_drains_and_retains_restoration(case, via_signal):
    """Cancellation observes native completion without sending a destructive child signal."""
    previous = signal.getsignal(signal.SIGTERM)
    case.polls = 1
    if via_signal:
        case.wait_hook = lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    result = run(case, (lambda: False) if via_signal else (lambda: case.spawn.called))
    assert result["status"] == "cancelled" and result["cleanup_confirmed"] is True
    assert case.written and signal.getsignal(signal.SIGTERM) == previous
    case.process.terminate.assert_not_called()
    case.process.kill.assert_not_called()
    case.process.send_signal.assert_not_called()
    case.spawn.assert_called_once()


@pytest.mark.parametrize("where", ["overall", "arm", "gpu_level_lock"])
def test_failed_restoration_is_quarantined_even_with_valid_images(case, where):
    """Actual failed frequency restoration dominates exported pixels and process exit status."""
    def mutate(report):
        observed = report["conditions_observed"]
        if where == "arm":
            observed["arms"][2]["restored"] = False
        elif where == "gpu_level_lock":
            observed[where] = {"restored": False}
        else:
            observed["restored"] = False

    case.mutate = mutate
    result = run(case)
    assert result["cleanup_confirmed"] is False and result["recovery_unknown"] is True
    assert result["status"] == "recovery_required" and not result["baseline"]
    assert result["report"] == case.native


@pytest.mark.parametrize("problem", ["source", "reference", "duplicate", "missing", "escape", "checksum"])
def test_invalid_export_attestation_cannot_become_quality_samples(case, problem):
    """Reject swapped inputs, repeated invocations, incomplete outputs and altered artifact bytes."""
    def mutate(report):
        proofs = report["evidence_provenance"]
        if problem == "source":
            proofs[0]["source"]["sha256"] = "f" * 64
        elif problem == "reference":
            proofs[1]["reference_rgba_sha256"] = "f" * 64
        elif problem == "duplicate":
            proofs[2]["source"]["remote_path"] = proofs[1]["source"]["remote_path"]
        elif problem == "missing":
            proofs.pop(2)
        elif problem == "escape":
            report["artifacts"][0]["name"] = "../escape.png"
        else:
            report["artifacts"][1]["sha256"] = "f" * 64

    case.mutate = mutate
    result = run(case)
    assert result["cleanup_confirmed"] is True and result["status"] == "not_measured"
    assert result["error"] and not result["baseline"] and not result["candidate"]


def test_frozen_shader_checksum_mismatch_fails_before_launch(case):
    """Never compile or substitute a different shader when the frozen input differs."""
    case.shaders["baseline_comp_sha256"] = "f" * 64
    result = run(case)
    assert result["cleanup_confirmed"] is True and "checksum mismatch" in result["error"]
    case.spawn.assert_not_called()


def test_hard_deadline_retains_original_child_and_never_replays(case, monkeypatch):
    """An unresolved child keeps its inherited lease; another call cannot dispatch replacement work."""
    case.never_finishes = True
    clock = iter([0, 50, 100, 150])
    monkeypatch.setattr(replay, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    result = run(case, timeout=1)
    assert result["status"] == "recovery_required" and result["cleanup_confirmed"] is False
    assert result["pid"] == case.process.pid and result["recovery_unknown"] is True
    case.process.kill.assert_not_called()
    case.process.terminate.assert_not_called()
    intent = (case.folder / "replay-intent.json").read_bytes()
    receipt = (case.folder / "replay-result.json").read_bytes()
    again = run(case)
    assert again["cleanup_confirmed"] is False and "refusing to repeat" in again["error"]
    assert (case.folder / "replay-intent.json").read_bytes() == intent
    assert (case.folder / "replay-result.json").read_bytes() == receipt
    case.spawn.assert_called_once()


def test_missing_overall_restoration_is_not_inferred_from_arm_receipts(case):
    """Individual arm/lock receipts cannot manufacture the missing aggregate cleanup confirmation."""
    def mutate(report):
        report["conditions_observed"].pop("restored")
        report["conditions_observed"]["gpu_level_lock"] = {"restored": True}

    case.mutate = mutate
    assert run(case)["cleanup_confirmed"] is False


@pytest.mark.parametrize("restored", [True, False, None])
def test_native_abort_keeps_restoration_separate_from_incomplete_measurement(case, restored):
    """An aborted partial replay stays unmeasured without quarantining a verified clean device."""
    def mutate(report):
        report.update(status="ABORT", verdict={"reason": "Cooling sensor became unavailable"})
        report["conditions_observed"]["arms"] = report["conditions_observed"]["arms"][:1]
        report["conditions_observed"]["restored"] = restored

    case.returncode = 2
    case.mutate = mutate
    result = run(case)
    assert result["cleanup_confirmed"] is (restored is True)
    assert result["recovery_unknown"] is (restored is not True)
    assert result["status"] == ("not_measured" if restored is True else "recovery_required")
    assert not result["baseline"] and not result["candidate"]
    assert result["report"]["status"] == "ABORT" and result["error"]
    if restored is True:
        assert "Cooling sensor became unavailable" in result["error"]


@pytest.mark.parametrize("field,value", [("serial", "another-device"), ("carrier", "refbench"), ("seconds", 2)])
def test_foreign_replay_report_cannot_confirm_owned_device_cleanup(case, field, value):
    """Restoration from a different request cannot release this attempt's physical device."""
    case.mutate = lambda report: report["resolved_config"].update({field: value})
    result = run(case)
    assert result["cleanup_confirmed"] is False and result["recovery_unknown"] is True
    assert result["status"] == "recovery_required"


def test_multiple_native_reports_cannot_be_selected_as_a_cleanup_receipt(case):
    """Ambiguous output requires recovery instead of guessing which restoration receipt applies."""
    case.returncode = 2
    case.encode = lambda report: json.dumps(report) + "\n" + json.dumps({"status": "ABORT"})
    result = run(case)
    assert result["status"] == "recovery_required" and result["cleanup_confirmed"] is False
    assert "Extra data" in result["error"] and not result["baseline"]


def test_stopped_native_abort_drains_without_exporting_partial_quality(case):
    """A cancelled frame retains its verified cleanup while declining partial GPU samples."""
    case.returncode = 2
    case.mutate = lambda report: report.update(status="ABORT", verdict={"reason": "Incomplete GPU replay"})
    result = run(case, lambda: case.spawn.called)
    assert result["status"] == "cancelled" and result["cleanup_confirmed"] is True
    assert result["recovery_unknown"] is False and not result["baseline"]
    case.process.kill.assert_not_called()
