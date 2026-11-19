"""Outbound gateway with process isolation, local journaling and reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import time
import uuid
import zipfile
from collections.abc import Callable, Coroutine
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

import aiohttp

from llm4ad.gateway.sdk.bundles import extract_candidate
from llm4ad.gateway.sdk.contracts import GatewayEvaluator, RemoteJob, load_evaluator, normalize_spec
from llm4ad.gateway.sdk.errors import EvaluationError, EvaluationUnavailableError
from llm4ad.gateway.sdk.executions import MAX_EXECUTIONS, TERMINAL, Executions
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import (
    execution_control,
    invoke,
    owned_worker,
    prepare_execution,
    request_stop,
    run_operation,
)
from llm4ad.gateway.sdk.local_logs import capture_worker_logs
from llm4ad.gateway.sdk.reporting import classify_report, exception_report, prepare_result

if TYPE_CHECKING:
    from llm4ad.gateway.sdk.scheduling import DurableExecutor


class _TransportError(TypedDict):
    """Expose a transport error without sensitive request data."""

    type: str
    status: NotRequired[int]


class AttemptLeaseExpiredError(Exception):
    """Stop transport work whose locally measured platform lease has expired."""


def stop_process(process):
    """Cancel a pool-owned execution handle without managing process groups."""
    if process is not None:
        process.terminate()


class PoolHandle:
    """Expose current execution activity to existing monitoring and retention."""

    def __init__(self, executor, key, future):
        """Retain a durable receipt until the controller acknowledges its result."""
        self.executor, self.key, self.future = executor, key, future
        self.pid = None

    def is_alive(self):
        """Return whether assigned computation has an outstanding future."""
        return not self.future.done()

    @property
    def exitcode(self):
        """Expose a diagnostic exit indicator independently of evaluation success."""
        if not self.future.done():
            return None
        return 1 if self.future.cancelled() or self.future.exception() else 0

    def join(self, timeout=None):
        """Wait through the public Future API when a caller needs termination."""
        with suppress(Exception):
            self.future.result(timeout=timeout)

    def terminate(self):
        """Stop this resource slot and its owned descendants through loky."""
        self.executor.cancel(self.key)

    kill = terminate


def execute(journal_path: str, spec: str, payload: dict, recovery: bool = False):
    """Execute one fenced generation inside an SDK-assigned loky worker."""
    journal = Journal(journal_path)
    if journal.lease_remaining(payload) == 0:
        return {"stale": True}
    with owned_worker(
        journal,
        payload["id"],
        payload["attempt_id"],
        recovery=recovery,
        execution_id=payload.get("execution_id"),
    ) as accepted:
        if not accepted:
            return {"stale": True}
        with capture_worker_logs(journal, payload):
            return _execute_owned(journal, spec, payload, recovery)


def _execute_owned(journal, spec: str, payload: dict, recovery: bool = False):
    """Return an outcome; only the controller persists terminal queue decisions."""
    reporter = Reporter(journal, payload["id"], payload["attempt_id"], payload.get("execution_id"))
    started = time.monotonic()
    job = None
    try:
        job = RemoteJob.model_validate(payload)
        os.chdir(job.project_root)
        Path(job.artifact_dir).mkdir(exist_ok=True)
        reporter.progress("Evaluation started", metadata={"execution_id": job.execution_id})
        adapter = load_evaluator(normalize_spec(spec))

        async def evaluate():
            value = await invoke(adapter.evaluate(job, reporter))
            if value is None:
                raise EvaluationUnavailableError("Evaluator returned no outcome; retrying this evaluation")
            return value

        result = asyncio.run(run_operation(adapter, "evaluate", job, reporter, evaluate))
        report = prepare_result(job, reporter, result, (time.monotonic() - started) * 1000)
        retryable = bool(
            payload.get("managed_attempt") and not report.get("success", True)
            and report["metadata"]["failure_category"] in {"infrastructure", "evaluator"}
        )
        return {"report": report, "retryable": retryable}
    except BaseException as exc:
        import traceback

        with suppress(OSError, ValueError):
            traceback.print_exception(exc)
        metadata = dict(exc.metadata) if isinstance(exc, EvaluationError) else {}
        try:
            report = exception_report(job, reporter, exc, (time.monotonic() - started) * 1000)
        except Exception as reporting_error:
            report = {
                "success": False,
                "metrics": {},
                "message": str(exc) or type(exc).__name__,
                "error_message": str(exc) or type(exc).__name__,
                "error_type": type(exc).__name__,
                "metadata": {**metadata, "reporting_error": type(reporting_error).__name__},
            }
        category = classify_report(report, exc)
        retryable = (
            category in {"infrastructure", "evaluator"} if payload.get("managed_attempt")
            else category not in {"candidate", "configuration"} and (
                isinstance(exc, (OSError, TimeoutError, aiohttp.ClientError, EvaluationUnavailableError))
                or not isinstance(exc, Exception)
            )
        )
        return {"report": report, "retryable": retryable}


class Gateway:
    """Own transport and local execution while adapters own evaluation logic."""

    cleanup_managed: bool
    local_log_sources: Callable[[dict, dict], dict[str, Path]]

    def __init__(
        self,
        server: str,
        name: str,
        allowed_users: list[str],
        evaluator: str,
        state_dir: Path,
        concurrency: int = 1,
        *,
        adapter: GatewayEvaluator | None = None,
        persist_identity: bool = True,
    ):
        """Load a persistent identity, adapter and execution journal."""
        self.url = server.rstrip("/") + "/api/v1/llm4ad/evaluation-gateways"
        self.name, self.allowed_users = name, [str(uuid.UUID(value)) for value in allowed_users]
        self.spec = normalize_spec(evaluator)
        self.adapter = adapter if adapter is not None else load_evaluator(self.spec)
        self._cleanup_timeout()
        self.capacity = concurrency
        self.root = state_dir.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not persist_identity:
            self.identity = {"id": str(uuid.uuid4()), "secret": "", "server": self.url}
        else:
            identity_file = self.root / "identity.json"
            if not identity_file.exists():
                identity = {
                    "id": str(uuid.uuid4()),
                    "secret": secrets.token_urlsafe(48),
                    "server": self.url,
                }
                fd = os.open(identity_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump(identity, stream)
            self.identity = json.loads(identity_file.read_text())
            if self.identity["server"] != self.url:
                raise ValueError("Use a separate state directory for each platform")
        self.journal = Journal(self.root / "jobs.sqlite3")
        self.processes: dict[str, PoolHandle] = {}
        self.executions = Executions(self.journal)
        self.executor: DurableExecutor | None = None
        self.executor_owned = True
        self.restart_jobs = {
            r["id"] for r in self.journal.rows(pending_only=True) if r["state"] not in {"completed", "failed", "cancelled"}
        }
        self.wakeup = asyncio.Event()
        self._lease_wakeup = asyncio.Event()
        self.last_contact: float | None = None
        self.last_error: str | None = None
        self.admission_paused = False
        self.registration_last_success = None
        self.registration_last_error = None
        self.heartbeat_last_success = None
        self.heartbeat_last_error = None
        self.notifications_connected = False
        self.notifications_reconnecting = False
        self.notifications_last_error: _TransportError | None = None
        self._job_locks: dict[str, asyncio.Lock] = {}
        self._cancellations: dict[tuple[str, str], asyncio.Task[None]] = {}

    def _cancel_task[K, T](
        self, cache_name: str, key: K, operation: Callable[[], Coroutine[Any, Any, T]]
    ) -> asyncio.Task[T]:
        """Share in-flight cancellation work even when one waiting caller leaves."""
        cache: dict[K, asyncio.Task[T]] | None = getattr(self, cache_name, None)
        if cache is None:
            cache = {}
            setattr(self, cache_name, cache)
        task = cache.get(key)
        if task is None:
            task = asyncio.create_task(operation())
            cache[key] = task

            def finished(completed: asyncio.Task[T]) -> None:
                if cache.get(key) is completed:
                    cache.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        return task

    def pause_accepting(self) -> None:
        """Pause new claims while continuing assigned work and result delivery."""
        self.admission_paused = True

    def resume_accepting(self) -> None:
        """Resume new claims, waking dispatch only when admission was paused."""
        was_paused = getattr(self, "admission_paused", False)
        self.admission_paused = False
        if was_paused and (wakeup := getattr(self, "wakeup", None)):
            wakeup.set()

    def ready(self) -> bool:
        """Advertise admission only when this resource has no unconfirmed cleanup."""
        with self.journal.connect() as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cleanup_debts'").fetchone()
            debt = db.execute("SELECT 1 FROM cleanup_debts LIMIT 1").fetchone() if table else None
        executor = getattr(self, "executor", None)
        if (
            getattr(self, "admission_paused", False) or debt
            or (executor is not None and (executor.paused or getattr(executor, "cleanup_debts", set())))
        ):
            return False
        if executor is not None and getattr(executor, "resource_exclusive", False) and (
            executor.entries() or getattr(executor, "claimed_attempts", set())
        ):
            return False
        active = sum(row["state"] not in TERMINAL for row in self.journal.rows(pending_only=True))
        return active < self.capacity

    def _reserve_attempt(self, job):
        """Reserve shared physical admission before candidate download or worker enqueue."""
        executor = self.executor
        if job.get("managed_attempt") and executor is not None:
            if not hasattr(executor, "claimed_attempts"):
                executor.claimed_attempts = set()
            executor.claimed_attempts.add((str(self.root), job["id"], job["attempt_id"]))

    @staticmethod
    def execution_limit(payload: dict) -> int:
        """Leave total pooled attempts to the server while preserving legacy retries."""
        return 1 if payload.get("managed_attempt") else MAX_EXECUTIONS

    def transport_snapshot(self) -> dict:
        """Return safe transport observations without contacting the platform."""
        return {
            "registration": {
                "last_success": getattr(self, "registration_last_success", None),
                "last_error": getattr(self, "registration_last_error", None),
            },
            "heartbeat": {
                "last_success": getattr(self, "heartbeat_last_success", None),
                "last_error": getattr(self, "heartbeat_last_error", None),
            },
            "notifications": {
                "connected": getattr(self, "notifications_connected", False),
                "reconnecting": getattr(self, "notifications_reconnecting", False),
                "last_error": getattr(self, "notifications_last_error", None),
            },
            "last_contact": getattr(self, "last_contact", None),
            "last_error": getattr(self, "last_error", None),
        }

    @property
    def transport(self) -> dict:
        """Expose the controller's read-only transport snapshot contract."""
        return self.transport_snapshot()

    @staticmethod
    def _transport_error(error: Exception) -> _TransportError:
        """Keep exception types and HTTP status without URLs, headers or bodies."""
        value: _TransportError = {"type": type(error).__name__}
        if isinstance(error, aiohttp.ClientResponseError):
            value["status"] = error.status
        return value

    def _job_lock(self, job_id: str) -> asyncio.Lock:
        """Lazily create locks for older constructors and monkeypatched fixtures."""
        if not hasattr(self, "_job_locks"):
            self._job_locks = {}
        return self._job_locks.setdefault(job_id, asyncio.Lock())

    async def request_cancel(self, job_id: str, attempt_id: str) -> dict:
        """Durably stop only the authoritative local attempt, including offline work.

        Args:
            job_id: Locally accepted job identifier.
            attempt_id: Exact current execution attempt, never a browser payload.

        Returns:
            Job and attempt identifiers, current journal state, and status equal to
            cancelled, cancelling or already_finished.

        Raises:
            LookupError: The job is not present locally.
            ValueError: The requested attempt has been superseded.
        """
        with self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job,state,started FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise LookupError("Job is not present locally")
            payload = json.loads(row[0])
            if payload["attempt_id"] != attempt_id:
                raise ValueError("Execution attempt is no longer current")
            if row[1] in {"completed", "failed", "cancelled"}:
                return {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "state": row[1],
                    "status": "already_finished",
                }
            # Persist before the first await; a lost HTTP/UI caller cannot undo this stop.
            db.execute(
                "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,?,'cancel') "
                "ON CONFLICT(job_id,attempt_id) DO UPDATE SET stop_requested='cancel'",
                (
                    job_id,
                    attempt_id,
                    "cancelling",
                ),
            )
        await self.reconcile(None, {**payload, "state": "cancel_requested"})
        current = self.journal.get(job_id)
        if current is None or json.loads(current["job"])["attempt_id"] != attempt_id:
            raise ValueError("Execution attempt is no longer current")
        state = current["state"]
        status = (
            "cancelled" if state == "cancelled" else ("already_finished" if state in {"completed", "failed"} else "cancelling")
        )
        return {"job_id": job_id, "attempt_id": attempt_id, "state": state, "status": status}

    def _cleanup_timeout(self) -> float:
        """Return the bounded cleanup deadline declared by this adapter.

        Returns:
            A positive, finite timeout covering cancellation and its hooks.

        Raises:
            ValueError: The adapter configured an invalid cleanup deadline.
        """
        timeout = getattr(self.adapter, "cleanup_timeout", 30.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Evaluator cleanup_timeout must be a positive finite number")
        return float(timeout)

    def _executor(self) -> DurableExecutor:
        """Lazily open the SDK queue; services share it across resource bindings."""
        if self.executor is None:
            from .scheduling import DurableExecutor

            self.executor = DurableExecutor(self.root / "queue", concurrency=self.capacity)
            self.executor.resource_exclusive = False
            self.restore_pending()
        return self.executor

    def restore_pending(self):
        """Fence interrupted generations before any recovered queue can dispatch."""
        executor = self.executor
        if executor is None:
            return
        if not hasattr(executor, "cleanup_debts"):
            executor.cleanup_debts = set()
        with self.journal.connect() as db:
            executor.cleanup_debts.update(row[0] for row in db.execute("SELECT execution_id FROM cleanup_debts"))
        for key, packet in executor.entries().items():
            args = packet.get("args", [])
            if packet.get("target") != "llm4ad.gateway.sdk.gateway:execute" or not args or args[0] != self.journal.path:
                continue
            queued = args[2]
            current = self.journal.get(queued["id"])
            live = json.loads(current["job"]) if current else {}
            if (not current or current["state"] in TERMINAL or self.execution_key(live) != key) and self._debt_key(
                queued
            ) not in executor.cleanup_debts:
                executor.ack(key)
        for row in self.journal.rows(pending_only=True):
            job = json.loads(row["job"])
            self.executions.ensure(job, row)
            if row["state"] in TERMINAL:
                if self._debt_key(job) not in executor.cleanup_debts:
                    executor.ack(self.execution_key(job))
                continue
            self._reserve_attempt(job)
            if row["started"] is None and not job.get("execution_id"):
                continue
            control = execution_control(self.journal, job["id"], job["attempt_id"])
            if not control.get("stop_requested"):
                request_stop(self.journal, job["id"], job["attempt_id"], "interrupt")
                with self.journal.connect() as db:
                    db.execute(
                        "UPDATE execution_controls SET paused_at=COALESCE(controller_seen_at,?,paused_at) "
                        "WHERE job_id=? AND attempt_id=?",
                        (row["updated_at"], job["id"], job["attempt_id"]),
                    )
            debt_key = self._debt_key(job)
            with self.journal.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO cleanup_debts(execution_id,payload,saved) VALUES(?,?,?)",
                    (debt_key, json.dumps(job), row["saved"]),
                )
            executor.cleanup_debts.add(debt_key)
            # The journal decision and cleanup debt survive a crash before queue ACK.
            executor.ack(self.execution_key(job))
        with self.journal.connect() as db:
            executor.cleanup_debts.update(row[0] for row in db.execute("SELECT execution_id FROM cleanup_debts"))
        executor.paused = bool(executor.cleanup_debts)

    @staticmethod
    def execution_key(payload: dict) -> str:
        """Keep queue identity distinct from remote attempt identity."""
        return f"{payload['id']}:{payload['attempt_id']}:{payload.get('execution_id', '')}"

    def start_process(self, payload: dict, recovery: bool = False):
        """Reserve and enqueue a bounded local execution in the SDK resource pool."""
        executor = self._executor()
        if not self.has_execution_slot(payload["id"]):
            return
        row = self.journal.get(payload["id"])
        assert row is not None
        record = self.executions.ensure(payload, row)
        if self.journal.lease_remaining(payload) == 0 or record["executions"] >= self.execution_limit(payload):
            return
        if not prepare_execution(self.journal, payload["id"], payload["attempt_id"]):
            return
        source = Path(payload["project_root"])
        template = str(self.root / "work" / payload["id"] / payload["attempt_id"] / "{execution_id}")
        job = self.executions.begin(payload, template)
        if job is None:
            return
        destination = Path(job["project_root"])
        try:
            import shutil

            candidate = self.root / "candidates" / payload["id"] / payload["attempt_id"]
            if not candidate.is_dir():
                # Legacy accepted jobs have their immutable input in the old worktree.
                from .bundles import pack_candidate

                extract_candidate(pack_candidate(str(source)), candidate)
            shutil.copytree(candidate, destination)
            reporter = Reporter(self.journal, job["id"], job["attempt_id"], job["execution_id"])
            record = self.executions.get(job["id"], job["attempt_id"])
            reporter.metadata(
                {
                    "execution_id": job["execution_id"],
                    "execution_attempts": record["executions"],
                    "execution_limit": self.execution_limit(job),
                    **({
                        "dispatch_attempt": job.get("dispatch_attempt", 1),
                        "dispatch_max_attempts": job.get("dispatch_max_attempts", 3),
                    } if job.get("managed_attempt") else {}),
                }
            )
            key = self.execution_key(job)
            future = executor.submit(key, execute, args=(self.journal.path, self.spec, job))
            self.processes[job["id"]] = PoolHandle(executor, key, future)
            executor.poll()
        except Exception as error:
            self._finish_failure(job, error, retryable=isinstance(error, OSError))

    def record_worker_exit(self, payload: dict, process) -> None:
        """Retain pool outcomes and lifecycle observations in the attempt logs."""
        reporter = Reporter(self.journal, payload["id"], payload["attempt_id"], payload.get("execution_id"))
        reporter.metadata(
            {
                "worker_exit": {
                    "execution_id": payload.get("execution_id"),
                    "exitcode": process.exitcode,
                    "observed_at": time.time(),
                }
            }
        )

    def has_execution_slot(self, job_id: str) -> bool:
        """Bound admitted local jobs while resource pools enforce physical exclusion."""
        active = {key for key, process in self.processes.items() if process.is_alive()}
        return job_id not in active and len(active) < self.capacity and not bool(getattr(self.executor, "cleanup_debts", set()))

    def _finish_failure(self, payload, error, *, retryable=True):
        """Turn an infrastructure fault into a bounded retry or a durable failure."""
        reporter = Reporter(self.journal, payload["id"], payload["attempt_id"], payload.get("execution_id"))
        report = exception_report(RemoteJob.model_validate(payload), reporter, error, 0)
        if payload.get("managed_attempt"):
            retryable = report["metadata"]["failure_category"] in {"infrastructure", "evaluator"}
        return self._record_outcome(payload, {"report": report, "retryable": retryable})

    def _record_outcome(self, payload, outcome):
        """Persist the result before releasing its queue receipt and resource slot."""
        if not self.executions.current(payload):
            return False
        if self.journal.lease_remaining(payload) == 0:
            request_stop(self.journal, payload["id"], payload["attempt_id"], "superseded")
            return False
        record = self.executions.get(payload["id"], payload["attempt_id"])
        report = outcome.get("report", {"success": False, "metrics": {}, "error_message": "Worker exited without an outcome"})
        if payload.get("managed_attempt") and not report.get("success", True):
            category = classify_report(report)
            outcome = {**outcome, "retryable": category in {"infrastructure", "evaluator"}}
            report.pop("score", None)
            if category != "candidate":
                report["metrics"] = {}
        if self._debt_key(payload) in getattr(self, "_cleanup_receipts", {}):
            report.setdefault("metadata", {})["external_cleanup_confirmed"] = True
        limit = self.execution_limit(payload)
        retry = outcome.get("retryable", False) and record["executions"] < limit
        control = execution_control(self.journal, payload["id"], payload["attempt_id"])
        if control.get("stop_requested") in {"cancel", "timeout", "superseded"}:
            return False
        if retry:
            report["message"] = f"Retrying evaluation ({record['executions'] + 1}/{limit}): " + report.get(
                "error_message", report.get("message", "worker interrupted")
            )
            status, wire, event = "retrying", "running", "retry"
        else:
            wire = "completed" if report.get("success", True) else "failed"
            event = "succeed" if wire == "completed" else "fail"
            status = "finished"
        return self.executions.commit(
            payload,
            [event],
            wire,
            report,
            execution_status=status,
            delay=record["executions"] if retry else 0,
        )

    async def request(self, session, method: str, suffix: str, **kwargs):
        """Use HTTP acknowledgements for every durable state transition."""
        channel = "registration" if suffix == "/register" else "heartbeat" if suffix.endswith("/heartbeat") else None
        try:
            async with session.request(method, self.url + suffix, **kwargs) as response:
                response.raise_for_status()
                value = await response.json()
        except Exception as error:
            if isinstance(error, aiohttp.ClientResponseError) and error.status in {404, 409} and "/jobs/" in suffix:
                self.last_contact = time.time()
                self.last_error = None
                raise
            self.last_error = type(error).__name__
            if channel:
                setattr(self, channel + "_last_error", self._transport_error(error))
            raise
        self.last_contact = time.time()
        self.last_error = None
        if channel:
            setattr(self, channel + "_last_success", self.last_contact)
            setattr(self, channel + "_last_error", None)
        return value

    async def upload_artifacts(self, session, job: dict) -> list[dict]:
        """Upload durable evidence before acknowledging its final result."""
        manifest = []
        metadata, _ = self.journal.observations(job["id"], job["attempt_id"])
        descriptions = metadata.get("artifact_descriptions", {})
        for item in self.journal.artifacts(job["id"], job["attempt_id"]):
            key = item["object_key"]
            if not key:
                path = Path(item["path"])
                if path.stat().st_size != item["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("Spooled evaluation evidence changed")
                with path.open("rb") as stream:
                    receipt = await self._leased_request(
                        job,
                        session,
                        "PUT",
                        f"/{self.identity['id']}/jobs/{job['id']}/artifacts/{item['sha256']}",
                        data=stream,
                        headers={
                            "X-Attempt-Id": job["attempt_id"],
                            "Content-Type": item["media_type"],
                        },
                        timeout=aiohttp.ClientTimeout(total=300, sock_connect=10, sock_read=60),
                    )
                key = receipt["object_key"]
                with self.journal.connect() as db:
                    db.execute(
                        "UPDATE artifacts SET object_key=? WHERE job_id=? AND attempt_id=? AND name=?",
                        (key, job["id"], job["attempt_id"], item["name"]),
                    )
            manifest.append(
                {k: item[k] for k in ("name", "sha256", "size", "media_type")}
                | {
                    "object_key": key,
                    "description": descriptions.get(item["name"], ""),
                    "download_path": (
                        f"/api/v1/llm4ad/evaluation-gateways/tasks/{job['task_id']}/jobs/{job['id']}/artifacts/{item['sha256']}"
                    ),
                }
            )
        return manifest

    async def _leased_request(self, job, session, method, suffix, **kwargs):
        """Cancel in-flight evidence or reports when their durable attempt lease expires."""
        remaining = self.journal.lease_remaining(job)
        if remaining == 0:
            raise AttemptLeaseExpiredError
        if remaining is None:
            return await self.request(session, method, suffix, **kwargs)
        operation = asyncio.create_task(self.request(session, method, suffix, **kwargs))
        try:
            while not operation.done():
                remaining = self.journal.lease_remaining(job)
                if remaining == 0:
                    raise AttemptLeaseExpiredError
                assert remaining is not None
                await asyncio.wait({operation}, timeout=min(remaining, 0.2))
            if self.journal.lease_remaining(job) == 0:
                raise AttemptLeaseExpiredError
            return await operation
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

    async def heartbeat(self, session):
        """Renew all returned attempts immediately using one local receipt timestamp."""
        remote = await self.request(session, "POST", f"/{self.identity['id']}/heartbeat", json={"ready": self.ready()})
        received_at = time.time()
        for payload in remote["jobs"]:
            self.journal.renew_lease(payload, received_at=received_at)
        return remote

    async def reconcile(self, session, payload: dict, fresh: bool = False) -> None:
        """Serialize same-job transitions and share concurrent cancellation hooks."""
        job_id, attempt_id = payload["id"], payload["attempt_id"]
        control = execution_control(self.journal, job_id, attempt_id)
        cancelling = payload.get("state") == "cancel_requested" or control.get("stop_requested") == "cancel"

        async def serialized() -> None:
            async with self._job_lock(job_id):
                if cancelling:
                    current = self.journal.get(job_id)
                    if current and json.loads(current["job"])["attempt_id"] != attempt_id:
                        return
                await self._reconcile(session, payload, fresh)

        if not cancelling:
            await serialized()
        else:
            key = (job_id, attempt_id)
            await asyncio.shield(self._cancel_task("_cancellations", key, serialized))

    async def _reconcile(self, session, payload: dict, fresh: bool = False) -> None:
        """Reconcile remote identity, then advance the SDK's durable local state."""
        job_id, attempt_id = payload["id"], payload["attempt_id"]
        local = self.journal.get(job_id)
        if local and json.loads(local["job"])["attempt_id"] != attempt_id:
            with self.journal.connect() as db:
                if payload.get("managed_attempt") and db.execute(
                    "SELECT 1 FROM retired_attempts WHERE job_id=? AND attempt_id=?", (job_id, attempt_id)
                ).fetchone():
                    return
            old = json.loads(local["job"])
            request_stop(self.journal, job_id, old["attempt_id"], "superseded")
            if not await self._cleanup(old):
                return
            self._ack_execution(old)
            local = None
        if local is None:
            candidate = self.root / "candidates" / job_id / attempt_id
            self.journal.accept({**payload, "project_root": str(candidate)})
            local = self.journal.get(job_id)
        assert local is not None
        job = json.loads(local["job"])
        job.setdefault("project_root", str(self.root / "candidates" / job_id / attempt_id))
        if local["state"] not in TERMINAL:
            self._reserve_attempt(job)
        record = self.executions.ensure(job, local)
        control = execution_control(self.journal, job_id, attempt_id)
        lease_expired = self.journal.lease_remaining(job) == 0
        if lease_expired and control.get("stop_requested") != "cancel":
            request_stop(self.journal, job_id, attempt_id, "superseded")
            control = execution_control(self.journal, job_id, attempt_id)
        if local["state"] in TERMINAL:
            if lease_expired:
                if not await self._cleanup(job):
                    return
                # Expired local results are retained for diagnostics, never delivered.
                current = self.journal.get(job_id)
                assert current is not None
                self.journal.acknowledge(job_id, current["seq"])
            self._ack_execution(job)
            return
        if payload.get("state") == "cancel_requested" or control.get("stop_requested") in {"cancel", "timeout", "superseded"}:
            reason = control.get("stop_requested") or "cancel"
            request_stop(self.journal, job_id, attempt_id, reason)
            if await self._cleanup(job):
                current = self.journal.get(job_id)
                if not current or current["state"] in TERMINAL or json.loads(current["job"])["attempt_id"] != attempt_id:
                    self._ack_execution(job)
                    return
                events = [] if record["state"] == "cancelling" else ["cancel"]
                failed = reason == "timeout" or lease_expired
                events.append("fail" if failed else "finish_cancel")
                state = "failed" if failed else "cancelled"
                metadata: dict[str, bool | str] = {"external_cleanup_confirmed": True}
                report = {
                    "success": False,
                    "metrics": {},
                    "message": "Execution timed out" if reason == "timeout" else "Cancellation confirmed",
                    "metadata": metadata,
                }
                if reason == "timeout":
                    report.update(error_message=report["message"], error_type="ExecutionTimeout")
                    classify_report(report, TimeoutError(report["message"]))
                if lease_expired:
                    report.update(message="Platform attempt lease expired", error_type="AttemptLeaseExpired")
                    report["error_message"] = report["message"]
                    metadata.update(failure_category="infrastructure", infrastructure_failure=True)
                self.executions.commit(job, events, state, report, allow_stopped=True)
                self._ack_execution(job)
                if lease_expired:
                    current = self.journal.get(job_id)
                    assert current is not None
                    self.journal.acknowledge(job_id, current["seq"])
            else:
                self._fail_cleanup(job)
            return
        if job_id in self.restart_jobs:
            self.restart_jobs.discard(job_id)
            if record["state"] == "running":
                self.executions.move(job, "retry", delay=1)
                record = self.executions.get(job_id, attempt_id)
        handle = self.processes.get(job_id)
        if handle:
            if not handle.future.done():
                return
            if not await self._cleanup(job):
                self._fail_cleanup(job)
                return
            self.record_worker_exit(job, handle)
            try:
                outcome = handle.future.result()
                if not outcome or outcome.get("stale"):
                    raise EvaluationUnavailableError("Worker ended before publishing an outcome")
                committed = self._record_outcome(job, outcome)
            except Exception as error:
                committed = self._finish_failure(job, error)
            if not committed:
                return
            self._ack_execution(job)
            local = self.journal.get(job_id)
            assert local is not None
            if local["state"] in TERMINAL:
                return
            record = self.executions.get(job_id, attempt_id)
        elif record["state"] == "running":
            # A controller restart reconciles orphan processes before queue replay.
            if not await self._cleanup(job):
                self._fail_cleanup(job)
                return
            self._ack_execution(job)
            self.executions.move(job, "retry", delay=1)
            record = self.executions.get(job_id, attempt_id)
        if record["state"] == "retrying":
            if not await self._cleanup(job):
                self._fail_cleanup(job)
                return
            self._ack_execution(job)
            if record["executions"] >= self.execution_limit(job):
                self._record_outcome(
                    job,
                    {
                        "retryable": False,
                        "report": {
                            "success": False,
                            "metrics": {},
                            "error_type": "ExecutionRetriesExhausted",
                            "error_message": "Evaluation retries exhausted",
                            "message": "Evaluation retries exhausted",
                            "metadata": {
                                "failure_category": "infrastructure", "infrastructure_failure": True,
                                "execution_attempts": record["executions"], "external_cleanup_confirmed": True,
                            },
                        },
                    },
                )
                return
            if record["retry_at"] > time.time():
                return
        if not self.has_execution_slot(job_id):
            return
        candidate = self.root / "candidates" / job_id / attempt_id
        if not candidate.is_dir():
            previous = Path(job["project_root"])
            if previous.is_dir() and any(previous.iterdir()):
                from .bundles import pack_candidate

                extract_candidate(pack_candidate(str(previous)), candidate)
            elif session is not None:
                async with session.get(self.url + f"/{self.identity['id']}/jobs/{job_id}/bundle") as response:
                    response.raise_for_status()
                    archive = await response.read()
                try:
                    extract_candidate(archive, candidate)
                except (ValueError, zipfile.BadZipFile, RuntimeError) as error:
                    self._finish_failure(job, error, retryable=False)
                    return
            else:
                return
        self.start_process({**job, "project_root": str(candidate)})

    def _ack_execution(self, payload):
        """Release an execution only after its durable transition and cleanup."""
        with self.journal.connect() as db:
            if db.execute("SELECT 1 FROM cleanup_debts WHERE execution_id=?", (self._debt_key(payload),)).fetchone():
                return
        if self.executor is not None:
            self.executor.ack(self.execution_key(payload))
            getattr(self.executor, "claimed_attempts", set()).discard((str(self.root), payload["id"], payload["attempt_id"]))
        handle = self.processes.get(payload["id"])
        if handle and handle.key == self.execution_key(payload):
            self.processes.pop(payload["id"], None)

    def _debt_key(self, payload):
        """Namespace a cleanup obligation shared across evaluator bindings."""
        return str(self.root) + ":" + self.execution_key(payload)

    async def _cleanup(self, payload: dict) -> bool:
        """Share one resource cleanup operation across browser and transport stops."""
        key = self.execution_key(payload)
        return await asyncio.shield(self._cancel_task("_cleanup_operations", key, lambda: self._cleanup_resource(payload)))

    async def _cleanup_resource(self, payload: dict) -> bool:
        """Fence physical restoration and queue dispatch across all resource bindings."""
        executor = self._executor()
        if not getattr(executor, "resource_exclusive", executor.concurrency == 1):
            return await self._cleanup_owned(payload)
        if not hasattr(executor, "cleanup_lock"):
            executor.cleanup_lock = asyncio.Lock()
        reason = "cleanup:" + self._debt_key(payload)
        executor.hold(reason)
        try:
            async with executor.cleanup_lock:
                return await self._cleanup_owned(payload)
        finally:
            executor.release(reason)

    async def _cleanup_owned(self, payload: dict) -> bool:
        """Terminate pool work and invoke optional resource restoration hooks."""
        executor = self._executor()
        key, debt_key = self.execution_key(payload), self._debt_key(payload)
        if not hasattr(executor, "cleanup_debts"):
            executor.cleanup_debts = set()
        with self.journal.connect() as db:
            debt = db.execute("SELECT next_at FROM cleanup_debts WHERE execution_id=?", (debt_key,)).fetchone()
        if debt and debt[0] > time.time():
            executor.cleanup_debts.add(debt_key)
            executor.paused = True
            return False
        reporter = Reporter(self.journal, payload["id"], payload["attempt_id"], payload.get("execution_id"))
        saved = reporter.saved_state
        with self.journal.connect() as db:
            prior = db.execute("SELECT saved FROM cleanup_debts WHERE execution_id=?", (debt_key,)).fetchone()
        if prior:
            saved = json.loads(prior[0])
        signature = hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest()
        receipts = getattr(self, "_cleanup_receipts", {})
        if not prior and receipts.get(debt_key) == signature:
            return True
        # Cleanup relies on the durable local lease, never an old transport duration.
        job = RemoteJob.model_validate({**payload, "lease_remaining": self.journal.lease_remaining(payload)})
        try:
            assigned = key in executor.active_keys()
            # loky owns process-tree termination; adapters restore external resources.
            await asyncio.to_thread(executor.cancel, key)
            other_futures = (
                [executor.future(active) for active in executor.active_keys() if active != key]
                if getattr(executor, "resource_exclusive", executor.concurrency == 1)
                else []
            )
            other = [future for future in other_futures if future is not None and not future.done()]
            if other and not assigned and not saved:
                # A queued job never used the device; its stop must not disturb its owner.
                confirmed = True
            else:
                if other:
                    wrapped = [asyncio.wrap_future(future) for future in other]
                    for future in wrapped:
                        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                    _, pending = await asyncio.wait(wrapped, timeout=30)
                    if pending:
                        raise EvaluationUnavailableError("Waiting for the current resource owner to finish")
                confirmed = await asyncio.wait_for(
                    run_operation(self.adapter, "cancel", job, reporter, lambda: self.adapter.cancel(job, saved)),
                    timeout=self._cleanup_timeout(),
                )
            if not confirmed:
                raise EvaluationUnavailableError("Resource restoration has not completed")
            current = self.journal.get(payload["id"])
            still_current = current and reporter._current(current["job"])
            if still_current and reporter.saved_state != saved:
                raise EvaluationUnavailableError("Resource state changed during cleanup; confirming the new state")
        except Exception as error:
            current = self.journal.get(payload["id"])
            if current and reporter._current(current["job"]):
                saved = reporter.saved_state
            with self.journal.connect() as db:
                db.execute(
                    "INSERT INTO cleanup_debts(execution_id,payload,saved,failures,next_at,error) VALUES(?,?,?,1,?,?) "
                    "ON CONFLICT(execution_id) DO UPDATE SET saved=excluded.saved,"
                    "failures=failures+1,next_at=excluded.next_at,error=excluded.error",
                    (debt_key, json.dumps(payload), json.dumps(saved), time.time() + 5, str(error)),
                )
            executor.cleanup_debts.add(debt_key)
            executor.paused = True
            return False
        with self.journal.connect() as db:
            db.execute("DELETE FROM cleanup_debts WHERE execution_id=?", (debt_key,))
        executor.cleanup_debts.discard(debt_key)
        executor.paused = bool(executor.cleanup_debts)
        receipts[debt_key] = signature
        if len(receipts) > 256:
            receipts.pop(next(iter(receipts)))
        self._cleanup_receipts = receipts
        self.journal.confirm_cleanup(payload)
        return True

    def _fail_cleanup(self, payload):
        """End repeated cleanup failures while continuing automatic resource probes."""
        with self.journal.connect() as db:
            debt = db.execute(
                "SELECT failures,error FROM cleanup_debts WHERE execution_id=?", (self._debt_key(payload),)
            ).fetchone()
        if debt and debt[0] >= MAX_EXECUTIONS:
            current = self.journal.get(payload["id"])
            if current and current["state"] not in TERMINAL:
                self.executions.commit(
                    payload,
                    ["fail"],
                    "failed",
                    {
                        "success": False,
                        "metrics": {},
                        "error_type": "ResourceCleanupError",
                        "error_message": debt[1],
                        "message": "Resource unavailable; automatic readiness probes continue",
                        "metadata": {
                            "failure_category": "infrastructure", "infrastructure_failure": True,
                            "external_cleanup_confirmed": False, "cleanup_error": debt[1],
                        },
                    },
                    allow_stopped=True,
                )

    async def probe_cleanup(self) -> None:
        """Recheck resource cleanup independently of platform/network availability."""
        with self.journal.connect() as db:
            debts = db.execute("SELECT payload FROM cleanup_debts").fetchall()
        for (payload,) in debts:
            job = json.loads(payload)
            if await self._cleanup(job):
                row = self.journal.get(job["id"])
                if not row or row["state"] in TERMINAL or json.loads(row["job"])["attempt_id"] != job["attempt_id"]:
                    self._ack_execution(job)

    async def cancel_execution(self, payload: dict) -> bool:
        """Coalesce cancellation and leave resource probes independent of callers."""
        request_stop(self.journal, payload["id"], payload["attempt_id"], "cancel")
        key = (payload["id"], payload["attempt_id"])
        return await asyncio.shield(self._cancel_task("_cancel_operations", key, lambda: self._cleanup(payload)))

    async def tick(self, session):
        """Reconcile assignments, flush the outbox, then claim free capacity."""
        gateway_id = self.identity["id"]
        remote = await self.heartbeat(session)
        for payload in remote["jobs"]:
            await self.reconcile(session, payload)
        for row in self.journal.rows(pending_only=True):
            job = json.loads(row["job"])
            if self.journal.lease_remaining(job) == 0:
                await self.reconcile(None, job)
                continue
            if row["seq"] > row["ack"]:
                try:
                    outgoing = Reporter(self.journal, job["id"], job["attempt_id"]).enrich(json.loads(row["report"]))
                    if row["state"] in {"completed", "failed", "cancelled"}:
                        artifacts = await self.upload_artifacts(session, job)
                        if artifacts:
                            outgoing.setdefault("metadata", {})["artifacts"] = artifacts
                    response = await self._leased_request(
                        job,
                        session,
                        "POST",
                        f"/{gateway_id}/jobs/{row['id']}/report",
                        json={
                            "attempt_id": job["attempt_id"],
                            "sequence": row["seq"],
                            "state": row["state"],
                            "report": outgoing,
                        },
                    )
                except AttemptLeaseExpiredError:
                    await self.reconcile(None, job)
                    continue
                except aiohttp.ClientResponseError as exc:
                    if exc.status not in {404, 409}:
                        raise
                    request_stop(self.journal, row["id"], job["attempt_id"], "superseded")
                    if await self._cleanup(job):
                        current = self.journal.get(row["id"])
                        assert current is not None
                        if current["state"] not in TERMINAL:
                            record = self.executions.get(job["id"], job["attempt_id"])
                            events = [] if record["state"] == "cancelling" else ["cancel"]
                            self.executions.commit(
                                job,
                                events + ["finish_cancel"],
                                "cancelled",
                                {"message": "Execution attempt superseded by platform"},
                                allow_stopped=True,
                            )
                        self._ack_execution(job)
                    else:
                        self._fail_cleanup(job)
                    current = self.journal.get(row["id"])
                    assert current is not None
                    self.journal.acknowledge(row["id"], current["seq"])
                    continue
                if response["state"] == "queued":
                    if job.get("managed_attempt"):
                        request_stop(self.journal, row["id"], job["attempt_id"], "superseded")
                        self.journal.acknowledge(row["id"], row["seq"])
                    else:
                        self.journal.update(
                            row["id"],
                            "cancelled",
                            {"message": "Attempt released for an explicitly confirmed retry"},
                        )
                        self.journal.acknowledge(row["id"], row["seq"] + 1)
                else:
                    self.journal.acknowledge(row["id"], response["ack"])
        for _ in range(self.capacity):
            executor = self._executor()
            if not hasattr(executor, "claim_lock"):
                executor.claim_lock = asyncio.Lock()
            async with executor.claim_lock:
                if not self.ready():
                    break
                payload = await self.request(session, "POST", f"/{gateway_id}/claim")
                if payload is None:
                    break
                self.journal.renew_lease(payload)
                if wakeup := getattr(self, "_lease_wakeup", None):
                    wakeup.set()
                await self.reconcile(session, payload, fresh=True)
        if not getattr(self, "cleanup_managed", False):
            await asyncio.to_thread(self.cleanup_evidence)

    def cleanup_evidence(self):
        """Delegate acknowledgement-fenced evidence cleanup to the shared collector."""
        from .retention import collect_finished

        return collect_finished(self)

    async def notifications(self, session):
        """Reconnect SSE hints while HTTP heartbeats reconcile durable job state."""
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30)
        self.notifications_reconnecting = True
        while True:
            try:
                async with session.get(
                    self.url + f"/{self.identity['id']}/events",
                    headers={"Accept": "text/event-stream"},
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    if response.content_type != "text/event-stream":
                        raise aiohttp.ClientError("Expected an SSE notification stream")
                    self.notifications_connected = True
                    self.notifications_reconnecting = False
                    self.notifications_last_error = None
                    event, has_data = b"message", False
                    async for raw in response.content:
                        line = raw.rstrip(b"\r\n")
                        if not line:
                            if has_data and event in {b"message", b"poll"}:
                                self.wakeup.set()
                            event, has_data = b"message", False
                        else:
                            field, _, value = line.partition(b":")
                            if field == b"event":
                                event = value.removeprefix(b" ")
                            elif field == b"data":
                                has_data = True
            except (aiohttp.ClientError, OSError, TimeoutError) as error:
                self.notifications_last_error = self._transport_error(error)
            except asyncio.CancelledError:
                self.notifications_reconnecting = False
                raise
            finally:
                self.notifications_connected = False
            self.notifications_reconnecting = True
            # Back off after clean EOF too, avoiding a reconnect loop through proxies.
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                self.notifications_reconnecting = False
                raise

    async def run(self) -> None:
        """Keep local execution and durable results alive through network outages."""
        self._executor()
        await self.probe_cleanup()
        headers = {"Authorization": "Bearer " + self.identity["secret"]}
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:

            async def monitor() -> None:
                operations: dict[tuple[str, str], asyncio.Task[None]] = {}
                probe: asyncio.Task[None] | None = None
                try:
                    while True:
                        self.enforce_deadlines()
                        with self.journal.connect() as db:
                            db.execute(
                                "UPDATE execution_controls SET controller_seen_at=? WHERE stop_requested='' "
                                "AND EXISTS(SELECT 1 FROM jobs WHERE jobs.id=execution_controls.job_id "
                                "AND json_extract(jobs.job,'$.attempt_id')=execution_controls.attempt_id "
                                "AND jobs.state NOT IN ('completed','failed','cancelled'))",
                                (time.time(),),
                            )
                        for key, task in list(operations.items()):
                            if task.done():
                                await task
                                operations.pop(key)
                        if probe is not None and probe.done():
                            await probe
                            probe = None
                        if probe is None:
                            probe = asyncio.create_task(self.probe_cleanup())
                        for row in self.journal.rows(pending_only=True):
                            job = json.loads(row["job"])
                            key = (job["id"], job["attempt_id"])
                            if key not in operations and (row["state"] not in TERMINAL or self.journal.lease_remaining(job) == 0):
                                operations[key] = asyncio.create_task(self.reconcile(None, job))
                        if self.executor is not None:
                            self.executor.poll()
                        await asyncio.sleep(0.2)
                finally:
                    tasks = [*operations.values(), *([probe] if probe is not None else [])]
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

            watchdog = asyncio.create_task(monitor())
            notifications = None
            renewals = None

            async def keep_leases():
                while True:
                    remaining = [
                        duration for row in self.journal.rows(pending_only=True)
                        if (duration := self.journal.lease_remaining(json.loads(row["job"]))) is not None and duration > 0
                    ]
                    interval = min(10.0, max(0.01, min(remaining) / 3)) if remaining else 10.0
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._lease_wakeup.wait(), timeout=interval)
                    self._lease_wakeup.clear()
                    with suppress(TimeoutError, aiohttp.ClientError, OSError):
                        await self.heartbeat(session)

            try:
                while True:
                    try:
                        await self.request(
                            session,
                            "POST",
                            "/register",
                            json={
                                **self.identity,
                                "name": self.name,
                                "allowed_user_ids": self.allowed_users,
                                "capacity": self.capacity,
                                "contract": self.adapter.contract.model_dump(mode="json"),
                                "protocol_version": 2,
                                "ready": self.ready(),
                            },
                        )
                        if notifications is None:
                            notifications = asyncio.create_task(self.notifications(session))
                        if renewals is None:
                            renewals = asyncio.create_task(keep_leases())
                        await self.tick(session)
                    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                        print(
                            f"Gateway disconnected; retaining work and retrying ({type(exc).__name__}).",
                            flush=True,
                        )
                    if watchdog.done():
                        await watchdog
                    if renewals is not None and renewals.done():
                        await renewals
                    self.enforce_deadlines()
                    self.wakeup.clear()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self.wakeup.wait(), timeout=10)
            finally:
                tasks = [task for task in (watchdog, notifications, renewals) if task is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.stop_workers(close_executor=False)
                cleanups = set(getattr(self, "_cleanup_operations", {}).values())
                if cleanups:
                    _, pending = await asyncio.wait(cleanups, timeout=self._cleanup_timeout() + 5)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*cleanups, return_exceptions=True)
                if self.executor is not None and self.executor_owned:
                    await asyncio.to_thread(self.executor.close)

    def stop_workers(self, *, close_executor=True):
        """Stop SDK slots while retaining retry counts, reports and queue receipts."""
        for row in self.journal.rows(pending_only=True):
            job = json.loads(row["job"])
            if row["state"] not in TERMINAL:
                control = execution_control(self.journal, row["id"], job["attempt_id"])
                if not control.get("stop_requested"):
                    request_stop(self.journal, row["id"], job["attempt_id"], "interrupt")
        for process in self.processes.values():
            stop_process(process)
        if close_executor and self.executor is not None and self.executor_owned:
            self.executor.close()

    def enforce_deadlines(self):
        """Request timeout cancellation without discarding cleanup obligations."""
        for row in self.journal.rows(pending_only=True):
            job = json.loads(row["job"])
            control = execution_control(self.journal, row["id"], job["attempt_id"])
            if self.journal.lease_remaining(job) == 0:
                if control.get("stop_requested") != "cancel":
                    request_stop(self.journal, row["id"], job["attempt_id"], "superseded")
                continue
            if row["state"] in TERMINAL or not row["started"]:
                continue
            if control.get("stop_requested") in {"cancel", "superseded", "interrupt"}:
                continue
            elapsed = time.time() - row["started"] - control.get("paused_seconds", 0)
            if elapsed > job["timeout"]:
                request_stop(self.journal, row["id"], job["attempt_id"], "timeout")
