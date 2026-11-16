"""Private executor compatibility facade over SDK durable resource executors."""

from __future__ import annotations

import fcntl
import logging
import threading
import time

from .backend import run_backend
from .contracts import FEATURES, SubmissionConflictError
from .jobs import JobStore
from .storage import write_json

logger = logging.getLogger(__name__)
NATIVE_TARGET = "llm4ad.gateway.examples.hgamey.execution.jobs:"


class Engine:
    """Expose native receipts while the SDK owns every standalone worker.

    Args:
        data: Shared native receipt directory.
        config: Operator-owned physical device configuration.
        runner: Importable synchronous backend for standalone execution.
        executor_factory: Optional SDK executor factory for deterministic tests.
    """

    def __init__(self, data, config, runner=run_backend, *, executor_factory=None, defer_dispatch=False):
        """Open durable SDK slots without starting a custom queue or thread."""
        from .devices import load_devices

        if executor_factory is None:
            from llm4ad.gateway.sdk.scheduling import DurableExecutor

            executor_factory = DurableExecutor
        self.store = JobStore(data, config)
        self.data, self.config, self.jobs = self.store.data, self.store.config, self.store.jobs
        self.devices = {d["id"]: d for d in load_devices(self.config) if d["enabled"]}
        if not self.devices:
            raise ValueError("executor requires at least one enabled device")
        self.guard = (self.data / "service.lock").open("a+")
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.guard.close()
            raise RuntimeError("another executor already owns this data directory") from None
        self.mutex, self.stopping = threading.RLock(), threading.Event()
        self.closed, self.runner = False, runner
        self._dispatch_ready = not defer_dispatch
        self.executors = {}
        self._observed = set()
        self._pump_lock = threading.Lock()
        self._known, self.errors = {}, {}
        self._cancelling = set()
        self._cancel_locks = {key: threading.Lock() for key in self.devices}
        try:
            self.executors = {key: executor_factory(self.data / "scheduling" / key, concurrency=1) for key in self.devices}
            for executor in self.executors.values():
                # Only the SDK controller may release recovered foreign packets.
                if defer_dispatch or any(
                    not packet.get("target", "").startswith(NATIVE_TARGET)
                    for packet in executor.entries().values()
                ):
                    executor.hold("sdk-restoration")
                if any(packet.get("target") == "llm4ad.gateway.examples.hgamey.runtime:run_startup_operation"
                       for packet in executor.entries().values()):
                    executor.hold("hgamey-startup-restoration")
            for job in sorted(self.store.records(), key=lambda j: j["created_at"]):
                self._known[job["id"]] = job
                if job.get("dispatch") == "direct":
                    continue
                if job["state"] == "queued":
                    self._submit(job)
                elif job["state"] in {"running", "cancel_requested", "uncertain", "interrupted"}:
                    self._recover(job)
            for executor in self.executors.values():
                for key, packet in executor.entries().items():
                    if packet.get("target", "").startswith(NATIVE_TARGET):
                        self._observe(executor, key)
                self._poll_executor(executor)
        except BaseException:
            self.close()
            raise

    @property
    def active_jobs(self):
        """Derive active resource observations from durable receipts."""
        return {job["device_id"]: job["id"] for job in self.store.records() if job["state"] in {"running", "cancel_requested"}}

    @property
    def active(self):
        """Retain the legacy oldest-active observation."""
        return next(iter(self.active_jobs.values()), None)

    @property
    def blocked_jobs(self):
        """Expose outstanding restoration receipts for evidence retention."""
        return {job["id"] for job in self.store.records() if job.get("cleanup_status") == "uncertain"}

    def activate(self):
        """Open integrated dispatch after every SDK binding has been restored.

        This does not clear a resource pause or its persisted cleanup debts.
        """
        self._dispatch_ready = True
        self.poll()

    def _poll_executor(self, executor):
        """Keep health/API polling behind the integrated startup fence."""
        if self._dispatch_ready:
            executor.poll()

    def _submit(self, job):
        """Delegate runnable work to its SDK resource slot."""
        from .jobs import execute_job

        executor = self.executors[job["device_id"]]
        executor.submit(
            job["id"], execute_job,
            args=(str(self.jobs / job["id"]),), kwargs={"runner": self._runner_spec()},
        )
        self._observe(executor, job["id"])

    def _runner_spec(self):
        """Persist only an importable backend reference in SDK task arguments."""
        if isinstance(self.runner, str):
            return self.runner
        if self.runner is run_backend:
            return None
        name = getattr(self.runner, "__qualname__", "")
        if not name or "<locals>" in name:
            raise ValueError("standalone backend must be an importable callable")
        return self.runner.__module__ + ":" + name

    def _recover(self, job):
        """Schedule automatic restoration through the same SDK slot."""
        from .jobs import restore_job

        count = job.get("cleanup_attempts", 0)
        if count >= 3 and job.get("next_probe_at", 0) > time.time():
            return
        if count >= 3:
            job["cleanup_probes"] = job.get("cleanup_probes", 0) + 1
        else:
            job["cleanup_attempts"] = count + 1
        job["next_probe_at"] = time.time() + 5
        write_json(self.jobs / job["id"] / "job.json", job)
        executor = self.executors[job["device_id"]]
        executor.submit(
            "restore-" + job["id"], restore_job,
            args=(str(self.jobs / job["id"]),),
        )
        self._observe(executor, "restore-" + job["id"])

    def _observe(self, executor, key):
        """Attach durable receipt reconciliation to an SDK completion."""
        identity = (id(executor), key)
        if identity in self._observed:
            return
        self._observed.add(identity)
        future = executor.future(key)
        if future is not None:
            future.add_done_callback(lambda done: self._complete(executor, key, done))

    def _complete(self, executor, key, future):
        """Notify a guarded pump; poll revisits any unfinished receipt commit."""
        try:
            self._drain(executor, key, future)
        except Exception as error:
            self.errors[key] = {"error_type": type(error).__name__, "message": str(error)}
            logger.exception("Native completion receipt retained for retry: %s", key)

    def _drain(self, executor, key, future):
        """Commit native outcomes without owning another scheduling lifecycle."""
        from llm4ad.gateway.sdk.retries import retry_read

        if not self._pump_lock.acquire(blocking=False):
            return
        try:
            self._drain_owned(executor, key, future, retry_read)
        finally:
            self._pump_lock.release()

    def _drain_owned(self, executor, key, future, retry_read):
        """Reconcile exactly the Future generation still retained by the SDK."""
        if self.closed:
            return
        if executor.future(key) is not future:
            return
        restoring = key.startswith("restore-")
        native_id = key[8:] if restoring else key
        if native_id in self._cancelling:
            return
        try:
            job = retry_read(self.store.get, native_id)
        except Exception as error:
            job = dict(self._known.get(native_id, {}))
            if not job:
                raise
            job.update(state="failed", cleanup_status="uncertain", error_type="NativeReceiptFailure",
                       error=str(error), started_at=job.get("started_at") or time.time(), finished_at=time.time())
            write_json(self.jobs / native_id / "job.json", job)
        if not restoring and job["state"] == "cancel_requested":
            return
        try:
            future.result()
        except BaseException as error:
            job.update(state="interrupted", cleanup_status="uncertain", error=str(error), finished_at=time.time())
            write_json(self.jobs / native_id / "job.json", job)
        if job.get("cleanup_status") != "completed" and job.get("cleanup_attempts", 0) >= 3:
            job.update(state="failed", error_type="NativeCleanupExhausted",
                       error=job.get("cleanup_error") or job.get("error") or "Native cleanup attempts exhausted",
                       finished_at=time.time(), next_probe_at=time.time() + 5)
            write_json(self.jobs / native_id / "job.json", job)
        self._known[native_id] = job
        executor.ack(key)
        self._observed.discard((id(executor), key))
        if job.get("cleanup_status") != "completed":
            self._recover(job)
        self.errors.pop(key, None)
        self._poll_executor(executor)

    def poll(self):
        """Acknowledge SDK completions only after native receipts are durable."""
        with self.mutex:
            if self.closed:
                return
            for executor in self.executors.values():
                for key, packet in list(executor.entries().items()):
                    if not packet.get("target", "").startswith(NATIVE_TARGET):
                        continue
                    self._observe(executor, key)
                    future = executor.future(key)
                    if future is not None and future.done():
                        self._complete(executor, key, future)
                self._poll_executor(executor)
            for job in self.store.records():
                if (job["id"] in self._cancelling or job.get("dispatch") == "direct"
                    or job.get("cleanup_status") != "uncertain"):
                    continue
                executor = self.executors[job["device_id"]]
                if ("restore-" + job["id"] not in executor.entries()
                    and job.get("next_probe_at", 0) <= time.time()):
                    self._recover(job)
                    self._poll_executor(executor)

    def submit(self, body):
        """Preserve idempotent private submissions without a second scheduler."""
        with self.mutex:
            if self.stopping.is_set():
                raise RuntimeError("executor is stopping")
            self.poll()
            job = self.store.prepare(body)
            self._known[job["id"]] = job
            if job["state"] == "queued" and job.get("dispatch") != "direct":
                self._submit(job)
                self._poll_executor(self.executors[job["device_id"]])
            return job

    def get(self, key):
        """Read current committed state after observing SDK completions."""
        self.poll()
        return self.store.get(key)

    def get_by_key(self, token):
        """Preserve lookup after a lost private API submit response."""
        self.poll()
        return self.store.get_by_key(token)

    def cancel(self, key):
        """Restore one fenced device without blocking independent device queues.

        Args:
            key: Original native operation identity to cancel.

        Returns:
            Its durable cancellation or pending-restoration receipt. The device
            slot remains reserved until its original worker is stopped and the
            restoration receipt has been committed.
        """
        from .jobs import restore_job

        with self.mutex:
            device_id = self.store.get(key)["device_id"]
        with self._cancel_locks[device_id]:
            with self.mutex:
                job = self.store.get(key)
                if job["state"] in {"completed", "failed", "cancelled"} and job.get("cleanup_status") == "completed":
                    return job
                if job.get("dispatch") == "direct" and job["state"] == "running":
                    job.update(state="cancel_requested", cancel_requested_at=time.time())
                    write_json(self.jobs / key / "job.json", job)
                    return job
                executor = self.executors[device_id]
                hold = "native-cancellation:" + key
                executor.hold(hold)
                self._cancelling.add(key)
                job.update(state="cancel_requested", cancel_requested_at=time.time(), cleanup_status="uncertain")
                write_json(self.jobs / key / "job.json", job)
            try:
                executor.cancel(key)
                restore_job(self.jobs / key)
                with self.mutex:
                    job = self.store.get(key)
                    executor.ack(key)
                    self._observed.discard((id(executor), key))
                    self._known[key] = job
                    if job.get("cleanup_status") != "completed":
                        self._recover(job)
            finally:
                with self.mutex:
                    self._cancelling.discard(key)
                    executor.release(hold)
            with self.mutex:
                self._poll_executor(executor)
                return self.store.get(key)

    def resolve(self, key, confirmation):
        """Retain explicit operator acknowledgement for legacy private clients."""
        if (confirmation != {"confirmed_stopped": True, "confirmed_restored": True}
            or any(type(v) is not bool for v in confirmation.values())):
            raise ValueError("confirm both stopped execution and restored device state")
        with self.mutex:
            job = self.store.get(key)
            if key in self._cancelling or job["state"] not in {"uncertain", "interrupted"}:
                raise SubmissionConflictError("only inactive uncertain/interrupted jobs can be resolved")
            job.update(state="cancelled", cleanup_status="completed", finished_at=time.time(),
                       cleanup_confirmation={"source": "operator", "confirmed_at": time.time(), **confirmation})
            write_json(self.jobs / key / "job.json", job)
            return job

    def health(self):
        """Report SDK slot liveness and automatic restoration progress."""
        self.poll()
        jobs = self.store.records()
        active = self.active_jobs
        blocked = {job["device_id"] for job in jobs if job.get("cleanup_status") == "uncertain"}
        return {
            "status": "degraded" if blocked else "ok", "max_active_jobs": len(self.devices),
            "active_job": self.active, "active_jobs": active,
            "queued": sum(job["state"] == "queued" for job in jobs),
            "transport": "wireless_adb", "features": FEATURES, "blocked_jobs": sorted(self.blocked_jobs),
            "errors": dict(self.errors),
            "devices": {key: {"status": "recovering" if key in blocked else "ok", "active_job": active.get(key),
                              "queued": sum(j["state"] == "queued" and j["device_id"] == key for j in jobs),
                              "worker_alive": not self.closed} for key in self.devices},
        }

    def close(self):
        """Leave unacknowledged SDK work durable while closing resource pools."""
        if self.closed:
            return
        self.stopping.set()
        self.closed = True
        for executor in self.executors.values():
            executor.close()
        self.guard.close()
