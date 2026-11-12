"""Persistent serialized execution, idempotency and restart reconciliation."""

import copy
import fcntl
import hashlib
import json
import queue
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .backend import run_backend
from .contracts import FEATURES, IDEMPOTENCY_KEY, JOB_ID, BackendNotStartedError, SubmissionConflictError
from .device import cleanup_completed
from .devices import load_devices, prepare_submission
from .storage import freeze_shader_inputs, read_json, sync_directory, write_json


class Engine:
    """Serialize device jobs with durable idempotency and recovery records."""
    def __init__(self, data, config, runner=run_backend):
        """Recover queued jobs and hold the exclusive executor directory lock."""
        self.data, self.config, self.runner = Path(data), Path(config), runner
        self.jobs = self.data / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)
        self.guard = (self.data / "service.lock").open("a+")
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.guard.close()
            raise RuntimeError("another executor already owns this data directory") from None
        try:
            load_devices(self.config)
            self.pending, self.mutex, self.stopping = deque(), threading.Lock(), threading.Event()
            self.ready = threading.Condition(self.mutex)
            self.active, self.by_key, self.blocked_jobs = None, {}, set()
            recovered = []
            for folder in self.jobs.iterdir():
                if not JOB_ID.fullmatch(folder.name) or not (folder / "job.json").is_file():
                    continue
                job = read_json(folder / "job.json")
                token = job.get("idempotency_key")
                if token is not None:
                    if not isinstance(token, str) or not IDEMPOTENCY_KEY.fullmatch(token) or token in self.by_key:
                        raise ValueError("invalid or duplicate persisted idempotency key")
                    if not isinstance(job.get("submission_hash"), str) or not IDEMPOTENCY_KEY.fullmatch(job["submission_hash"]):
                        raise ValueError("invalid persisted submission hash")
                    self.by_key[token] = folder.name
                if job["state"] in ("running", "cancel_requested"):
                    job.update(
                        state="interrupted",
                        cleanup_status="uncertain",
                        error="executor restarted; cleanup unknown, original evidence retained, task not replayed",
                        finished_at=time.time(),
                    )
                    write_json(folder / "job.json", job)
                if job["state"] in ("uncertain", "interrupted") or job.get("cleanup_status") == "uncertain":
                    self.blocked_jobs.add(folder.name)
                elif job["state"] == "queued":
                    recovered.append(job)
            for job in sorted(recovered, key=lambda j: j["created_at"]):
                self.pending.append(job["id"])
            self.thread = threading.Thread(target=self._work, name="single-phone-worker")
            self.thread.start()
        except BaseException:
            self.guard.close()
            raise

    def submit(self, body):
        """Persist a validated immutable submission before making it runnable."""
        if not isinstance(body, dict):
            raise ValueError("submission must be an object")
        body = copy.deepcopy(body)
        has_token = "idempotency_key" in body
        token = body.pop("idempotency_key", None)
        if has_token and (not isinstance(token, str) or not IDEMPOTENCY_KEY.fullmatch(token)):
            raise ValueError("idempotency_key must be 64 lowercase hexadecimal characters")
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest()
        with self.mutex:
            if token in self.by_key:
                job = self.get(self.by_key[token])
                if job["submission_hash"] != digest:
                    raise SubmissionConflictError("idempotency_key already used with a different submitted body")
                return job
            if self.stopping.is_set():
                raise RuntimeError("executor is stopping")
            if len(self.pending) >= 64:
                raise queue.Full("executor queue is full")
            frozen = prepare_submission(body, load_devices(self.config))
            frozen.update(submitted_body=body, submission_hash=digest, idempotency_key=token)
            key = uuid.uuid4().hex
            folder = self.jobs / key
            folder.mkdir()
            sync_directory(self.jobs)
            freeze_shader_inputs(folder, frozen)
            if frozen["submission"]["operation"] == "game-ab":
                from llm4ad.gateway.examples.hgamey.protocol import game_ab

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
            }
            try:
                write_json(folder / "job.json", job)
            finally:
                # A directory fsync failure can follow a successful atomic rename.
                if (folder / "job.json").is_file():
                    if token is not None:
                        self.by_key[token] = key
                    self.pending.append(key)
                    self.ready.notify()
            return job

    def get(self, key):
        """Read an existing job by its validated identifier."""
        if not JOB_ID.fullmatch(key):
            raise KeyError("invalid job id")
        path = self.jobs / key / "job.json"
        if not path.is_file():
            raise KeyError("unknown job")
        return read_json(path)

    def get_by_key(self, token):
        """Find the original job accepted for an idempotent submission."""
        if not isinstance(token, str) or not IDEMPOTENCY_KEY.fullmatch(token):
            raise KeyError("invalid idempotency key")
        with self.mutex:
            if token not in self.by_key:
                raise KeyError("unknown idempotency key")
            return self.get(self.by_key[token])

    def cancel(self, key):
        """Cancel pending work or record cancellation of an active device job."""
        with self.mutex:
            job = self.get(key)
            if job["state"] == "queued":
                job.update(
                    state="cancelled", cancel_requested_at=time.time(), finished_at=time.time(), cleanup_status="completed"
                )
            elif job["state"] == "running":
                job.update(state="cancel_requested", cancel_requested_at=time.time())
            else:
                if job["state"] == "cancelled" and key in self.pending:
                    self.pending.remove(key)
                return job
            write_json(self.jobs / key / "job.json", job)
            if job["state"] == "cancelled":
                self.pending.remove(key)
            return job

    def health(self):
        """Report queue activity and jobs requiring device restoration."""
        with self.mutex:
            return {
                "status": "blocked" if self.blocked_jobs else "ok",
                "max_active_jobs": 1,
                "active_job": self.active,
                "queued": len(self.pending),
                "transport": "wireless_adb",
                "features": FEATURES,
                "blocked_jobs": sorted(self.blocked_jobs),
            }

    def resolve(self, key, confirmation):
        """Release quarantined work after an explicit local operator confirmation.

        Args:
            key: Interrupted or uncertain executor job ID.
            confirmation: Boolean assertions that old work stopped and was restored.

        Returns:
            Terminal metadata retaining operator attribution and original evidence.
        """
        if not isinstance(confirmation, dict) or confirmation != {"confirmed_stopped": True, "confirmed_restored": True}:
            raise ValueError("confirm both stopped execution and restored device state")
        if any(type(value) is not bool for value in confirmation.values()):
            raise ValueError("operator confirmation values must be boolean true")
        with self.ready:
            job = self.get(key)
            if self.active == key or job["state"] not in {"uncertain", "interrupted"}:
                raise SubmissionConflictError("only inactive uncertain/interrupted jobs can be operator-resolved")
            job.update(
                state="cancelled",
                cleanup_status="completed",
                finished_at=time.time(),
                cleanup_confirmation={"source": "operator", "confirmed_at": time.time(), **confirmation},
            )
            write_json(self.jobs / key / "job.json", job)
            self.blocked_jobs.discard(key)
            self.ready.notify_all()
            return job

    def _work(self):
        while True:
            with self.ready:
                while (not self.pending or self.blocked_jobs) and not self.stopping.is_set():
                    self.ready.wait(timeout=0.2)
                if self.stopping.is_set():
                    return
                key = self.pending.popleft()
                folder = self.jobs / key
                job = self.get(key)
                if job["state"] != "queued":
                    continue
                job.update(state="running", started_at=time.time())
                write_json(folder / "job.json", job)
                self.active = key
            try:
                code = self.runner(folder)
                completed = cleanup_completed(folder, job["operation"])
                outcome = {"state": "completed" if code in (0, 1) else "failed", "backend_exit_code": code}
            except BackendNotStartedError as error:
                completed = True
                outcome = {"state": "failed", "error": str(error)}
            except Exception as error:
                completed = False
                outcome = {"state": "failed", "error": str(error)}
            with self.mutex:
                job = self.get(key)
                cancelled = job["state"] == "cancel_requested"
                job.update(outcome, finished_at=time.time(), cleanup_status="completed" if completed else "uncertain")
                if not completed:
                    job["state"] = "uncertain"
                    self.blocked_jobs.add(key)
                elif cancelled:
                    job["state"] = "cancelled"
                write_json(folder / "job.json", job)
                self.active = None

    def close(self):
        """Stop accepting work and join the worker before releasing its lock."""
        with self.ready:
            self.stopping.set()
            self.ready.notify_all()
        self.thread.join()
        self.guard.close()
