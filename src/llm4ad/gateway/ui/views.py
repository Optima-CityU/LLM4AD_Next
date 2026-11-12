"""Dependency-free dialog state over fenced controller observations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .helpers import job_identity, value_text


@dataclass(frozen=True)
class TaskReference:
    """Keep dialog identity and startup purpose immutable across table refreshes."""

    binding: str
    job_id: str
    attempt_id: str
    purpose: str = "task"

    @classmethod
    def from_row(cls, row: dict, *, purpose: str = "task") -> TaskReference:
        """Take a complete attempt fence from a selected row, never a row index."""
        return cls(*job_identity(row), purpose=purpose)

    @property
    def identity(self) -> tuple[str, str, str]:
        """Return positional identifiers accepted by the SDK controller."""
        return self.binding, self.job_id, self.attempt_id


@dataclass
class DialogScope:
    """Stop view refreshes without owning gateway or evaluation lifecycle methods."""

    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: set[asyncio.Task] = field(default_factory=set, repr=False)

    async def query(self, operation: Callable) -> Any:
        """Track only an in-flight view query so closing can cancel its wait."""
        if self.closed:
            return None
        task = asyncio.current_task()
        if task:
            self.pending.add(task)
        try:
            return await operation()
        except asyncio.CancelledError:
            if not self.closed:
                raise
            return None
        finally:
            if task:
                self.pending.discard(task)

    def close(self) -> None:
        """Mark this dialog closed; service lifetime is deliberately outside this object."""
        self.closed = True
        for task in tuple(self.pending):
            if task is not asyncio.current_task():
                task.cancel()


class ReportingFeed:
    """Read platform reporting records by sequence, independently of local log files."""

    def __init__(self, controller: Any, reference: TaskReference):
        """Bind one immutable attempt and an initially empty reporting window."""
        self.controller, self.reference = controller, reference
        self.scope = DialogScope()
        self.items: list[dict] = []
        self.has_older = self.has_newer = False
        self.total = 0
        self.level: str | None = None
        self.search = ""
        self.initialized = False
        self.generation = 0

    async def fetch(self, direction: str = "newer") -> list[dict]:
        """Fetch an initial tail, older history or records after the newest sequence."""
        async with self.scope.lock:
            if self.scope.closed:
                return []
            arguments: dict[str, Any] = {
                "limit": 100,
                "level": self.level,
                "search": self.search,
                "purpose": self.reference.purpose,
            }
            if self.initialized and self.items:
                if direction == "older":
                    arguments["before_sequence"] = self.items[0]["sequence"]
                elif direction != "initial":
                    arguments["after_sequence"] = self.items[-1]["sequence"]
            generation = self.generation
            result = await self.scope.query(lambda: self.controller.get_messages(*self.reference.identity, **arguments))
            if result is None or self.scope.closed or generation != self.generation:
                return []
            previous = {} if direction == "initial" else {item["sequence"]: item for item in self.items}
            incoming = [item for item in result.get("items", []) if item["sequence"] not in previous]
            previous.update({item["sequence"]: item for item in result.get("items", [])})
            self.items = [previous[key] for key in sorted(previous)]
            self.total = result.get("total", len(self.items))
            if not self.initialized or direction in {"initial", "older"}:
                self.has_older = bool(result.get("has_older"))
            self.has_newer = bool(result.get("has_newer"))
            self.initialized = True
            return incoming

    def reset(self, *, level: str | None, search: str) -> None:
        """Start a new filtered tail without changing its execution attempt."""
        self.level, self.search, self.initialized = level, search, False
        self.generation += 1
        self.items = []


class LocalLogFeed:
    """Read local stdout/stderr by byte offsets without using reporting messages."""

    def __init__(self, controller: Any, reference: TaskReference):
        """Bind a task or startup check to a selected local log source."""
        self.controller, self.reference = controller, reference
        self.scope = DialogScope()
        self.source: str | None = None
        self.first_offset: int | None = None
        self.next_offset: int | None = None
        self.available = True
        self.size = 0
        self.generation = 0

    def select(self, source: str) -> None:
        """Reset offsets when the operator explicitly chooses a different source."""
        self.source, self.first_offset, self.next_offset = source, None, None
        self.generation += 1

    async def fetch(self, *, older: bool = False) -> dict:
        """Fetch a tail, incremental bytes or a bounded preceding chunk."""
        async with self.scope.lock:
            if self.scope.closed or self.source is None:
                return {}
            offset = max(0, self.first_offset - 65536) if older and self.first_offset is not None else self.next_offset
            limit = self.first_offset - offset if older and self.first_offset is not None else 65536
            if limit <= 0:
                return {}
            generation, source = self.generation, self.source
            result = await self.scope.query(
                lambda: self.controller.read_local_log(
                    *self.reference.identity, source, offset=offset, limit=limit, purpose=self.reference.purpose
                )
            )
            if result is None or self.scope.closed or generation != self.generation:
                return {}
            self.available, self.size = result.get("available", False), result.get("size", 0)
            if self.first_offset is None or older or result.get("reset"):
                self.first_offset = result.get("offset", 0)
            if not older or result.get("reset"):
                self.next_offset = result.get("next_offset", self.next_offset)
            return result


def metric_rows(detail: dict) -> list[dict[str, str]]:
    """Flatten recorded metrics as exact text values instead of a report JSON panel."""
    report = detail.get("report") or {}
    metrics = detail.get("metrics", report.get("metrics", {})) or {}
    rows = []

    def visit(name: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(f"{name}.{key}" if name else str(key), item)
        else:
            rows.append({"name": name, "value": value_text(value), "exact": value_text(value)})

    visit("", metrics)
    monitors = detail.get("monitor_metrics", report.get("monitor_metrics", {})) or {}
    if monitors:
        visit("monitor", monitors)
    for name in ("score", "duration_ms", "completed", "total", "success"):
        if name in report and name not in {row["name"] for row in rows}:
            visit(name, report[name])
    return rows
