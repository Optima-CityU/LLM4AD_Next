"""Interactive TUI for memory management using Textual.

Bilingual (Chinese/English) terminal UI for browsing and managing LLM4AD
memories. Keyboard-driven (no buttons): every action has a shortcut, modals
submit with ``ctrl+s`` and cancel with ``esc``. Styling is intentionally plain
(borders for structure, no background fills) for readability across terminals.
"""

from __future__ import annotations

import contextlib
import uuid
from threading import Event
from typing import Any

try:
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import (
        Checkbox,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        Select,
        Static,
        TextArea,
    )

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

from rich.console import Console

from llm4ad.memory import config as memory_config
from llm4ad.memory import mindmemos_client
from llm4ad.memory.i18n import MEMORY_TYPE_KEYS, Translator

console = Console()

MEMORY_TYPES = list(MEMORY_TYPE_KEYS.keys())

def _card_enabled(memory: dict[str, Any]) -> bool:
    """Enabled = status active and metadata.enabled not False (backend rule)."""
    metadata = memory.get("metadata", {}) or {}
    status = str(memory.get("status") or metadata.get("status") or "active")
    return status == "active" and metadata.get("enabled", True) is not False


def _card_type(memory: dict[str, Any]) -> str:
    """Extract the LLM4AD memory type.

    The card type lives in ``property_name`` (good_algorithm / error_reflection
    / domain_knowledge / general_insight); ``memory_type`` holds MindMemOS
    internal kinds (fact/episodic) which we ignore. Falls back to general_insight.
    """
    metadata = memory.get("metadata", {}) or {}
    for candidate in (
        memory.get("property_name"),
        metadata.get("property_name"),
        metadata.get("memory_type"),
    ):
        value = str(candidate or "").strip()
        if value in MEMORY_TYPES:
            return value
    return "general_insight"


def _card_title(memory: dict[str, Any]) -> str:
    """Extract the memory title."""
    metadata = memory.get("metadata", {}) or {}
    return str(metadata.get("title") or memory.get("title") or "")


def _card_tags(memory: dict[str, Any]) -> list[str]:
    """Extract tags as a list of strings."""
    metadata = memory.get("metadata", {}) or {}
    tags = metadata.get("tags") or memory.get("tags") or []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


class EditModal(ModalScreen):
    """Edit a memory. ctrl+s saves (dismisses with a draft), esc cancels."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save/保存"),
        Binding("escape", "cancel", "Cancel/取消"),
    ]

    def __init__(self, memory: dict[str, Any], tr: Translator):
        """Store the memory to edit and a translator."""
        super().__init__()
        self.memory = memory
        self.tr = tr

    def compose(self) -> ComposeResult:
        """Build the edit form."""
        tr = self.tr
        with VerticalScroll(id="dialog"):
            yield Label(
                f"{tr.t('edit_title')}  "
                f"[dim]ctrl+s {tr.t('key_save')} · esc {tr.t('key_cancel')}[/dim]"
            )
            yield Label(tr.t("edit_field_title"))
            yield Input(value=_card_title(self.memory), id="f-title")
            yield Label(tr.t("edit_field_type"))
            current = _card_type(self.memory)
            if current not in MEMORY_TYPES:
                current = "general_insight"
            yield Select(
                [(tr.memory_type(t), t) for t in MEMORY_TYPES],
                value=current,
                id="f-type",
                allow_blank=False,
            )
            yield Label(tr.t("edit_field_content"))
            yield TextArea(
                text=str(self.memory.get("memory", "") or self.memory.get("content", "")),
                id="f-content",
            )
            yield Label(tr.t("edit_field_tags"))
            yield Input(value=", ".join(_card_tags(self.memory)), id="f-tags")

    def action_save(self) -> None:
        """Dismiss with the edited draft (enabled state preserved)."""
        tags_raw = self.query_one("#f-tags", Input).value
        self.dismiss(
            {
                "memory_id": self.memory.get("id", ""),
                "title": self.query_one("#f-title", Input).value.strip(),
                "type": self.query_one("#f-type", Select).value,
                "content": self.query_one("#f-content", TextArea).text.strip(),
                "tags": [t.strip() for t in tags_raw.split(",") if t.strip()],
                "enabled": _card_enabled(self.memory),
            }
        )

    def action_cancel(self) -> None:
        """Close without saving."""
        self.dismiss(None)


class SearchModal(ModalScreen):
    """Search input. Enter/ctrl+s submits (dismiss with query), esc cancels."""

    BINDINGS = [
        Binding("ctrl+s", "submit", "Search/搜索"),
        Binding("escape", "cancel", "Cancel/取消"),
    ]

    def __init__(self, tr: Translator):
        """Store a translator."""
        super().__init__()
        self.tr = tr

    def compose(self) -> ComposeResult:
        """Build the search form."""
        with VerticalScroll(id="dialog-sm"):
            yield Label(
                f"{self.tr.t('search_title')}  "
                f"[dim]enter {self.tr.t('key_search')} · esc {self.tr.t('key_cancel')}[/dim]"
            )
            yield Input(placeholder=self.tr.t("search_placeholder"), id="f-query")

    @on(Input.Submitted, "#f-query")
    def action_submit(self) -> None:
        """Dismiss with the query (or None if empty)."""
        self.dismiss(self.query_one("#f-query", Input).value.strip() or None)

    def action_cancel(self) -> None:
        """Close without searching."""
        self.dismiss(None)


class ConfigModal(ModalScreen):
    """Edit connection + provider config. ctrl+s saves, esc cancels.

    On save, writes settings.yaml and dismisses with the embedding-lock note.
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save/保存"),
        Binding("escape", "cancel", "Cancel/取消"),
    ]

    def __init__(self, tr: Translator):
        """Store a translator."""
        super().__init__()
        self.tr = tr

    def compose(self) -> ComposeResult:
        """Build the editable config form."""
        tr = self.tr
        cfg = memory_config.get_config()
        p = memory_config.get_providers()
        chat, embed, rerank = p["chat"], p["embedding"], p["rerank"]
        with VerticalScroll(id="dialog"):
            yield Label(
                f"{tr.t('config_title')}  "
                f"[dim]ctrl+s {tr.t('key_save')} · esc {tr.t('key_cancel')}[/dim]"
            )
            yield Label(tr.t("config_connection"))
            yield Label(tr.t("config_base_url"))
            yield Input(value=cfg.get("base_url", ""), id="c-url")
            yield Label(tr.t("config_jwt_secret"))
            yield Input(value=cfg.get("jwt_secret", ""), password=True, id="c-secret")
            yield Label(tr.t("config_chat"))
            yield Label(tr.t("config_base_url"))
            yield Input(value=chat.get("base_url", ""), id="c-chat-url")
            yield Label("API Key")
            yield Input(value=chat.get("api_key", ""), password=True, id="c-chat-key")
            yield Label("Model")
            yield Input(value=chat.get("model", ""), id="c-chat-model")
            yield Label(tr.t("config_embedding"))
            yield Label(f"[dim]{tr.t('config_embedding_locked')}[/dim]")
            yield Label(tr.t("config_base_url"))
            yield Input(value=embed.get("base_url", ""), id="c-emb-url")
            yield Label("API Key")
            yield Input(value=embed.get("api_key", ""), password=True, id="c-emb-key")
            yield Label("Model")
            yield Input(value=embed.get("model", ""), id="c-emb-model")
            yield Label(tr.t("config_embedding_dim"))
            yield Input(value=str(embed.get("dimensions", 1536)), id="c-emb-dim")
            yield Label(f"{tr.t('config_rerank')}  [dim]{tr.t('config_optional')}[/dim]")
            yield Label(tr.t("config_base_url"))
            yield Input(value=rerank.get("base_url", ""), id="c-rr-url")
            yield Label("API Key")
            yield Input(value=rerank.get("api_key", ""), password=True, id="c-rr-key")
            yield Label("Model")
            yield Input(value=rerank.get("model", ""), id="c-rr-model")

    def _v(self, sel: str) -> str:
        return self.query_one(sel, Input).value.strip()

    def action_save(self) -> None:
        """Persist to settings.yaml; enforce the embedding lock; dismiss."""
        try:
            dimensions = int(self._v("#c-emb-dim") or 1536)
        except ValueError:
            dimensions = 1536
        emb_model = self._v("#c-emb-model")
        lock = memory_config.get_embedding_lock()
        overridden = False
        if lock is not None:
            if str(lock["model"]) != emb_model or int(lock["dimensions"]) != dimensions:
                overridden = True
            emb_model = str(lock["model"])
            dimensions = int(lock["dimensions"])
        memory_config.save_settings(
            {
                "mindmemos": {"base_url": self._v("#c-url"), "jwt_secret": self._v("#c-secret")},
                "providers": {
                    "chat": {
                        "base_url": self._v("#c-chat-url"),
                        "api_key": self._v("#c-chat-key"),
                        "model": self._v("#c-chat-model"),
                    },
                    "embedding": {
                        "base_url": self._v("#c-emb-url"),
                        "api_key": self._v("#c-emb-key"),
                        "model": emb_model,
                        "dimensions": dimensions,
                    },
                    "rerank": {
                        "base_url": self._v("#c-rr-url"),
                        "api_key": self._v("#c-rr-key"),
                        "model": self._v("#c-rr-model"),
                    },
                },
            }
        )
        self.dismiss({"embedding_overridden": overridden})

    def action_cancel(self) -> None:
        """Close without saving."""
        self.dismiss(None)


class NewMemoryModal(ModalScreen):
    """Insert new memory in one dialog: draft -> extracting -> preview.

    ctrl+s advances (extract, then confirm); esc cancels/discards. The app
    drives extraction via workers and updates this modal through call_from_thread.
    """

    BINDINGS = [
        Binding("ctrl+s", "advance", "Next/继续"),
        Binding("escape", "cancel", "Cancel/取消"),
    ]

    def __init__(self, tr: Translator):
        """Store a translator; start in draft mode."""
        super().__init__()
        self.tr = tr
        self.mode = "draft"
        self.cards: list[dict[str, Any]] = []
        self._events: list[str] = []

    def compose(self) -> ComposeResult:
        """Build the draft form; progress/preview areas start hidden."""
        tr = self.tr
        with VerticalScroll(id="dialog"):
            yield Label(
                f"{tr.t('new_title')}  "
                f"[dim]ctrl+s {tr.t('key_extract')} · esc {tr.t('key_cancel')}[/dim]",
                id="n-header",
            )
            yield Label(tr.t("new_field_content"), id="n-content-label")
            yield TextArea(text="", id="n-content")
            yield Label(tr.t("new_field_language"), id="n-lang-label")
            yield Input(value="auto", id="n-language")
            yield Static("", id="n-progress", classes="hidden")
            with VerticalScroll(id="n-preview", classes="hidden"):
                yield Label("")

    def action_advance(self) -> None:
        """Draft: start extraction. Preview: confirm selection."""
        if self.mode == "draft":
            content = self.query_one("#n-content", TextArea).text.strip()
            language = self.query_one("#n-language", Input).value.strip() or "auto"
            if content:
                self.app.start_extraction(self, content, language)
        elif self.mode == "preview":
            self.dismiss(self._selection())

    def begin_extraction(self) -> None:
        """Switch to extracting mode: hide form, show progress."""
        self.mode = "extracting"
        for wid in ("#n-content-label", "#n-content", "#n-lang-label", "#n-language"):
            self.query_one(wid).add_class("hidden")
        self.query_one("#n-progress", Static).remove_class("hidden")

    def update_progress(self, message: str, percent: Any = None) -> None:
        """Render the latest stage and a small rolling log."""
        head = f"{message}{f' {percent}%' if percent is not None else ''}"
        self._events = [*self._events[-4:], message]
        log = "\n".join(f"[dim]· {e}[/dim]" for e in self._events)
        self.query_one("#n-progress", Static).update(f"{head}\n{log}")

    def show_error(self, error: str) -> None:
        """Show an error inline, keep the dialog open."""
        self.mode = "error"
        self.query_one("#n-progress", Static).update(f"[red]{error}[/red]")

    def show_preview(self, cards: list[dict[str, Any]]) -> None:
        """Switch to preview: list extracted cards as checkboxes (all kept)."""
        self.mode = "preview"
        self.cards = cards
        self.query_one("#n-progress", Static).add_class("hidden")
        self.query_one("#n-header", Label).update(
            f"{self.tr.t('new_title')}  [dim]{self.tr.t('preview_hint')}[/dim]"
        )
        box = self.query_one("#n-preview", VerticalScroll)
        box.remove_class("hidden")
        box.remove_children()
        if not cards:
            box.mount(Label(f"[yellow]{self.tr.t('preview_empty')}[/yellow]"))
            return
        for i, card in enumerate(cards):
            mem_type = self.tr.memory_type(_card_type(card))
            text = str(card.get("memory", "") or card.get("content", "")).replace("\n", " ")
            box.mount(Checkbox(f"[{mem_type}] {text[:80]}", value=True, id=f"keep-{i}"))

    def _selection(self) -> dict[str, list[str]]:
        keep, discard = [], []
        for i, card in enumerate(self.cards):
            mid = str(card.get("id") or card.get("memory_id") or "")
            if not mid:
                continue
            (keep if self.query_one(f"#keep-{i}", Checkbox).value else discard).append(mid)
        return {"keep_ids": keep, "discard_ids": discard}

    def action_cancel(self) -> None:
        """Cancel extraction / discard preview / close."""
        if self.mode == "extracting":
            self.app.cancel_extraction()
            self.dismiss(None)
        elif self.mode == "preview":
            all_ids = [
                str(c.get("id") or c.get("memory_id") or "")
                for c in self.cards
                if c.get("id") or c.get("memory_id")
            ]
            self.dismiss({"keep_ids": [], "discard_ids": all_ids})
        else:
            self.dismiss(None)


class MemoryBrowser(App):
    """Keyboard-driven memory browser with plain, borders-only styling."""

    CSS = """
    Screen { background: $background; color: $foreground; }

    ModalScreen { align: center middle; }

    #dialog {
        width: 80%;
        max-width: 96;
        height: 88%;
        padding: 1 2;
        border: round $primary;
        background: $background;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
    }
    #dialog-sm {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $background;
    }
    #dialog Label, #dialog-sm Label { margin-top: 1; }

    Input, TextArea, Select { border: tall $panel; background: $background; }
    Input:focus, TextArea:focus, Select:focus { border: tall $primary; }
    #n-content, #f-content { height: 8; }

    #n-progress { margin-top: 1; }
    #n-preview { height: auto; margin-top: 1; }
    Checkbox { border: none; background: $background; }

    #memory-table { height: 1fr; }

    #status-bar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        color: $foreground;
    }

    #guidance {
        height: 1fr;
        padding: 2 4;
        content-align: center middle;
        text-align: center;
    }

    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit/退出"),
        Binding("r", "refresh", "Refresh/刷新"),
        Binding("n", "new", "New/新增"),
        Binding("space", "toggle", "Toggle/启用"),
        Binding("d", "delete", "Delete/删除"),
        Binding("slash", "search", "Search/搜索", key_display="/"),
        Binding("c", "config", "Config/配置"),
        Binding("l", "lang", "Lang/语言"),
    ]

    def __init__(self, scope: str, task_id: str | None = None, lang: str = "zh"):
        """Build lazily; the client is created from settings on mount."""
        super().__init__()
        self.scope = scope
        self.task_id = task_id
        self.tr = Translator(lang)
        self.client: mindmemos_client.MindMemOSClient | None = None
        self.memories: list[dict[str, Any]] = []
        self.total = 0
        self._search_query: str | None = None
        self._extract_cancel: Event | None = None

    def compose(self) -> ComposeResult:
        """Build the main layout."""
        yield Header(show_clock=False)
        yield Static("", id="guidance", classes="hidden")
        yield DataTable(id="memory-table", cursor_type="row")
        yield Static("", id="status-bar")
        yield Footer()

    def _build_client(self) -> mindmemos_client.MindMemOSClient | None:
        """Build a client from settings, or None if connection unconfigured."""
        if not memory_config.is_connection_configured():
            return None
        cfg = memory_config.get_config()
        return mindmemos_client.MindMemOSClient(
            base_url=cfg["base_url"],
            jwt_secret=cfg["jwt_secret"],
            jwt_issuer=cfg["jwt_issuer"],
            jwt_audience=cfg["jwt_audience"],
            timeout=cfg["timeout"],
        )

    def on_mount(self) -> None:
        """Set up the table, then show list or guidance."""
        self.title = self.tr.t("app_title")
        self._rebuild_columns()
        self.client = self._build_client()
        if self.client is None or not memory_config.is_binding_configured():
            self._show_guidance()
        else:
            self.action_refresh()

    def _rebuild_columns(self) -> None:
        """(Re)create table columns with current-language labels."""
        table = self.query_one("#memory-table", DataTable)
        table.clear(columns=True)
        tr = self.tr
        table.add_column(tr.t("col_enabled"), width=10)
        table.add_column(tr.t("col_type"), width=14)
        table.add_column(tr.t("col_title"), width=22)
        table.add_column(tr.t("col_content"))
        table.add_column(tr.t("col_tags"), width=18)

    def _show_guidance(self) -> None:
        """Show the unconfigured guidance panel; hide the table."""
        tr = self.tr
        self.query_one("#memory-table", DataTable).add_class("hidden")
        g = self.query_one("#guidance", Static)
        g.remove_class("hidden")
        g.update(
            f"[b yellow]{tr.t('guide_not_enabled')}[/b yellow]\n\n"
            f"{tr.t('guide_prompt')}\n\n"
            f"[dim]c {tr.t('key_config')}   ·   q {tr.t('key_quit')}[/dim]"
        )
        self._set_status(f"[yellow]{tr.t('guide_not_enabled')}[/yellow]")

    def _show_list(self) -> None:
        """Show the table; hide the guidance panel."""
        self.query_one("#guidance", Static).add_class("hidden")
        self.query_one("#memory-table", DataTable).remove_class("hidden")

    def _set_status(self, text: str) -> None:
        """Set a raw status-bar message.

        The status bar lives on the base screen, so query there directly —
        ``self.query_one`` would search the active modal (which has none) and
        raise while a dialog is open.
        """
        base = self.screen_stack[0]
        with contextlib.suppress(Exception):
            base.query_one("#status-bar", Static).update(text)

    def _status_summary(self) -> None:
        """Set the default list/search summary in the status bar."""
        tr = self.tr
        if self._search_query:
            self._set_status(
                tr.t("status_search", query=self._search_query, count=len(self.memories))
            )
        else:
            self._set_status(
                f"{tr.t('status_showing', shown=len(self.memories), total=self.total)}"
                f"   |   {tr.t('status_scope', scope=self.scope)}"
            )

    def _populate_table(self) -> None:
        """Fill the table from self.memories."""
        tr = self.tr
        table = self.query_one("#memory-table", DataTable)
        table.clear()
        type_color = {
            "good_algorithm": "#9ece6a",
            "error_reflection": "#f7768e",
            "domain_knowledge": "#7aa2f7",
            "general_insight": "#bb9af7",
        }
        for memory in self.memories:
            enabled = _card_enabled(memory)
            state = (
                f"[#9ece6a]● {tr.t('state_enabled')}[/]"
                if enabled
                else f"[#8087a2]○ {tr.t('state_disabled')}[/]"
            )
            tkey = _card_type(memory)
            mem_type = f"[{type_color.get(tkey, '#a9b1d6')}]{tr.memory_type(tkey)}[/]"
            title = _card_title(memory) or "-"
            content = str(memory.get("memory", "") or memory.get("content", "")).replace("\n", " ")
            tags = ", ".join(_card_tags(memory)) or "-"
            table.add_row(state, mem_type, title[:22], content[:80], f"[#8087a2]{tags[:18]}[/]")

    def _selected(self) -> dict[str, Any] | None:
        table = self.query_one("#memory-table", DataTable)
        if 0 <= table.cursor_row < len(self.memories):
            return self.memories[table.cursor_row]
        return None

    def _require_ready(self) -> bool:
        """True when memory is usable; otherwise guide the user."""
        if self.client is None or not memory_config.is_binding_configured():
            self._set_status(f"[yellow]{self.tr.t('guide_not_enabled')}[/yellow]")
            return False
        return True

    def action_refresh(self) -> None:
        """Reload the memory list from the service (off the UI thread)."""
        if self.client is None:
            self._show_guidance()
            return
        self._show_list()
        self._search_query = None
        self._set_status(self.tr.t("msg_loading"))
        self._refresh_worker()

    @work(thread=True, exclusive=True, group="refresh")
    def _refresh_worker(self) -> None:
        """Fetch the memory list in a thread; report errors clearly."""
        try:
            result = self.client.list_memories(
                scope=self.scope, task_id=self.task_id, page=1, page_size=self.total or 50
            )
            data = result.get("data", {})
            memories = data.get("memories", [])
            total = data.get("total", len(memories))
            self.call_from_thread(self._apply_list, memories, total)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self._set_status, f"[red]{self.tr.t('msg_error', error=str(e))}[/red]"
            )

    def _apply_list(self, memories: list[dict[str, Any]], total: int) -> None:
        """Render a freshly-fetched memory list on the UI thread."""
        self.memories = memories
        self.total = total
        self._populate_table()
        self._status_summary()

    # --- edit -------------------------------------------------------------
    def action_edit(self) -> None:
        """Open the edit dialog for the highlighted row."""
        if not self._require_ready():
            return
        memory = self._selected()
        if memory:
            self.push_screen(EditModal(memory, self.tr), self._on_edit)

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_edit()

    def _on_edit(self, draft: dict[str, Any] | None) -> None:
        if not draft:
            return
        try:
            self.client.update_memory(
                scope=self.scope,
                task_id=self.task_id,
                memory_id=draft["memory_id"],
                content=draft["content"],
                status="active" if draft["enabled"] else "archived",
                metadata_patch={
                    "title": draft["title"],
                    "memory_type": draft["type"],
                    "tags": draft["tags"],
                    "enabled": draft["enabled"],
                },
            )
            self._set_status(self.tr.t("msg_saved"))
            self.action_refresh()
        except Exception as e:  # noqa: BLE001
            self._set_status(self.tr.t("msg_error", error=str(e)))

    # --- toggle / delete --------------------------------------------------
    def action_toggle(self) -> None:
        """Enable/disable the highlighted memory."""
        if not self._require_ready():
            return
        memory = self._selected()
        if not memory:
            return
        mid = memory.get("id", "")
        new_enabled = not _card_enabled(memory)
        try:
            self.client.update_memory(
                scope=self.scope,
                task_id=self.task_id,
                memory_id=mid,
                status="active" if new_enabled else "archived",
                metadata_patch={"enabled": new_enabled},
            )
            self._set_status(
                self.tr.t("msg_enabled" if new_enabled else "msg_disabled", id=mid[:16])
            )
            self.action_refresh()
        except Exception as e:  # noqa: BLE001
            self._set_status(self.tr.t("msg_error", error=str(e)))

    def action_delete(self) -> None:
        """Delete the highlighted memory."""
        if not self._require_ready():
            return
        memory = self._selected()
        if not memory:
            return
        mid = memory.get("id", "")
        try:
            self.client.delete_memory(memory_id=mid)
            self._set_status(self.tr.t("msg_deleted", id=mid[:16]))
            self.action_refresh()
        except Exception as e:  # noqa: BLE001
            self._set_status(self.tr.t("msg_error", error=str(e)))

    # --- new memory (streaming extraction) --------------------------------
    def action_new(self) -> None:
        """Open the new-memory dialog."""
        if not self._require_ready():
            return
        self.push_screen(NewMemoryModal(self.tr), self._on_new)

    def start_extraction(self, modal: NewMemoryModal, content: str, language: str) -> None:
        """Begin streaming extraction (called by the modal)."""
        if self.client is None:
            return
        self._extract_cancel = Event()
        modal.begin_extraction()
        self._set_status(self.tr.t("msg_extracting"))
        lang = language if language in ("ZH", "EN") else None
        self._extract_worker(content, lang, self._extract_cancel, modal)

    def cancel_extraction(self) -> None:
        """Stop an in-flight extraction (called by the modal)."""
        if self._extract_cancel is not None:
            self._extract_cancel.set()
        self._set_status(self.tr.t("msg_extract_cancelled"))

    def _on_new(self, result: dict[str, Any] | None) -> None:
        """Commit the keep/discard decision from the new-memory dialog."""
        if not result:
            return
        self._commit_worker(result.get("keep_ids", []), result.get("discard_ids", []))

    @work(thread=True, exclusive=True, group="extract")
    def _extract_worker(
        self, content: str, language: str | None, cancel: Event, modal: NewMemoryModal
    ) -> None:
        """Stream extraction off the UI thread; show progress then preview."""
        tr = self.tr
        gen_id = f"cli-{uuid.uuid4().hex[:12]}"
        ids: list[str] = []
        completed = False
        try:
            for event in self.client.add_memory_stream(
                scope=self.scope,
                task_id=self.task_id,
                content=content,
                generation_id=gen_id,
                prompt_language=language,
                timeout=self.client.timeout,
                cancel_event=cancel,
            ):
                if cancel.is_set():
                    return
                name = event.get("event")
                if name in ("progress", "heartbeat"):
                    self.call_from_thread(
                        modal.update_progress,
                        str(event.get("message") or tr.t("msg_extracting")),
                        event.get("percent"),
                    )
                elif name == "completed":
                    completed = True
                    for m in (event.get("data") or {}).get("memories") or []:
                        if isinstance(m, dict):
                            mid = m.get("memory_id") or m.get("id")
                            if mid:
                                ids.append(str(mid))
                elif name == "error":
                    err = str(event.get("message", "extract error"))
                    self.call_from_thread(modal.show_error, err)
                    return
        except Exception as e:  # noqa: BLE001
            if not cancel.is_set():
                self.call_from_thread(modal.show_error, str(e))
            return
        if cancel.is_set():
            return
        if not completed:
            self.call_from_thread(modal.show_error, tr.t("msg_extract_stream_ended"))
            return
        cards: list[dict[str, Any]] = []
        if ids:
            try:
                cards = self.client.fetch_cards_by_ids(
                    scope=self.scope, task_id=self.task_id, memory_ids=ids
                )
            except Exception:  # noqa: BLE001
                cards = [{"id": i} for i in ids]
        self.call_from_thread(modal.show_preview, cards)

    @work(thread=True, exclusive=True, group="commit")
    def _commit_worker(self, keep_ids: list[str], discard_ids: list[str]) -> None:
        """Enable kept cards, delete discarded ones, then refresh."""
        tr = self.tr
        enabled = 0
        for mid in keep_ids:
            try:
                self.client.update_memory(
                    scope=self.scope,
                    task_id=self.task_id,
                    memory_id=mid,
                    status="active",
                    metadata_patch={"enabled": True},
                )
                enabled += 1
            except Exception:  # noqa: BLE001
                continue
        discarded = 0
        for mid in discard_ids:
            try:
                self.client.delete_memory(memory_id=mid)
                discarded += 1
            except Exception:  # noqa: BLE001
                continue
        msg = (
            tr.t("msg_discarded", count=discarded)
            if discarded and not enabled
            else tr.t("msg_inserted", count=enabled)
        )
        self.call_from_thread(self._set_status, msg)
        self.call_from_thread(self.action_refresh)

    # --- search -----------------------------------------------------------
    def action_search(self) -> None:
        """Open the search dialog."""
        if not self._require_ready():
            return
        self.push_screen(SearchModal(self.tr), self._on_search)

    def _on_search(self, query: str | None) -> None:
        if not query:
            return
        try:
            result = self.client.search_memories(
                scope=self.scope, task_id=self.task_id, query=query, top_k=20
            )
            data = result.get("data", {})
            self.memories = data.get("memories", [])
            self._search_query = query
            self._populate_table()
            self._status_summary()
        except Exception as e:  # noqa: BLE001
            self._set_status(self.tr.t("msg_error", error=str(e)))

    # --- config -----------------------------------------------------------
    def action_config(self) -> None:
        """Open the config dialog."""
        self.push_screen(ConfigModal(self.tr), self._on_config)

    def _on_config(self, result: dict[str, Any] | None) -> None:
        """Rebuild the client, show the list immediately, bind in background."""
        if not result:
            return
        self.client = self._build_client()
        if self.client is None:
            self._show_guidance()
            return
        if memory_config.is_binding_configured():
            # Listing works without binding, so show the list right away and
            # bind providers in the background (needed for add/extract).
            self._set_status(self.tr.t("msg_config_saved"))
            self.action_refresh()
            self._bind_worker(result.get("embedding_overridden", False))
        else:
            self._show_guidance()

    @work(thread=True, exclusive=True, group="bind")
    def _bind_worker(self, embedding_overridden: bool) -> None:
        """Bind providers off the UI thread; lock embedding on first bind."""
        tr = self.tr
        p = memory_config.get_providers()
        chat, embed = p["chat"], p["embedding"]
        emb_model = embed["model"]
        emb_dim = embed.get("dimensions", 1536)
        try:
            self.client.bind_providers(
                chat_base_url=chat["base_url"],
                chat_api_key=chat["api_key"],
                chat_model=chat["model"],
                embedding_base_url=embed["base_url"],
                embedding_api_key=embed["api_key"],
                embedding_model=emb_model,
                embedding_dim=emb_dim,
            )
            if memory_config.get_embedding_lock() is None:
                memory_config.set_embedding_lock(emb_model, emb_dim)
            msg = tr.t("msg_embed_model_locked") if embedding_overridden else tr.t("msg_bound")
            self.call_from_thread(self._set_status, msg)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._set_status, tr.t("msg_error", error=str(e)))

    # --- language ---------------------------------------------------------
    def action_lang(self) -> None:
        """Toggle UI language and re-render labels."""
        self.tr.toggle()
        self.title = self.tr.t("app_title")
        self._rebuild_columns()
        if self.query_one("#guidance", Static).has_class("hidden"):
            self._populate_table()
            self._status_summary()
        else:
            self._show_guidance()


def run_memory_browser(scope: str, task_id: str | None = None, lang: str = "zh") -> None:
    """Launch the memory browser TUI (guides the user when unconfigured)."""
    if not TEXTUAL_AVAILABLE:
        console.print("[red]textual 未安装 / textual not installed[/red]")
        console.print("请运行 / Run: pip install 'llm4ad[mindmemos]'")
        return
    MemoryBrowser(scope, task_id, lang).run()
