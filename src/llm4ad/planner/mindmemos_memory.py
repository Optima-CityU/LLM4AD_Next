"""MindMemOS-backed memory implementation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib import error, request

from loguru import logger

from llm4ad.planner.memory import BaseMemory, MemoryCard, MemoryType


def _config_str(config: dict[str, Any], key: str, default: str = "") -> str:
    value = config.get(key, default)
    return str(value).strip() if value is not None else ""


def _config_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


@BaseMemory.register("mindmemos_cloud")
class MindMemOSMemory(BaseMemory):
    """Memory backend that stores and searches cards through MindMemOS."""

    def __init__(
        self,
        config: dict[str, Any],
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize the MindMemOS client."""
        super().__init__(config)
        self.base_url = _required(config, "mindmemos_base_url")
        self.api_key = _required(config, "mindmemos_api_key")
        self.user_id = _required(config, "mindmemos_user_id")
        self.app_id = _config_str(config, "mindmemos_app_id", "llm4ad")
        self.agent_id = _config_str(config, "mindmemos_agent_id", "planner")
        self.session_id = _config_str(config, "mindmemos_session_id")
        self.project_id = _config_str(config, "mindmemos_project_id")
        self.search_strategy = _config_str(config, "mindmemos_search_strategy", "fast")
        self.rerank = _config_bool(config, "mindmemos_rerank", False)
        self.fail_open = _config_bool(config, "mindmemos_fail_open", True)
        self.sync_static_cards = _config_bool(config, "mindmemos_sync_static_cards", False)
        self.allow_remote_clear = _config_bool(config, "mindmemos_allow_remote_clear", False)
        self.score_threshold = config.get("mindmemos_score_threshold")
        self.max_prompt_cards = int(config.get("max_prompt_cards", 5))
        self.include_user_memory = _config_bool(config, "include_user_memory", True)
        self.include_project_memory = _config_bool(config, "include_project_memory", True)
        self.include_task_memory = _config_bool(config, "include_task_memory", True)
        self.user_memory_limit = int(config.get("user_memory_limit", self.max_prompt_cards))
        self.project_memory_limit = int(config.get("project_memory_limit", self.max_prompt_cards))
        self.task_memory_limit = int(config.get("task_memory_limit", self.max_prompt_cards))
        self.memory_dir: Path | None = None
        self._stats = {"add_count": 0, "search_count": 0, "last_error": None}

        if client_factory is None:
            try:
                from mindmemos_sdk import MindMemOSClient
            except ImportError:
                MindMemOSClient = _HttpMindMemOSClient

            client_factory = MindMemOSClient
        self.client = client_factory(
            base_url=self.base_url,
            api_key=self.api_key,
            user_id=self.user_id,
            app_id=self.app_id or None,
            agent_id=self.agent_id or None,
            session_id=self.session_id or None,
        )

    def set_memory_dir(self, memory_dir: Path) -> None:
        """Set the local directory used by the memory interface."""
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load_static_cards(self, inline_cards: list[Any]) -> None:
        """Ignore local static cards unless explicit remote sync is enabled."""
        if inline_cards and not self.sync_static_cards:
            logger.info("MindMemOS static card sync is disabled; ignoring {} inline cards", len(inline_cards))

    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
        """Add a card to MindMemOS through the SDK."""
        del persist
        try:
            message = self._dialogue_message(
                role="assistant",
                content=self._format_card_content(card),
            )
            self.client.memory.add(
                messages=[message],
                user_id=self.user_id,
                app_id=self.app_id or None,
                agent_id=self.agent_id or None,
                session_id=self.session_id or None,
                mode="sync",
                metadata=self._card_metadata(card),
                score=card.score,
                task_id=self.session_id or None,
            )
            self._stats["add_count"] += 1
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning("MindMemOS add_card failed: {}", exc)

    def list_cards(self) -> list[MemoryCard]:
        """Return task-scope cards from MindMemOS."""
        try:
            return self._list_remote_cards()
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning("MindMemOS list_cards failed: {}", exc)
            return []

    async def upsert_card(self, card: MemoryCard, persist: bool | None = None) -> MemoryCard:
        """Create or update a card in MindMemOS."""
        del persist
        update = getattr(self.client.memory, "update", None)
        if card.id and update is not None:
            try:
                update(
                    memory_id=card.id,
                    content=card.content,
                    metadata_patch=self._card_metadata(card),
                    status="active" if card.enabled else "archived",
                )
                return card
            except Exception as exc:  # noqa: BLE001
                self._record_error(exc)
                if not self.fail_open:
                    raise
                logger.warning("MindMemOS upsert_card update failed: {}", exc)
                return card
        await self.add_card(card)
        return card

    async def delete_card(self, card_id: str) -> None:
        """Delete a card from MindMemOS when explicitly allowed."""
        delete = getattr(self.client.memory, "delete", None)
        if delete is None or not self.allow_remote_clear:
            return
        try:
            delete(memory_id=card_id, user_id=self.user_id, hard=True)
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning("MindMemOS delete_card failed: {}", exc)

    async def set_card_enabled(self, card_id: str, enabled: bool) -> MemoryCard:
        """Enable or disable a card in MindMemOS."""
        update = getattr(self.client.memory, "update", None)
        if update is None:
            raise KeyError(card_id)
        for card in self.list_cards():
            if card.id == card_id:
                updated = card.model_copy(update={"enabled": enabled})
                update(
                    memory_id=card_id,
                    content=updated.content,
                    metadata_patch=self._card_metadata(updated),
                    status="active" if enabled else "archived",
                )
                return updated
        raise KeyError(card_id)

    def get_prompt_context(self, query: str = "", max_cards: int | None = None) -> str:
        """Build prompt context from MindMemOS search results."""
        sections: dict[MemoryType, list[str]] = {memory_type: [] for memory_type in MemoryType}
        remote_scopes = [
            (self.include_user_memory, "user", "global", "global", self.user_memory_limit),
            (self.include_project_memory, "project", self.project_id, "project", self.project_memory_limit),
            (self.include_task_memory, "task", self.session_id, "task", self.task_memory_limit),
        ]
        for enabled, scope, session_id, agent_id, configured_limit in remote_scopes:
            if not enabled or not session_id:
                continue
            requested_limit = max_cards if max_cards is not None else configured_limit
            top_k = min(requested_limit, configured_limit)
            if top_k <= 0:
                continue
            try:
                result = self._search_remote_scope(query, top_k, scope, session_id, agent_id)
                for hit in getattr(result, "memories", []) or []:
                    memory_type = _memory_type_from_hit(hit)
                    sections[memory_type].append(f"- {getattr(hit, 'memory', str(hit))}")
            except Exception as exc:  # noqa: BLE001
                self._record_error(exc)
                if not self.fail_open:
                    raise
                logger.warning("MindMemOS search failed for {} scope: {}", scope, exc)
                if not any(sections.values()):
                    return ""

        return _format_sections(sections)

    def _search_remote_scope(self, query: str, top_k: int, scope: str, session_id: str, agent_id: str) -> Any:
        filters = {"llm4ad_scope": scope}
        if scope == "project" and self.project_id:
            filters["project_id"] = self.project_id
        if scope == "task" and self.session_id:
            filters["task_id"] = self.session_id
        result = self.client.memory.search(
            query or "llm4ad algorithm design memory",
            top_k=top_k,
            user_id=self.user_id,
            app_id=self.app_id or None,
            agent_id=agent_id,
            session_id=session_id,
            search_strategy=self.search_strategy,
            rerank=self.rerank,
            score_threshold=self.score_threshold,
            filters=filters,
        )
        self._stats["search_count"] += 1
        return result

    def get_stats(self) -> dict[str, Any]:
        """Return local operation counters and backend identity."""
        return {
            **self._stats,
            "type": "mindmemos_cloud",
            "base_url": self.base_url,
            "user_id": self.user_id,
            "app_id": self.app_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "include_user_memory": self.include_user_memory,
            "user_memory_limit": self.user_memory_limit,
        }

    def clear(self) -> None:
        """Clear local operation counters."""
        self._stats["add_count"] = 0
        self._stats["search_count"] = 0
        self._stats["last_error"] = None

    @staticmethod
    def _dialogue_message(role: str, content: str) -> Any:
        try:
            from mindmemos_sdk.memory import DialogueMessage
        except ImportError:
            return SimpleNamespace(role=role, content=content)
        return DialogueMessage(role=role, content=content)

    @staticmethod
    def _format_card_content(card: MemoryCard) -> str:
        return (
            f"Title: {card.title}\n"
            f"Type: {card.type.value}\n"
            f"Source: {card.source}\n\n"
            f"{card.content}"
        )

    def _card_metadata(self, card: MemoryCard) -> dict[str, Any]:
        metadata = {
            "source": "llm4ad",
            "llm4ad_scope": "task",
            "memory_type": card.type.value,
            "title": card.title,
            "card_id": card.id,
            "card_source": card.source,
            "generation": card.generation,
            "algorithm_id": card.algorithm_id,
            "score": card.score,
            "enabled": card.enabled,
            "tags": card.tags,
            "project_id": self.project_id or None,
            "task_id": self.session_id or None,
            "session_id": self.session_id or None,
        }
        metadata.update(card.metadata)
        return {key: value for key, value in metadata.items() if value is not None}

    def _record_error(self, exc: Exception) -> None:
        self._stats["last_error"] = str(exc)

    def _list_remote_cards(self) -> list[MemoryCard]:
        list_method = getattr(self.client.memory, "list", None)
        if list_method is None:
            return []
        result = list_method(
            user_id=self.user_id,
            app_id=self.app_id or None,
            agent_id="task",
            session_id=self.session_id or None,
            page=1,
            page_size=max(self.task_memory_limit, 20),
            include_total=False,
            include_inactive=True,
        )
        cards: list[MemoryCard] = []
        for item in getattr(result, "memories", []) or []:
            card = _card_from_hit(item)
            if card is not None:
                cards.append(card)
        return cards


def _required(config: dict[str, Any], key: str) -> str:
    value = _config_str(config, key)
    if not value:
        raise ValueError(f"{key} is required for memory.type='mindmemos_cloud'")
    return value


def _memory_type_from_hit(hit: Any) -> MemoryType:
    metadata = getattr(hit, "metadata", None) or {}
    raw = getattr(hit, "memory_type", None) or metadata.get("memory_type") or "general_insight"
    try:
        return MemoryType(str(raw))
    except ValueError:
        return MemoryType.GENERAL_INSIGHT


def _card_from_hit(hit: Any) -> MemoryCard | None:
    memory_id = str(getattr(hit, "id", "") or getattr(hit, "memory_id", "") or "")
    content = str(getattr(hit, "memory", "") or getattr(hit, "content", "") or "")
    if not memory_id or not content:
        return None
    metadata = dict(getattr(hit, "metadata", None) or {})
    memory_type = _memory_type_from_hit(hit)
    title = str(metadata.get("title") or content.splitlines()[0][:80] or "MindMemOS memory")
    status = str(getattr(hit, "status", "") or metadata.get("status") or "active")
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    return MemoryCard(
        id=memory_id,
        type=memory_type,
        title=title,
        content=content,
        source="auto",
        enabled=status == "active" and metadata.get("enabled", True) is not False,
        tags=[str(tag) for tag in tags],
        score=metadata.get("score"),
        generation=metadata.get("generation"),
        algorithm_id=metadata.get("algorithm_id"),
        metadata=metadata,
    )


class _HttpMindMemOSMemoryResource:
    """Minimal HTTP client for MindMemOS public memory APIs."""

    def __init__(self, parent: "_HttpMindMemOSClient") -> None:
        self._parent = parent

    def add(self, **kwargs: Any) -> Any:
        payload = dict(kwargs)
        messages = []
        for message in payload.get("messages") or []:
            if hasattr(message, "model_dump"):
                messages.append(message.model_dump(exclude_none=True))
            elif isinstance(message, dict):
                messages.append(message)
            else:
                messages.append(
                    {
                        "role": getattr(message, "role", "user"),
                        "content": getattr(message, "content", ""),
                    }
                )
        payload["messages"] = messages
        return self._parent.post("/v1/memory/add", payload)

    def search(self, query: str, **kwargs: Any) -> Any:
        return self._parent.post("/v1/memory/search", {"query": query, **kwargs})

    def list(self, **kwargs: Any) -> Any:
        response = self._parent.post("/v1/memory/list", kwargs)
        data = response.get("data") if isinstance(response, dict) else None
        memories = []
        for item in (data or {}).get("memories") or []:
            memories.append(SimpleNamespace(**item))
        return SimpleNamespace(memories=memories)

    def delete(self, **kwargs: Any) -> Any:
        memory_id = kwargs.get("memory_id") or kwargs.get("id")
        payload = {"memory_id": memory_id}
        if kwargs.get("hard") is not None:
            payload["hard"] = bool(kwargs.get("hard"))
        return self._parent.post("/v1/memory/delete", payload)

    def update(self, memory_id: str, content: str, **kwargs: Any) -> Any:
        return self._parent.post("/v1/memory/update", {"memory_id": memory_id, "content": content, **kwargs})


class _HttpMindMemOSClient:
    """Small SDK-compatible fallback used when mindmemos-sdk is not installed."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        user_id: str,
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self.app_id = app_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.memory = _HttpMindMemOSMemoryResource(self)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(_json_safe(payload)).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MindMemOS HTTP request failed: {exc.code} {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"MindMemOS HTTP request failed: {exc}") from exc

        memories = ((data.get("data") or {}).get("memories") or [])
        return SimpleNamespace(
            code=data.get("code"),
            request_id=data.get("request_id"),
            message=data.get("message") or "",
            memories=[SimpleNamespace(**item) if isinstance(item, dict) else item for item in memories],
        )


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def _format_sections(sections: dict[MemoryType, list[str]]) -> str:
    parts: list[str] = []
    labels = [
        (MemoryType.GOOD_ALGORITHM, "Successful Patterns"),
        (MemoryType.ERROR_REFLECTION, "Error Reflections"),
        (MemoryType.DOMAIN_KNOWLEDGE, "Domain Knowledge"),
        (MemoryType.GENERAL_INSIGHT, "General Insights"),
    ]
    for memory_type, label in labels:
        lines = sections.get(memory_type) or []
        if lines:
            parts.append(f"# {label}\n" + "\n".join(lines))
    return "\n\n".join(parts)
