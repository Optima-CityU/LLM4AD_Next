"""SQLite journal shared by the gateway and isolated evaluator processes."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
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
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for column in ("created_at", "updated_at", "finished_at"):
                if column not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {column} REAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifacts (job_id TEXT, attempt_id TEXT, name TEXT, "
                "path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, media_type TEXT NOT NULL, "
                "object_key TEXT, PRIMARY KEY(job_id,attempt_id,name))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS observations (job_id TEXT, attempt_id TEXT, metadata TEXT NOT NULL, "
                "PRIMARY KEY(job_id,attempt_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, "
                "attempt_id TEXT, created_at REAL, level TEXT, kind TEXT, message TEXT, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS messages_attempt ON messages(job_id,attempt_id,id)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS execution_controls (job_id TEXT, attempt_id TEXT, status TEXT NOT NULL, "
                "stop_requested TEXT NOT NULL DEFAULT '', PRIMARY KEY(job_id,attempt_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_log_attempts (job_id TEXT, attempt_id TEXT, snapshot TEXT NOT NULL, "
                "PRIMARY KEY(job_id,attempt_id))"
            )
            retired_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='retired_attempts'"
            ).fetchone()
            db.execute(
                "CREATE TABLE IF NOT EXISTS retired_attempts (job_id TEXT, attempt_id TEXT, snapshot TEXT NOT NULL, "
                "PRIMARY KEY(job_id,attempt_id))"
            )
            if not retired_exists:
                db.execute(
                    "INSERT INTO retired_attempts(job_id,attempt_id,snapshot) "
                    "SELECT job_id,attempt_id,snapshot FROM local_log_attempts"
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
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],))
            previous = cursor.fetchone()
            old = (
                dict(zip((column[0] for column in cursor.description), previous, strict=True))
                if previous
                else None
            )
            if old and (attempt_id := json.loads(old["job"])["attempt_id"]) != job["attempt_id"]:
                snapshot = json.dumps({**old, "superseded_at": time.time()})
                db.execute(
                    "INSERT OR REPLACE INTO retired_attempts(job_id,attempt_id,snapshot) VALUES(?,?,?)",
                    (job["id"], attempt_id, snapshot),
                )
                identity = hashlib.sha256(f"{job['id']}|{attempt_id}".encode()).hexdigest()
                catalog = (
                    Path(self.path).parent
                    / "local-logs"
                    / Path(self.path).stem
                    / identity
                    / "manifest.json"
                )
                if catalog.is_file():
                    db.execute(
                        "INSERT OR REPLACE INTO local_log_attempts(job_id,attempt_id,snapshot) VALUES(?,?,?)",
                        (job["id"], attempt_id, snapshot),
                    )
                db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,job,state,report,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (job["id"], json.dumps(job), "accepted", "{}", (now := time.time()), now),
            )

    def rows(self, pending_only: bool = False):
        """Read current local state for reconciliation and outbox upload."""
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            where = (
                " WHERE state NOT IN ('completed','failed','cancelled') OR seq>ack"
                if pending_only
                else ""
            )
            return [dict(row) for row in db.execute("SELECT * FROM jobs" + where)]

    def get(self, job_id: str) -> dict | None:
        """Find a single execution without scanning completed history."""
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update(
        self,
        job_id: str,
        state: str,
        report: dict | None = None,
        attempt_id: str | None = None,
        *,
        execution_status: str | None = None,
    ):
        """Atomically save progress and increment its idempotent sequence."""
        with self.connect() as db:
            fence = " AND json_extract(job,'$.attempt_id')=? AND state NOT IN ('completed','failed','cancelled')"
            now = time.time()
            values = (
                state,
                json.dumps(report or {}, allow_nan=False),
                state,
                now,
                now,
                state,
                now,
                job_id,
            )
            changed = db.execute(
                "UPDATE jobs SET state=?,report=?,seq=seq+1,started="
                "CASE WHEN ?='running' THEN COALESCE(started,?) ELSE started END,updated_at=?,finished_at="
                "CASE WHEN ? IN ('completed','failed','cancelled') THEN COALESCE(finished_at,?) ELSE NULL END "
                "WHERE id=?" + (fence if attempt_id else ""),
                values + ((attempt_id,) if attempt_id else ()),
            )
            if changed.rowcount and execution_status:
                db.execute(
                    "UPDATE execution_controls SET status=? WHERE job_id=? "
                    "AND attempt_id=(SELECT json_extract(job,'$.attempt_id') FROM jobs WHERE id=?)",
                    (execution_status, job_id, job_id),
                )

    def acknowledge(self, job_id: str, seq: int):
        """Mark only the uploaded sequence as acknowledged."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET ack=MAX(ack,?) WHERE id=?", (seq, job_id))

    def artifacts(self, job_id: str, attempt_id: str) -> list[dict]:
        """Read only the immutable evidence belonging to this execution attempt."""
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM artifacts WHERE job_id=? AND attempt_id=? ORDER BY name",
                    (job_id, attempt_id),
                )
            ]

    def observations(self, job_id: str, attempt_id: str) -> tuple[dict, list[dict]]:
        """Read durable extra metadata and ordered messages for exactly one attempt."""
        with self.connect() as db:
            row = db.execute(
                "SELECT metadata FROM observations WHERE job_id=? AND attempt_id=?",
                (job_id, attempt_id),
            ).fetchone()
            metadata = json.loads(row[0]) if row else {}
            messages = [
                {
                    "sequence": row[0],
                    "created_at": datetime.fromtimestamp(row[1], UTC).isoformat(),
                    "level": row[2],
                    "kind": row[3],
                    "message": row[4],
                    "data": json.loads(row[5]),
                }
                for row in db.execute(
                    "SELECT id,created_at,level,kind,message,data FROM messages "
                    "WHERE job_id=? AND attempt_id=? ORDER BY id",
                    (job_id, attempt_id),
                )
            ]
            return metadata, messages

    def _observe(
        self, db, job_id, attempt_id, *, report=None, metadata=None, message=None, state=None
    ):
        """Update an observation inside the caller's authoritative write transaction."""
        row = db.execute("SELECT job,state,report FROM jobs WHERE id=?", (job_id,)).fetchone()
        if (
            row is None
            or json.loads(row[0])["attempt_id"] != attempt_id
            or row[1] in {"completed", "failed", "cancelled"}
        ):
            return False
        if metadata is not None:
            prior = db.execute(
                "SELECT metadata FROM observations WHERE job_id=? AND attempt_id=?",
                (job_id, attempt_id),
            ).fetchone()
            values = json.loads(prior[0]) if prior else {}
            descriptions = {
                **values.get("artifact_descriptions", {}),
                **metadata.get("artifact_descriptions", {}),
            }
            values.update(metadata)
            if descriptions:
                values["artifact_descriptions"] = descriptions
            db.execute(
                "INSERT OR REPLACE INTO observations(job_id,attempt_id,metadata) VALUES(?,?,?)",
                (job_id, attempt_id, json.dumps(values, allow_nan=False)),
            )
        if message is not None:
            db.execute(
                "INSERT INTO messages(job_id,attempt_id,created_at,level,kind,message,data) VALUES(?,?,?,?,?,?,?)",
                (
                    job_id,
                    attempt_id,
                    time.time(),
                    message["level"],
                    message["kind"],
                    message["message"],
                    json.dumps(message.get("data", {}), allow_nan=False),
                ),
            )
        value = dict(report or {}) if state else {**json.loads(row[2]), **(report or {})}
        db.execute(
            "UPDATE jobs SET state=?,report=?,seq=seq+1,started="
            "CASE WHEN ?='running' THEN COALESCE(started,?) ELSE started END,updated_at=?,finished_at="
            "CASE WHEN ? IN ('completed','failed','cancelled') THEN COALESCE(finished_at,?) ELSE NULL END WHERE id=?",
            (
                state or row[1],
                json.dumps(value, allow_nan=False),
                state,
                (now := time.time()),
                now,
                state,
                now,
                job_id,
            ),
        )
        return True

    def observe(self, job_id, attempt_id, **kwargs):
        """Append diagnostics without changing ownership or resetting an execution timer."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._observe(db, job_id, attempt_id, **kwargs)


class Reporter:
    """Persist adapter progress, diagnostics, evidence and external recovery handles."""

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

    def progress(
        self,
        message: str,
        completed: int | None = None,
        total: int | None = None,
        *,
        metadata: dict | None = None,
    ):
        """Report observed progress, without inventing a percentage."""
        self.journal.observe(
            self.job_id,
            self.attempt_id,
            state="running",
            metadata=metadata,
            report={"message": message, "completed": completed, "total": total},
            message={
                "message": message,
                "kind": "progress",
                "level": "info",
                "data": {"completed": completed, "total": total},
            },
        )

    def message(self, message: str, *, level: str = "info", data: dict | None = None) -> None:
        """Persist a diagnostic message without starting or restarting evaluation.

        Args:
            message: Human-readable observation for this attempt.
            level: Diagnostic level retained with the message.
            data: Optional JSON-compatible context, independent of progress counts.
        """
        self.journal.observe(
            self.job_id,
            self.attempt_id,
            report={"message": message, "level": level},
            message={"message": message, "kind": "message", "level": level, "data": data or {}},
        )

    def metadata(self, values: dict) -> None:
        """Merge JSON-compatible context independently of the final evaluation result."""
        self.journal.observe(self.job_id, self.attempt_id, metadata=values)

    def local_log(self, name: str, *, description: str = "") -> Path:
        """Create a private output file without registering an uploaded artifact.

        Args:
            name: Relative, task-scoped source name such as compiler.stderr.
            description: Optional explanation shown in the local log viewer.

        Returns:
            An appendable managed path, or the null device if logging storage is unavailable.
        """
        from contextlib import suppress

        from .local_logs import LocalLogStore

        try:
            return LocalLogStore(self.journal).create(
                self.job_id, self.attempt_id, name, description
            )
        except (OSError, sqlite3.Error) as error:
            with suppress(Exception):
                self.metadata({"local_log_error": type(error).__name__})
            return Path(os.devnull)

    def record_phase(self, observation: dict) -> None:
        """Observe lifecycle work without changing the authoritative task state."""
        from contextlib import suppress

        from .local_logs import LocalLogStore

        values = {"lifecycle": observation}
        with suppress(OSError, ValueError, LookupError, sqlite3.Error):
            values = LocalLogStore(self.journal).record_phase(
                self.job_id, self.attempt_id, observation
            )
        with suppress(OSError, ValueError, LookupError, sqlite3.Error):
            self.metadata(values)

    def enrich(self, report: dict) -> dict:
        """Attach durable observations to live reports and final results."""
        saved, messages = self.journal.observations(self.job_id, self.attempt_id)
        final = report.get("metadata", {})
        metadata = {**saved, **final}
        descriptions = {
            **saved.get("artifact_descriptions", {}),
            **final.get("artifact_descriptions", {}),
        }
        if descriptions:
            metadata["artifact_descriptions"] = descriptions
        if messages:
            metadata["evaluation_messages"] = messages
        return {**report, "metadata": metadata} if metadata else dict(report)

    def artifact(
        self,
        name: str,
        path: str | Path,
        media_type: str = "application/octet-stream",
        *,
        description: str = "",
    ) -> None:
        """Spool an immutable evidence file for retry-safe upload before the result.

        Args:
            name: Human-readable relative evidence name.
            path: Local regular file generated by this evaluator.
            media_type: MIME type retained in the result manifest.
            description: Explanation shown with the saved evidence file.
        """
        from pathlib import PurePosixPath

        from llm4ad.evaluator.models import artifact_media_type

        if (
            not name
            or "\\" in name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
        ):
            raise ValueError("Artifact name must be a relative evidence path")
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            raise ValueError("Artifact must be a regular local file")
        identity = hashlib.sha256(f"{self.job_id}|{self.attempt_id}".encode()).hexdigest()
        folder = Path(self.journal.path).parent / "artifacts" / identity
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as stream:
                shutil.copyfileobj(stream, output)
                output.flush()
                os.fsync(output.fileno())
            copied = Path(temporary)
            digest = hashlib.sha256(copied.read_bytes()).hexdigest()
            size = copied.stat().st_size
            if not 0 < size <= 64 * 1024 * 1024:
                raise ValueError("Evidence file must be nonempty and at most 64 MiB")
            target = folder / digest
            copied.replace(target)
            with self.journal.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT job,state FROM jobs WHERE id=?", (self.job_id,)).fetchone()
                if (
                    row is None
                    or json.loads(row[0])["attempt_id"] != self.attempt_id
                    or row[1] in {"completed", "failed", "cancelled"}
                ):
                    raise ValueError("Execution attempt no longer accepts artifacts")
                old = db.execute(
                    "SELECT sha256 FROM artifacts WHERE job_id=? AND attempt_id=? AND name=?",
                    (self.job_id, self.attempt_id, name),
                ).fetchone()
                if old and old[0] != digest:
                    raise ValueError("An existing evidence name cannot change its content")
                db.execute(
                    "INSERT OR IGNORE INTO artifacts(job_id,attempt_id,name,path,sha256,size,media_type) VALUES(?,?,?,?,?,?,?)",
                    (
                        self.job_id,
                        self.attempt_id,
                        name,
                        str(target),
                        digest,
                        size,
                        artifact_media_type(name, media_type),
                    ),
                )
                if not old or description:
                    self.journal._observe(
                        db,
                        self.job_id,
                        self.attempt_id,
                        metadata=(
                            {"artifact_descriptions": {name: description}} if description else None
                        ),
                    )
        finally:
            Path(temporary).unlink(missing_ok=True)
