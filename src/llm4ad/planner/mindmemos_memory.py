"""MindMemOS-backed memory implementation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

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
    ):
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
        self.memory_dir: Path | None = None
        self._local_cards: list[MemoryCard] = []
        self._stats = {"add_count": 0, "search_count": 0, "last_error": None}

        if client_factory is None:
            from mindmemos_sdk import MindMemOSClient

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
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load_static_cards(self, inline_cards: list[Any]) -> None:
        for card_config in inline_cards:
            try:
                card = MemoryCard(
                    type=MemoryType(card_config.type),
                    title=card_config.title,
                    content=card_config.content,
                    source="static",
                    tags=card_config.tags,
                    score=card_config.score,
                    metadata=card_config.metadata,
                )
                self._local_cards.append(card)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load MindMemOS static card: {}", exc)

    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
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

    def get_prompt_context(self, query: str = "", max_cards: int | None = None) -> str:
        sections: dict[MemoryType, list[str]] = {memory_type: [] for memory_type in MemoryType}
        for card in self._local_cards[: max_cards or self.max_prompt_cards]:
            sections[card.type].append(f"- {card.title}: {card.content}")

        try:
            top_k = self.max_prompt_cards if max_cards is None else max_cards
            filters = {"project_id": self.project_id} if self.project_id else None
            result = self.client.memory.search(
                query or "llm4ad algorithm design memory",
                top_k=top_k,
                user_id=self.user_id,
                app_id=self.app_id or None,
                agent_id=self.agent_id or None,
                session_id=self.session_id or None,
                search_strategy=self.search_strategy,
                rerank=self.rerank,
                score_threshold=self.score_threshold,
                filters=filters,
            )
            self._stats["search_count"] += 1
            for hit in getattr(result, "memories", []) or []:
                memory_type = _memory_type_from_hit(hit)
                sections[memory_type].append(f"- {getattr(hit, 'memory', str(hit))}")
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning("MindMemOS search failed: {}", exc)
            if not any(sections.values()):
                return ""

        return _format_sections(sections)

    def get_stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "type": "mindmemos_cloud",
            "base_url": self.base_url,
            "user_id": self.user_id,
            "app_id": self.app_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "local_static_cards": len(self._local_cards),
        }

    def clear(self) -> None:
        self._local_cards.clear()
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
            "memory_type": card.type.value,
            "title": card.title,
            "card_id": card.id,
            "card_source": card.source,
            "generation": card.generation,
            "algorithm_id": card.algorithm_id,
            "score": card.score,
            "tags": card.tags,
            "project_id": self.project_id or None,
            "task_id": self.session_id or None,
            "session_id": self.session_id or None,
        }
        metadata.update(card.metadata)
        return {key: value for key, value in metadata.items() if value is not None}

    def _record_error(self, exc: Exception) -> None:
        self._stats["last_error"] = str(exc)


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
