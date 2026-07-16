"""MindMemOS-backed memory implementation."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib import error, request

from loguru import logger
from pydantic import BaseModel, Field

from llm4ad.planner.memory import BaseMemory, BaseMemoryExtractor, MemoryCard, MemoryType
from llm4ad.planner.task_memory_selector import (
    TaskMemoryCandidate,
    create_task_memory_selector,
)


LLM4AD_MEMORY_ENTITY_TYPE = "llm4ad_memory_card"
LLM4AD_MEMORY_CARD_PROPERTY_FILTER = {
    "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
}
_LLM4AD_METADATA_WRITE_EXCLUDE = frozenset(
    {
        "llm4ad_scope",
        "project_id",
        "task_id",
        "session_id",
        "card_id",
        "card_source",
        "entity_type",
    }
)


def _clean_llm4ad_write_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if value is not None and key not in _LLM4AD_METADATA_WRITE_EXCLUDE
    }


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


def _config_timeout(config: dict[str, Any], key: str, default: float) -> float:
    if key not in config or config.get(key) is None:
        return default
    value = config.get(key)
    if value == "":
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _timeout_arg(timeout: float) -> float | None:
    return None if timeout == 0 else timeout


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _single_line(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return text[:limit].rstrip()


def _truncate_block(value: Any, limit: int = 1600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[truncated]"


def _format_algorithm_generation_evidence(algorithm: Any) -> str:
    """Summarize generation provenance that helps MindMemOS extract concrete memories."""
    lines: list[str] = []
    generation_meta = getattr(algorithm, "generation_meta", None)
    operator = _single_line(getattr(generation_meta, "operator", "") or getattr(generation_meta, "agent_name", ""))
    if operator:
        lines.append(f"Sampler/operator: {operator}")

    parent_ids = getattr(algorithm, "parent_ids", None) or []
    if parent_ids:
        lines.append(f"Parent IDs: {', '.join(str(parent_id) for parent_id in parent_ids)}")

    operation_params = getattr(generation_meta, "operation_params", None) or {}
    if isinstance(operation_params, dict):
        for key in (
            "parent_score",
            "parent_description",
            "parent_1_score",
            "parent_1_description",
            "parent_2_score",
            "parent_2_description",
            "cluster_id",
            "cluster_id_1",
            "cluster_id_2",
            "parent_cluster_score",
            "best_cluster_score",
            "worst_cluster_score",
        ):
            value = _single_line(operation_params.get(key))
            if value:
                lines.append(f"{key}: {value}")
        parents = operation_params.get("parents")
        if isinstance(parents, list):
            for index, parent in enumerate(parents[:3], start=1):
                if not isinstance(parent, dict):
                    continue
                parent_id = _single_line(parent.get("id"), limit=80)
                parent_score = _single_line(parent.get("score"), limit=80)
                parent_description = _single_line(parent.get("description"), limit=300)
                parent_parts = []
                if parent_id:
                    parent_parts.append(f"id={parent_id}")
                if parent_score:
                    parent_parts.append(f"score={parent_score}")
                if parent_description:
                    parent_parts.append(f"description={parent_description}")
                if parent_parts:
                    lines.append(f"Parent {index}: " + ", ".join(parent_parts))

    change_description = _single_line(getattr(generation_meta, "change_description", ""))
    if change_description:
        lines.append(f"Generated change: {change_description}")

    targeted_files = getattr(generation_meta, "targeted_files", None) or []
    if targeted_files:
        lines.append(f"Targeted files: {', '.join(str(item) for item in targeted_files)}")

    changed_files = getattr(algorithm, "changed_files", None) or []
    if changed_files:
        lines.append(f"Changed files: {', '.join(str(item) for item in changed_files)}")

    lines_added = int(getattr(algorithm, "lines_added", 0) or 0)
    lines_removed = int(getattr(algorithm, "lines_removed", 0) or 0)
    lines_modified = int(getattr(algorithm, "lines_modified", 0) or 0)
    if lines_added or lines_removed or lines_modified:
        lines.append(f"Line changes: +{lines_added} / -{lines_removed} / ~{lines_modified}")

    return "\n".join(lines) or "N/A"


def _format_algorithm_implementation_evidence(algorithm: Any) -> str:
    """Return a short code/diff excerpt when the algorithm carries code artifacts."""
    artifacts = list(getattr(algorithm, "code_artifacts", None) or [])
    if not artifacts:
        return "N/A"

    sections: list[str] = []
    remaining_budget = 2400
    for artifact in artifacts[:2]:
        file_path = _single_line(getattr(artifact, "file_path", ""), limit=160)
        language = _single_line(getattr(artifact, "language", ""), limit=40) or "text"
        content_mode = _single_line(getattr(artifact, "content_mode", ""), limit=40)
        content = str(getattr(artifact, "content", "") or "").strip()
        if not file_path or not content:
            continue
        excerpt = _truncate_block(content, max(300, min(remaining_budget, 1200)))
        remaining_budget -= len(excerpt)
        header = f"File: {file_path}"
        if content_mode:
            header += f" ({content_mode})"
        sections.append(f"{header}\n```{language}\n{excerpt}\n```")
        if remaining_budget <= 300:
            break

    return "\n\n".join(sections) or "N/A"


class _MemorySearchQuerySchema(BaseModel):
    """Structured query rewrite response."""

    query: str = Field(..., description="Concise retrieval query for algorithm memory search")


@BaseMemoryExtractor.register("mindmemos_raw_extractor")
class MindMemOSRawMemoryExtractor(BaseMemoryExtractor):
    """Select old extraction candidates but let MindMemOS do the actual extraction."""

    def __init__(self, provider: Any, config: Any):
        super().__init__(provider, config)
        self._cards_this_gen = 0

    def reset_generation(self) -> None:
        self._cards_this_gen = 0

    def _budget_available(self) -> bool:
        return self._cards_this_gen < int(_cfg_get(self.config, "max_cards_per_generation", 3))

    def _is_good(self, score: float, population_scores: list[float]) -> bool:
        threshold = _cfg_get(self.config, "good_score_threshold")
        if threshold is not None:
            return score >= float(threshold)
        if not population_scores:
            return False
        relative = float(_cfg_get(self.config, "good_relative_threshold", 0.8))
        threshold_idx = min(int(len(population_scores) * relative), len(population_scores) - 1)
        return score >= sorted(population_scores)[threshold_idx]

    def _is_bad(self, score: float, population_scores: list[float]) -> bool:
        threshold = _cfg_get(self.config, "bad_score_threshold")
        if threshold is not None:
            return score <= float(threshold)
        if not population_scores:
            return False
        relative = float(_cfg_get(self.config, "bad_relative_threshold", 0.2))
        threshold_idx = max(int(len(population_scores) * relative), 0)
        threshold_idx = min(threshold_idx, len(population_scores) - 1)
        return score <= sorted(population_scores)[threshold_idx]

    async def extract_from_good(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        if not _cfg_get(self.config, "enabled", True) or not _cfg_get(self.config, "extract_good", True):
            return None
        if not self._budget_available() or not algorithm.is_evaluated():
            return None
        population_scores = [
            item.score for item in population if item.is_evaluated() and item.score is not None
        ]
        if not self._is_good(float(algorithm.score), population_scores):
            return None
        return self._raw_card("good_algorithm", algorithm, generation, background)

    async def extract_from_bad(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        if not _cfg_get(self.config, "enabled", True) or not _cfg_get(self.config, "extract_bad", True):
            return None
        if not self._budget_available() or not algorithm.is_evaluated():
            return None
        population_scores = [
            item.score for item in population if item.is_evaluated() and item.score is not None
        ]
        if not self._is_bad(float(algorithm.score), population_scores):
            return None
        return self._raw_card("error_reflection", algorithm, generation, background)

    async def extract_from_failure(
        self,
        algorithm: Any,
        error: str,
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        if not _cfg_get(self.config, "enabled", True) or not _cfg_get(self.config, "extract_on_failure", True):
            return None
        if not self._budget_available():
            return None
        return self._raw_card("error_reflection", algorithm, generation, background, error=error)

    def _raw_card(
        self,
        event: str,
        algorithm: Any,
        generation: int,
        background: str,
        *,
        error: str = "",
    ) -> MemoryCard:
        score = getattr(algorithm, "score", None)
        evaluation = getattr(algorithm, "evaluation", None)
        metrics = getattr(evaluation, "metrics", None) or {}
        metrics_text = ", ".join(f"{key}: {value}" for key, value in metrics.items()) or "N/A"
        error_text = error or getattr(evaluation, "error", "") or ""
        generation_evidence = _format_algorithm_generation_evidence(algorithm)
        implementation_evidence = _format_algorithm_implementation_evidence(algorithm)
        if event == "good_algorithm":
            observation_intent = (
                "You are an expert algorithm analyst. This algorithm performed WELL. "
                "Extract a concise, reusable insight about what made it successful."
            )
            task_intent = (
                "Extract one actionable insight (2-5 sentences) about:\n"
                "1. what key design decisions led to this good performance\n"
                "2. what patterns or strategies are worth reusing in future designs"
            )
            extraction_rule = (
                "Create good_algorithm and optional tags only. Do not create "
                "error_reflection, domain_knowledge, or general_insight from this observation."
            )
        else:
            observation_intent = (
                "You are an expert algorithm analyst. This algorithm performed POORLY or failed. "
                "Extract a concise lesson about what went wrong and what to avoid."
            )
            task_intent = (
                "Extract one actionable lesson (2-5 sentences) about:\n"
                "1. what design decisions or patterns led to poor performance or failure\n"
                "2. what specific pitfalls future algorithm designs should AVOID"
            )
            extraction_rule = (
                "Create error_reflection and optional tags only. Do not create "
                "good_algorithm, domain_knowledge, or general_insight from this observation."
            )
        content = (
            "Extract reusable LLM4AD algorithm memory from this task observation.\n\n"
            f"{observation_intent}\n\n"
            f"Event: {event}\n"
            f"Generation: {generation}\n"
            f"Problem background:\n{background or 'N/A'}\n\n"
            f"Algorithm name: {getattr(algorithm, 'name', '')}\n"
            f"Algorithm description:\n{getattr(algorithm, 'description', '')}\n\n"
            f"Score: {score if score is not None else 'N/A'}\n"
            f"Metrics: {metrics_text}\n"
            f"Error: {error_text or 'N/A'}\n\n"
            f"Generation evidence:\n{generation_evidence}\n\n"
            f"Implementation evidence:\n{implementation_evidence}\n\n"
            f"{task_intent}\n\n"
            f"{extraction_rule}\n"
            "The memory must be specific, actionable, and supported by the score, "
            "metrics, error, algorithm description, generation evidence, or implementation evidence.\n"
            "Do not extract a memory that only says performance improved, exploration increased, "
            "or results got better. Name the concrete mechanism, the condition where it applies, "
            "the observed evidence, and the action future algorithms should reuse or avoid."
        )
        self._cards_this_gen += 1
        logger.info(
            "MindMemOS raw memory candidate selected: event={} generation={} "
            "algorithm_id={} score={} budget_used={}",
            event,
            generation,
            getattr(algorithm, "id", None),
            score,
            self._cards_this_gen,
        )
        return MemoryCard(
            type=MemoryType.GOOD_ALGORITHM if event == "good_algorithm" else MemoryType.ERROR_REFLECTION,
            title=f"Raw {event.replace('_', ' ')} observation",
            content=content,
            source="auto",
            score=score,
            generation=generation,
            algorithm_id=getattr(algorithm, "id", None),
            tags=["mindmemos_raw", event],
            metadata={
                "mindmemos_raw_extraction": True,
                "extraction_event": event,
            },
        )


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
        self.request_timeout = _config_timeout(config, "mindmemos_request_timeout", 300.0)
        self.add_timeout = _config_timeout(config, "mindmemos_add_timeout", 300.0)
        self.extraction_prompt_language = _config_str(config, "mindmemos_extraction_prompt_language", "auto")
        self.sync_static_cards = _config_bool(config, "mindmemos_sync_static_cards", False)
        self.allow_remote_clear = _config_bool(config, "mindmemos_allow_remote_clear", False)
        self.score_threshold = config.get("mindmemos_score_threshold") if self.rerank else None
        self.max_prompt_cards = int(config.get("max_prompt_cards", 5))
        self.include_user_memory = _config_bool(config, "include_user_memory", True)
        self.include_project_memory = _config_bool(config, "include_project_memory", True)
        self.include_task_memory = _config_bool(config, "include_task_memory", True)
        self.user_memory_limit = int(config.get("user_memory_limit", self.max_prompt_cards))
        self.project_memory_limit = int(config.get("project_memory_limit", self.max_prompt_cards))
        self.task_memory_limit = int(config.get("task_memory_limit", self.max_prompt_cards))
        self.context_char_budget = int(config.get("mindmemos_context_char_budget", 6000))
        self.retrieval_mode = _config_str(config, "retrieval_mode", "auto")
        self.pinned_card_ids = [str(cid) for cid in (config.get("pinned_card_ids") or []) if str(cid)]
        self.task_injection_mode = _config_str(config, "task_injection_mode", "topk")
        # Retrieve a larger task-scope candidate pool so weight/random selection
        # has something to choose from; the selector trims it to task_memory_limit.
        self.task_candidate_pool = max(
            int(config.get("task_candidate_pool", self.task_memory_limit * 4)),
            self.task_memory_limit,
        )
        self._task_selector = create_task_memory_selector(self.task_injection_mode)
        self.query_provider: Any | None = None
        self.memory_dir: Path | None = None
        self._stats = {
            "add_count": 0,
            "search_count": 0,
            "last_error": None,
            "last_search_elapsed_ms": None,
            "last_search_scope_hits": {},
            "last_injected_chars": 0,
        }

        if client_factory is None:
            try:
                from mindmemos_sdk import MindMemOSClient
            except ImportError:
                MindMemOSClient = _HttpMindMemOSClient

            client_factory = MindMemOSClient
        self.client = _create_client_with_timeout(
            client_factory,
            {
                "base_url": self.base_url,
                "api_key": self.api_key,
                "user_id": self.user_id,
                "app_id": self.app_id or None,
                "agent_id": self.agent_id or None,
                "session_id": self.session_id or None,
            },
            self.request_timeout,
        )
        self.add_client = _create_client_with_timeout(
            client_factory,
            {
                "base_url": self.base_url,
                "api_key": self.api_key,
                "user_id": self.user_id,
                "app_id": self.app_id or None,
                "agent_id": self.agent_id or None,
                "session_id": self.session_id or None,
            },
            self.add_timeout,
        )

    def set_memory_dir(self, memory_dir: Path) -> None:
        """Set the local directory used by the memory interface."""
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def set_query_provider(self, provider: Any) -> None:
        """Attach planner provider used only for lightweight query rewriting."""
        self.query_provider = provider

    def load_static_cards(self, inline_cards: list[Any]) -> None:
        """Ignore local static cards unless explicit remote sync is enabled."""
        if inline_cards and not self.sync_static_cards:
            logger.info("MindMemOS static card sync is disabled; ignoring {} inline cards", len(inline_cards))

    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
        """Add a card to MindMemOS through the SDK."""
        del persist
        started_at = time.perf_counter()
        metadata = self._card_metadata(card)
        event = str(metadata.get("extraction_event") or metadata.get("memory_type") or card.type.value)
        try:
            message = self._dialogue_message(
                role="assistant",
                content=self._format_card_content(card),
            )
            add_payload = {
                "messages": [message],
                "user_id": self.user_id,
                "app_id": self.app_id or None,
                "agent_id": self.agent_id or None,
                "session_id": self.session_id or None,
                "mode": "sync",
                "metadata": metadata,
                "score": card.score,
                "task_id": self.session_id or None,
            }
            if self.extraction_prompt_language in {"ZH", "EN"}:
                add_payload["prompt_language"] = self.extraction_prompt_language
            result = self.add_client.memory.add(**add_payload)
            self._stats["add_count"] += 1
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            memory_id = _memory_id_from_add_result(result)
            logger.info(
                "MindMemOS memory add completed: event={} scope=task task_id={} "
                "project_id={} generation={} elapsed_ms={:.0f}",
                event,
                self.session_id or None,
                self.project_id or None,
                card.generation,
                elapsed_ms,
            )
            logger.bind(
                event_type="memory_card_created",
                scope="task",
                task_id=self.session_id or None,
                project_id=self.project_id or None,
                memory_id=memory_id,
                generation=card.generation,
                algorithm_id=card.algorithm_id,
                memory_type=card.type.value,
            ).info("MindMemOS task memory created")
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning(
                "MindMemOS add_card failed: event={} scope=task fail_open={} "
                "elapsed_ms={:.0f}: {}",
                event,
                self.fail_open,
                elapsed_ms,
                exc,
            )

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
        return self._build_prompt_context(query=query, max_cards=max_cards)

    async def aget_prompt_context(
        self,
        query: str = "",
        max_cards: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build prompt context, rewriting only agentic-search queries."""
        retrieval_query = _build_retrieval_query(query, context)
        effective_query = retrieval_query
        if self.search_strategy == "agentic":
            effective_query = await self._rewrite_query(retrieval_query, context=context)
        return self._build_prompt_context(query=effective_query, max_cards=max_cards, context=context)

    def _build_prompt_context(
        self,
        query: str = "",
        max_cards: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build prompt context from MindMemOS search results."""
        started_at = time.perf_counter()
        sampler = _sampler_name(context)
        sections: dict[MemoryType, list[str]] = {memory_type: [] for memory_type in MemoryType}
        seen: set[str] = set()
        remote_scopes = [
            (self.include_task_memory, "task", self.session_id, "task", self.task_memory_limit),
            (self.include_project_memory, "project", self.project_id, "project", self.project_memory_limit),
            (self.include_user_memory, "user", "global", "global", self.user_memory_limit),
        ]
        enabled_scope_names = [
            scope
            for enabled, scope, session_id, _agent_id, configured_limit in remote_scopes
            if enabled and session_id and configured_limit > 0
        ]
        logger.debug(
            "MindMemOS memory search started: sampler={} strategy={} rerank={} "
            "enabled_scopes={} query_chars={}",
            sampler,
            self.search_strategy,
            self.rerank,
            enabled_scope_names,
            len(query or ""),
        )
        search_jobs: list[dict[str, Any]] = []
        for enabled, scope, session_id, agent_id, configured_limit in remote_scopes:
            if not enabled or not session_id:
                continue
            # Manual retrieval mode injects a fixed, user-selected set for the
            # shared (user/project) scopes instead of searching. Task scope is
            # always retrieved so the injection selector has candidates.
            pinned = self.retrieval_mode == "manual" and scope != "task"
            if pinned:
                # Injection count equals the number of pinned cards, so skip the
                # configured-limit gate. Nothing pinned for this scope -> skip.
                if not self.pinned_card_ids:
                    continue
                search_jobs.append(
                    {
                        "scope": scope,
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "top_k": len(self.pinned_card_ids),
                        "fetch_k": len(self.pinned_card_ids),
                        "pinned": True,
                    }
                )
                continue
            requested_limit = max_cards if max_cards is not None else configured_limit
            top_k = min(requested_limit, configured_limit)
            if top_k <= 0:
                continue
            # Weight/random injection needs a wider task-scope candidate pool to
            # choose from; the pool is trimmed back to top_k after selection.
            # TopK fetches exactly top_k since trimming a ranked list is a no-op.
            needs_pool = scope == "task" and self.task_injection_mode in ("weight", "random")
            fetch_k = max(top_k, self.task_candidate_pool) if needs_pool else top_k
            search_jobs.append(
                {
                    "scope": scope,
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "top_k": top_k,
                    "fetch_k": fetch_k,
                    "pinned": False,
                }
            )

        def run_search(job: dict[str, Any]) -> dict[str, Any]:
            scope_started_at = time.perf_counter()
            try:
                if job.get("pinned"):
                    hits = self._fetch_pinned_hits(
                        job["scope"],
                        job["session_id"],
                        job["agent_id"],
                    )
                else:
                    result = self._search_remote_scope(
                        query,
                        job.get("fetch_k", job["top_k"]),
                        job["scope"],
                        job["session_id"],
                        job["agent_id"],
                    )
                    hits = list(getattr(result, "memories", []) or [])
                return {
                    **job,
                    "hits": hits,
                    "elapsed_ms": (time.perf_counter() - scope_started_at) * 1000,
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    **job,
                    "hits": [],
                    "elapsed_ms": (time.perf_counter() - scope_started_at) * 1000,
                    "error": exc,
                }

        completed: dict[int, dict[str, Any]] = {}
        if len(search_jobs) > 1:
            with ThreadPoolExecutor(max_workers=len(search_jobs), thread_name_prefix="mindmemos-search") as executor:
                futures = {
                    executor.submit(run_search, job): index
                    for index, job in enumerate(search_jobs)
                }
                for future in as_completed(futures):
                    completed[futures[future]] = future.result()
        else:
            for index, job in enumerate(search_jobs):
                completed[index] = run_search(job)

        scope_hits: dict[str, int] = {}
        for index, job in enumerate(search_jobs):
            outcome = completed[index]
            scope = str(outcome["scope"])
            session_id = str(outcome["session_id"])
            agent_id = str(outcome["agent_id"])
            top_k = int(outcome["top_k"])
            exc = outcome["error"]
            if exc is None:
                hits = list(outcome["hits"])
                # Task-scope candidates are always retrieved first, then ordered
                # and trimmed by the configured injection selector (topk/weight/
                # random). Other scopes keep their retrieval order untouched.
                if scope == "task" and hits:
                    hits = self._select_task_hits(hits, top_k)
                scope_hits[scope] = len(hits)
                logger.info(
                    "MindMemOS scope search completed: sampler={} scope={} agent_id={} "
                    "session_id={} top_k={} injection_mode={} hits={} elapsed_ms={:.0f}",
                    sampler,
                    scope,
                    agent_id,
                    session_id,
                    top_k,
                    self.task_injection_mode if scope == "task" else "n/a",
                    len(hits),
                    outcome["elapsed_ms"],
                )
                for hit in hits:
                    dedupe_key = _hit_key(hit)
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    memory_type = _memory_type_from_hit(hit)
                    sections[memory_type].append(_format_hit_for_prompt(hit, scope, memory_type))
            else:
                scope_hits[scope] = 0
                self._record_error(exc)
                if not self.fail_open:
                    raise exc
                logger.warning(
                    "MindMemOS search failed: sampler={} scope={} agent_id={} "
                    "session_id={} top_k={} fail_open={} elapsed_ms={:.0f}: {}",
                    sampler,
                    scope,
                    agent_id,
                    session_id,
                    top_k,
                    self.fail_open,
                    outcome["elapsed_ms"],
                    exc,
                )

        prompt_context = _trim_context(_format_sections(sections), self.context_char_budget)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        self._stats["last_search_elapsed_ms"] = elapsed_ms
        self._stats["last_search_scope_hits"] = dict(scope_hits)
        self._stats["last_injected_chars"] = len(prompt_context)
        # Strategy summary for at-a-glance log visibility. 🧠 marks long-term
        # memory; retrieval_mode covers shared scopes (auto search vs manual
        # pinned) and task_injection_mode covers task-memory selection.
        retrieval_label = "manual-pinned" if self.retrieval_mode == "manual" else "auto-search"
        logger.info(
            "🧠 [long-term memory] injection completed: sampler={} retrieval={} "
            "search_strategy={} task_injection={} scope_hits={} deduped_hits={} "
            "injected_chars={} elapsed_ms={:.0f}",
            sampler,
            retrieval_label,
            self.search_strategy,
            self.task_injection_mode,
            scope_hits,
            len(seen),
            len(prompt_context),
            elapsed_ms,
        )
        logger.bind(
            event_type="mindmemos_memory_injected",
            memory_kind="long_term",
            task_id=self.session_id or None,
            project_id=self.project_id or None,
            sampler=sampler,
            retrieval_mode=self.retrieval_mode,
            strategy=self.search_strategy,
            task_injection_mode=self.task_injection_mode,
            scope_hits=scope_hits,
            deduped_hits=len(seen),
            injected_chars=len(prompt_context),
            elapsed_ms=round(elapsed_ms),
        ).info("🧠 long-term memory injection event")
        return prompt_context

    def _select_task_hits(self, hits: list[Any], limit: int) -> list[Any]:
        """Order and trim task-scope hits using the injection selector.

        Retrieved task-scope hits are wrapped as selector candidates (carrying
        their retrieval score and metadata), passed through the configured
        selector, and unwrapped back into raw hits preserving the selector's
        order.

        Args:
            hits: Raw task-scope search hits from MindMemOS.
            limit: Maximum number of hits to inject.

        Returns:
            The selected hits, at most ``limit`` items.
        """
        candidates = [
            TaskMemoryCandidate(
                key=_hit_key(hit),
                score=_optional_hit_float(_hit_get(hit, "score", None)),
                metadata=_hit_metadata(hit),
                payload=hit,
            )
            for hit in hits
        ]
        selected = self._task_selector.select(candidates, limit)
        return [candidate.payload for candidate in selected]

    def _fetch_pinned_hits(self, scope: str, session_id: str, agent_id: str) -> list[Any]:
        """List a scope's memories and keep only user-pinned cards.

        Used by manual retrieval mode for the shared (user/project) scopes: the
        user selects fixed memories via the memory-management UI and only those
        are injected. Ids that no longer resolve are skipped.

        Args:
            scope: Scope name (``user`` or ``project``).
            session_id: Scope session identifier.
            agent_id: Scope agent identifier.

        Returns:
            The raw hits whose ids are in ``pinned_card_ids``.
        """
        if not self.pinned_card_ids:
            return []
        pinned = set(self.pinned_card_ids)
        list_method = getattr(self.client.memory, "list", None)
        if list_method is None:
            return []
        result = list_method(
            user_id=self.user_id,
            app_id=self.app_id or None,
            agent_id=agent_id,
            session_id=session_id or None,
            page=1,
            page_size=max(len(pinned) * 2, 20),
            include_total=False,
            include_inactive=False,
        )
        hits: list[Any] = []
        matched: set[str] = set()
        for item in getattr(result, "memories", []) or []:
            memory_id = str(_hit_get(item, "id", "") or _hit_get(item, "memory_id", "") or "")
            if memory_id and memory_id in pinned:
                hits.append(item)
                matched.add(memory_id)
        missing = pinned - matched
        if missing:
            logger.warning(
                "MindMemOS manual mode: {} pinned memory id(s) not found in scope {}: {}",
                len(missing),
                scope,
                sorted(missing),
            )
        return hits

    async def _rewrite_query(self, query: str, context: dict[str, Any] | None = None) -> str:
        """Use planner provider to compress broad sampler context into a search query."""
        if self.query_provider is None:
            return query
        started_at = time.perf_counter()
        try:
            context_text = json.dumps(context or {}, ensure_ascii=False, default=str)
            prompt = (
                "Rewrite the current LLM4AD algorithm-evolution sampling context into a concise "
                "memory retrieval query for MindMemOS. Keep only algorithmic objectives, domain, "
                "operators, constraints, failure modes, and parent-strategy clues. Do not judge "
                "memory relevance and do not add unsupported facts.\n\n"
                f"Original query/background:\n{query or 'N/A'}\n\n"
                f"Sampling context JSON:\n{context_text}\n\n"
                "Return one short search query."
            )
            response = await self.query_provider.generate(
                prompt,
                temperature=0.0,
                max_tokens=160,
                schema=_MemorySearchQuerySchema,
                request_stage="memory",
            )
            parsed = getattr(response, "parsed", None)
            rewritten = str(getattr(parsed, "query", "") or getattr(response, "text", "") or "").strip()
            if rewritten:
                rewritten = " ".join(rewritten.split())[:500]
                logger.debug(
                    "MindMemOS query rewrite completed: sampler={} original_chars={} "
                    "rewritten_chars={} elapsed_ms={:.0f}",
                    _sampler_name(context),
                    len(query or ""),
                    len(rewritten),
                    (time.perf_counter() - started_at) * 1000,
                )
                return rewritten
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            logger.warning(
                "MindMemOS query rewrite failed: sampler={} fail_open={} "
                "elapsed_ms={:.0f}: {}",
                _sampler_name(context),
                self.fail_open,
                (time.perf_counter() - started_at) * 1000,
                exc,
            )
        return query

    def _search_remote_scope(self, query: str, top_k: int, scope: str, session_id: str, agent_id: str) -> Any:
        filters: dict[str, Any] = {
            "user_id": self.user_id,
            "app_id": self.app_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "entity_type": LLM4AD_MEMORY_ENTITY_TYPE,
            "property_name": LLM4AD_MEMORY_CARD_PROPERTY_FILTER,
        }
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
        self._stats["last_search_elapsed_ms"] = None
        self._stats["last_search_scope_hits"] = {}
        self._stats["last_injected_chars"] = 0

    @staticmethod
    def _dialogue_message(role: str, content: str) -> Any:
        try:
            from mindmemos_sdk.memory import DialogueMessage
        except ImportError:
            return SimpleNamespace(role=role, content=content)
        return DialogueMessage(role=role, content=content)

    @staticmethod
    def _format_card_content(card: MemoryCard) -> str:
        if card.metadata.get("mindmemos_raw_extraction") is True:
            return card.content
        return (
            f"Title: {card.title}\n"
            f"Type: {card.type.value}\n"
            f"Source: {card.source}\n\n"
            f"{card.content}"
        )

    def _card_metadata(self, card: MemoryCard) -> dict[str, Any]:
        if card.metadata.get("mindmemos_raw_extraction") is True:
            metadata = {
                "source": "llm4ad",
                "generation": card.generation,
                "algorithm_id": card.algorithm_id,
                "score": card.score,
                **card.metadata,
            }
            metadata["source"] = "llm4ad"
            return _clean_llm4ad_write_metadata(metadata)
        metadata = {
            "source": "llm4ad",
            "memory_type": card.type.value,
            "title": card.title,
            "generation": card.generation,
            "algorithm_id": card.algorithm_id,
            "score": card.score,
            "enabled": card.enabled,
            "tags": card.tags,
        }
        metadata.update(card.metadata)
        metadata["source"] = "llm4ad"
        return _clean_llm4ad_write_metadata(metadata)

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


def _create_client_with_timeout(
    client_factory: Callable[..., Any],
    client_kwargs: dict[str, Any],
    request_timeout: float,
) -> Any:
    timeout = _timeout_arg(request_timeout)
    for timeout_key in ("request_timeout", "timeout"):
        try:
            return client_factory(**client_kwargs, **{timeout_key: timeout})
        except TypeError as exc:
            message = str(exc)
            if timeout_key not in message and "unexpected keyword" not in message:
                raise
    return client_factory(**client_kwargs)


def _hit_get(hit: Any, key: str, default: Any = None) -> Any:
    if isinstance(hit, dict):
        return hit.get(key, default)
    return getattr(hit, key, default)


def _hit_metadata(hit: Any) -> dict[str, Any]:
    metadata = _hit_get(hit, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _optional_hit_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_hit_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _memory_text(hit: Any) -> str:
    return str(_hit_get(hit, "memory", None) or _hit_get(hit, "content", None) or hit).strip()


def _memory_type_from_hit(hit: Any) -> MemoryType:
    metadata = _hit_metadata(hit)
    raw = _hit_get(hit, "memory_type", None) or metadata.get("memory_type") or "general_insight"
    try:
        return MemoryType(str(raw))
    except ValueError:
        return MemoryType.GENERAL_INSIGHT


def _hit_key(hit: Any) -> str:
    memory_id = str(_hit_get(hit, "id", "") or _hit_get(hit, "memory_id", "") or "")
    if memory_id:
        return f"id:{memory_id}"
    return f"memory:{_memory_text(hit)}"


def _format_hit_for_prompt(hit: Any, scope: str, memory_type: MemoryType) -> str:
    metadata = _hit_metadata(hit)
    text = _memory_text(hit)
    title = str(
        metadata.get("title")
        or metadata.get("entity_name")
        or _hit_get(hit, "title", "")
        or ""
    ).strip()
    score = _optional_hit_float(_hit_get(hit, "score", None))
    if score is None:
        score = _optional_hit_float(metadata.get("score"))
    generation = _optional_hit_int(metadata.get("generation"))
    algorithm_id = str(metadata.get("algorithm_id") or "").strip()

    attrs = [f"scope: {scope}", f"type: {memory_type.value}"]
    if score is not None:
        attrs.append(f"score: {score:.4f}")
    if generation is not None:
        attrs.append(f"gen: {generation}")
    if algorithm_id:
        attrs.append(f"algorithm: {algorithm_id}")
    prefix = f"**{title}** " if title else ""
    lines = [f"- {prefix}({', '.join(attrs)}):"]
    if text:
        lines.append(f"  Mechanism: {text}")
    evidence_parts: list[str] = []
    if score is not None:
        evidence_parts.append(f"score={score:.4f}")
    if generation is not None:
        evidence_parts.append(f"gen={generation}")
    if algorithm_id:
        evidence_parts.append(f"algorithm={algorithm_id}")
    if evidence_parts:
        lines.append(f"  Evidence: {', '.join(evidence_parts)}")
    extra_fields = [
        ("evidence", "Observed evidence"),
        ("applicability", "Applicability"),
        ("reuse_guidance", "Reuse guidance"),
        ("avoidance", "Avoidance guidance"),
    ]
    for key, label in extra_fields:
        value = _single_line(metadata.get(key), limit=800)
        if value:
            lines.append(f"  {label}: {value}")
    return "\n".join(lines)


def _sampler_name(context: dict[str, Any] | None) -> str:
    if not context:
        return "unknown"
    sampler = context.get("sampler")
    return str(sampler).strip() or "unknown"


def _build_retrieval_query(query: str, context: dict[str, Any] | None) -> str:
    """Build a compact retrieval query from sampler context without an LLM call."""
    lines: list[str] = []
    background = " ".join(str(query or "").split())
    if background:
        lines.append(background[:800])
    if context:
        for key in (
            "sampler",
            "parent_score",
            "parent_description",
            "parent_1_score",
            "parent_1_description",
            "parent_2_score",
            "parent_2_description",
            "cluster_id",
        ):
            value = context.get(key)
            if value is None or value == "":
                continue
            text = " ".join(str(value).split())
            if not text:
                continue
            lines.append(f"{key}: {text[:500]}")
    retrieval_query = "\n".join(lines).strip()
    return retrieval_query[:2400] or query


def _card_from_hit(hit: Any) -> MemoryCard | None:
    memory_id = str(_hit_get(hit, "id", "") or _hit_get(hit, "memory_id", "") or "")
    content = str(_hit_get(hit, "memory", "") or _hit_get(hit, "content", "") or "")
    if not memory_id or not content:
        return None
    metadata = _hit_metadata(hit)
    memory_type = _memory_type_from_hit(hit)
    title = str(metadata.get("title") or content.splitlines()[0][:80] or "MindMemOS memory")
    status = str(_hit_get(hit, "status", "") or metadata.get("status") or "active")
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


def _memory_id_from_add_result(result: Any) -> str | None:
    """Extract the first remote memory id from MindMemOS add responses."""
    memories = getattr(result, "memories", None)
    if memories is None and isinstance(result, dict):
        data = result.get("data") or {}
        memories = data.get("memories") if isinstance(data, dict) else result.get("memories")
    if not isinstance(memories, list | tuple) or not memories:
        return None
    first = memories[0]
    if isinstance(first, dict):
        raw_id = first.get("memory_id") or first.get("id")
    else:
        raw_id = getattr(first, "memory_id", None) or getattr(first, "id", None)
    return str(raw_id).strip() or None


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
        request_timeout: float = 60.0,
        timeout: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self.app_id = app_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.request_timeout = timeout if timeout is not None else request_timeout
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
            with request.urlopen(req, timeout=self.request_timeout) as response:
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


def _trim_context(context: str, char_budget: int) -> str:
    if char_budget <= 0 or len(context) <= char_budget:
        return context
    return context[:char_budget].rstrip() + "\n\n[Memory context truncated]"
