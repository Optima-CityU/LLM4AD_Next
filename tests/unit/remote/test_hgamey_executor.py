"""Exercise the split executor with synthetic CLI tools and no device access."""

import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(os.name == "nt", reason="phonefarm leases require POSIX")]


@pytest.fixture
def screenshot_job(tmp_path):
    """Create captured synthetic images and an unchanged protected result."""
    key = "a" * 32
    folder = tmp_path / "jobs" / key
    evidence = folder / "evidence"
    shots = evidence / "shots"
    shots.mkdir(parents=True)
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    arms = []
    for side in "AB":
        source = shots / f"{side}1.png"
        source.write_bytes(image)
        arms.append({"arm": source.stem, "side": side, "pair": 1, "in_game": True, "shot": str(source)})
    (evidence / "arms.jsonl").write_text("\n".join(json.dumps(arm) for arm in arms))
    frozen = {"submission": {"operation": "game-ab", "options": {"rounds": 1, "candidate_id": "c" * 64}}}
    (folder / "input.json").write_text(json.dumps(frozen))
    report = {"status": "PASS", "score": 12, "metrics": {"display_fps": 30}, "artifacts": []}
    original = json.dumps(report).encode()
    (folder / "result.json").write_bytes(original)
    return SimpleNamespace(key=key, folder=folder, evidence=evidence, image=image, report=report, original=original)


@pytest.fixture
def executor_http(screenshot_job):
    """Expose the real executor endpoints without device or gateway activity."""
    from llm4ad.gateway.examples.hgamey.execution.api import make_handler

    key, folder = screenshot_job.key, screenshot_job.folder
    engine = SimpleNamespace(jobs=folder.parent, get=lambda job_id: {"id": key, "state": "completed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/jobs/{key}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get_executor(base, suffix="/result"):
    """Read original artifact bytes or the exported result from loopback HTTP."""
    with urllib.request.urlopen(base + suffix, timeout=10) as response:
        return json.load(response) if suffix == "/result" else response.read()


def test_executor_exports_screenshots_without_changing_measured_report(screenshot_job, executor_http):
    """Serve captured PNG bytes through the same result and artifact endpoints."""
    job = screenshot_job
    exported = get_executor(executor_http)
    assert {key: value for key, value in exported.items() if key != "artifacts"} == {
        key: value for key, value in job.report.items() if key != "artifacts"
    }
    assert len(exported["artifacts"]) == 2
    assert "Baseline" in exported["artifacts"][0]["description"]
    assert "Candidate" in exported["artifacts"][1]["description"]
    for artifact in exported["artifacts"]:
        assert artifact["media_type"] == "image/png"
        assert get_executor(executor_http, "/artifacts/" + artifact["sha256"]) == job.image
        assert artifact["sha256"] == hashlib.sha256(job.image).hexdigest()
    assert (job.folder / "result.json").read_bytes() == job.original


@pytest.mark.parametrize("mutation", ["overwrite", "delete"])
def test_screenshot_snapshot_survives_source_changes_and_recovery(screenshot_job, executor_http, mutation):
    """A fresh result read and download keep the first exported image identity."""
    from llm4ad.gateway.examples.hgamey.evidence import exported_result

    job = screenshot_job
    first = get_executor(executor_http)
    for source in (job.evidence / "shots").iterdir():
        if mutation == "overwrite":
            source.write_bytes(job.image + b"changed after export")
        else:
            source.unlink()
    (job.evidence / "arms.jsonl").unlink()
    (job.folder / "input.json").unlink()
    assert get_executor(executor_http) == first
    assert exported_result(job.folder) == first
    for artifact in first["artifacts"]:
        assert get_executor(executor_http, "/artifacts/" + artifact["sha256"]) == job.image
    assert (job.folder / "result.json").read_bytes() == job.original


def test_screenshot_snapshot_is_collected_with_executor_bulk(screenshot_job, executor_http):
    """The normal executor retention allowlist removes snapshots and keeps receipts."""
    from llm4ad.gateway.examples.hgamey.evidence import EXECUTOR_RECEIPTS
    from llm4ad.gateway.sdk.retention import prune_directory

    job = screenshot_job
    assert len(get_executor(executor_http)["artifacts"]) == 2
    assert (job.folder / "screenshot-export").is_dir()
    assert prune_directory(job.folder, EXECUTOR_RECEIPTS) > 0
    assert not (job.folder / "screenshot-export").exists()
    assert get_executor(executor_http) == job.report
    assert (job.folder / "result.json").read_bytes() == job.original


@pytest.mark.parametrize("component", ["evidence", "shots", "job", "jobs", "ancestor"])
def test_executor_rejects_symlinked_evidence_and_job_ancestors(screenshot_job, executor_http, component, tmp_path):
    """Neither result export nor artifact serving follows a private directory alias."""
    job = screenshot_job
    paths = {"evidence": job.evidence, "shots": job.evidence / "shots", "job": job.folder, "jobs": job.folder.parent, "ancestor": tmp_path}
    path = paths[component]
    private = path.with_name(path.name + "-private")
    path.rename(private)
    try:
        path.symlink_to(private, target_is_directory=True)
        if component in {"evidence", "shots"}:
            assert get_executor(executor_http) == job.report
        else:
            with pytest.raises(urllib.error.HTTPError) as error:
                get_executor(executor_http)
            assert error.value.code == 404
        digest = hashlib.sha256(job.image).hexdigest()
        with pytest.raises(urllib.error.HTTPError) as error:
            get_executor(executor_http, "/artifacts/" + digest)
        assert error.value.code == 404
        assert not (job.folder / "screenshot-export").exists()
    finally:
        path.unlink()
        private.rename(path)
    assert (job.folder / "result.json").read_bytes() == job.original


@pytest.mark.parametrize("component", ["evidence", "trace_directory", "trace_file", "job"])
def test_declared_trace_download_rejects_symlink_escapes(screenshot_job, executor_http, component):
    """Existing non-screenshot declarations cannot bypass the artifact ownership fence."""
    job = screenshot_job
    trace = job.evidence / "traces" / "trace.pftrace"
    trace.parent.mkdir()
    content = b"synthetic private trace bytes"
    trace.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    report = {**job.report, "artifacts": [{"name": "traces/trace.pftrace", "size": len(content), "sha256": digest}]}
    (job.folder / "result.json").write_text(json.dumps(report))
    assert get_executor(executor_http, "/artifacts/" + digest) == content
    paths = {"evidence": job.evidence, "trace_directory": trace.parent, "trace_file": trace, "job": job.folder}
    path = paths[component]
    private = path.with_name(path.name + "-private")
    path.rename(private)
    try:
        path.symlink_to(private, target_is_directory=private.is_dir())
        with pytest.raises(urllib.error.HTTPError) as error:
            get_executor(executor_http, "/artifacts/" + digest)
        assert error.value.code == 404
    finally:
        path.unlink()
        private.rename(path)


def test_concurrent_first_exports_publish_one_complete_snapshot(screenshot_job, executor_http, monkeypatch):
    """Threaded requests see one committed snapshot despite source deletion mid-export."""
    from llm4ad.gateway.examples.hgamey import evidence

    job = screenshot_job
    ready, release = threading.Event(), threading.Event()
    persist = evidence.persist
    publications = []

    def pause_publication(path, value):
        persist(path, value)
        publications.append(value)
        ready.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(evidence, "persist", pause_publication)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(get_executor, executor_http)
        try:
            assert ready.wait(timeout=5)
            requests = [pool.submit(get_executor, executor_http) for _ in range(3)]
            for source in (job.evidence / "shots").iterdir():
                source.unlink()
        finally:
            release.set()
        reports = [first.result(timeout=10), *(request.result(timeout=10) for request in requests)]
    assert len(publications) == 1
    assert all(report == reports[0] for report in reports)
    assert len(reports[0]["artifacts"]) == 2
    for artifact in reports[0]["artifacts"]:
        assert get_executor(executor_http, "/artifacts/" + artifact["sha256"]) == job.image
    assert (job.folder / "result.json").read_bytes() == job.original


@pytest.mark.parametrize("failure", ["input_missing", "input_list", "body_list", "rounds_bool", "arms_invalid", "artifacts_null", "artifact_invalid", "manifest_invalid", "operation", "input_symlink", "arms_symlink"])
def test_invalid_optional_inputs_leave_measured_report_intact(screenshot_job, executor_http, failure):
    """Corrupt optional inputs and sidecars never manufacture a measurement failure."""
    job = screenshot_job
    report = job.report
    if failure.endswith("symlink"):
        path = job.folder / "input.json" if failure == "input_symlink" else job.evidence / "arms.jsonl"
        private = path.with_name(path.name + "-private")
        path.rename(private)
        path.symlink_to(private)
    elif failure == "input_missing":
        (job.folder / "input.json").unlink()
    elif failure == "input_list":
        (job.folder / "input.json").write_text("[]")
    elif failure == "body_list":
        (job.folder / "input.json").write_text('{"submission": []}')
    elif failure == "rounds_bool":
        (job.folder / "input.json").write_text('{"submission": {"operation": "game-ab", "options": {"rounds": true}}}')
    elif failure == "operation":
        (job.folder / "input.json").write_text('{"submission": {"operation": "eval"}}')
    elif failure == "arms_invalid":
        (job.evidence / "arms.jsonl").write_text("not JSON")
    elif failure in {"artifacts_null", "artifact_invalid"}:
        report = {**report, "artifacts": None if failure == "artifacts_null" else [{"sha256": "invalid"}]}
        (job.folder / "result.json").write_text(json.dumps(report))
    else:
        snapshot = job.folder / "screenshot-export"
        snapshot.mkdir()
        (snapshot / "manifest.json").write_text('{"schema": "invalid", "artifacts": []}')
    original = (job.folder / "result.json").read_bytes()
    assert get_executor(executor_http) == report
    assert (job.folder / "result.json").read_bytes() == original


@pytest.mark.parametrize("failure", ["json", "artifacts_null", "entry", "name", "checksum", "size", "duplicate", "job", "directory_symlink", "manifest_symlink", "image_symlink", "image_changed"])
def test_invalid_snapshots_are_not_refrozen_or_served(screenshot_job, executor_http, failure):
    """Corrupt or unowned sidecars neither leak files nor silently acquire new identities."""
    job = screenshot_job
    exported = get_executor(executor_http)
    snapshot = job.folder / "screenshot-export"
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text())
    if failure.endswith("symlink"):
        if failure == "directory_symlink":
            path = snapshot
        elif failure == "image_symlink":
            path = snapshot / exported["artifacts"][0]["sha256"]
        private = path.with_name(path.name + "-private")
        path.rename(private)
        path.symlink_to(private, target_is_directory=private.is_dir())
    elif failure == "image_changed":
        (snapshot / exported["artifacts"][0]["sha256"]).write_bytes(job.image + b"corrupt snapshot")
    elif failure == "json":
        path.write_text("not JSON")
    else:
        if failure == "artifacts_null":
            manifest["artifacts"] = None
        elif failure == "entry":
            manifest["artifacts"] = [None]
        elif failure == "duplicate":
            manifest["artifacts"].append(manifest["artifacts"][0])
        elif failure == "job":
            manifest["job_id"] = "another-job"
        else:
            key, value = {"name": ("name", "../private-reference.png"), "checksum": ("sha256", "../private"), "size": ("size", True)}[failure]
            manifest["artifacts"][0][key] = value
        path.write_text(json.dumps(manifest))
    for source in (job.evidence / "shots").iterdir():
        source.write_bytes(job.image + b"new source identity")
    assert get_executor(executor_http) == job.report
    digest = exported["artifacts"][0]["sha256"]
    with pytest.raises(urllib.error.HTTPError) as error:
        get_executor(executor_http, "/artifacts/" + digest)
    assert error.value.code == 404
    assert (job.folder / "result.json").read_bytes() == job.original


def test_failed_snapshot_publication_can_retry_without_changing_measurement(screenshot_job, executor_http, monkeypatch):
    """A failed optional write leaves no partially committed snapshot to advertise."""
    from llm4ad.gateway.examples.hgamey import evidence

    job = screenshot_job
    persist = evidence.persist

    def cannot_publish(path, value):
        raise OSError("synthetic snapshot storage failure")

    monkeypatch.setattr(evidence, "persist", cannot_publish)
    assert get_executor(executor_http) == job.report
    assert not (job.folder / "screenshot-export").exists()
    assert not list(job.folder.glob(".screenshot-export-*"))
    assert (job.folder / "result.json").read_bytes() == job.original
    monkeypatch.setattr(evidence, "persist", persist)
    exported = get_executor(executor_http)
    assert len(exported["artifacts"]) == 2
    for artifact in exported["artifacts"]:
        assert get_executor(executor_http, "/artifacts/" + artifact["sha256"]) == job.image


def test_invalid_optional_snapshot_does_not_block_declared_traces(screenshot_job, executor_http):
    """An unsafe optional screenshot sidecar does not invalidate an owned native trace."""
    job = screenshot_job
    content = b"synthetic independent trace"
    trace = job.evidence / "trace.pftrace"
    trace.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    report = {**job.report, "artifacts": [{"name": trace.name, "sha256": digest, "size": len(content)}]}
    original = json.dumps(report).encode()
    (job.folder / "result.json").write_bytes(original)
    assert len(get_executor(executor_http)["artifacts"]) == 3
    snapshot = job.folder / "screenshot-export"
    private = job.folder / "private-snapshot"
    snapshot.rename(private)
    snapshot.symlink_to(private, target_is_directory=True)
    assert get_executor(executor_http) == report
    assert get_executor(executor_http, "/artifacts/" + digest) == content
    assert (job.folder / "result.json").read_bytes() == original


def test_leased_probe_runs_packaged_device_worker(tmp_path, monkeypatch):
    """Run the real queue/backend/worker modules with explicit fake phone CLIs."""
    from llm4ad.gateway.examples.hgamey.execution import Engine

    tools = tmp_path / "tools"
    tools.mkdir()
    phonefarm = tools / "phonefarm"
    phonefarm.write_text(
        f"#!{sys.executable}\n"
        "import fcntl, json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['lease', 'devices']:\n"
        "    print(json.dumps({'revision': 0, 'devices': []}))\n"
        "elif args[:2] == ['lease', 'devices-set']:\n"
        "    print(json.dumps({'registered': True}))\n"
        "else:\n"
        "    assert args[0] == 'lease' and '--' in args, args\n"
        "    lock = os.open(os.environ['FIXTURE_LEASE'], os.O_CREAT | os.O_RDWR, 0o600)\n"
        "    fcntl.flock(lock, fcntl.LOCK_EX)\n"
        "    os.set_inheritable(lock, True)\n"
        "    os.environ['PF_LOCK_FDS'] = str(lock)\n"
        "    command = args[args.index('--')+1:]\n"
        "    assert command[1:4] == ['-m', 'llm4ad.gateway.examples.hgamey.execution.executor', '_device']\n"
        "    os.execv(command[0], command)\n"
    )
    adb = tools / "fake-adb"
    adb.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "for fd in os.environ['PF_LOCK_FDS'].split(','):\n"
        "    try:\n"
        "        os.fstat(int(fd))\n"
        "    except OSError:\n"
        "        pass\n"
        "    else:\n"
        "        raise SystemExit('ADB inherited the phone lease')\n"
        "with Path(os.environ['FIXTURE_ADB_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps(args)+'\\n')\n"
        "if args[:1] == ['connect']:\n"
        "    assert args[1] == '192.0.2.10:5555'\n"
        "    print('connected')\n"
        "else:\n"
        "    assert args[:2] == ['-s', '192.0.2.10:5555']\n"
        "    if args[2:] == ['get-state']:\n"
        "        print('device')\n"
        "    elif args[-1] == 'ro.serialno':\n"
        "        print('fixture-serial')\n"
        "    else:\n"
        "        print('Fixture model / uid=0(root)')\n"
    )
    for path in (phonefarm, adb):
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ADB_BIN", str(adb))
    monkeypatch.setenv("FIXTURE_LEASE", str(tmp_path / "lease"))
    monkeypatch.setenv("FIXTURE_ADB_LOG", str(tmp_path / "adb.jsonl"))
    devices = tmp_path / "devices.json"
    devices.write_text(json.dumps({"version": 1, "devices": [{
        "id": "fixture", "label": "Synthetic phone", "host": "192.0.2.10", "port": 5555,
        "expected_serial": "fixture-serial", "enabled": True,
    }]}))
    engine = Engine(tmp_path / "queue", devices)
    try:
        accepted = engine.submit({"operation": "probe"})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            observed = engine.get(accepted["id"])
            if observed["state"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        folder = Path(accepted["evidence_directory"])
        assert observed["state"] == "completed", {"job": observed, "stderr": (folder / "worker.stderr").read_text()}
        assert observed["cleanup_status"] == "completed"
        result = json.loads((folder / "result.json").read_text())
        assert result["root_verified"] is True
        assert result["connection"]["physical_serial"] == "fixture-serial"
        assert len((tmp_path / "adb.jsonl").read_text().splitlines()) == 4
    finally:
        engine.close()
