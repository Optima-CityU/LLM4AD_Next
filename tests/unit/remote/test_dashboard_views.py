"""Dialog state tests use real cursor contracts without requiring NiceGUI."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest

from llm4ad.gateway.ui.helpers import (
    duration_text,
    legacy_task_target,
    observed_duration,
    phase_text,
    settings_diff,
    settings_payload,
)
from llm4ad.gateway.ui.presentation import translate
from llm4ad.gateway.ui.views import LocalLogFeed, ReportingFeed, TaskReference, metric_rows

pytestmark = pytest.mark.unit


class ObservationController:
    """Record cursor calls while withholding any gateway lifecycle methods."""

    def __init__(self):
        """Prepare independently queued platform and runtime responses."""
        self.calls = []
        self.reporting = []
        self.runtime = []

    async def get_messages(self, *identity, **kwargs):
        """Return only platform messages, never local stdout/stderr."""
        self.calls.append(("reporting", identity, kwargs))
        return self.reporting.pop(0)

    async def read_local_log(self, *identity, **kwargs):
        """Return only local source bytes, never reporting messages."""
        self.calls.append(("runtime", identity, kwargs))
        return self.runtime.pop(0)


def records(*sequences, older=False, total=300):
    """Make an ascending sequence result matching the controller contract."""
    return {
        "items": [{"sequence": sequence, "message": f"record {sequence}"} for sequence in sequences],
        "has_older": older,
        "has_newer": False,
        "total": total,
    }


async def test_reporting_tail_history_incremental_and_attempt_fence():
    """History uses the oldest sequence; new records use the newest immutable attempt."""
    controller = ObservationController()
    controller.reporting = [records(201, 202, older=True), records(203), records(199, 200, older=True), records(203, 204)]
    reference = TaskReference("resource", "job", "attempt")
    view = ReportingFeed(controller, reference)
    await view.fetch()
    assert "after_sequence" not in controller.calls[0][2] and "before_sequence" not in controller.calls[0][2]
    await view.fetch()
    assert controller.calls[1][2]["after_sequence"] == 202
    await view.fetch("older")
    assert controller.calls[2][2]["before_sequence"] == 201
    await view.fetch()
    assert [item["sequence"] for item in view.items] == [199, 200, 201, 202, 203, 204]
    assert all(call[1] == reference.identity for call in controller.calls)
    assert all(call[0] == "reporting" for call in controller.calls)


async def test_reporting_close_and_filters_stop_old_cursor_work():
    """A closed dialog cannot poll again; changing filters starts a new tail."""
    controller = ObservationController()
    controller.reporting = [records(10), records(12)]
    view = ReportingFeed(controller, TaskReference("b", "j", "a", purpose="startup"))
    await view.fetch()
    view.reset(level="warning", search="compile")
    await view.fetch()
    assert controller.calls[-1][2] == {"limit": 100, "level": "warning", "search": "compile", "purpose": "startup"}
    view.scope.close()
    assert await view.fetch() == []
    assert len(controller.calls) == 2


async def test_close_during_read_discards_result():
    """Closing a view never installs a late response from its former timer."""
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowController:
        async def get_messages(self, *identity, **kwargs):
            entered.set()
            await release.wait()
            return records(1)

    view = ReportingFeed(SlowController(), TaskReference("b", "j", "a"))
    task = asyncio.create_task(view.fetch())
    await entered.wait()
    view.scope.close()
    release.set()
    assert await task == [] and view.items == []


async def test_reporting_filter_generation_rejects_an_old_inflight_response():
    """A slow response from former filters cannot populate the replacement filter view."""
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowController:
        async def get_messages(self, *identity, **kwargs):
            entered.set()
            await release.wait()
            return records(1)

    view = ReportingFeed(SlowController(), TaskReference("b", "j", "a"))
    task = asyncio.create_task(view.fetch())
    await entered.wait()
    view.reset(level="error", search="NEW")
    release.set()
    assert await task == [] and view.items == []
    assert view.level == "error" and view.search == "NEW" and not view.initialized


async def test_local_source_generation_rejects_old_stdout_offsets():
    """Changing to stderr while stdout is pending cannot install stdout text or offsets."""
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowController:
        async def read_local_log(self, *identity, **kwargs):
            entered.set()
            await release.wait()
            return {"text": "old stdout", "offset": 100, "next_offset": 110, "size": 110, "available": True}

    view = LocalLogFeed(SlowController(), TaskReference("b", "j", "a"))
    view.select("stdout")
    task = asyncio.create_task(view.fetch())
    await entered.wait()
    view.select("stderr")
    release.set()
    assert await task == {}
    assert view.source == "stderr" and view.first_offset is None and view.next_offset is None


async def test_close_cancels_only_the_pending_view_read():
    """A closed dialog releases an outstanding query without any controller stop method."""
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class SlowController:
        async def get_messages(self, *identity, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    view = ReportingFeed(SlowController(), TaskReference("b", "j", "a"))
    task = asyncio.create_task(view.fetch())
    await entered.wait()
    view.scope.close()
    assert await task == []
    assert cancelled.is_set() and not view.scope.pending


async def test_local_runtime_tail_history_offsets_and_rotation():
    """Runtime output uses byte cursors and resets after truncation or source changes."""
    controller = ObservationController()
    controller.runtime = [
        {"text": "tail", "offset": 100000, "next_offset": 100004, "size": 100004, "available": True},
        {"text": "more", "offset": 100004, "next_offset": 100008, "size": 100008, "available": True},
        {"text": "older", "offset": 34464, "next_offset": 100000, "size": 100008, "available": True},
        {"text": "new", "offset": 0, "next_offset": 3, "size": 3, "available": True, "reset": True},
    ]
    view = LocalLogFeed(controller, TaskReference("b", "j", "a", purpose="startup"))
    view.select("stdout")
    await view.fetch()
    assert controller.calls[0][2]["offset"] is None
    await view.fetch()
    assert controller.calls[1][2]["offset"] == 100004
    await view.fetch(older=True)
    assert controller.calls[2][2]["offset"] == 34464
    assert controller.calls[2][2]["limit"] == 65536
    assert view.next_offset == 100008
    await view.fetch()
    assert view.first_offset == 0 and view.next_offset == 3
    assert all(call[1] == ("b", "j", "a", "stdout") for call in controller.calls)
    assert all(call[2]["purpose"] == "startup" for call in controller.calls)
    view.select("stderr")
    assert view.first_offset is None and view.next_offset is None
    view.scope.close()
    assert await view.fetch() == {}
    assert len(controller.calls) == 4


def test_metrics_preserve_original_precision_without_report_json():
    """Metric rows contain exact textual values, including integers larger than JavaScript's range."""
    rows = metric_rows(
        {
            "report": {
                "metrics": {"time": 0.12345678901234568, "large": 9007199254740993, "nested": {"quality": 1.0000000000000002}},
                "score": 4,
            }
        }
    )
    values = {row["name"]: row["exact"] for row in rows}
    assert values == {
        "time": "0.12345678901234568",
        "large": "9007199254740993",
        "nested.quality": "1.0000000000000002",
        "score": "4",
    }
    assert metric_rows({"report": {"monitor_metrics": {"gpu_time": 2.000000000000001}}}) == [
        {"name": "monitor.gpu_time", "value": "2.000000000000001", "exact": "2.000000000000001"}
    ]


def test_failure_phase_beats_completed_finalization_and_unknown_timing():
    """A completed cleanup hook must not hide the recorded failed preparation stage."""
    phase = {"stage": "finalizing", "status": "completed", "failed_stage": "preparing"}
    assert phase_text(phase) == "Failed during preparing"
    assert phase_text(phase, lambda text: translate("zh", text)) == "失败阶段： 准备"
    assert duration_text(None) == "Not recorded"
    assert duration_text(-1) == "Not recorded"
    assert duration_text(1250) == "1.2 s"
    assert duration_text(90061) == "1 min 30 s"
    assert observed_duration({"elapsed_ms": None, "duration_ms": 43}) == 43
    assert observed_duration({"elapsed_ms": 0, "duration_ms": 43}) == 0


def test_legacy_link_redirect_retains_the_attempt_and_display_preferences():
    """The legacy detail URL becomes one task-list dialog and never loses its attempt fence."""
    target = legacy_task_target({"binding": "b/?", "job_id": "j&", "attempt_id": "a#", "lang": "zh", "theme": "dark"})
    parsed = urlsplit(target)
    assert parsed.path == "/tasks"
    assert parse_qs(parsed.query) == {
        "binding": ["b/?"],
        "job_id": ["j&"],
        "attempt_id": ["a#"],
        "lang": ["zh"],
        "theme": ["dark"],
        "dialog": ["info"],
    }


def test_builtin_selection_clears_stale_import_and_log_retention_is_independent():
    """Built-in evaluator selection cannot persist a stale custom evaluator import."""
    patch = settings_payload(
        {"adapter": "example", "evaluator": "stale:Custom", "cleanup_keep_seconds": 0, "local_log_keep_seconds": 604800}
    )
    assert patch["evaluator"] is None
    assert patch["cleanup_keep_seconds"] == 0 and patch["local_log_keep_seconds"] == 604800
    assert settings_diff(
        {"concurrency": 1, "local_log_keep_seconds": 100}, {"concurrency": 1, "local_log_keep_seconds": 200}
    ) == [{"field": "local_log_keep_seconds", "applied": "100", "pending": "200"}]
