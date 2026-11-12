"""Exercise the split executor with synthetic CLI tools and no device access."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(os.name == "nt", reason="phonefarm leases require POSIX")]


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
