"""SQLite journal shared by the gateway and isolated evaluator processes."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def gateway_lease(directory: Path):
    """Hold an OS-released SQLite lease for one gateway instance per directory."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(directory / "gateway.lock.sqlite3", timeout=0)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        yield
    finally:
        connection.close()


class Journal:
    """Persist accepted work and reports before any network acknowledgement."""

    def __init__(self, path: str | Path):
        """Create the journal schema without resetting saved work."""
        self.path = str(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, job TEXT NOT NULL, "
                "state TEXT NOT NULL, report TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, "
                "ack INTEGER NOT NULL DEFAULT -1, started REAL, saved TEXT NOT NULL DEFAULT '{}')"
            )

    @contextmanager
    def connect(self):
        """Open a short-lived, process-safe journal connection."""
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def accept(self, job: dict):
        """Durably acknowledge a job before starting user code."""
        with self.connect() as db:
            old = db.execute("SELECT job FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if old and json.loads(old[0])["attempt_id"] != job["attempt_id"]:
                db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,job,state,report) VALUES(?,?,?,?)", (job["id"], json.dumps(job), "accepted", "{}")
            )

    def rows(self, pending_only: bool = False):
        """Read current local state for reconciliation and outbox upload."""
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            where = " WHERE state NOT IN ('completed','failed','cancelled') OR seq>ack" if pending_only else ""
            return [dict(row) for row in db.execute("SELECT * FROM jobs" + where)]

    def get(self, job_id: str) -> dict | None:
        """Find a single execution without scanning completed history."""
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update(self, job_id: str, state: str, report: dict | None = None, attempt_id: str | None = None):
        """Atomically save progress and increment its idempotent sequence."""
        with self.connect() as db:
            fence = " AND json_extract(job,'$.attempt_id')=? AND state NOT IN ('completed','failed','cancelled')"
            values = (state, json.dumps(report or {}, allow_nan=False), state, time.time(), job_id)
            db.execute(
                "UPDATE jobs SET state=?,report=?,seq=seq+1,started="
                "CASE WHEN ?='running' THEN COALESCE(started,?) ELSE started END WHERE id=?" + (fence if attempt_id else ""),
                values + ((attempt_id,) if attempt_id else ()),
            )

    def acknowledge(self, job_id: str, seq: int):
        """Mark only the uploaded sequence as acknowledged."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET ack=MAX(ack,?) WHERE id=?", (seq, job_id))


class Reporter:
    """User adapter progress and external-scheduler recovery interface."""

    def __init__(self, journal: Journal, job_id: str, attempt_id: str | None = None):
        """Bind progress and recovery operations to one durable job."""
        self.journal, self.job_id = journal, job_id
        row = journal.get(job_id)
        self.attempt_id = attempt_id or (json.loads(row["job"])["attempt_id"] if row else None)

    @property
    def saved_state(self) -> dict:
        """Return the last durable external execution handle."""
        with self.journal.connect() as db:
            row = db.execute(
                "SELECT saved FROM jobs WHERE id=? AND json_extract(job,'$.attempt_id')=?",
                (self.job_id, self.attempt_id),
            ).fetchone()
            return json.loads(row[0]) if row else {}

    def save_state(self, state: dict):
        """Persist external job identifiers before awaiting their completion."""
        with self.journal.connect() as db:
            db.execute(
                "UPDATE jobs SET saved=? WHERE id=? AND json_extract(job,'$.attempt_id')=?",
                (json.dumps(state, allow_nan=False), self.job_id, self.attempt_id),
            )

    def progress(self, message: str, completed: int | None = None, total: int | None = None):
        """Report observed progress, without inventing a percentage."""
        self.journal.update(self.job_id, "running", {"message": message, "completed": completed, "total": total}, self.attempt_id)
