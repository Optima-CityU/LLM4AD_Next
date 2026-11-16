"""Durable local execution generations and bounded retry accounting."""

from __future__ import annotations

import json
import math
import time
import uuid

from .states import transition

TERMINAL = {"completed", "failed", "cancelled"}
MAX_EXECUTIONS = 3


class Executions:
    """Keep controller decisions atomic and fence callbacks from older workers."""

    def __init__(self, journal):
        """Open execution bookkeeping alongside the authoritative job journal."""
        self.journal = journal
        with journal.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_executions (job_id TEXT,attempt_id TEXT,"
                "execution_id TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,executions INTEGER NOT NULL DEFAULT 0,"
                "retry_at REAL NOT NULL DEFAULT 0,cleanup_failures INTEGER NOT NULL DEFAULT 0,"
                "PRIMARY KEY(job_id,attempt_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS execution_history (execution_id TEXT PRIMARY KEY,job_id TEXT,"
                "attempt_id TEXT,snapshot TEXT NOT NULL,created_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS cleanup_debts (execution_id TEXT PRIMARY KEY,"
                "payload TEXT NOT NULL,saved TEXT NOT NULL,failures INTEGER NOT NULL DEFAULT 0,"
                "next_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '')"
            )

    def get(self, job_id: str, attempt_id: str) -> dict:
        """Read a current generation without confusing platform and local attempts."""
        import sqlite3

        with self.journal.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM local_executions WHERE job_id=? AND attempt_id=?", (job_id, attempt_id)).fetchone()
            return dict(row) if row else {}

    def ensure(self, job: dict, row: dict) -> dict:
        """Migrate legacy recovery records into automatic cleanup and retry."""
        state = row["state"]
        initial = state if state in TERMINAL else ("accepted" if state == "accepted" and row["started"] is None else "retrying")
        with self.journal.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO local_executions(job_id,attempt_id,state,executions,execution_id) VALUES(?,?,?,?,?)",
                (job["id"], job["attempt_id"], initial, int(initial == "retrying"), job.get("execution_id", "")),
            )
        return self.get(job["id"], job["attempt_id"])

    def move(self, job: dict, event: str, *, delay: float = 0) -> bool:
        """Commit a declared state transition under the current execution fence."""
        with self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,execution_id FROM local_executions WHERE job_id=? AND attempt_id=?",
                (job["id"], job["attempt_id"]),
            ).fetchone()
            if row is None or (job.get("execution_id") and row[1] != job["execution_id"]):
                return False
            state = transition(row[0], event)
            db.execute(
                "UPDATE local_executions SET state=?,retry_at=? WHERE job_id=? AND attempt_id=?",
                (state, time.time() + delay if delay else 0, job["id"], job["attempt_id"]),
            )
            return True

    def commit(
        self,
        job: dict,
        events: list[str],
        wire_state: str,
        report: dict,
        *,
        execution_status: str = "finished",
        delay: float = 0,
        allow_stopped: bool = False,
    ) -> bool:
        """Atomically commit an execution decision and its durable outbound report.

        Args:
            job: Exact current platform attempt and local execution generation.
            events: Ordered explicit FSM events, applied within this transaction.
            wire_state: Existing transport state; retrying uses running, and
                cancelling uses running or cancel_requested.
            report: JSON-compatible report, rejecting nonfinite numeric values.
            execution_status: Status to persist in execution_controls.
            delay: Nonnegative finite delay before another execution is eligible.
            allow_stopped: Permit cancellation or cleanup outcomes after a stop
                request, while retaining attempt, generation and terminal fences.

        Returns:
            True if committed, or False for missing, stale, terminal or stopped work.

        Raises:
            ValueError: Inputs, FSM transitions or wire-state correspondence are invalid.
            TypeError: The report cannot be serialized as JSON.
            sqlite3.Error: A database failure rolls back every part of the decision.
        """
        if not isinstance(events, list) or any(not isinstance(event, str) for event in events):
            raise ValueError("events must be a list of explicit FSM event names")
        if not isinstance(report, dict):
            raise ValueError("report must be a JSON object")
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("delay must be finite and nonnegative")
        if not isinstance(execution_status, str) or not execution_status:
            raise ValueError("execution_status must be a nonempty string")
        encoded = json.dumps(report, allow_nan=False)
        attempt_id = job["attempt_id"]
        execution_id = job.get("execution_id") or ""
        with self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT job,state FROM jobs WHERE id=?", (job["id"],)).fetchone()
            record = db.execute(
                "SELECT state,execution_id FROM local_executions WHERE job_id=? AND attempt_id=?",
                (job["id"], attempt_id),
            ).fetchone()
            if current is None or record is None or current[1] in TERMINAL or record[0] in TERMINAL:
                return False
            live = json.loads(current[0])
            if live["attempt_id"] != attempt_id or (live.get("execution_id") or "") != execution_id or record[1] != execution_id:
                return False
            control = db.execute(
                "SELECT stop_requested FROM execution_controls WHERE job_id=? AND attempt_id=?",
                (job["id"], attempt_id),
            ).fetchone()
            if not allow_stopped and control and control[0] in {"cancel", "timeout", "superseded"}:
                return False
            state = record[0]
            for event in events:
                state = transition(state, event)
            wire_states = {
                "accepted": {"accepted"},
                "running": {"running"},
                "retrying": {"running", "recovery_required"},
                "cancelling": {"running", "cancel_requested"},
                **{terminal: {terminal} for terminal in TERMINAL},
            }
            if wire_state not in wire_states.get(state, set()):
                raise ValueError(f"Execution state {state!r} does not match wire state {wire_state!r}")
            now = time.time()
            db.execute(
                "UPDATE local_executions SET state=?,retry_at=? WHERE job_id=? AND attempt_id=?",
                (state, now + delay if delay else 0, job["id"], attempt_id),
            )
            changed = db.execute(
                "UPDATE jobs SET state=?,report=?,seq=seq+1,started="
                "CASE WHEN ?='running' THEN COALESCE(started,?) ELSE started END,updated_at=?,finished_at="
                "CASE WHEN ? IN ('completed','failed','cancelled') THEN COALESCE(finished_at,?) ELSE NULL END "
                "WHERE id=? AND json_extract(job,'$.attempt_id')=? "
                "AND COALESCE(json_extract(job,'$.execution_id'),'')=? "
                "AND state NOT IN ('completed','failed','cancelled')",
                (wire_state, encoded, wire_state, now, now, wire_state, now, job["id"], attempt_id, execution_id),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Execution changed while committing its outcome")
            db.execute(
                "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,?,'') "
                "ON CONFLICT(job_id,attempt_id) DO UPDATE SET status=excluded.status",
                (job["id"], attempt_id, execution_status),
            )
            return True

    def begin(self, job: dict, project_root: str | None = None) -> dict | None:
        """Reserve one of three total executions before enqueueing the worker."""
        with self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT job,state,saved,report FROM jobs WHERE id=?", (job["id"],)).fetchone()
            record = db.execute(
                "SELECT state,executions,retry_at FROM local_executions WHERE job_id=? AND attempt_id=?",
                (job["id"], job["attempt_id"]),
            ).fetchone()
            control = db.execute(
                "SELECT stop_requested FROM execution_controls WHERE job_id=? AND attempt_id=?",
                (job["id"], job["attempt_id"]),
            ).fetchone()
            if (
                current is None
                or current[1] in TERMINAL
                or json.loads(current[0])["attempt_id"] != job["attempt_id"]
                or record is None
                or record[0] not in {"accepted", "retrying"}
                or record[1] >= MAX_EXECUTIONS
                or record[2] > time.time()
                or (control and control[0] in {"cancel", "timeout", "superseded"})
            ):
                return None
            previous = json.loads(current[0])
            if previous.get("execution_id"):
                observation = db.execute(
                    "SELECT metadata FROM observations WHERE job_id=? AND attempt_id=?", (job["id"], job["attempt_id"])
                ).fetchone()
                artifacts = [
                    dict(zip(("name", "path", "sha256", "size", "media_type", "object_key"), r, strict=True))
                    for r in db.execute(
                        "SELECT name,path,sha256,size,media_type,object_key FROM artifacts WHERE job_id=? AND attempt_id=?",
                        (job["id"], job["attempt_id"]),
                    )
                ]
                db.execute(
                    "INSERT OR IGNORE INTO execution_history VALUES(?,?,?,?,?)",
                    (
                        previous["execution_id"],
                        job["id"],
                        job["attempt_id"],
                        json.dumps(
                            {
                                "job": previous,
                                "saved": json.loads(current[2]),
                                "report": json.loads(current[3]),
                                "metadata": json.loads(observation[0]) if observation else {},
                                "artifacts": artifacts,
                            }
                        ),
                        time.time(),
                    ),
                )
            payload = {**previous, "execution_id": uuid.uuid4().hex}
            if project_root:
                payload["project_root"] = project_root.format(execution_id=payload["execution_id"])
            db.execute(
                "UPDATE local_executions SET state=?,execution_id=?,executions=executions+1,retry_at=0,cleanup_failures=0 "
                "WHERE job_id=? AND attempt_id=?",
                (transition(record[0], "launch"), payload["execution_id"], job["id"], job["attempt_id"]),
            )
            db.execute("DELETE FROM artifacts WHERE job_id=? AND attempt_id=?", (job["id"], job["attempt_id"]))
            db.execute("DELETE FROM observations WHERE job_id=? AND attempt_id=?", (job["id"], job["attempt_id"]))
            db.execute(
                "UPDATE jobs SET job=?,saved='{}',state='running',report='{}',seq=seq+1,updated_at=? WHERE id=?",
                (json.dumps(payload), time.time(), job["id"]),
            )
            return payload

    def current(self, job: dict) -> bool:
        """Reject late worker callbacks after a retry, stop or platform supersession."""
        row = self.journal.get(job["id"])
        if not row or row["state"] in TERMINAL:
            return False
        live = json.loads(row["job"])
        return live["attempt_id"] == job["attempt_id"] and live.get("execution_id", "") == job.get("execution_id", "")
