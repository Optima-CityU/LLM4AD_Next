"""Outbound gateway with process isolation, local journaling and reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import secrets
import signal
import sqlite3
import subprocess
import time
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path

import aiohttp

from llm4ad.gateway.sdk.bundles import extract_candidate
from llm4ad.gateway.sdk.contracts import GatewayEvaluator, RemoteJob, load_evaluator, normalize_spec
from llm4ad.gateway.sdk.errors import EvaluationUnavailableError
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import (
    execution_control,
    finish_execution,
    owned_worker,
    prepare_execution,
    request_stop,
    reset_local_outputs,
    run_operation,
    worker_active,
)
from llm4ad.gateway.sdk.local_logs import capture_worker_logs
from llm4ad.gateway.sdk.reporting import exception_report, prepare_result


def stop_process(process):
    """Stop a local process tree on POSIX and Windows."""
    if process is None or not process.is_alive():
        return
    try:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                process.kill()
        elif os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    process.join(timeout=2)
    if process.is_alive():
        with suppress(ProcessLookupError):
            if os.name != "nt" and os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        process.join(timeout=2)


def execute(journal_path: str, spec: str, payload: dict, recovery: bool = False):
    """Own one isolated execution lease, even across gateway process restarts."""
    if os.name != "nt":
        os.setsid()
    journal = Journal(journal_path)
    try:
        with owned_worker(
            journal, payload["id"], payload["attempt_id"], recovery=recovery
        ) as accepted:
            if accepted:
                with capture_worker_logs(journal, payload):
                    _execute_owned(journal, spec, payload, recovery)
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise


def _execute_owned(journal, spec: str, payload: dict, recovery: bool):
    """Run domain work and hooks inside SDK-owned reporting and error boundaries."""
    reporter = Reporter(journal, payload["id"], payload["attempt_id"])
    started = time.monotonic()
    job = adapter = None
    try:
        job = RemoteJob.model_validate(payload)
        spec = normalize_spec(spec)
        os.chdir(job.project_root)
        Path(job.artifact_dir).mkdir(exist_ok=True)
        if not recovery:
            reporter.progress("Evaluation started")
        adapter = load_evaluator(spec)
        if (
            recovery
            and type(adapter).recover is GatewayEvaluator.recover
            and not reporter.saved_state
        ):
            reset_local_outputs(job, reporter)
            reporter.progress("Restarting interrupted local evaluation")
        operation = "recover" if recovery else "evaluate"

        def action():
            return adapter.recover(job, reporter) if recovery else adapter.evaluate(job, reporter)

        result = asyncio.run(run_operation(adapter, operation, job, reporter, action))
        if result is None:
            journal.update(
                job.id,
                "recovery_required",
                reporter.enrich(
                    {"message": "Execution state is unknown; confirm recovery or retry explicitly"}
                ),
                job.attempt_id,
                execution_status="suspended",
            )
        else:
            report = prepare_result(job, reporter, result, (time.monotonic() - started) * 1000)
            journal.update(job.id, "completed", report, job.attempt_id, execution_status="finished")
    except BaseException as exc:
        import traceback

        with suppress(OSError, ValueError):
            traceback.print_exception(exc)
        custom_recovery = (
            recovery
            and adapter is not None
            and type(adapter).recover is not GatewayEvaluator.recover
        )
        uncertain = (
            custom_recovery or reporter.saved_state or isinstance(exc, EvaluationUnavailableError)
        )
        state = "recovery_required" if uncertain else "failed"
        try:
            report = exception_report(job, reporter, exc, (time.monotonic() - started) * 1000)
        except Exception as reporting_error:
            message = str(exc) or type(exc).__name__
            report = {
                "success": False,
                "metrics": {},
                "message": message,
                "error_message": message,
                "error_type": type(exc).__name__,
                "metadata": {"reporting_error": type(reporting_error).__name__},
            }
        journal.update(
            payload["id"],
            state,
            report,
            payload["attempt_id"],
            execution_status="suspended" if uncertain else "finished",
        )


class Gateway:
    """Own transport and local execution while adapters own evaluation logic."""

    def __init__(
        self,
        server: str,
        name: str,
        allowed_users: list[str],
        evaluator: str,
        state_dir: Path,
        concurrency: int = 1,
    ):
        """Load a persistent identity, adapter and execution journal."""
        self.url = server.rstrip("/") + "/api/v1/llm4ad/evaluation-gateways"
        self.name, self.allowed_users = name, [str(uuid.UUID(value)) for value in allowed_users]
        self.spec = normalize_spec(evaluator)
        self.adapter = load_evaluator(self.spec)
        self.capacity = concurrency
        self.root = state_dir.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        self.processes: dict[str, multiprocessing.Process] = {}
        self.restart_jobs = {
            r["id"]
            for r in self.journal.rows(pending_only=True)
            if r["state"] not in {"completed", "failed", "cancelled"}
        }
        self.wakeup = asyncio.Event()
        self.last_contact = None
        self.last_error = None
        self.admission_paused = False
        self.registration_last_success = None
        self.registration_last_error = None
        self.heartbeat_last_success = None
        self.heartbeat_last_error = None
        self.notifications_connected = False
        self.notifications_reconnecting = False
        self.notifications_last_error = None
        self._job_locks = {}
        self._cancellations = {}

    def _cancel_task(self, cache_name, key, operation):
        """Share in-flight cancellation work even when one waiting caller leaves."""
        cache = getattr(self, cache_name, None)
        if cache is None:
            cache = {}
            setattr(self, cache_name, cache)
        task = cache.get(key)
        if task is None:
            task = asyncio.create_task(operation())
            cache[key] = task

            def finished(completed):
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
    def _transport_error(error: Exception) -> dict:
        """Keep exception types and HTTP status without URLs, headers or bodies."""
        value = {"type": type(error).__name__}
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
                    "idle" if row[1] == "accepted" and row[2] is None else "unknown",
                ),
            )
        await self.reconcile(None, {**payload, "state": "cancel_requested"})
        current = self.journal.get(job_id)
        if current is None or json.loads(current["job"])["attempt_id"] != attempt_id:
            raise ValueError("Execution attempt is no longer current")
        state = current["state"]
        status = (
            "cancelled"
            if state == "cancelled"
            else ("already_finished" if state in {"completed", "failed"} else "cancelling")
        )
        return {"job_id": job_id, "attempt_id": attempt_id, "state": state, "status": status}

    def start_process(self, payload: dict, recovery: bool = False):
        """Spawn a portable isolated executor without inheriting platform secrets."""
        if not self.has_execution_slot(payload["id"]):
            return
        if not prepare_execution(self.journal, payload["id"], payload["attempt_id"]):
            return
        if not recovery:
            self.journal.update(
                payload["id"],
                "running",
                {"message": "Starting local evaluator"},
                payload["attempt_id"],
            )
        process = multiprocessing.get_context("spawn").Process(
            target=execute, args=(self.journal.path, self.spec, payload, recovery), daemon=False
        )
        process.start()
        self.processes[payload["id"]] = process

    def has_execution_slot(self, job_id: str) -> bool:
        """Apply local capacity to new work and recovered workers alike."""
        active = {key for key, process in self.processes.items() if process.is_alive()}
        for row in self.journal.rows(pending_only=True):
            if row["state"] in {"completed", "failed", "cancelled"} or row["id"] in active:
                continue
            job = json.loads(row["job"])
            if worker_active(self.journal, row["id"], job["attempt_id"]):
                active.add(row["id"])
        return job_id not in active and len(active) < self.capacity

    async def request(self, session, method: str, suffix: str, **kwargs):
        """Use HTTP acknowledgements for every durable state transition."""
        channel = (
            "registration"
            if suffix == "/register"
            else "heartbeat" if suffix.endswith("/heartbeat") else None
        )
        try:
            async with session.request(method, self.url + suffix, **kwargs) as response:
                response.raise_for_status()
                value = await response.json()
        except Exception as error:
            if (
                isinstance(error, aiohttp.ClientResponseError)
                and error.status in {404, 409}
                and "/jobs/" in suffix
            ):
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
                if (
                    path.stat().st_size != item["size"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
                ):
                    raise ValueError("Spooled evaluation evidence changed")
                with path.open("rb") as stream:
                    receipt = await self.request(
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

    async def reconcile(self, session, payload: dict, fresh: bool = False):
        """Serialize same-job transitions and share concurrent cancellation hooks."""
        job_id, attempt_id = payload["id"], payload["attempt_id"]
        control = execution_control(self.journal, job_id, attempt_id)
        cancelling = (
            payload.get("state") == "cancel_requested" or control.get("stop_requested") == "cancel"
        )

        async def serialized():
            async with self._job_lock(job_id):
                if cancelling:
                    current = self.journal.get(job_id)
                    if current and json.loads(current["job"])["attempt_id"] != attempt_id:
                        return
                await self._reconcile(session, payload, fresh)

        if not cancelling:
            return await serialized()
        key = (job_id, attempt_id)
        return await asyncio.shield(self._cancel_task("_cancellations", key, serialized))

    async def _reconcile(self, session, payload: dict, fresh: bool = False):
        """Resume known work, preserving uncertainty after lost local state."""
        job_id = payload["id"]
        local = self.journal.get(job_id)
        if local and json.loads(local["job"])["attempt_id"] != payload["attempt_id"]:
            old = json.loads(local["job"])
            request_stop(self.journal, job_id, old["attempt_id"], "superseded")
            stop_process(self.processes.pop(job_id, None))
            if worker_active(self.journal, job_id, old["attempt_id"]):
                return
            local = None
        control = execution_control(self.journal, job_id, payload["attempt_id"])
        if (
            local
            and not control
            and local["state"] not in {"completed", "failed", "cancelled"}
            and not (local["state"] == "accepted" and local["started"] is None)
        ):
            finish_execution(self.journal, job_id, payload["attempt_id"], "unknown")
            control = execution_control(self.journal, job_id, payload["attempt_id"])
        if payload["state"] == "cancel_requested" or control.get("stop_requested") == "cancel":
            if local and local["state"] in {"completed", "failed", "cancelled"}:
                return
            request_stop(self.journal, job_id, payload["attempt_id"], "cancel")
            if local is None:
                self.journal.accept(payload)
                finish_execution(self.journal, job_id, payload["attempt_id"], "unknown")
            confirmed = False
            saved = (
                Reporter(self.journal, job_id, payload["attempt_id"]).saved_state if local else {}
            )
            if local and (control.get("status") != "unknown" or saved):
                confirmed = await self.cancel_execution(json.loads(local["job"]))
                local = self.journal.get(job_id)
            if local and (
                json.loads(local["job"])["attempt_id"] != payload["attempt_id"]
                or local["state"] in {"completed", "failed", "cancelled"}
            ):
                return
            process = self.processes.get(job_id)
            confirmed = (
                confirmed
                and not (process and process.is_alive())
                and not worker_active(self.journal, job_id, payload["attempt_id"])
            )
            report = json.loads(local["report"]) if local else {}
            self.journal.update(
                job_id,
                "cancelled" if confirmed else "recovery_required",
                {
                    **report,
                    "message": (
                        "Cancellation confirmed"
                        if confirmed
                        else "Waiting for execution cancellation"
                    ),
                },
                payload["attempt_id"],
                execution_status="finished" if confirmed else None,
            )
            return
        if local is None:
            self.restart_jobs.discard(job_id)
            payload = {
                **payload,
                "project_root": str(self.root / "work" / job_id / payload["attempt_id"]),
            }
            self.journal.accept(payload)
            if not fresh:
                finish_execution(self.journal, job_id, payload["attempt_id"], "unknown")
                self.journal.update(
                    job_id,
                    "recovery_required",
                    {"message": "Local execution state is missing; confirm recovery"},
                )
                return
            local = self.journal.get(job_id)
        if local["state"] in {"completed", "failed", "cancelled"}:
            self.restart_jobs.discard(job_id)
            return
        if json.loads(local["report"]).get("retry_confirmed") is True:
            return
        if worker_active(self.journal, job_id, payload["attempt_id"]):
            return
        process = self.processes.get(job_id)
        if process and process.is_alive():
            return
        if process:
            process.join()
            self.processes.pop(job_id, None)
        if not self.has_execution_slot(job_id):
            return
        reporter = Reporter(self.journal, job_id, payload["attempt_id"])
        local_replay = control.get("status") in {"pending", "running"} and not reporter.saved_state
        never_started = local["state"] == "accepted" and local["started"] is None
        if local_replay and control.get("stop_requested") not in {
            "cancel",
            "timeout",
            "superseded",
        }:
            self.restart_jobs.discard(job_id)
            self.journal.update(
                job_id,
                "recovery_required",
                {"message": "Recovering interrupted local evaluation"},
                payload["attempt_id"],
            )
            self.start_process(json.loads(local["job"]), recovery=True)
            return
        if (
            control.get("status") in {"pending", "running"}
            and reporter.saved_state
            and type(self.adapter).recover is not GatewayEvaluator.recover
        ):
            self.restart_jobs.discard(job_id)
            self.journal.update(
                job_id,
                "recovery_required",
                {"message": "Reconnecting external evaluation"},
                payload["attempt_id"],
            )
            self.start_process(json.loads(local["job"]), recovery=True)
            return
        if job_id in self.restart_jobs:
            self.restart_jobs.remove(job_id)
            if not never_started:
                if not control and not reporter.saved_state:
                    finish_execution(self.journal, job_id, payload["attempt_id"], "unknown")
                self.journal.update(
                    job_id,
                    "recovery_required",
                    {"message": "Reconciling execution after gateway restart"},
                    payload["attempt_id"],
                )
                if type(self.adapter).recover is not GatewayEvaluator.recover:
                    self.start_process(json.loads(local["job"]), recovery=True)
                return
        if local["state"] == "accepted":
            job = json.loads(local["job"])
            async with session.get(
                self.url + f"/{self.identity['id']}/jobs/{job_id}/bundle"
            ) as response:
                response.raise_for_status()
                candidate = await response.read()
            try:
                extract_candidate(candidate, Path(job["project_root"]))
            except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
                self.journal.update(
                    job_id, "failed", {"message": str(exc), "error_type": "CandidateArchiveError"}
                )
                return
            self.start_process(job)

    async def cancel_execution(self, payload: dict) -> bool:
        """Coalesce cleanup hooks shared by reconciliation and outbox supersession."""
        key = (payload["id"], payload["attempt_id"])
        current = self.journal.get(key[0])
        if current is None or json.loads(current["job"])["attempt_id"] != key[1]:
            return False
        if current["state"] in {"completed", "failed", "cancelled"}:
            return True
        request_stop(self.journal, *key, "cancel")
        return await asyncio.shield(
            self._cancel_task("_cancel_operations", key, lambda: self._cancel_execution(payload))
        )

    async def _cancel_execution(self, payload: dict) -> bool:
        """Own local termination/external confirmation within optional operation hooks."""
        job = RemoteJob.model_validate(payload)
        reporter = Reporter(self.journal, job.id, job.attempt_id)
        request_stop(self.journal, job.id, job.attempt_id, "cancel")
        confirmed_saved = None

        def current_attempt():
            current = self.journal.get(job.id)
            return (
                current is not None and json.loads(current["job"])["attempt_id"] == job.attempt_id
            )

        def stop_local():
            if not current_attempt():
                return
            process = self.processes.get(job.id)
            stop_process(process)
            if process is None or not process.is_alive():
                self.processes.pop(job.id, None)

        async def action():
            nonlocal confirmed_saved
            if not current_attempt():
                return False
            stop_local()
            for _ in range(20):
                if not worker_active(self.journal, job.id, job.attempt_id):
                    saved = reporter.saved_state
                    confirmed = await self.adapter.cancel(job, saved) if saved else True
                    if confirmed:
                        confirmed_saved = saved
                    return confirmed
                await asyncio.sleep(0.05)
            return False

        try:
            confirmed = await asyncio.wait_for(
                run_operation(self.adapter, "cancel", job, reporter, action), timeout=30
            )
            return bool(confirmed and current_attempt() and reporter.saved_state == confirmed_saved)
        except Exception as error:
            request_stop(self.journal, job.id, job.attempt_id, "cancel")
            stop_local()
            reporter.message(
                f"Cancellation is pending: {error}",
                level="warning",
                data={"error_type": type(error).__name__},
            )
            return False

    async def tick(self, session):
        """Reconcile assignments, flush the outbox, then claim free capacity."""
        gateway_id = self.identity["id"]
        remote = await self.request(session, "POST", f"/{gateway_id}/heartbeat")
        for payload in remote["jobs"]:
            await self.reconcile(session, payload)
        for row in self.journal.rows(pending_only=True):
            process = self.processes.get(row["id"])
            if process and not process.is_alive():
                process.join()
                del self.processes[row["id"]]
                if row["state"] in {"accepted", "running"}:
                    attempt_id = json.loads(row["job"])["attempt_id"]
                    if not worker_active(self.journal, row["id"], attempt_id):
                        self.journal.update(
                            row["id"],
                            "recovery_required",
                            {"message": "Executor exited without a durable result"},
                            attempt_id,
                        )
                row = self.journal.get(row["id"])
            job = json.loads(row["job"])
            if row["seq"] > row["ack"]:
                try:
                    outgoing = Reporter(self.journal, job["id"], job["attempt_id"]).enrich(
                        json.loads(row["report"])
                    )
                    if row["state"] in {"completed", "failed", "cancelled"}:
                        artifacts = await self.upload_artifacts(session, job)
                        if artifacts:
                            outgoing.setdefault("metadata", {})["artifacts"] = artifacts
                    response = await self.request(
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
                except aiohttp.ClientResponseError as exc:
                    if exc.status not in {404, 409}:
                        raise
                    process = self.processes.pop(row["id"], None)
                    request_stop(self.journal, row["id"], job["attempt_id"], "superseded")
                    stop_process(process)
                    saved = json.loads(row["saved"])
                    stopped = not worker_active(self.journal, row["id"], job["attempt_id"])
                    confirmed = stopped and (
                        not saved or row["state"] in {"completed", "failed", "cancelled"}
                    )
                    if stopped and not confirmed:
                        confirmed = await self.cancel_execution(job)
                    self.journal.update(
                        row["id"],
                        "cancelled" if confirmed else "recovery_required",
                        {
                            "message": (
                                "Execution attempt superseded by platform; confirm external cleanup"
                                if not confirmed
                                else "Execution attempt superseded by platform"
                            )
                        },
                    )
                    self.journal.acknowledge(row["id"], row["seq"] + 1)
                    continue
                if response["state"] == "queued":
                    self.journal.update(
                        row["id"],
                        "cancelled",
                        {"message": "Attempt released for an explicitly confirmed retry"},
                    )
                    self.journal.acknowledge(row["id"], row["seq"] + 1)
                else:
                    self.journal.acknowledge(row["id"], response["ack"])
        for _ in range(self.capacity):
            if getattr(self, "admission_paused", False):
                break
            payload = await self.request(session, "POST", f"/{gateway_id}/claim")
            if payload is None:
                break
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

    async def run(self):
        """Keep local execution and durable results alive through network outages."""
        headers = {"Authorization": "Bearer " + self.identity["secret"]}
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as session:

            async def monitor():
                while True:
                    self.enforce_deadlines()
                    await asyncio.sleep(0.2)

            watchdog = asyncio.create_task(monitor())
            notifications = None
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
                            },
                        )
                        if notifications is None:
                            notifications = asyncio.create_task(self.notifications(session))
                        await self.tick(session)
                    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                        print(
                            f"Gateway disconnected; retaining work and retrying ({type(exc).__name__}).",
                            flush=True,
                        )
                    # Deadlines are enforced even when HTTP communication fails.
                    self.enforce_deadlines()
                    self.wakeup.clear()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self.wakeup.wait(), timeout=10)
            finally:
                watchdog.cancel()
                if notifications:
                    notifications.cancel()
                await asyncio.gather(
                    watchdog, *([notifications] if notifications else []), return_exceptions=True
                )
                self.stop_workers()

    def stop_workers(self):
        """Interrupt SDK workers on shutdown while leaving local work recoverable."""
        for row in self.journal.rows(pending_only=True):
            job = json.loads(row["job"])
            if row["state"] in {"accepted", "running", "recovery_required"}:
                control = execution_control(self.journal, row["id"], job["attempt_id"])
                if control.get("status") == "running" and not control.get("stop_requested"):
                    request_stop(self.journal, row["id"], job["attempt_id"], "interrupt")
        for process in self.processes.values():
            stop_process(process)

    def enforce_deadlines(self):
        """Apply actual execution budgets locally, independently of connectivity."""
        for row in self.journal.rows(pending_only=True):
            if row["state"] != "running" or not row["started"]:
                continue
            job = json.loads(row["job"])
            control = execution_control(self.journal, row["id"], job["attempt_id"])
            if control.get("stop_requested") in {"cancel", "superseded"}:
                continue
            if (
                control
                and control.get("stop_requested") != "timeout"
                and not worker_active(self.journal, row["id"], job["attempt_id"])
            ):
                continue
            if time.time() - row["started"] > job["timeout"]:
                process = self.processes.get(row["id"])
                request_stop(self.journal, row["id"], job["attempt_id"], "timeout")
                stop_process(process)
                if worker_active(self.journal, row["id"], job["attempt_id"]):
                    continue
                saved = Reporter(self.journal, row["id"], job["attempt_id"]).saved_state
                self.journal.update(
                    row["id"],
                    "recovery_required" if saved else "failed",
                    {
                        "message": f"Execution timed out after {job['timeout']}s",
                        "error_type": "ExecutionTimeout",
                    },
                    job["attempt_id"],
                    execution_status="suspended" if saved else "finished",
                )
