"""Compact task-first dashboard pages with separate, attempt-fenced dialogs."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from fastapi import Request

from .dialogs import DIALOG_TITLES, TaskDialogs, run_busy
from .forms import settings_form
from .helpers import (
    PAGES,
    STATES,
    duration_text,
    format_timestamp,
    format_transport_error,
    job_identity,
    observed_duration,
    phase_text,
    pretty_json,
    resolve_language,
    short_identifier,
    state_color,
)
from .presentation import CSS, translate
from .views import TaskReference

if TYPE_CHECKING:
    from llm4ad.gateway.sdk.controller import GatewayController


@dataclass
class PageShell:
    """Carry page-local language and theme without saving business configuration."""

    tr: Callable[[str], str]
    content: Any
    preferences: dict[str, str]

    def url(self, path: str, **query: str) -> str:
        """Build an internal link with the operator's current display preferences."""
        return path + "?" + urlencode({**self.preferences, **query})


class DashboardPages:
    """Render browser-local views over the server-owned SDK controller."""

    def __init__(self, controller: GatewayController, ui: Any):
        """Keep NiceGUI injectable and leave service lifetime to the server."""
        self.controller, self.ui = controller, ui

    def register(self) -> None:
        """Register primary pages; legacy detail URLs redirect at the HTTP layer."""
        for path, handler in (("/", self.overview), ("/tasks", self.tasks), ("/settings", self.settings)):
            self.ui.page(path, title="LLM4AD · Gateway", response_timeout=15)(handler)

    def _shell(self, request: Request, title: str, subtitle: str = "") -> PageShell:
        ui = self.ui
        language = resolve_language(
            request.query_params.get("lang", self.controller.settings.language), request.headers.get("accept-language", "")
        )
        theme = request.query_params.get("theme", self.controller.settings.theme)
        theme = theme if theme in {"auto", "light", "dark"} else "auto"
        preferences = {"lang": language, "theme": theme}

        def tr(text: str) -> str:
            return translate(language, text)

        ui.add_css(CSS)
        ui.colors(primary="#277a6c", positive="#39805c", negative="#b64e42", warning="#bf8b32", info="#537c9a")
        dark = ui.dark_mode(None if theme == "auto" else theme == "dark")
        ui.run_javascript(f'document.documentElement.lang="{language}"')
        content = ui.column().classes("lab-shell")
        shell = PageShell(tr, content, preferences)

        def change_theme(event) -> None:
            preferences["theme"] = event.value
            dark.set_value(None if event.value == "auto" else event.value == "dark")
            ui.run_javascript(
                "const u=new URL(window.location.href);u.searchParams.set('theme',"
                + json.dumps(event.value)
                + ");history.replaceState(null,'',u);"
            )

        with ui.header().classes("lab-header"), ui.row().classes("lab-header-inner"):
            with ui.column().style("gap:0"):
                ui.label("LLM4AD").classes("lab-brand")
                ui.label(tr("Gateway console")).classes("lab-eyebrow")
            with ui.row().classes("lab-nav"):
                for path, label in PAGES:
                    ui.button(tr(label), on_click=lambda _, target=path: ui.navigate.to(shell.url(target))).props(
                        "unelevated" if request.url.path == path else "flat"
                    )
            ui.select(
                {"en": "English", "zh": "中文"},
                value=language,
                label=tr("Language"),
                on_change=lambda event: ui.navigate.to(
                    request.url.path
                    + "?"
                    + urlencode(
                        {
                            **dict(request.query_params),
                            **preferences,
                            "lang": event.value,
                        }
                    )
                ),
            ).props("dense outlined").style("width:108px")
            ui.select(
                {"auto": tr("Auto"), "light": tr("Light"), "dark": tr("Dark")},
                value=theme,
                label=tr("Theme"),
                on_change=change_theme,
            ).props("dense outlined").style("width:108px")
            ui.button(
                tr("Sign out"),
                on_click=lambda: ui.run_javascript("fetch('/logout',{method:'POST'}).then(()=>location.assign('/login'))"),
            ).props("flat dense")
        with content, ui.column().style("gap:7px"):
            ui.label(tr("Evaluation gateway")).classes("lab-eyebrow")
            ui.label(tr(title)).props("role=heading aria-level=1").classes("lab-title")
            if subtitle:
                ui.label(tr(subtitle)).classes("lab-muted")
        return shell

    def _poll(self, render: Callable, error_label: Any, tr: Callable | None = None) -> None:
        """Refresh only observation widgets, never an entire page or form."""
        lock = asyncio.Lock()

        async def refresh() -> None:
            if lock.locked():
                return
            async with lock:
                try:
                    await render()
                    error_label.set_text("")
                except Exception:
                    message = "Unable to refresh. Retrying automatically."
                    error_label.set_text(tr(message) if tr else message)

        self.ui.timer(2.0, refresh)

    async def overview(self, request: Request) -> None:
        """Keep resource status and transport observations stable during refresh."""
        ui = self.ui
        shell = self._shell(request, "Overview", "Live service state and evaluation activity.")
        tr = shell.tr
        dialogs = TaskDialogs(self.controller, ui, tr)
        binding_widgets: dict[str, dict] = {}
        with shell.content:
            error = ui.label().classes("lab-inline-error").props('role="alert"')
            with ui.card().classes("lab-panel"):
                with ui.row().classes("lab-row lab-between"):
                    with ui.row().classes("items-center"):
                        ui.label(tr("Gateway status"))
                        status = ui.badge(tr("starting"), color="info")
                        admission = ui.label().classes("lab-muted")

                    async def toggle() -> None:
                        async def action() -> None:
                            snapshot = await self.controller.get_snapshot()
                            await (self.controller.resume() if snapshot.get("paused") else self.controller.pause())
                            await render()

                        await run_busy(pause, action, error)

                    pause = ui.button(tr("Pause intake"), on_click=toggle).props("outline")
                problem = ui.label().classes("lab-inline-error")
                setup = ui.button(tr("Settings"), on_click=lambda: ui.navigate.to(shell.url("/settings"))).props("flat")
                setup.set_visibility(False)
                pending = ui.label().classes("lab-muted")
            counts: dict[str, Any] = {}
            with ui.row().classes("lab-grid"):
                for key, label in (
                    ("pending", "Local queued"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                ):
                    with ui.card().classes("lab-panel"):
                        ui.label(tr(label)).classes("lab-eyebrow")
                        counts[key] = ui.label("—").classes("lab-stat")
            with ui.row().classes("lab-row lab-secondary-stats"):
                for key in ("total", "recovery_required", "cancelled", "cancelling", "pending_upload"):
                    counts[key] = ui.label().classes("lab-muted")
            resources = ui.column().classes("full-width")
            service_values: dict[str, Any] = {}
            with ui.card().classes("lab-panel"):
                with ui.row().classes("lab-row lab-between"):
                    ui.label(tr("Service")).props("role=heading aria-level=2").classes("lab-section-title")

                    async def diagnostics() -> None:
                        data = await self.controller.get_snapshot()
                        with ui.dialog() as dialog, ui.card().classes("lab-dialog"):
                            with ui.row().classes("lab-row lab-between lab-dialog-header"):
                                ui.label(tr("Diagnostics")).props("role=heading aria-level=2").classes("lab-section-title")
                                ui.button(icon="close", on_click=dialog.close).props(
                                    "flat round aria-label=" + json.dumps(tr("Close"))
                                )
                            with ui.column().classes("lab-dialog-body"):
                                ui.label(pretty_json(data)).classes("lab-code")
                        dialog.open()

                    ui.button(tr("Diagnostics"), icon="data_object", on_click=diagnostics).props("flat dense")
                service_grid = ui.column().classes("lab-kv")
            ui.label(tr("Browser tabs do not control the gateway lifetime.")).classes("lab-muted lab-footer")

            async def render() -> None:
                snapshot = await self.controller.get_snapshot()
                current = snapshot.get("status", "starting")
                status.set_text(tr(current))
                status.props("color=" + state_color(current))
                admission.set_text(tr("Paused" if snapshot.get("paused") else "Active"))
                pause.set_text(tr("Resume intake" if snapshot.get("paused") else "Pause intake"))
                problem.set_text(str(snapshot.get("error") or snapshot.get("pending_error") or ""))
                setup.set_visibility(current == "configuration_required")
                pending.set_text(
                    " · ".join(
                        text
                        for text in (
                            tr("Settings will apply after active evaluations finish.")
                            if snapshot.get("pending_settings")
                            else "",
                            tr("Saved listener settings require a restart.") if snapshot.get("restart_required") else "",
                        )
                        if text
                    )
                )
                summary = snapshot.get("summary") or {}
                for key, widget in counts.items():
                    number = str(summary.get(key, "—"))
                    widget.set_text(
                        number
                        if key in {"pending", "running", "success", "failed"}
                        else f"{tr('Total' if key == 'total' else key)}  {number}"
                    )
                service = snapshot.get("service") or {}
                scalar = {key: value for key, value in service.items() if isinstance(value, str | int | float | bool)}
                scalar["Capacity"] = snapshot.get("max_concurrency") or self.controller.settings.concurrency
                scalar["Evidence cleanup"] = tr("Enabled" if self.controller.settings.cleanup_enabled else "Disabled")
                for key, value in scalar.items():
                    if key not in service_values:
                        with service_grid:
                            ui.label(tr(key.replace("_", " ").capitalize())).classes("lab-kv-key")
                            service_values[key] = ui.label()
                    service_values[key].set_text(tr("Yes" if value is True else "No" if value is False else str(value)))
                bindings = snapshot.get("bindings") or []
                for binding in bindings:
                    key = str(binding["key"])
                    if key not in binding_widgets:
                        with resources, ui.card().classes("lab-panel") as panel:
                            with ui.row().classes("lab-row lab-between"):
                                name = ui.label().props("role=heading aria-level=2").classes("lab-section-title lab-grow")
                                ready = ui.badge(color="grey")
                                check = ui.button(
                                    tr("Startup check"),
                                    icon="fact_check",
                                    on_click=lambda _, binding_key=key: dialogs.startup(binding_key),
                                ).props("flat dense")
                            with ui.column().classes("lab-kv"):
                                observations = {}
                                for label in ("Registration", "Heartbeat", "Notifications", "Last contact", "Lifecycle phase"):
                                    ui.label(tr(label)).classes("lab-kv-key")
                                    observations[label] = ui.label()
                        binding_widgets[key] = {"panel": panel, "name": name, "ready": ready, "check": check, **observations}
                    widgets = binding_widgets[key]
                    widgets["panel"].set_visibility(True)
                    widgets["name"].set_text(str(binding.get("name") or self.controller.settings.name))
                    health = service.get("gateways", {}).get(key, {})
                    readiness = binding.get("status", health.get("status", current))
                    widgets["ready"].set_text(tr(readiness))
                    widgets["ready"].props("color=" + state_color(readiness))
                    transport = binding.get("transport") or {}
                    for field, label in (("registration", "Registration"), ("heartbeat", "Heartbeat")):
                        observation = transport.get(field) or {}
                        text = f"{tr('Last success')}: {format_timestamp(observation.get('last_success'))}"
                        if observation.get("last_error"):
                            text += " · " + format_transport_error(observation["last_error"])
                        widgets[label].set_text(text)
                    notifications = transport.get("notifications") or {}
                    notification = (
                        "reconnecting"
                        if notifications.get("reconnecting")
                        else "connected"
                        if notifications.get("connected")
                        else "disconnected"
                    )
                    widgets["Notifications"].set_text(tr(notification) if notifications else tr("Not recorded"))
                    widgets["Last contact"].set_text(format_timestamp(transport.get("last_contact")))
                    startup = binding.get("startup")
                    widgets["check"].set_visibility(bool(startup))
                    widgets["Lifecycle phase"].set_text(phase_text((startup or {}).get("phase"), tr) or tr("Not recorded"))
                active_keys = {str(binding["key"]) for binding in bindings}
                for key, widgets in binding_widgets.items():
                    if key not in active_keys:
                        widgets["panel"].set_visibility(False)

            self._poll(render, error, tr)

    async def tasks(self, request: Request) -> None:
        """Keep task rows primary and route every icon to a separate dialog."""
        ui = self.ui
        shell = self._shell(request, "Tasks", "Recorded evaluations, scoped to each execution attempt.")
        tr = shell.tr
        page = 1

        def dialog_state(reference: TaskReference | None, kind: str | None) -> None:
            query = (
                {}
                if reference is None
                else {
                    "binding": reference.binding,
                    "job_id": reference.job_id,
                    "attempt_id": reference.attempt_id,
                    "dialog": kind or "info",
                }
            )
            ui.run_javascript("history.replaceState(null,''," + json.dumps(shell.url("/tasks", **query)) + ");")

        dialogs = TaskDialogs(self.controller, ui, tr, on_state=dialog_state)
        with shell.content:
            snapshot = await self.controller.get_snapshot()
            names = {
                str(item["key"]): str(item.get("name") or self.controller.settings.name) for item in snapshot.get("bindings", [])
            }
            with ui.row().classes("lab-row"):
                search = ui.input(tr("Search jobs")).props("dense outlined clearable debounce=400").classes("lab-grow")
                state = ui.select({"": tr("All states"), **{key: tr(key) for key in STATES}}, value="").props("dense outlined")
                resource = ui.select({"": tr("All resources"), **names}, value="").props("dense outlined")
            error = ui.label().classes("lab-inline-error").props('role="alert"')
            table = ui.table(
                columns=[
                    {
                        "name": key,
                        "field": key,
                        "label": tr(label),
                        "align": "left",
                        **(
                            {"classes": f"lab-{key}-cell", "headerClasses": f"lab-{key}-cell"}
                            if key in {"actions", "resource", "started", "duration", "state"}
                            else {}
                        ),
                    }
                    for key, label in (
                        ("algorithm_name", "Algorithm"),
                        ("resource", "Resource"),
                        ("started", "Started"),
                        ("duration", "Duration"),
                        ("state", "State"),
                        ("actions", "Actions"),
                    )
                ],
                rows=[],
                row_key="identity",
                pagination={"rowsPerPage": 0},
            ).classes("lab-table lab-task-table")
            table.props("flat dense hide-bottom wrap-cells")
            table.add_slot(
                "body-cell-algorithm_name",
                """<q-td :props="props" class="lab-algorithm">
                <div class="text-weight-medium">{{ props.row.algorithm_name }}</div>
                <div class="lab-muted" :title="props.row.job_id + ' / ' + props.row.attempt_id">
                {{ props.row.job_short }} · {{ props.row.attempt_short }}</div></q-td>""",
            )
            table.add_slot(
                "body-cell-started",
                """<q-td :props="props" class="lab-started-cell">{{ props.row.started_epoch !== null
                ? new Date(props.row.started_epoch * 1000).toLocaleString(props.row.locale,
                {dateStyle:'short',timeStyle:'medium'}) : props.row.started }}</q-td>""",
            )
            table.add_slot(
                "body-cell-state",
                """<q-td :props="props" class="lab-state-cell"><q-badge :color="props.row.state_color">
                {{ props.row.state_label }}</q-badge><span class="lab-phase"
                :title="props.row.phase_label">{{ props.row.phase_label }}</span></q-td>""",
            )
            table.add_slot("body-cell-actions", ACTIONS_SLOT)
            empty = ui.label(tr("No evaluations match these filters.")).classes("lab-panel lab-muted")
            empty.set_visibility(False)
            ui.label(tr("Times follow your browser's time zone.")).classes("lab-muted")
            with ui.row().classes("lab-row lab-between"):
                previous = ui.button(tr("Previous"), on_click=lambda: change_page(-1)).props("outline")
                pagination = ui.label().classes("lab-muted")
                following = ui.button(tr("Next"), on_click=lambda: change_page(1)).props("outline")

            async def open_action(event) -> None:
                kind, row = event.args["kind"], event.args["row"]
                reference = TaskReference.from_row(row)
                if kind == "cancel":
                    await dialogs.cancel(reference)
                elif kind == "copy":
                    ui.clipboard.write("\n".join(reference.identity))
                    ui.notify(tr("IDs copied"))
                else:
                    await dialogs.open(reference, kind, row)

            table.on("action", open_action)
            prior = None

            async def render() -> None:
                nonlocal prior
                result = await self.controller.list_jobs(
                    page=page, page_size=20, state=state.value or None, search=search.value or "", binding=resource.value or None
                )
                signature = pretty_json(result)
                if signature != prior:
                    rows = []
                    for item in result.get("items", []):
                        binding, job, attempt = job_identity(item)
                        started = item.get("started_at", item.get("started"))
                        rows.append(
                            {
                                **item,
                                "binding": binding,
                                "job_id": job,
                                "attempt_id": attempt,
                                "identity": pretty_json([binding, job, attempt]),
                                "algorithm_name": item.get("algorithm_name") or f"{tr('Evaluation')} #{job[:8]}",
                                "resource": item.get("gateway_name") or names.get(binding) or f"{tr('Evaluation')} #{job[:8]}",
                                "job_short": short_identifier(job),
                                "attempt_short": short_identifier(attempt),
                                "state_label": tr(item.get("state", "")),
                                "state_color": state_color(item.get("state")),
                                "phase_label": phase_text(item.get("phase"), tr),
                                "started_epoch": started if isinstance(started, int | float) and math.isfinite(started) else None,
                                "started": format_timestamp(started) if started is not None else tr("Not recorded"),
                                "duration": duration_text(observed_duration(item), tr("Not recorded")),
                                "locale": "zh-CN" if shell.preferences["lang"] == "zh" else "en",
                                "labels": {
                                    **{key: tr(value) for key, value in DIALOG_TITLES.items()},
                                    "more": tr("More actions"),
                                    "copy": tr("Copy IDs"),
                                    "cancel": tr("Cancel evaluation"),
                                },
                            }
                        )
                    table.rows = rows
                    prior = signature
                total = int(result.get("total", 0))
                pagination.set_text(f"{tr('Page')} {page} / {max(1, (total + 19) // 20)} · {tr('Total')} {total}")
                previous.set_enabled(page > 1)
                following.set_enabled(page * 20 < total)
                empty.set_visibility(not result.get("items"))

            async def change_page(delta: int) -> None:
                nonlocal page
                page = max(1, page + delta)
                await render()

            async def change_filter() -> None:
                nonlocal page
                page = 1
                await render()

            search.on_value_change(change_filter)
            state.on_value_change(change_filter)
            resource.on_value_change(change_filter)
            self._poll(render, error, tr)
            query = request.query_params
            if all(query.get(key) for key in ("binding", "job_id", "attempt_id")):
                reference = TaskReference(query["binding"], query["job_id"], query["attempt_id"])
                kind = query.get("dialog", "info")
                ui.timer(0.1, lambda: dialogs.open(reference, kind if kind in DIALOG_TITLES else "info"), once=True)

    async def settings(self, request: Request) -> None:
        """Edit universal settings with explicit deferred-change feedback."""
        shell = self._shell(request, "Settings", "Universal gateway settings.")
        await settings_form(self.controller, self.ui, shell, lambda render, error: self._poll(render, error, shell.tr))


ACTIONS_SLOT = """<q-td :props="props" class="lab-actions-cell">
    <q-btn v-for="action in [{kind:'records',icon:'receipt_long'},{kind:'local',icon:'terminal'},
      {kind:'metrics',icon:'analytics'},{kind:'files',icon:'folder_open'}]" :key="action.kind"
      flat round dense :icon="action.icon" :aria-label="props.row.labels[action.kind]"
      :class="['metrics','files'].includes(action.kind) ? 'lab-desktop-action' : ''"
      @click="$parent.$emit('action',{kind:action.kind,row:props.row})">
      <q-tooltip>{{ props.row.labels[action.kind] }}</q-tooltip></q-btn>
    <q-btn flat round dense icon="more_vert" :aria-label="props.row.labels.more">
      <q-tooltip>{{ props.row.labels.more }}</q-tooltip><q-menu><q-list style="min-width:200px">
        <q-item v-for="kind in ['metrics','files','info','diagnostics','copy','cancel']" :key="kind"
          clickable v-close-popup :disable="kind==='cancel' && !props.row.can_cancel"
          :class="['metrics','files'].includes(kind) ? 'lab-mobile-action' : ''"
          @click="$parent.$emit('action',{kind:kind,row:props.row})">
          <q-item-section>{{ props.row.labels[kind] }}</q-item-section></q-item>
      </q-list></q-menu></q-btn></q-td>"""
