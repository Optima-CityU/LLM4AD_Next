"""HGamey-only assignment of comparable benchmarks to local phones."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, cast

import aiohttp

from llm4ad.gateway.sdk.contracts import GatewayEvaluator, load_evaluator
from llm4ad.gateway.sdk.errors import EvaluationUnavailableError

if TYPE_CHECKING:
    from .evaluator import HGameyShaderEvaluator


class HGameyPoolEvaluator(GatewayEvaluator):
    """Lease an idle, verified phone for each SDK execution."""

    manages_evidence_retention = True

    def __init__(self, config: str):
        """Load the frozen local members of a single comparison contract."""
        self.config = json.loads(Path(config).read_text())
        self.members = {
            key: load_evaluator(spec) for key, spec in self.config["members"].items()
        }
        self.contract = next(iter(self.members.values())).contract
        if any(member.contract != self.contract for member in self.members.values()):
            raise ValueError("HGamey pool members must have the same evaluation contract")
        self.cleanup_timeout = max(member.cleanup_timeout for member in self.members.values())
        self.database = Path(self.config["database"])
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.ready_path = Path(self.config["ready"])
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS assignment (job TEXT NOT NULL, attempt TEXT NOT NULL, "
                "execution TEXT NOT NULL, device TEXT NOT NULL, spec TEXT NOT NULL, active INTEGER NOT NULL, "
                "PRIMARY KEY (job,attempt,execution))"
            )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_phone ON assignment(device) WHERE active=1")

    def connect(self):
        """Open the local assignment database shared across HGamey evaluators."""
        return sqlite3.connect(self.database, timeout=10)

    @staticmethod
    def key(job):
        """Identify an execution independently of its platform job ID."""
        return job.id, job.attempt_id, job.execution_id

    def assignment(self, job):
        """Find the original phone even after its assignment has been released."""
        with closing(self.connect()) as db:
            return db.execute(
                "SELECT device,spec,active FROM assignment WHERE job=? AND attempt=? AND execution=?",
                self.key(job),
            ).fetchone()

    def adapter_for(self, job):
        """Recover the original device-specific adapter for a persisted execution."""
        row = self.assignment(job)
        if row is None:
            return None
        device, spec, _ = row
        if self.config["members"].get(device) == spec:
            return self.members[device]
        adapter = load_evaluator(spec)
        if adapter.contract != self.contract:
            raise RuntimeError("Saved HGamey assignment uses a different contract")
        return adapter

    def reserve(self, job, available):
        """Reserve an eligible phone atomically, preferring untried phones."""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT active FROM assignment WHERE job=? AND attempt=? AND execution=?", self.key(job)
            ).fetchone()
            if existing is not None:
                if not existing[0]:
                    raise RuntimeError("A released HGamey execution cannot be assigned again")
                return True
            busy = {row[0] for row in db.execute("SELECT device FROM assignment WHERE active=1")}
            tried = {
                row[0]: row[1] for row in db.execute(
                    "SELECT device,count(*) FROM assignment WHERE job=? GROUP BY device", (job.id,)
                )
            }
            choices = sorted(set(available) - busy, key=lambda device: (tried.get(device, 0), device))
            if not choices:
                return False
            device = choices[0]
            db.execute("INSERT INTO assignment VALUES (?,?,?,?,?,1)", (*self.key(job), device, self.config["members"][device]))
            return True

    def release(self, job):
        """Release only the phone assignment owned by this execution."""
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE assignment SET active=0 WHERE job=? AND attempt=? AND execution=?",
                self.key(job),
            )

    def verified(self):
        """Read the phones that passed startup verification in this runtime."""
        return json.loads(self.ready_path.read_text())["devices"]

    async def before_task(self, job, reporter, operation):
        """Select a ready phone for new work and resume the original phone on recovery."""
        if operation == "cancel":
            return
        owned = self.assignment(job)
        if owned is not None and not owned[2]:
            raise RuntimeError("The saved HGamey execution has already released its phone")
        if operation == "evaluate" and owned is None:
            reporter.progress("Waiting for an idle compatible HGamey phone")
            stop = asyncio.get_running_loop().time() + job.timeout
            probe = cast("HGameyShaderEvaluator", next(iter(self.members.values())))
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                while True:
                    health = await probe.rpc(session, "GET", "/v1/health")
                    verified = set(self.verified())
                    available = [
                        key for key, device in health["devices"].items()
                        if key in verified and key in self.members and device["status"] == "ok"
                        and device.get("worker_alive") and not device.get("active_job") and not device.get("queued")
                    ]
                    if self.reserve(job, available):
                        break
                    if asyncio.get_running_loop().time() >= stop:
                        raise EvaluationUnavailableError("No compatible HGamey phone became idle")
                    await asyncio.sleep(0.25)
        adapter = self.adapter_for(job)
        if adapter is None:
            raise EvaluationUnavailableError("The original HGamey assignment could not be recovered")
        await adapter.before_task(job, reporter, operation)

    async def evaluate(self, job, reporter):
        """Evaluate the project on its assigned phone."""
        return await self.adapter_for(job).evaluate(job, reporter)

    async def recover(self, job, reporter):
        """Recover the existing experiment on its original phone."""
        return await self.adapter_for(job).recover(job, reporter)

    async def after_task(self, job, reporter, operation, *, result=None, error=None):
        """Publish diagnostics from the owning device adapter."""
        adapter = self.adapter_for(job)
        if adapter is None or operation == "cancel":
            return
        await adapter.after_task(job, reporter, operation, result=result, error=error)

    async def cancel(self, job, saved_state):
        """Restore only the assigned device and release it after confirmation."""
        adapter = self.adapter_for(job)
        if adapter is None:
            return not saved_state
        if saved_state:
            adapter.validate_prepared(job, saved_state)
        confirmed = await adapter.cancel(job, saved_state)
        if confirmed:
            self.release(job)
        return confirmed
