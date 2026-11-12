"""Universal gateway settings with inline validation and applied/pending diffs."""

from __future__ import annotations

import json
from typing import Any

from .dialogs import run_busy
from .helpers import pretty_json, settings_diff, settings_payload, settings_values
from .presentation import SETTINGS_NOTICE

FIELD_LABELS = {
    "server": "Server URL",
    "name": "Gateway name",
    "allowed_users": "Allowed users",
    "adapter": "Evaluator source",
    "evaluator": "Evaluator import",
    "state_dir": "State directory",
    "concurrency": "Concurrency",
    "cleanup_enabled": "Enable cleanup",
    "cleanup_interval": "Cleanup interval (seconds)",
    "cleanup_keep_seconds": "Keep evidence (seconds)",
    "local_log_keep_seconds": "Local log retention (seconds)",
    "ui_host": "Listen host",
    "ui_port": "Listen port",
    "open_browser": "Open browser",
    "language": "Language",
    "theme": "Theme",
}


async def settings_form(controller: Any, ui: Any, shell: Any, poll: Any) -> None:
    """Render settings without rebuilding form fields during background refreshes."""
    from llm4ad.gateway.settings import ADAPTERS

    tr = shell.tr
    snapshot = await controller.get_snapshot()
    values = settings_values(snapshot.get("pending_settings") or snapshot.get("settings") or controller.settings)
    fields: dict[str, Any] = {}
    with shell.content:
        ui.label(tr(SETTINGS_NOTICE)).classes("lab-muted")
        feedback = ui.label().classes("lab-panel").props('role="status"')
        pending_error = ui.label().classes("lab-inline-error").props('role="alert"')
        refresh_error = ui.label().classes("lab-inline-error").props('role="alert"')
        diff = ui.table(
            columns=[
                {"name": key, "field": key, "label": tr(label), "align": "left"}
                for key, label in (("field", "Setting"), ("applied", "Applied"), ("pending", "Pending"))
            ],
            rows=[],
            row_key="field",
        ).classes("lab-table")
        diff.props("flat dense wrap-cells hide-bottom")
        diff.set_visibility(False)
        with ui.card().classes("lab-panel"):
            ui.label(tr("Connection and evaluator")).props("role=heading aria-level=2").classes("lab-section-title")
            with ui.column().classes("lab-form"):
                for key in ("server", "name", "state_dir"):
                    fields[key] = ui.input(tr(FIELD_LABELS[key]), value=values.get(key) or "").props("outlined dense")
                fields["adapter"] = ui.select(
                    {
                        "custom": tr("Custom evaluator"),
                        "example": tr("Built-in example"),
                        "hgamey": tr("HGamey shader evaluator"),
                    },
                    value=values.get("adapter", "custom"),
                    label=tr("Evaluator source"),
                ).props("outlined dense")
                fields["evaluator"] = ui.input(tr("Evaluator import"), value=values.get("evaluator") or "").props(
                    "outlined dense"
                )
                fields["concurrency"] = ui.number(
                    tr("Concurrency"), value=values.get("concurrency", 1), min=1, step=1, precision=0
                ).props("outlined dense")
                fields["allowed_users"] = (
                    ui.textarea(tr("Allowed users"), value="\n".join(values.get("allowed_users", [])))
                    .props("outlined dense autogrow")
                    .style("grid-column:1/-1")
                )
            ui.label(tr("One user per line, or comma-separated.")).classes("lab-muted")
            builtin_hint = ui.label(tr("Read-only built-in evaluator import.")).classes("lab-muted")
            serialized = ui.label(tr("HGamey runs one evaluation at a time.")).classes("lab-muted")
            previous_source = values.get("adapter", "custom")
            custom_import = (values.get("evaluator") or "") if previous_source == "custom" else ""

            def source_changed() -> None:
                nonlocal previous_source, custom_import
                source = fields["adapter"].value
                if previous_source == "custom" and source != "custom":
                    custom_import = fields["evaluator"].value
                if source == "custom":
                    fields["evaluator"].props(remove="readonly")
                    if previous_source != "custom":
                        fields["evaluator"].set_value(custom_import)
                else:
                    fields["evaluator"].set_value(ADAPTERS[source])
                    fields["evaluator"].props("readonly")
                builtin_hint.set_visibility(source != "custom")
                serialized.set_visibility(source == "hgamey")
                fields["concurrency"].set_enabled(source != "hgamey")
                if source == "hgamey":
                    fields["concurrency"].set_value(1)
                previous_source = source

            source_changed()
            fields["adapter"].on_value_change(source_changed)
        with ui.card().classes("lab-panel"):
            ui.label(tr("Evidence retention")).props("role=heading aria-level=2").classes("lab-section-title")
            fields["cleanup_enabled"] = ui.switch(tr("Enable cleanup"), value=values.get("cleanup_enabled", True))
            with ui.column().classes("lab-form"):
                for key, default in (("cleanup_interval", 30), ("cleanup_keep_seconds", 0)):
                    fields[key] = ui.number(
                        tr(FIELD_LABELS[key]), value=values.get(key, default), min=1 if key == "cleanup_interval" else 0
                    ).props("outlined dense")
        with ui.card().classes("lab-panel"):
            ui.label(tr("Local log retention")).props("role=heading aria-level=2").classes("lab-section-title")
            fields["local_log_keep_seconds"] = (
                ui.number(tr(FIELD_LABELS["local_log_keep_seconds"]), value=values.get("local_log_keep_seconds", 604800), min=0)
                .props("outlined dense")
                .classes("full-width")
            )
        with ui.card().classes("lab-panel"):
            ui.label(tr("Dashboard preferences")).props("role=heading aria-level=2").classes("lab-section-title")
            with ui.column().classes("lab-form"):
                fields["ui_host"] = ui.input(tr("Listen host"), value=values.get("ui_host", "127.0.0.1")).props("outlined dense")
                fields["ui_port"] = ui.number(
                    tr("Listen port"), value=values.get("ui_port", 8768), min=0, max=65535, precision=0
                ).props("outlined dense")
                fields["open_browser"] = ui.select(
                    {"auto": tr("Auto"), "yes": tr("Yes"), "no": tr("No")},
                    value={None: "auto", True: "yes", False: "no"}[values.get("open_browser")],
                    label=tr("Open browser"),
                ).props("outlined dense")
                fields["language"] = ui.select(
                    {"auto": tr("Auto"), "zh": "中文", "en": "English"},
                    value=values.get("language", "auto"),
                    label=tr("Language"),
                ).props("outlined dense")
                fields["theme"] = ui.select(
                    {"auto": tr("Auto"), "light": tr("Light"), "dark": tr("Dark")},
                    value=values.get("theme", "auto"),
                    label=tr("Theme"),
                ).props("outlined dense")
        error = ui.label().classes("lab-inline-error").props('role="alert"')

        def field_error(key: str, text: str) -> None:
            if key in fields:
                fields[key].props("error error-message=" + json.dumps(text))

        async def save() -> None:
            for widget in fields.values():
                widget.props(remove="error error-message")

            async def apply() -> None:
                patch = {key: widget.value for key, widget in fields.items()}
                patch["open_browser"] = {"auto": None, "yes": True, "no": False}[patch["open_browser"]]
                valid = True
                if not str(patch["allowed_users"]).strip():
                    field_error("allowed_users", tr("At least one allowed user is required."))
                    valid = False
                if patch["adapter"] == "custom" and not str(patch["evaluator"]).strip():
                    field_error("evaluator", tr("A custom evaluator import is required."))
                    valid = False
                if not valid:
                    raise ValueError(tr("Please correct the highlighted fields."))
                try:
                    result = await controller.apply_settings(settings_payload(patch))
                except Exception as exception:
                    if callable(getattr(exception, "errors", None)):
                        for item in exception.errors(include_input=False, include_url=False):
                            field_error(str(item["loc"][0]), item["msg"])
                        raise ValueError(tr("Please correct the highlighted fields.")) from None
                    for key in fields:
                        if key in str(exception):
                            field_error(key, str(exception))
                    raise
                feedback.set_text(
                    tr("Settings will apply after active evaluations finish." if result.get("pending") else "Settings applied")
                )
                await render_pending()

            await run_busy(save_button, apply, error)

        async def discard() -> None:
            if await run_busy(discard_button, controller.discard_pending, error):
                ui.navigate.to(shell.url("/settings"))

        with ui.row().classes("lab-row"):
            save_button = ui.button(tr("Apply settings"), on_click=save).props("unelevated")
            discard_button = ui.button(tr("Discard pending"), on_click=discard).props("outline")
        prior = None

        async def render_pending() -> None:
            nonlocal prior
            latest = await controller.get_snapshot()
            signature = pretty_json(
                [
                    latest.get("settings"),
                    latest.get("pending_settings"),
                    latest.get("pending_error"),
                    latest.get("restart_required"),
                ]
            )
            if signature == prior:
                return
            prior = signature
            pending = latest.get("pending_settings")
            discard_button.set_visibility(bool(pending))
            rows = settings_diff(latest.get("settings") or controller.settings, pending or {})
            diff.rows = [{**row, "field": tr(FIELD_LABELS.get(row["field"], row["field"]))} for row in rows]
            diff.set_visibility(bool(rows))
            pending_error.set_text(str(latest.get("pending_error") or ""))
            messages = (
                [tr("Settings will apply after active evaluations finish.")]
                if pending
                else [tr("Applied settings"), tr("No pending changes")]
            )
            if latest.get("restart_required"):
                messages.append(tr("Saved listener settings require a restart."))
            feedback.set_text(" ".join(messages))

        poll(render_pending, refresh_error)
