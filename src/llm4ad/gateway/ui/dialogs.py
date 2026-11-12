"""Separate NiceGUI task dialogs with view-scoped polling and immutable attempt fences."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from .helpers import (
    artifact_url,
    duration_text,
    format_timestamp,
    local_log_url,
    observed_duration,
    phase_text,
    pretty_json,
    value_text,
)
from .presentation import CANCELLATION_WARNING
from .preview import can_preview, read_preview
from .views import DialogScope, LocalLogFeed, ReportingFeed, TaskReference, metric_rows

DIALOG_TITLES = {
    "records": "Platform reporting records",
    "local": "Local runtime logs",
    "metrics": "Metrics",
    "files": "Files",
    "info": "Execution information",
    "diagnostics": "Diagnostics",
}


async def run_busy(button: Any, action: Callable, error: Any) -> bool:
    """Disable a manual action while it runs and keep failures next to the controls."""
    button.disable()
    button.props("loading")
    error.set_text("")
    try:
        await action()
        return True
    except Exception as exception:
        error.set_text(str(exception) if isinstance(exception, ValueError | LookupError) else type(exception).__name__)
        return False
    finally:
        button.props(remove="loading")
        button.enable()


class TaskDialogs:
    """Open independent records, runtime logs, metrics, files and diagnostic views."""

    def __init__(self, controller: Any, ui: Any, tr: Callable[[str], str], *, on_state: Callable | None = None):
        """Keep dialog state local to the browser page that owns its action icons."""
        self.controller, self.ui, self.tr = controller, ui, tr
        self.on_state = on_state
        self.current: Any = None

    def _poll(self, state: dict, action: Callable, error: Any, loading: Any) -> Callable:
        async def refresh() -> None:
            scope = state["scope"]
            if scope.closed or scope.lock.locked():
                return
            async with scope.lock:
                try:
                    await action()
                    error.set_text("")
                except Exception as exception:
                    error.set_text(
                        f"{self.tr('Operation failed')}: "
                        + (str(exception) if isinstance(exception, ValueError | LookupError) else type(exception).__name__)
                    )
                finally:
                    loading.set_visibility(False)

        state["timers"].append(self.ui.timer(2.0, refresh))
        return refresh

    async def open(self, reference: TaskReference, kind: str, row: dict | None = None) -> None:
        """Open one dialog, retaining its original binding, job, attempt and purpose."""
        if kind not in DIALOG_TITLES:
            return
        if self.current:
            self.current["dialog"].close()
        ui, tr = self.ui, self.tr
        state = {"scope": DialogScope(), "timers": [], "cleanup": []}
        dialog = ui.dialog()
        state["dialog"] = dialog
        self.current = state

        def closed(event) -> None:
            if event.value:
                return
            state["scope"].close()
            for cleanup in state["cleanup"]:
                cleanup()
            for timer in state["timers"]:
                timer.cancel(with_current_invocation=True)
            if self.current is state:
                self.current = None
                if self.on_state:
                    self.on_state(None, None)

        dialog.on_value_change(closed)
        title = "Startup check records" if kind == "records" and reference.purpose == "startup" else DIALOG_TITLES[kind]
        with dialog, ui.card().classes("lab-dialog"):
            with ui.row().classes("lab-row lab-between lab-dialog-header"):
                with ui.column().classes("lab-grow").style("gap:4px"):
                    ui.label(tr(title)).props("role=heading aria-level=2").classes("lab-section-title")
                    if row:
                        ui.label(str(row.get("algorithm_name") or row.get("gateway_name") or "")).classes("lab-muted")
                loading = ui.spinner(size="sm")
                ui.button(icon="close", on_click=dialog.close).props("flat round dense aria-label=" + json.dumps(tr("Close")))
            with ui.column().classes("lab-dialog-body"):
                error = ui.label().classes("lab-inline-error").props('role="alert"')
                if kind == "records":
                    self._records(reference, state, error, loading)
                elif kind == "local":
                    self._local(reference, state, error, loading)
                elif kind == "files":
                    self._files(reference, state, error, loading)
                else:
                    self._detail(reference, kind, state, error, loading)
        dialog.open()
        if self.on_state:
            self.on_state(reference, kind)

    async def startup(self, binding: str, kind: str = "records") -> None:
        """Resolve a startup attempt only after its observed progress link is opened."""
        detail = await self.controller.get_startup(binding)
        if not detail:
            self.ui.notify(self.tr("Startup check is not recorded locally."))
            return
        await self.open(TaskReference.from_row({**detail, "binding": binding}, purpose="startup"), kind, detail)

    async def cancel(self, reference: TaskReference) -> None:
        """Confirm the existing single-attempt cancellation semantics."""
        ui, tr = self.ui, self.tr
        with ui.dialog() as dialog, ui.card().classes("lab-confirm"):
            ui.label(tr("Cancel this evaluation?")).props("role=heading aria-level=2").classes("lab-section-title")
            ui.label(tr(CANCELLATION_WARNING))
            error = ui.label().classes("lab-inline-error").props('role="alert"')

            async def confirm() -> None:
                if await run_busy(button, lambda: self.controller.request_cancel(*reference.identity), error):
                    ui.notify(tr("Cancellation requested"), type="positive")
                    dialog.close()

            with ui.row().classes("lab-row lab-between"):
                ui.button(tr("Keep running"), on_click=dialog.close).props("flat")
                button = ui.button(tr("Confirm cancellation"), on_click=confirm, color="negative").props("unelevated")
        dialog.open()

    async def _scroll_snapshot(self, area: Any) -> dict:
        return await self.ui.run_javascript(
            "const e=document.getElementById(" + json.dumps(area.html_id) + ").querySelector('.q-scrollarea__container');"
            "return e ? {top:e.scrollTop,height:e.scrollHeight} : {top:0,height:0};"
        )

    def _restore_scroll(self, area: Any, before: dict) -> None:
        self.ui.run_javascript(
            "requestAnimationFrame(()=>{const e=document.getElementById("
            + json.dumps(area.html_id)
            + ")?.querySelector('.q-scrollarea__container');if(e)e.scrollTop="
            + str(before.get("top", 0))
            + "+e.scrollHeight-"
            + str(before.get("height", 0))
            + ";});"
        )

    def _tail(self, area: Any) -> None:
        """Follow after Vue has rendered new lines and the scroll container has resized."""
        self.ui.run_javascript(
            "requestAnimationFrame(()=>requestAnimationFrame(()=>{const e=document.getElementById("
            + json.dumps(area.html_id)
            + ")?.querySelector('.q-scrollarea__container');if(e)e.scrollTop=e.scrollHeight;}));"
        )

    def _scroll_intent(self, area: Any, intent: dict) -> None:
        """Distinguish an operator scroll from initial layout and programmatic following."""

        def mark() -> None:
            intent["at"] = time.monotonic()

        area.props("tabindex=0")
        area.on("wheel", mark, args=[])
        area.on("touchmove", mark, args=[])
        area.on(
            "keydown",
            mark,
            args=[],
            js_handler=("e=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))emit();}"),
        )
        area.on(
            "pointerdown",
            mark,
            args=[],
            js_handler=("e=>{if(e.target.closest('.q-scrollarea__thumb,.q-scrollarea__bar'))emit();}"),
        )

    def _records(self, reference: TaskReference, state: dict, error: Any, loading: Any) -> None:
        ui, tr = self.ui, self.tr
        feed = ReportingFeed(self.controller, reference)
        state["cleanup"].append(feed.scope.close)
        ui.label(
            tr(
                "Startup checks are local records; they are not platform task reports."
                if reference.purpose == "startup"
                else "These records describe reports and events sent to the platform."
            )
        ).classes("lab-muted")
        phase = ui.label().classes("lab-muted")
        phase.set_visibility(reference.purpose == "startup")
        if reference.purpose == "startup":
            ui.button(tr("Local runtime logs"), icon="terminal", on_click=lambda: self.open(reference, "local")).props("flat")
        with ui.row().classes("lab-row"):
            level = ui.select(
                {"": tr("All levels"), **{name: name.upper() for name in ("debug", "info", "warning", "error")}},
                value="",
                label=tr("Level"),
            ).props("dense outlined")
            search = ui.input(tr("Search records")).props("dense outlined clearable debounce=400").classes("lab-grow")
            follow = ui.checkbox(tr("Follow new records"), value=True)
        widgets: dict[int, Any] = {}
        intent = {"at": 0.0}

        def on_scroll(event) -> None:
            if (
                time.monotonic() - intent["at"] < 1.5
                and event.vertical_percentage < 0.97
                and event.vertical_size > event.vertical_container_size
            ):
                follow.set_value(False)

        with ui.scroll_area(on_scroll=on_scroll).classes("lab-scroll") as area:
            records = ui.column().classes("lab-records")
        self._scroll_intent(area, intent)
        empty = ui.label(tr("No messages recorded")).classes("lab-muted")
        with ui.row().classes("lab-row lab-between"):
            older = ui.button(tr("Load older records"), on_click=lambda: run_busy(older, load_older, error)).props("outline")
            count = ui.label().classes("lab-muted")

        async def update(direction: str = "newer") -> None:
            if reference.purpose == "startup":
                detail = await state["scope"].query(lambda: self.controller.get_job(*reference.identity, purpose="startup"))
                if detail is None:
                    return
                phase.set_text(phase_text(detail.get("phase"), tr) or tr("Not recorded"))
            before = await state["scope"].query(lambda: self._scroll_snapshot(area)) if direction == "older" else None
            incoming = await feed.fetch(direction)
            if state["scope"].closed:
                return
            for message in incoming:
                with records, ui.column().classes("lab-record") as entry:
                    with ui.row().classes("lab-row"):
                        ui.badge(str(message.get("level", "info")).upper(), color="grey")
                        ui.label(format_timestamp(message.get("created_at"))).classes("lab-muted")
                        ui.label(str(message.get("kind", ""))).classes("lab-muted")
                    ui.label(str(message.get("message", ""))).classes("lab-code")
                widgets[message["sequence"]] = entry
            if direction == "older":
                for index, item in enumerate(feed.items):
                    widgets[item["sequence"]].move(records, index)
                if before:
                    self._restore_scroll(area, before)
            elif follow.value and incoming:
                self._tail(area)
            older.set_enabled(feed.has_older)
            count.set_text(f"{len(feed.items)} / {feed.total}")
            empty.set_visibility(not feed.items)

        async def load_older() -> None:
            follow.set_value(False)
            async with state["scope"].lock:
                await update("older")

        async def filters_changed() -> None:
            async with state["scope"].lock:
                feed.reset(level=level.value or None, search=search.value or "")
                widgets.clear()
                records.clear()
                await update("initial")

        level.on_value_change(filters_changed)
        search.on_value_change(filters_changed)

        def following_changed(event) -> None:
            if event.value:
                intent["at"] = 0
                self._tail(area)

        follow.on_value_change(following_changed)
        self._poll(state, update, error, loading)

    def _local(self, reference: TaskReference, state: dict, error: Any, loading: Any) -> None:
        ui, tr = self.ui, self.tr
        feed = LocalLogFeed(self.controller, reference)
        state["cleanup"].append(feed.scope.close)
        ui.label(tr("Local stdout and stderr are stored separately from platform reporting records.")).classes("lab-muted")
        with ui.row().classes("lab-row"):
            source = ui.select({}, label=tr("Source")).props("dense outlined").classes("lab-grow")
            follow = ui.checkbox(tr("Follow new output"), value=True)
            download = ui.button(
                tr("Download log snapshot"),
                icon="download",
                on_click=lambda: ui.navigate.to(
                    local_log_url(*reference.identity, feed.source or "", purpose=reference.purpose), new_tab=True
                ),
            ).props("flat")
        notice = ui.label().classes("lab-muted")
        intent = {"at": 0.0}

        def on_scroll(event) -> None:
            if (
                time.monotonic() - intent["at"] < 1.5
                and event.vertical_percentage < 0.97
                and event.vertical_size > event.vertical_container_size
            ):
                follow.set_value(False)

        with ui.scroll_area(on_scroll=on_scroll).classes("lab-scroll") as area:
            text = ui.label().classes("lab-code").style("padding:12px 14px")
        self._scroll_intent(area, intent)
        with ui.row().classes("lab-row lab-between"):
            older = ui.button(tr("Load earlier output"), on_click=lambda: run_busy(older, load_older, error)).props("outline")
            size = ui.label().classes("lab-muted")
        content = ""

        async def read(*, history: bool = False) -> None:
            nonlocal content
            before = await state["scope"].query(lambda: self._scroll_snapshot(area)) if history else None
            result = await feed.fetch(older=history)
            if not result or state["scope"].closed:
                return
            chunk = result.get("text", "")
            if result.get("reset"):
                content = ""
                notice.set_text(tr("Log was rotated or truncated; showing the current file."))
            elif not result.get("available", False):
                notice.set_text(tr("Log no longer available"))
            else:
                notice.set_text("")
            if chunk or result.get("reset"):
                content = chunk + content if history else content + chunk
                text.set_text(content)
                if before:
                    self._restore_scroll(area, before)
                elif follow.value:
                    self._tail(area)
            older.set_enabled(bool(feed.first_offset and feed.available))
            download.set_enabled(feed.available)
            size.set_text(f"{feed.size:,} B")

        async def update() -> None:
            sources = await self.controller.list_local_logs(*reference.identity, purpose=reference.purpose)
            options = {
                item["name"]: (f"{item['description']} · {item['name']}" if item.get("description") else item["name"])
                for item in sources
            }
            if feed.source and feed.source not in options:
                options[feed.source] = feed.source
            if source.options != options:
                source.set_options(options)
            if feed.source is None and sources:
                selected = next((item["name"] for item in sources if item.get("available")), sources[0]["name"])
                feed.select(selected)
                source.set_value(selected)
            if not sources:
                notice.set_text(tr("No local logs recorded"))
                download.disable()
                older.disable()
                return
            await read()

        async def selected(event) -> None:
            nonlocal content
            if not event.value or event.value == feed.source:
                return
            async with state["scope"].lock:
                feed.select(event.value)
                content = ""
                text.set_text("")
                await read()

        async def load_older() -> None:
            follow.set_value(False)
            async with state["scope"].lock:
                await read(history=True)

        source.on_value_change(selected)

        def following_changed(event) -> None:
            if event.value:
                intent["at"] = 0
                self._tail(area)

        follow.on_value_change(following_changed)
        self._poll(state, update, error, loading)

    def _files(self, reference: TaskReference, state: dict, error: Any, loading: Any) -> None:
        ui, tr = self.ui, self.tr
        table = ui.table(
            columns=[
                {"name": name, "field": name, "label": tr(label), "align": "left"}
                for name, label in (
                    ("name", "Name"),
                    ("size", "Size"),
                    ("availability", "Available"),
                    ("delivery", "Uploaded"),
                    ("actions", "Actions"),
                )
            ],
            rows=[],
            row_key="name",
        ).classes("lab-table")
        table.props("flat dense wrap-cells hide-bottom")
        table.add_slot(
            "body-cell-name",
            """<q-td :props="props"><div>{{ props.row.name }}</div>
            <div class="lab-muted">{{ props.row.description }}</div></q-td>""",
        )
        table.add_slot(
            "body-cell-actions",
            """<q-td :props="props">
            <q-btn flat round dense icon="visibility" :disable="!props.row.previewable"
            :aria-label="props.row.preview_label" @click="$parent.$emit('preview', props.row.name)">
            <q-tooltip>{{ props.row.preview_label }}</q-tooltip></q-btn>
            <q-btn flat round dense icon="download" :disable="!props.row.available" :href="props.row.url" target="_blank"
            :aria-label="props.row.download_label"><q-tooltip>{{ props.row.download_label }}</q-tooltip></q-btn></q-td>""",
        )
        empty = ui.label(tr("No artifacts recorded")).classes("lab-muted")
        with ui.column().classes("lab-file-preview") as preview:
            with ui.row().classes("lab-row lab-between"):
                selected_name = ui.label().classes("text-weight-medium lab-grow")
                ui.button(tr("Clear preview"), on_click=lambda: preview.set_visibility(False)).props("flat dense")
            note = ui.label().classes("lab-muted")
            preview_text = ui.label().classes("lab-code").style("max-height:42vh;overflow:auto")
        preview.set_visibility(False)
        selection = 0

        async def select_file(event) -> None:
            nonlocal selection
            selection += 1
            generation = selection
            name = str(event.args)
            selected_name.set_text(name)
            preview_text.set_text("")
            preview.set_visibility(True)
            try:
                path = await state["scope"].query(lambda: self.controller.artifact_path(*reference.identity, name))
                if path is None:
                    return
                result = await state["scope"].query(lambda: read_preview(path))
                if result is None or state["scope"].closed or generation != selection:
                    return
                value, truncated = result
                preview_text.set_text(value)
                note.set_text(
                    tr(
                        "Preview limited to the first 2 MiB. Download the original for the complete file."
                        if truncated
                        else "Plain-text preview. Download the original for the complete file."
                    )
                )
            except (OSError, ValueError, LookupError):
                if generation == selection:
                    note.set_text(tr("Preview unavailable"))

        table.on("preview", select_file)
        prior = None

        async def update() -> None:
            nonlocal prior
            detail = await self.controller.get_job(*reference.identity, purpose=reference.purpose)
            artifacts = detail.get("artifacts") or []
            signature = pretty_json(artifacts)
            if signature == prior:
                return
            prior = signature
            table.rows = [
                {
                    **item,
                    "availability": tr("Available" if item.get("available") else "Unavailable"),
                    "delivery": tr("Uploaded" if item.get("uploaded") else "Not uploaded"),
                    "previewable": bool(item.get("available") and can_preview(item["name"], item.get("media_type", ""))),
                    "url": artifact_url(*reference.identity, item["name"]),
                    "preview_label": tr("Preview"),
                    "download_label": tr("Download"),
                }
                for item in artifacts
            ]
            empty.set_visibility(not artifacts)

        self._poll(state, update, error, loading)

    def _detail(self, reference: TaskReference, kind: str, state: dict, error: Any, loading: Any) -> None:
        ui, tr = self.ui, self.tr
        if kind == "metrics":
            table = ui.table(
                columns=[
                    {"name": key, "field": key, "label": tr(label), "align": "left"}
                    for key, label in (("name", "Metric"), ("value", "Value"), ("copy", "Copy exact value"))
                ],
                rows=[],
                row_key="name",
            ).classes("lab-table")
            table.props("flat dense wrap-cells hide-bottom")
            table.add_slot(
                "body-cell-copy",
                """<q-td :props="props"><q-btn flat round dense icon="content_copy"
                :aria-label="props.row.copy_label" @click="$parent.$emit('copy_value', props.row.exact)">
                <q-tooltip>{{ props.row.copy_label }}</q-tooltip></q-btn></q-td>""",
            )
            table.on("copy_value", lambda event: ui.clipboard.write(str(event.args)))
            empty = ui.label(tr("No metrics recorded")).classes("lab-muted")
        elif kind == "diagnostics":
            text = ui.label().classes("lab-code")
        else:
            info = ui.column().classes("lab-kv")
            values: dict[str, Any] = {}
            for label in (
                "Resource",
                "Algorithm",
                "Job",
                "Attempt",
                "Purpose",
                "State",
                "Lifecycle phase",
                "Started",
                "Finished",
                "Duration",
            ):
                with info:
                    ui.label(tr(label)).classes("lab-kv-key")
                    values[label] = ui.label().style("overflow-wrap:anywhere")
        prior = None

        async def update() -> None:
            nonlocal prior
            detail = await self.controller.get_job(*reference.identity, purpose=reference.purpose)
            signature = pretty_json(detail)
            if signature == prior:
                return
            prior = signature
            if kind == "metrics":
                rows = metric_rows(detail)
                table.rows = [{**row, "copy_label": tr("Copy exact value")} for row in rows]
                empty.set_visibility(not rows)
            elif kind == "diagnostics":
                text.set_text(signature)
            else:
                data = {
                    "Resource": detail.get("gateway_name"),
                    "Algorithm": detail.get("algorithm_name"),
                    "Job": reference.job_id,
                    "Attempt": reference.attempt_id,
                    "Purpose": tr(reference.purpose),
                    "State": tr(detail.get("state", "")),
                    "Lifecycle phase": phase_text(detail.get("phase"), tr),
                    "Started": (
                        format_timestamp(detail.get("started_at", detail.get("started")))
                        if detail.get("started_at", detail.get("started")) is not None
                        else tr("Not recorded")
                    ),
                    "Finished": (
                        format_timestamp(detail.get("finished_at"))
                        if detail.get("finished_at") is not None
                        else tr("Not recorded")
                    ),
                    "Duration": duration_text(observed_duration(detail), tr("Not recorded")),
                }
                for label, value in data.items():
                    values[label].set_text(value_text(value, tr("Not recorded")))

        self._poll(state, update, error, loading)
