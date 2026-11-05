"""Outbound gateway with process isolation, local journaling and reconciliation."""

from __future__ import annotations

import asyncio
import inspect
import json
import multiprocessing
import os
import secrets
import signal
import subprocess
import time
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path

import aiohttp

from llm4ad.evaluator.base import EvaluationResult
from llm4ad.remote.bundles import extract_candidate
from llm4ad.remote.contracts import GatewayEvaluator, RemoteJob, load_evaluator
from llm4ad.remote.journal import Journal, Reporter


def stop_process(process):
    """Stop a local process tree on POSIX and Windows."""
    if process is None or not process.is_alive():
        return
    try:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=5)
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
    """Run adapter code outside the heartbeat/transport process."""
    if os.name != "nt":
        os.setsid()
    journal = Journal(journal_path)
    job = RemoteJob.model_validate(payload)
    reporter = Reporter(journal, job.id, job.attempt_id)
    adapter = load_evaluator(spec)
    try:
        os.chdir(job.project_root)
        if not recovery:
            reporter.progress("Evaluation started")
        operation = adapter.recover(job, reporter) if recovery else adapter.evaluate(job, reporter)
        result = asyncio.run(operation) if inspect.isawaitable(operation) else operation
        if result is None:
            journal.update(
                job.id,
                "recovery_required",
                {"message": "Execution state is unknown; confirm recovery or retry explicitly"},
                job.attempt_id,
            )
        else:
            result = EvaluationResult.model_validate(result)
            journal.update(job.id, "completed", result.model_dump(mode="json"), job.attempt_id)
    except BaseException as exc:
        state = "recovery_required" if recovery or reporter.saved_state else "failed"
        journal.update(job.id, state, {"message": str(exc), "error_type": type(exc).__name__}, job.attempt_id)


class Gateway:
    """Own transport and local execution while adapters own evaluation logic."""

    def __init__(self, server: str, name: str, allowed_users: list[str], evaluator: str, state_dir: Path, concurrency: int = 1):
        """Load a persistent identity, adapter and execution journal."""
        self.url = server.rstrip("/") + "/api/v1/llm4ad/evaluation-gateways"
        self.name, self.allowed_users = name, [str(uuid.UUID(value)) for value in allowed_users]
        filename, klass = evaluator.rsplit(":", 1)
        self.spec = f"{Path(filename).expanduser().resolve()}:{klass}"
        self.adapter = load_evaluator(self.spec)
        self.capacity = concurrency
        self.root = state_dir.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity_file = self.root / "identity.json"
        if not identity_file.exists():
            identity = {"id": str(uuid.uuid4()), "secret": secrets.token_urlsafe(48), "server": self.url}
            fd = os.open(identity_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(identity, stream)
        self.identity = json.loads(identity_file.read_text())
        if self.identity["server"] != self.url:
            raise ValueError("Use a separate state directory for each platform")
        self.journal = Journal(self.root / "jobs.sqlite3")
        self.processes: dict[str, multiprocessing.Process] = {}
        self.restart_jobs = {
            r["id"] for r in self.journal.rows(pending_only=True) if r["state"] not in {"completed", "failed", "cancelled"}
        }
        self.wakeup = asyncio.Event()

    def start_process(self, payload: dict, recovery: bool = False):
        """Spawn a portable isolated executor without inheriting platform secrets."""
        if not recovery:
            self.journal.update(payload["id"], "running", {"message": "Starting local evaluator"}, payload["attempt_id"])
        process = multiprocessing.get_context("spawn").Process(
            target=execute, args=(self.journal.path, self.spec, payload, recovery), daemon=False
        )
        process.start()
        self.processes[payload["id"]] = process

    async def request(self, session, method: str, suffix: str, **kwargs):
        """Use HTTP acknowledgements for every durable state transition."""
        async with session.request(method, self.url + suffix, **kwargs) as response:
            response.raise_for_status()
            return await response.json()

    async def reconcile(self, session, payload: dict, fresh: bool = False):
        """Resume known work, preserving uncertainty after lost local state."""
        job_id = payload["id"]
        local = self.journal.get(job_id)
        if local and json.loads(local["job"])["attempt_id"] != payload["attempt_id"]:
            stop_process(self.processes.pop(job_id, None))
            local = None
        if payload["state"] == "cancel_requested":
            if local and local["state"] in {"completed", "failed", "cancelled"}:
                self.journal.update(job_id, "cancelled", {"message": "Cancelled by platform"})
                return
            process = self.processes.pop(job_id, None)
            stop_process(process)
            saved = Reporter(self.journal, job_id).saved_state if local else {}
            job = RemoteJob.model_validate(json.loads(local["job"]) if local else payload)
            try:
                confirmed = await asyncio.wait_for(self.adapter.cancel(job, saved), timeout=30)
            except Exception:
                confirmed = False
            # Missing state may hide an external scheduler job. Preserve the slot
            # until recovery/cancellation is explicitly confirmed.
            if confirmed or (not saved and process is not None):
                self.journal.accept(payload)
                self.journal.update(job_id, "cancelled", {"message": "Cancellation confirmed"})
            elif local:
                self.journal.update(job_id, "recovery_required", {"message": "Confirm external cancellation"})
            else:
                self.journal.accept(payload)
                self.journal.update(job_id, "recovery_required", {"message": "Local state missing; confirm cancellation"})
            return
        if local is None:
            self.restart_jobs.discard(job_id)
            payload = {**payload, "project_root": str(self.root / "work" / job_id / payload["attempt_id"])}
            self.journal.accept(payload)
            if not fresh:
                self.journal.update(
                    job_id, "recovery_required", {"message": "Local execution state is missing; confirm recovery"}
                )
                return
            local = self.journal.get(job_id)
        if job_id in self.restart_jobs:
            self.restart_jobs.remove(job_id)
            if local["state"] in {"completed", "failed", "cancelled"}:
                return
            self.journal.update(job_id, "recovery_required", {"message": "Reconciling execution after gateway restart"})
            if type(self.adapter).recover is not GatewayEvaluator.recover:
                self.start_process(json.loads(local["job"]), recovery=True)
        elif local["state"] == "accepted" and job_id not in self.processes:
            job = json.loads(local["job"])
            async with session.get(self.url + f"/{self.identity['id']}/jobs/{job_id}/bundle") as response:
                response.raise_for_status()
                candidate = await response.read()
            try:
                extract_candidate(candidate, Path(job["project_root"]))
            except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
                self.journal.update(job_id, "failed", {"message": str(exc), "error_type": "CandidateArchiveError"})
                return
            self.start_process(job)

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
                    self.journal.update(row["id"], "recovery_required", {"message": "Executor exited without a durable result"})
            job = json.loads(row["job"])
            if row["seq"] > row["ack"]:
                try:
                    response = await self.request(
                        session,
                        "POST",
                        f"/{gateway_id}/jobs/{row['id']}/report",
                        json={
                            "attempt_id": job["attempt_id"],
                            "sequence": row["seq"],
                            "state": row["state"],
                            "report": json.loads(row["report"]),
                        },
                    )
                except aiohttp.ClientResponseError as exc:
                    if exc.status not in {404, 409}:
                        raise
                    process = self.processes.pop(row["id"], None)
                    stop_process(process)
                    saved = json.loads(row["saved"])
                    confirmed = not saved or row["state"] in {"completed", "failed", "cancelled"}
                    if not confirmed:
                        with suppress(Exception):
                            confirmed = await asyncio.wait_for(
                                self.adapter.cancel(RemoteJob.model_validate(job), saved), timeout=30
                            )
                    self.journal.update(
                        row["id"],
                        "cancelled" if confirmed else "recovery_required",
                        {
                            "message": "Execution attempt superseded by platform; confirm external cleanup"
                            if not confirmed
                            else "Execution attempt superseded by platform"
                        },
                    )
                    self.journal.acknowledge(row["id"], row["seq"] + 1)
                    continue
                if response["state"] == "queued":
                    self.journal.update(row["id"], "cancelled", {"message": "Attempt released for an explicitly confirmed retry"})
                    self.journal.acknowledge(row["id"], row["seq"] + 1)
                else:
                    self.journal.acknowledge(row["id"], response["ack"])
        for _ in range(self.capacity):
            payload = await self.request(session, "POST", f"/{gateway_id}/claim")
            if payload is None:
                break
            await self.reconcile(session, payload, fresh=True)

    async def notifications(self, session):
        """Reconnect outbound notifications; heartbeat polling also repairs missed hints."""
        url = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        while True:
            try:
                async with session.ws_connect(url + f"/{self.identity['id']}/events", heartbeat=10) as connection:
                    async for _message in connection:
                        self.wakeup.set()
            except (aiohttp.ClientError, OSError, TimeoutError):
                await asyncio.sleep(2)

    async def run(self):
        """Keep local execution and durable results alive through network outages."""
        headers = {"Authorization": "Bearer " + self.identity["secret"]}
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:

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
                        print(f"Gateway disconnected; retaining work and retrying ({type(exc).__name__}).", flush=True)
                    # Deadlines are enforced even when HTTP communication fails.
                    self.enforce_deadlines()
                    self.wakeup.clear()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self.wakeup.wait(), timeout=10)
            finally:
                watchdog.cancel()
                if notifications:
                    notifications.cancel()
                await asyncio.gather(watchdog, *([notifications] if notifications else []), return_exceptions=True)
                for process in self.processes.values():
                    stop_process(process)

    def enforce_deadlines(self):
        """Apply actual execution budgets locally, independently of connectivity."""
        for row in self.journal.rows(pending_only=True):
            if row["state"] != "running" or not row["started"]:
                continue
            job = json.loads(row["job"])
            if time.time() - row["started"] > job["timeout"]:
                process = self.processes.get(row["id"])
                stop_process(process)
                self.journal.update(
                    row["id"],
                    "recovery_required" if json.loads(row["saved"]) else "failed",
                    {"message": f"Execution timed out after {job['timeout']}s", "error_type": "ExecutionTimeout"},
                )
