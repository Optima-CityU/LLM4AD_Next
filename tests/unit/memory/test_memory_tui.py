"""Headless Textual tests for the memory management TUI."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.widgets import Static, TextArea

from llm4ad.memory import tui


class _ReadyClient:
    timeout = 60

    def list_memories(self, **_kwargs):
        return {"data": {"memories": [], "total": 0}}

    def add_memory_stream(self, **_kwargs):
        yield {
            "event": "progress",
            "stage": "extracting",
            "message": "Extracting memory card",
            "percent": 40,
        }
        yield {"event": "completed", "data": {"memories": []}}

    def fetch_cards_by_ids(self, **_kwargs):
        return []


@pytest.mark.asyncio
async def test_refresh_error_surfaces_in_status_bar(monkeypatch) -> None:
    """A failing list call reports the error clearly in the status bar."""

    class _FailingClient(_ReadyClient):
        def list_memories(self, **_kwargs):
            raise RuntimeError("Not Found")

    monkeypatch.setattr(tui.memory_config, "is_connection_configured", lambda: True)
    monkeypatch.setattr(tui.memory_config, "is_binding_configured", lambda: True)
    monkeypatch.setattr(tui.MemoryBrowser, "_build_client", lambda _self: _FailingClient())
    app = tui.MemoryBrowser("global")

    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.pause(0.3)
        assert "Not Found" in str(app.query_one("#status-bar", Static).renderable)


@pytest.mark.asyncio
async def test_new_memory_modal_streams_then_previews_in_place(monkeypatch) -> None:
    """The new-memory dialog stays open through extraction into the preview."""
    client = _ReadyClient()
    monkeypatch.setattr(tui.memory_config, "is_connection_configured", lambda: True)
    monkeypatch.setattr(tui.memory_config, "is_binding_configured", lambda: True)
    monkeypatch.setattr(tui.MemoryBrowser, "_build_client", lambda _self: client)
    app = tui.MemoryBrowser("global")

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.client = client
        app.action_new()
        await pilot.pause(0.1)
        modal = app.screen
        modal.query_one("#n-content", TextArea).text = (
            "Use a two-opt local search after greedy initialization."
        )
        await pilot.press("ctrl+s")
        await pilot.pause(0.3)

        # The dialog stays open and advances past the draft stage (the stream
        # completes with no cards, so it lands in the preview stage in place).
        assert app.screen is modal
        assert modal.mode in ("extracting", "preview")
