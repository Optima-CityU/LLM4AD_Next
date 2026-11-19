"""Durable native job receipts shared by direct workers and the private API."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .backend import run_backend
from .contracts import IDEMPOTENCY_KEY, JOB_ID, BackendNotStartedError, SubmissionConflictError
from .device import cleanup_completed
from .devices import load_devices, prepare_submission
from .storage import freeze_shader_inputs, lock_exclusive, read_json, sync_directory, write_json


class JobStore:
    """Persist immutable inputs without creating a scheduler or worker thread.

    Args:
        data: Shared executor receipt directory.
        config: Operator-owned physical device configuration.
    """

    def __init__(self, data, config):
        """Open the common receipt location without scheduling device work."""
        self.data, self.config = Path(data), Path(config)
        self.jobs = self.data / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        """Serialize receipt publication across SDK workers and HTTP clients."""
        with (self.data / "receipts.lock").open("a+") as guard:
            lock_exclusive(guard)
            yield

    def records(self):
        """Read durable records without inferring execution from a thread."""
        return [read_json(path) for path in self.jobs.glob("*/job.json") if JOB_ID.fullmatch(path.parent.name)]

    def get(self, key):
        """Read one validated native operation identity."""
        if not isinstance(key, str) or not JOB_ID.fullmatch(key):
            raise KeyError("invalid job id")
        path = self.jobs / key / "job.json"
        if not path.is_file():
            raise KeyError("unknown job")
        return read_json(path)

    def get_by_key(self, token):
        """Find the original operation after a lost submission response."""
        if not isinstance(token, str) or not IDEMPOTENCY_KEY.fullmatch(token):
            raise KeyError("invalid idempotency key")
        matches = [job for job in self.records() if job.get("idempotency_key") == token]
        if len(matches) > 1:
            raise ValueError("duplicate persisted idempotency key")
        if not matches:
            raise KeyError("unknown idempotency key")
        return matches[0]

    def prepare(self, body, *, direct=False):
        """Freeze one submission before returning its durable identity.

        Args:
            body: Validated private API request with an optional idempotency key.
            direct: Whether the already assigned SDK worker owns execution.

        Returns:
            The original or newly persisted queued operation receipt.
        """
        if not isinstance(body, dict):
            raise ValueError("submission must be an object")
        body = copy.deepcopy(body)
        has_token = "idempotency_key" in body
        token = body.pop("idempotency_key", None)
        if has_token and (not isinstance(token, str) or not IDEMPOTENCY_KEY.fullmatch(token)):
            raise ValueError("idempotency_key must be 64 lowercase hexadecimal characters")
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        with self.transaction():
            if token:
                try:
                    previous = self.get_by_key(token)
                except KeyError:
                    previous = None
                if previous:
                    if previous["submission_hash"] != digest:
                        raise SubmissionConflictError("idempotency_key already used with a different submitted body")
                    return previous
            frozen = prepare_submission(body, load_devices(self.config))
            frozen.update(submitted_body=body, submission_hash=digest, idempotency_key=token)
            key = uuid.uuid4().hex
            folder = self.jobs / key
            folder.mkdir()
            sync_directory(self.jobs)
            freeze_shader_inputs(folder, frozen)
            if frozen["submission"]["operation"] == "game-ab":
                from ..protocol import game_ab

                game_ab.freeze_inputs(folder, frozen)
            write_json(folder / "input.json", frozen)
            job = {
                "id": key,
                "state": "queued",
                "device_id": frozen["device"]["id"],
                "endpoint": frozen["device"]["endpoint"],
                "operation": frozen["submission"]["operation"],
                "created_at": time.time(),
                "evidence_directory": str(folder),
                "submission_hash": digest,
                "idempotency_key": token,
                "cleanup_status": "pending",
                "dispatch": "direct" if direct else "standalone",
            }
            write_json(folder / "job.json", job)
            return job


def execute_job(folder, runner=None):
    """Run one native operation in the calling SDK worker, without another pool.

    Args:
        folder: Durable native operation directory.
        runner: Optional synchronous backend used by deterministic tests.

    Returns:
        The committed result receipt. Measurement bytes remain unchanged.
    """
    folder = Path(folder)
    with (folder / "execution.lock").open("a+") as guard:
        try:
            lock_exclusive(guard, nonblocking=True)
        except BlockingIOError:
            return read_json(folder / "job.json")
        return _execute_job(folder, runner)


def _execute_job(folder, runner):
    """Commit results while the caller retains exclusive operation ownership."""
    if isinstance(runner, str):
        module, name = runner.split(":", 1)
        runner = importlib.import_module(module)
        for part in name.split("."):
            runner = getattr(runner, part)
    job = read_json(folder / "job.json")
    if job["state"] != "queued":
        return job
    job.update(state="running", started_at=time.time())
    write_json(folder / "job.json", job)
    try:
        try:
            _restore_prior_jobs(folder, job)
        except Exception as error:
            raise BackendNotStartedError("Prior device restoration is unavailable: " + str(error)) from error
        code = (runner or run_backend)(folder)
        completed = cleanup_completed(folder, job["operation"])
        outcome = {"state": "completed" if code in (0, 1) else "failed", "backend_exit_code": code}
    except BackendNotStartedError as error:
        completed = True
        outcome = {"state": "failed", "error": str(error)}
        preparation = {
            "schema": "hgamey/device-preparation@1",
            "job_id": folder.name,
            "device_id": job["device_id"],
            "device_started": False,
        }
        write_json(folder / "device-preparation.json", preparation)
        write_json(
            folder / "result.json",
            {
                "status": "ERROR",
                "error": str(error),
                "external_cleanup_confirmed": True,
                "preparation": preparation,
            },
        )
    except Exception as error:
        completed = False
        outcome = {"state": "failed", "error": str(error)}
    current = read_json(folder / "job.json")
    cancelled = current["state"] == "cancel_requested"
    current.update(outcome, finished_at=time.time(), cleanup_status="completed" if completed else "uncertain")
    if not completed:
        current["state"] = "uncertain"
    elif cancelled:
        current["state"] = "cancelled"
    write_json(folder / "job.json", current)
    return current


def _restore_prior_jobs(folder, job):
    """Reconcile older same-device obligations before any new native mutation.

    SDK delivery order cannot move a recovery packet ahead of an already queued
    private request. This ownership check runs inside that assigned SDK worker.
    """
    expected = read_json(folder / "input.json")["device"]["expected_serial"]
    for path in sorted(folder.parent.glob("*/job.json")):
        if path.parent == folder or not JOB_ID.fullmatch(path.parent.name):
            continue
        previous = read_json(path)
        if previous.get("device_id") != job["device_id"] or previous.get("cleanup_status") == "completed":
            continue
        if previous.get("cleanup_status") != "uncertain" and previous.get("state") not in {
            "running",
            "cancel_requested",
            "interrupted",
            "uncertain",
        }:
            continue
        frozen = read_json(path.parent / "input.json")
        if frozen["device"]["expected_serial"] != expected:
            raise ValueError("Prior restoration belongs to a different physical device")
        if not restore_job(path.parent):
            raise RuntimeError("Native restoration remains unconfirmed for " + previous["id"])


def restore_job(folder):
    """Recover native owners after the SDK has terminated the original worker.

    Args:
        folder: Original operation directory retaining restoration evidence.

    Returns:
        Whether automatic native and foreground restoration is verified.
    """
    from .recovery import restore_job as restore

    return restore(Path(folder))
