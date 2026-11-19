"""Verify interrupted evidence reads without rerunning device acquisition."""

import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr/evidence_transfer.py"
SPEC = importlib.util.spec_from_file_location("hgamey_evidence_transfer", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class Device:
    """Serve fixed bytes, with controllable failed or corrupted reads."""

    def __init__(self, data, fail_at=None, corrupt=False):
        """Keep acquisition immutable while varying transfer behavior."""
        self.data, self.fail_at, self.corrupt = data, fail_at, corrupt
        self.reads = []

    def su(self, command, **kwargs):
        """Return the original physical and content identity."""
        assert "rm " not in command
        return f"physical-device\nboot-1\n{len(self.data)}\n{hashlib.sha256(self.data).hexdigest()}  /fixture/video.mp4\n"

    def adb(self, command, **kwargs):
        """Write a requested chunk, including partial output before a timeout."""
        assert kwargs["check"] is True
        args = shlex.split(command)
        read = args[args.index("-c") + 1]
        chunk_size = int(re.search(r"\bbs=(\d+)", read)[1])
        skip = int(re.search(r"\bskip=(\d+)", read)[1])
        self.reads.append(skip)
        target = Path(args[-1])
        data = self.data[skip * chunk_size:(skip + 1) * chunk_size]
        if skip == self.fail_at:
            target.write_bytes(data[:1])
            raise subprocess.TimeoutExpired("fixture-read", 60)
        target.write_bytes(bytes([data[0] ^ 1]) + data[1:] if self.corrupt else data)


def test_interrupted_read_retries_only_its_chunk(tmp_path, monkeypatch):
    """A lost chunk reply cannot append partial bytes or repeat earlier reads."""
    dev = Device(b"abcdefghijkl", fail_at=1)
    original = dev.adb

    def retry(command, **kwargs):
        try:
            return original(command, **kwargs)
        except subprocess.TimeoutExpired:
            dev.fail_at = None
            raise

    dev.adb = retry
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    path = tmp_path / "video.mp4"
    receipt = module.download_root_file(dev, "/fixture/video.mp4", path, chunk_size=4)
    assert dev.reads == [0, 1, 1, 2]
    assert path.read_bytes() == dev.data and receipt["complete"]
    assert not path.with_suffix(".mp4.part").exists()


def test_restart_continues_acknowledged_bytes(tmp_path, monkeypatch):
    """A later invocation resumes the same file without replaying acquisition."""
    dev = Device(b"abcdefghijkl", fail_at=1)
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    path = tmp_path / "video.mp4"
    with pytest.raises(subprocess.TimeoutExpired):
        module.download_root_file(dev, "/fixture/video.mp4", path, chunk_size=4, retries=1)
    assert not path.exists()
    checkpoint = json.loads(path.with_suffix(".mp4.transfer.json").read_text())
    assert checkpoint["bytes_complete"] == 4 and checkpoint["complete"] is False
    # Simulate a process dying after append, before checkpoint commit.
    with path.with_suffix(".mp4.part").open("ab") as stream:
        stream.write(b"uncommitted")
    dev.fail_at = None
    module.download_root_file(dev, "/fixture/video.mp4", path, chunk_size=4)
    assert dev.reads == [0, 1, 1, 2]
    assert path.read_bytes() == dev.data


def test_changed_remote_cannot_be_combined_with_partial_evidence(tmp_path):
    """A new recording or device identity cannot reuse the old prefix."""
    dev = Device(b"abcdefghijkl", fail_at=1)
    path = tmp_path / "video.mp4"
    with pytest.raises(subprocess.TimeoutExpired):
        module.download_root_file(dev, "/fixture/video.mp4", path, chunk_size=4, retries=1)
    dev.data = b"different-video"
    with pytest.raises(ValueError, match="identity changed"):
        module.download_root_file(dev, "/fixture/video.mp4", path, chunk_size=4)
    assert not path.exists()


def test_corrupted_bytes_are_never_published(tmp_path):
    """Even successful ADB exits cannot substitute for a matching full digest."""
    dev = Device(b"abcdefgh", corrupt=True)
    path = tmp_path / "video.mp4"
    with pytest.raises(ValueError, match="checksum"):
        module.download_root_file(dev, "/fixture/video.mp4", path, chunk_size=4)
    assert not path.exists()
    assert not json.loads(path.with_suffix(".mp4.transfer.json").read_text())["complete"]
