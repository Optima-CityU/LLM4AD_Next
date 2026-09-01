"""MindMemOS-backed memory implementation."""

from __future__ import annotations

import ast
import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
LLM4AD_MEMORY_CARD_PROPERTIES = frozenset(LLM4AD_MEMORY_CARD_PROPERTY_FILTER["in"])
_SCOPE_PRESENCE_TTL_SECONDS = 60.0

# Human-readable labels for memory event/type in user-facing logs. Keeps the
# implementation name (MindMemOS) out of logs — users just see "long-term memory".
_MEMORY_EVENT_LABELS = {
    "good_algorithm": "good algorithm",
    "error_reflection": "error reflection",
    "domain_knowledge": "domain knowledge",
    "general_insight": "general insight",
}


def _memory_event_label(event: str) -> str:
    """Return a human-readable label for a memory event/type value."""
    return _MEMORY_EVENT_LABELS.get(str(event), str(event))

# Runtime source of truth for manual-mode pinned shared memory ids. Lives under
# the run's memory/ dir; seeded from config at run start, then editable while the
# task runs (task-memory panel) and re-read on each injection.
PINNED_MEMORY_FILENAME = "pinned_memory.json"
_LLM4AD_METADATA_WRITE_EXCLUDE = frozenset(
    {
        "_mindmemos_source_artifacts",
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


def _preview_block(value: Any, limit: int = 1600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[preview shortened; complete source is attached as immutable evidence]"


def _source_artifact_id(index: int, file_path: Any) -> str:
    path = _single_line(file_path, limit=480) or f"artifact-{index}"
    return f"code-{index}:{path}"


def _is_structured_candidate_artifact(artifact: Any) -> bool:
    """Recognize model specifications without coupling memory to one solver problem."""
    file_path = str(getattr(artifact, "file_path", "") or "").lower()
    language = str(getattr(artifact, "language", "") or "").lower()
    content = str(getattr(artifact, "content", "") or "")
    return (
        language in {"json", "yaml", "yml", "math_spec", "solver_spec"}
        or "model_spec" in file_path
        or "candidate_spec" in file_path
        or "MODEL_SPEC" in content
    )


def _algorithm_source_artifacts(algorithm: Any) -> list[dict[str, str]]:
    """Return complete code artifacts for lossless storage outside the model prompt."""
    result: list[dict[str, str]] = []
    for index, artifact in enumerate(getattr(algorithm, "code_artifacts", None) or [], start=1):
        content = str(getattr(artifact, "content", "") or "")
        if not content.strip():
            continue
        item = {
            "artifact_id": _source_artifact_id(index, getattr(artifact, "file_path", "")),
            "type": "code",
            "content": content,
        }
        language = _single_line(getattr(artifact, "language", ""), limit=64)
        if language:
            item["language"] = language
        result.append(item)
    return result


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

    evaluation = getattr(algorithm, "evaluation", None)
    evolution_feedback = getattr(evaluation, "evolution_feedback", None) or {}
    if evolution_feedback:
        lines.append(
            "Reusable evaluator findings: "
            + json.dumps(
                evolution_feedback,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
        )

    return "\n".join(lines) or "N/A"


def _format_algorithm_implementation_evidence(algorithm: Any) -> str:
    """Return a short code/diff excerpt when the algorithm carries code artifacts."""
    artifacts = list(getattr(algorithm, "code_artifacts", None) or [])
    if not artifacts:
        return "N/A"

    sections: list[str] = []
    remaining_budget = 2400
    for artifact_index, artifact in enumerate(artifacts[:2], start=1):
        file_path = _single_line(getattr(artifact, "file_path", ""), limit=160)
        content_mode = _single_line(getattr(artifact, "content_mode", ""), limit=40)
        content = str(getattr(artifact, "content", "") or "").strip()
        if not file_path or not content:
            continue
        excerpt = _preview_block(content, max(300, min(remaining_budget, 1200)))
        remaining_budget -= len(excerpt)
        header = (
            f"Structured mathematical candidate: {file_path}"
            if _is_structured_candidate_artifact(artifact)
            else f"File: {file_path}"
        )
        if content_mode:
            header += f" ({content_mode})"
        artifact_id = _source_artifact_id(artifact_index, file_path)
        sections.append(f"{header}\nAttached source artifact: {artifact_id}\nAnalysis preview:\n{excerpt}")
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
        """Initialize the extractor and its per-generation state."""
        super().__init__(provider, config)
        self._cards_this_gen = 0
        self._best_score = float("-inf")

    def reset_generation(self) -> None:
        """Clear extraction candidates accumulated for the current generation."""
        self._cards_this_gen = 0

    def _budget_available(self) -> bool:
        return self._cards_this_gen < int(_cfg_get(self.config, "max_cards_per_generation", 3))

    def _is_new_global_best(self, score: float, population_scores: list[float]) -> bool:
        """Return whether score is a strictly improved population-best score."""
        if not population_scores or score != max(population_scores):
            return False
        if score <= self._best_score:
            return False
        self._best_score = score
        return True

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
        """Collect a strictly improved result as a potential good-algorithm memory."""
        if not _cfg_get(self.config, "enabled", True) or not _cfg_get(self.config, "extract_good", True):
            return None
        if not self._budget_available() or not algorithm.is_evaluated():
            return None
        population_scores = [
            item.score for item in population if item.is_evaluated() and item.score is not None
        ]
        if not self._is_good(float(algorithm.score), population_scores):
            return None
        if not self._is_new_global_best(float(algorithm.score), population_scores):
            return None
        return self._raw_card("good_algorithm", algorithm, generation, background)

    async def extract_from_bad(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        """Collect low-score feedback as a potential reflection memory."""
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
        """Collect an execution failure as a potential reflection memory."""
        if not _cfg_get(self.config, "enabled", True) or not _cfg_get(self.config, "extract_on_failure", True):
            return None
        if not self._budget_available():
            return None
        return self._raw_card("execution_failure", algorithm, generation, background, error=error)

    def _raw_card(
        self,
        event: str,
        algorithm: Any,
        generation: int,
        background: str,
        *,
        error: str = "",
    ) -> MemoryCard:
        is_execution_failure = event == "execution_failure"
        score = None if is_execution_failure else getattr(algorithm, "score", None)
        score_text = (
            "N/A (evaluation failed before scoring)"
            if is_execution_failure
            else str(score) if score is not None else "N/A"
        )
        evaluation_outcome = (
            "Evaluation outcome: failed before a valid score was produced\n"
            if is_execution_failure
            else ""
        )
        evaluation = getattr(algorithm, "evaluation", None)
        metrics = getattr(evaluation, "metrics", None) or {}
        metrics_text = ", ".join(f"{key}: {value}" for key, value in metrics.items()) or "N/A"
        error_text = error or getattr(evaluation, "error", "") or ""
        generation_evidence = _format_algorithm_generation_evidence(algorithm)
        implementation_evidence = _format_algorithm_implementation_evidence(algorithm)
        has_structured_candidate = any(
            _is_structured_candidate_artifact(artifact)
            for artifact in (getattr(algorithm, "code_artifacts", None) or [])
        )
        structured_candidate_guidance = (
            "\nThis observation contains a structured mathematical candidate. Analyze reusable "
            "formula families, parameterization, structural constraints, or symmetry, and relate "
            "them to feasibility, objective, solver gap, node count, or solve time when those "
            "metrics are available. Distinguish model-authored search hypotheses from benchmark "
            "constraints locked by the trusted adapter. Do not reduce the memory to Python syntax.\n"
            if has_structured_candidate
            else ""
        )
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
        elif is_execution_failure:
            observation_intent = (
                "You are an expert algorithm analyst. Evaluation execution failed before a valid "
                "score was produced. Do not infer that this algorithm scored zero or performed poorly. "
                "Extract a concise lesson about preventing or repairing the concrete failure."
            )
            task_intent = (
                "Extract one actionable lesson (2-5 sentences) about:\n"
                "1. what implementation or constraint violation caused execution to fail\n"
                "2. what validation, guard, or design change future algorithms should apply to avoid it"
            )
            extraction_rule = (
                "Create error_reflection and optional tags only. Do not claim a score or comparative "
                "performance from this observation."
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
            f"Score: {score_text}\n"
            f"{evaluation_outcome}"
            f"Metrics: {metrics_text}\n"
            f"Error: {error_text or 'N/A'}\n\n"
            f"Generation evidence:\n{generation_evidence}\n\n"
            f"Implementation evidence:\n{implementation_evidence}\n\n"
            f"{structured_candidate_guidance}"
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
            "📝 [long-term memory] extracted candidate: type={} generation={} "
            "algorithm={} score={} (#{} this generation)",
            _memory_event_label(event),
            generation,
            getattr(algorithm, "id", None),
            f"{score:.4f}" if score is not None else "N/A",
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
                "_mindmemos_source_artifacts": _algorithm_source_artifacts(algorithm),
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
        self.context_char_budget = int(config.get("mindmemos_context_char_budget", 20000))
        self.elite_code_slots = int(config.get("mindmemos_elite_code_slots", 1))
        self.elite_code_char_budget = int(config.get("mindmemos_elite_code_char_budget", 12000))
        self.retrieval_mode = _config_str(config, "retrieval_mode", "auto")
        self.pinned_card_ids = [str(cid) for cid in (config.get("pinned_card_ids") or []) if str(cid)]
        self.task_injection_mode = _config_str(config, "task_injection_mode", "topk")
        self.task_injection_lambda = config.get("task_injection_lambda", 0.5)
        # Retrieve a larger task-scope candidate pool so weight/random selection
        # has something to choose from; the selector trims it to task_memory_limit.
        self.task_candidate_pool = max(
            int(config.get("task_candidate_pool", self.task_memory_limit * 4)),
            self.task_memory_limit,
        )
        self._task_selector = create_task_memory_selector(
            self.task_injection_mode,
            {"lambda": self.task_injection_lambda},
        )
        # Elite implementation selection is independent from the ordinary
        # memory-card ordering. Keeping a separate selector also prevents a
        # weighted/random text selection from consuming the code lane's RNG
        # state (and vice versa).
        self._elite_code_selector = create_task_memory_selector(
            self.task_injection_mode,
            {"lambda": self.task_injection_lambda},
        )
        self.query_provider: Any | None = None
        self.memory_dir: Path | None = None
        self._stats = {
            "add_count": 0,
            "update_count": 0,
            "reinforcement_count": 0,
            "search_count": 0,
            "last_error": None,
            "last_search_elapsed_ms": None,
            "last_search_scope_hits": {},
            "last_injected_chars": 0,
            "last_elite_algorithm_ids": [],
            "last_elite_code_chars": 0,
            "last_elite_code_complete": False,
        }
        # A task repeatedly asks the same scopes for prompt injection. Remember
        # whether a scope has at least one active card, so known-empty scopes do
        # not issue a semantic search on every sampler invocation. The complete
        # identity stays in the key even though this object is task-local, which
        # prevents accidental cross-user reuse if the cache scope changes later.
        self._scope_presence: dict[tuple[str, str, str, str, str], tuple[bool, float]] = {}

        injected_client_factory = client_factory is not None
        if client_factory is None:
            # Structured search results carry schema-specific fields that older
            # public SDK models discard. Keep the complete structured path on
            # the lossless HTTP adapter.
            client_factory = _HttpMindMemOSClient
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
        # mindmemos-sdk 0.1.x predates the structured ``document_blocks``
        # contract. Keep it for reads, but use the small HTTP-compatible client
        # for structured writes until the public SDK exposes that field. Tests
        # and callers that inject a client factory retain their observable fake.
        self.structured_add_client = (
            self.add_client
            if injected_client_factory or client_factory is _HttpMindMemOSClient
            else _create_client_with_timeout(
                _HttpMindMemOSClient,
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
        )

    def set_memory_dir(self, memory_dir: Path) -> None:
        """Set the local directory used by the memory interface.

        (Re)seeds the pinned-memory file from the task config at each run start,
        so a rerun with changed parameters always reflects the latest config.
        The file is the runtime source of truth thereafter, so edits made while
        the task runs (e.g. from the task-memory panel) take effect on the next
        injection without restarting the task. This only writes for the manual
        retrieval mode; auto mode injects by retrieval and needs no pinned file.
        """
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if self.retrieval_mode == "manual":
            pinned_file = self.memory_dir / PINNED_MEMORY_FILENAME
            _write_pinned_file(pinned_file, self.pinned_card_ids)

    def _current_pinned_ids(self) -> list[str]:
        """Return the live pinned-memory ids for manual retrieval mode.

        Reads the pinned-memory file each call so runtime edits are picked up.
        Falls back to the config snapshot when no memory dir/file is available.

        Returns:
            The list of pinned memory card ids to inject.
        """
        if self.memory_dir is None:
            return self.pinned_card_ids
        pinned_file = self.memory_dir / PINNED_MEMORY_FILENAME
        ids = _read_pinned_file(pinned_file)
        return ids if ids is not None else self.pinned_card_ids

    def set_query_provider(self, provider: Any) -> None:
        """Attach planner provider used only for lightweight query rewriting."""
        self.query_provider = provider

    def load_static_cards(self, inline_cards: list[Any]) -> None:
        """Ignore local static cards unless explicit remote sync is enabled."""
        if inline_cards and not self.sync_static_cards:
            logger.info("[long-term memory] static card sync is disabled; ignoring {} inline cards", len(inline_cards))

    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
        """Add one task observation through the structured batch contract."""
        await self.add_cards([card], persist=persist)

    async def add_cards(
        self,
        cards: list[MemoryCard],
        persist: bool | None = None,
    ) -> None:
        """Insert one generation of task observations in one structured request."""
        del persist
        if not cards:
            return
        started_at = time.perf_counter()
        blocks: list[dict[str, Any]] = []
        idempotency_blocks: list[dict[str, Any]] = []
        for index, card in enumerate(cards):
            metadata = self._card_metadata(card)
            content = self._format_card_content(card)
            source_artifacts = [
                dict(artifact)
                for artifact in (card.metadata.get("_mindmemos_source_artifacts") or [])
                if isinstance(artifact, dict) and str(artifact.get("content") or "").strip()
            ]
            event = str(
                metadata.get("extraction_event")
                or metadata.get("memory_type")
                or card.type.value
            )
            stable_identity = {
                "task_id": self.session_id or "",
                "generation": card.generation,
                "algorithm_id": card.algorithm_id,
                "extraction_event": event,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "source_artifacts": [
                    {
                        "artifact_id": str(artifact.get("artifact_id") or ""),
                        "content_hash": hashlib.sha256(
                            str(artifact.get("content") or "").encode("utf-8")
                        ).hexdigest(),
                    }
                    for artifact in source_artifacts
                ],
            }
            block_digest = hashlib.sha256(
                json.dumps(
                    stable_identity,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            block_id = f"llm4ad-task-{block_digest[:32]}"
            if any(item["block_id"] == block_id for item in blocks):
                block_id = f"{block_id}-{index + 1}"
            idempotency_blocks.append({**stable_identity, "block_id": block_id})
            blocks.append(
                {
                    "block_id": block_id,
                    "document_id": str(card.algorithm_id or card.id or block_id),
                    "messages": [
                        {
                            "role": "user",
                            "content": content,
                        }
                    ],
                    "source_artifacts": source_artifacts,
                    "locator": {
                        "task_id": self.session_id or "",
                        "generation": card.generation,
                        "algorithm_id": card.algorithm_id,
                        "card_id": card.id,
                    },
                    "metadata": metadata,
                }
            )
        idempotency_source = json.dumps(
            {
                "user_id": self.user_id,
                "session_id": self.session_id,
                "blocks": idempotency_blocks,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        idempotency_key = (
            "llm4ad-task-batch:"
            + hashlib.sha256(idempotency_source.encode("utf-8")).hexdigest()
        )
        try:
            add_payload = {
                "document_blocks": blocks,
                "user_id": self.user_id,
                "app_id": self.app_id or None,
                "agent_id": self.agent_id or None,
                "session_id": self.session_id or None,
                "mode": "sync",
                "metadata": {
                    "source": "llm4ad",
                    "llm4ad_scope": "task",
                    "batch_size": len(blocks),
                    # Task cards intentionally span multiple Episodes. Recall
                    # prior typed cards from this task before deciding whether
                    # to create, reinforce, update, or supersede.
                    "structured_history_scope": "session",
                },
                "idempotency_key": idempotency_key,
                "task_id": self.session_id or None,
            }
            if self.extraction_prompt_language in {"ZH", "EN"}:
                add_payload["prompt_language"] = self.extraction_prompt_language
            result = self.structured_add_client.memory.add(**add_payload)
            operation_events = _structured_operation_events(result)
            for event in operation_events:
                counter = {
                    "add": "add_count",
                    "update": "update_count",
                    "reinforcement": "reinforcement_count",
                }[event["operation"]]
                self._stats[counter] += 1
            # A successful sync write invalidates a previously cached empty
            # task scope even when an older server omits response events.
            self._mark_task_scope_present()
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "🧠 [long-term memory] inserted structured task-memory batch: cards={} "
                "add={} update={} reinforcement={} task={} elapsed_ms={:.0f}",
                len(cards),
                sum(event["operation"] == "add" for event in operation_events),
                sum(event["operation"] == "update" for event in operation_events),
                sum(event["operation"] == "reinforcement" for event in operation_events),
                self.session_id or None,
                elapsed_ms,
            )
            card_by_block_id = {
                str(block["block_id"]): card
                for block, card in zip(blocks, cards, strict=True)
            }
            event_type_by_operation = {
                "add": "memory_card_created",
                "update": "memory_card_updated",
                "reinforcement": "memory_card_reinforced",
            }
            for event in operation_events:
                event_card = next(
                    (
                        card_by_block_id[block_id]
                        for block_id in event["source_block_ids"]
                        if block_id in card_by_block_id
                    ),
                    cards[0],
                )
                logger.bind(
                    event_type=event_type_by_operation[event["operation"]],
                    memory_operation=event["operation"],
                    scope="task",
                    task_id=self.session_id or None,
                    project_id=self.project_id or None,
                    memory_id=event["memory_id"],
                    related_memory_ids=event["related_memory_ids"],
                    generation=event_card.generation,
                    algorithm_id=event_card.algorithm_id,
                    memory_type=event_card.type.value,
                    batch_size=len(cards),
                ).info("🧠 long-term memory card persisted")
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning(
                "🧠 [long-term memory] structured batch insert failed: cards={} fail_open={} "
                "elapsed_ms={:.0f}: {}",
                len(cards),
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
            logger.warning("[long-term memory] list cards failed: {}", exc)
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
                if card.enabled:
                    self._mark_task_scope_present()
                else:
                    self._invalidate_task_scope_presence()
                return card
            except Exception as exc:  # noqa: BLE001
                self._record_error(exc)
                if not self.fail_open:
                    raise
                logger.warning("[long-term memory] upsert card failed: {}", exc)
                return card
        await self.add_card(card)
        return card

    async def delete_card(self, card_id: str) -> None:
        """Delete a card from MindMemOS when explicitly allowed."""
        delete = getattr(self.client.memory, "delete", None)
        if delete is None or not self.allow_remote_clear:
            return
        try:
            delete(memory_id=card_id, hard=True)
            self._invalidate_task_scope_presence()
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            if not self.fail_open:
                raise
            logger.warning("[long-term memory] delete card failed: {}", exc)

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
                if enabled:
                    self._mark_task_scope_present()
                else:
                    self._invalidate_task_scope_presence()
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
        # Re-read pinned ids each injection so task-memory-panel edits take effect
        # without restarting the task (the file is the runtime source of truth).
        pinned_ids = self._current_pinned_ids()
        sections: dict[MemoryType, list[str]] = {memory_type: [] for memory_type in MemoryType}
        elite_recall_pool: list[tuple[Any, str]] = []
        seen: set[str] = set()
        remote_scopes = [
            (self.include_task_memory, "task", self.session_id, "task", self.task_memory_limit),
            (self.include_project_memory, "project", self.project_id, "project", self.project_memory_limit),
            (self.include_user_memory, "user", "global", "global", self.user_memory_limit),
        ]
        enabled_scope_names = [
            scope
            for enabled, scope, session_id, _agent_id, configured_limit in remote_scopes
            if session_id
            and (
                enabled and configured_limit > 0
                # Fixed shared-memory selection is controlled by the pinned
                # ids themselves. Honour it for older tasks whose hidden
                # include_* flags still carry their default false values.
                or (
                    self.retrieval_mode == "manual"
                    and scope != "task"
                    and bool(pinned_ids)
                )
            )
        ]
        logger.debug(
            "🔎 [long-term memory] retrieval started: sampler={} strategy={} rerank={} "
            "enabled_scopes={} query_chars={}",
            sampler,
            self.search_strategy,
            self.rerank,
            enabled_scope_names,
            len(query or ""),
        )
        search_jobs: list[dict[str, Any]] = []
        for enabled, scope, session_id, agent_id, configured_limit in remote_scopes:
            # Manual retrieval mode injects a fixed, user-selected set for the
            # shared (user/project) scopes instead of searching. Task scope is
            # always retrieved so the injection selector has candidates.
            pinned = self.retrieval_mode == "manual" and scope != "task"
            if not session_id or (not enabled and not pinned):
                continue
            if pinned:
                # Injection count equals the number of pinned cards, so skip the
                # configured-limit gate. Nothing pinned for this scope -> skip.
                if not pinned_ids:
                    continue
                search_jobs.append(
                    {
                        "scope": scope,
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "top_k": len(pinned_ids),
                        "fetch_k": len(pinned_ids),
                        "pinned": True,
                    }
                )
                continue
            requested_limit = max_cards if max_cards is not None else configured_limit
            top_k = min(requested_limit, configured_limit)
            if top_k <= 0:
                continue
            if not self._scope_has_active_cards(scope, session_id, agent_id):
                logger.debug(
                    "🔎 [long-term memory] scope search skipped: scope={} agent_id={} session_id={} reason=empty",
                    scope,
                    agent_id,
                    session_id,
                )
                continue
            # Task retrieval always uses a wider candidate pool. Besides giving
            # weight/random modes room to sample, this lets Top-K retain strong
            # successful designs when many similar error reflections occupy the
            # first few raw recall positions.
            needs_pool = scope == "task"
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
                        pinned_ids,
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
        for index, _job in enumerate(search_jobs):
            outcome = completed[index]
            scope = str(outcome["scope"])
            session_id = str(outcome["session_id"])
            agent_id = str(outcome["agent_id"])
            top_k = int(outcome["top_k"])
            exc = outcome["error"]
            pinned = bool(outcome.get("pinned"))
            # Manual mode fetches a fixed, user-selected set of shared cards by id
            # (no query/search). Auto mode searches. The log reflects which path
            # ran so "search"/"top_k" never appears for a pinned fetch.
            retrieval_kind = "pinned-fetch" if pinned else "search"
            if exc is None:
                hits = list(outcome["hits"])
                # The complete-code lane selects independently from the wider
                # recall pool. Otherwise the first code-bearing card in the
                # final text Top-K permanently occupies the only elite slot.
                elite_recall_pool.extend(
                    (hit, scope)
                    for hit in hits
                    if _memory_type_from_hit(hit) is MemoryType.GOOD_ALGORITHM
                    and _hit_code_artifacts(hit)
                )
                # Task-scope candidates are always retrieved first, then ordered
                # and trimmed by the configured injection selector (topk/weight/
                # random). Other scopes keep their retrieval order untouched.
                if scope == "task" and hits:
                    hits = self._select_task_hits(hits, top_k, context=context)
                scope_hits[scope] = len(hits)
                if pinned:
                    logger.info(
                        "🔎 [long-term memory] scope pinned-fetch completed: sampler={} scope={} agent_id={} "
                        "session_id={} pinned={} matched={} elapsed_ms={:.0f}",
                        sampler,
                        scope,
                        agent_id,
                        session_id,
                        len(pinned_ids),
                        len(hits),
                        outcome["elapsed_ms"],
                    )
                else:
                    logger.info(
                        "🔎 [long-term memory] scope search completed: sampler={} scope={} agent_id={} "
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
                    sections[memory_type].append(
                        _format_hit_for_prompt(
                            hit,
                            scope,
                            memory_type,
                            # Successful code uses the protected elite lane.
                            # Failed code remains bounded diagnostic evidence.
                            include_artifacts=(
                                memory_type is not MemoryType.GOOD_ALGORITHM
                                or self.elite_code_slots <= 0
                            ),
                        )
                    )
            else:
                scope_hits[scope] = 0
                self._record_error(exc)
                if not self.fail_open:
                    raise exc
                logger.warning(
                    "🔎 [long-term memory] scope {} failed: sampler={} scope={} agent_id={} "
                    "session_id={} fail_open={} elapsed_ms={:.0f}: {}",
                    retrieval_kind,
                    sampler,
                    scope,
                    agent_id,
                    session_id,
                    self.fail_open,
                    outcome["elapsed_ms"],
                    exc,
                )

        strategy_note = _format_island_strategy_note(context)
        elite_pool = self._select_elite_code_pool(elite_recall_pool)
        elite_section, elite_algorithm_ids, elite_code_chars = _format_elite_code_section(
            elite_pool,
            slots=self.elite_code_slots if _is_elite_code_enabled(context) else 0,
            char_budget=_effective_elite_budget(
                total_budget=self.context_char_budget,
                configured_budget=self.elite_code_char_budget,
                reserved_prefix=strategy_note,
            ),
        )
        prompt_context = _compose_memory_prompt_context(
            strategy_note=strategy_note,
            elite_section=elite_section,
            sections=sections,
            char_budget=self.context_char_budget,
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        self._stats["last_search_elapsed_ms"] = elapsed_ms
        self._stats["last_search_scope_hits"] = dict(scope_hits)
        self._stats["last_injected_chars"] = len(prompt_context)
        self._stats["last_elite_algorithm_ids"] = elite_algorithm_ids
        self._stats["last_elite_code_chars"] = elite_code_chars
        self._stats["last_elite_code_complete"] = bool(elite_algorithm_ids)
        # Strategy summary for at-a-glance log visibility. 🧠 marks long-term
        # memory; retrieval_mode covers shared scopes (auto search vs manual
        # pinned) and task_injection_mode covers task-memory selection.
        retrieval_label = "manual-pinned" if self.retrieval_mode == "manual" else "auto-search"
        logger.info(
            "🧠 [long-term memory] injection completed: sampler={} retrieval={} "
            "search_strategy={} task_injection={} scope_hits={} deduped_hits={} "
            "injected_chars={} elite_algorithms={} elite_code_chars={} "
            "elite_code_complete={} elapsed_ms={:.0f}",
            sampler,
            retrieval_label,
            self.search_strategy,
            self.task_injection_mode,
            scope_hits,
            len(seen),
            len(prompt_context),
            elite_algorithm_ids,
            elite_code_chars,
            bool(elite_algorithm_ids),
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
            elite_algorithm_ids=elite_algorithm_ids,
            elite_code_chars=elite_code_chars,
            elite_code_complete=bool(elite_algorithm_ids),
            elapsed_ms=round(elapsed_ms),
        ).info("🧠 long-term memory injection event")
        return prompt_context

    def _select_elite_code_pool(
        self,
        recalled: list[tuple[Any, str]],
    ) -> list[tuple[Any, str]]:
        """Order complete-code candidates independently from text memories."""
        unique: list[tuple[Any, str]] = []
        seen: set[str] = set()
        for hit, scope in recalled:
            key = _hit_key(hit)
            if key in seen:
                continue
            seen.add(key)
            unique.append((hit, scope))
        if not unique:
            return []

        count = len(unique)
        objective_order = sorted(
            range(count),
            key=lambda index: (
                _objective_score_from_hit(unique[index][0]) is not None,
                _objective_score_from_hit(unique[index][0]) or 0.0,
                -index,
            ),
            reverse=True,
        )
        objective_rank = {index: rank for rank, index in enumerate(objective_order)}
        candidates: list[TaskMemoryCandidate] = []
        for recall_rank, (hit, scope) in enumerate(unique):
            objective_score = _objective_score_from_hit(hit)
            relevance = (count - recall_rank) / count
            quality = (
                (count - objective_rank[recall_rank]) / count
                if objective_score is not None
                else 0.0
            )
            # Measured quality leads, while semantic relevance still prevents a
            # high-scoring but unrelated implementation from dominating recall.
            priority = 0.65 * quality + 0.35 * relevance
            candidates.append(
                TaskMemoryCandidate(
                    key=_hit_key(hit),
                    score=priority,
                    objective_score=objective_score,
                    timestamp=_hit_timestamp(hit),
                    metadata=_hit_metadata(hit),
                    payload=(hit, scope),
                )
            )

        quality_ordered = sorted(
            candidates,
            key=lambda candidate: candidate.score or 0.0,
            reverse=True,
        )
        ordered = self._elite_code_selector.select(quality_ordered, len(quality_ordered))
        return [candidate.payload for candidate in ordered]

    def _select_task_hits(
        self,
        hits: list[Any],
        limit: int,
        context: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Order and trim task-scope hits using the injection selector.

        Retrieved task-scope hits are wrapped as selector candidates (carrying
        their retrieval score and metadata), passed through the configured
        selector, and unwrapped back into raw hits preserving the selector's
        order.

        Args:
            hits: Raw task-scope search hits from MindMemOS.
            limit: Maximum number of hits to inject.
            context: Optional island and sampler strategy context.

        Returns:
            The selected hits, at most ``limit`` items.
        """
        unique_hits = _deduplicate_hits_by_content(hits)
        candidates = [
            TaskMemoryCandidate(
                key=_hit_key(hit),
                score=_optional_hit_float(_hit_get(hit, "score", None)),
                objective_score=_objective_score_from_hit(hit),
                timestamp=_hit_timestamp(hit),
                metadata=_hit_metadata(hit),
                payload=hit,
            )
            for hit in unique_hits
        ]
        ranked = self._task_selector.select(candidates, len(candidates))
        profile = context.get("island_strategy") if context else None
        if isinstance(profile, dict):
            selected = _select_island_strategy_candidates(ranked, limit, profile)
        elif self.task_injection_mode == "topk":
            selected = _select_balanced_task_candidates(ranked, limit)
        else:
            selected = ranked[:limit]
        return [candidate.payload for candidate in selected]

    def _fetch_pinned_hits(
        self, scope: str, session_id: str, agent_id: str, pinned_ids: list[str]
    ) -> list[Any]:
        """List a scope's card rows and keep only user-pinned cards.

        Used by manual retrieval mode for the shared (user/project) scopes: the
        user selects fixed memories via the memory-management UI and only those
        are injected. The same card filters as the backend/search path are
        applied so only card content rows are returned (not name/tag/message
        property rows), and results are paginated until every pinned id is found.

        ``pinned_ids`` is a flat cross-scope list, so an id pinned in the user
        scope is legitimately absent from the project scope (and vice versa).
        Each scope therefore returns only the pinned cards it owns and does not
        warn about ids that belong to the other scope.

        Args:
            scope: Scope name (``user`` or ``project``).
            session_id: Scope session identifier.
            agent_id: Scope agent identifier.
            pinned_ids: Live pinned memory card ids for this injection.

        Returns:
            The raw card-row hits whose ids are in ``pinned_ids``.
        """
        if not pinned_ids:
            return []
        pinned = set(pinned_ids)
        client = self.client
        list_method = getattr(client.memory, "list", None)
        if list_method is None:
            return []
        filters = {
            "user_id": self.user_id,
            "app_id": self.app_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "entity_type": LLM4AD_MEMORY_ENTITY_TYPE,
            "property_name": LLM4AD_MEMORY_CARD_PROPERTY_FILTER,
        }
        hits: list[Any] = []
        matched: set[str] = set()
        page = 1
        page_size = 50
        max_pages = 20
        while page <= max_pages and matched != pinned:
            result = list_method(
                user_id=self.user_id,
                app_id=self.app_id or None,
                agent_id=agent_id,
                session_id=session_id or None,
                page=page,
                page_size=page_size,
                include_total=False,
                include_inactive=True,
                filters=filters,
            )
            memories = list(getattr(result, "memories", []) or [])
            for item in memories:
                memory_id = str(_hit_get(item, "id", "") or _hit_get(item, "memory_id", "") or "")
                if (
                    memory_id
                    and memory_id in pinned
                    and memory_id not in matched
                    and _hit_is_enabled(item)
                ):
                    hits.append(item)
                    matched.add(memory_id)
            if len(memories) < page_size:
                break
            page += 1
        return hits

    def _scope_presence_key(self, scope: str, session_id: str, agent_id: str) -> tuple[str, str, str, str, str]:
        """Return the complete identity for one remote memory scope."""
        return (self.user_id, self.app_id, scope, session_id, agent_id)

    def _scope_has_active_cards(self, scope: str, session_id: str, agent_id: str) -> bool:
        """Return whether a scope has active cards, falling back to search on probe errors."""
        key = self._scope_presence_key(scope, session_id, agent_id)
        now = time.monotonic()
        cached = self._scope_presence.get(key)
        if cached is not None and now - cached[1] < _SCOPE_PRESENCE_TTL_SECONDS:
            return cached[0]

        client = self.client
        list_method = getattr(client.memory, "list", None)
        if list_method is None:
            return True
        filters: dict[str, Any] = {
            "user_id": self.user_id,
            "app_id": self.app_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "entity_type": LLM4AD_MEMORY_ENTITY_TYPE,
            "property_name": LLM4AD_MEMORY_CARD_PROPERTY_FILTER,
        }
        try:
            result = list_method(
                user_id=self.user_id,
                app_id=self.app_id or None,
                agent_id=agent_id,
                session_id=session_id,
                page=1,
                page_size=1,
                include_total=False,
                include_inactive=False,
                filters=filters,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            logger.warning(
                "🔎 [long-term memory] scope presence probe failed; searching anyway: "
                "scope={} agent_id={} session_id={}: {}",
                scope,
                agent_id,
                session_id,
                exc,
            )
            return True

        if getattr(result, "code", None) not in (None, "ok", "queued"):
            return True
        has_active_cards = any(_hit_is_enabled(item) for item in (getattr(result, "memories", None) or []))
        self._scope_presence[key] = (has_active_cards, now)
        return has_active_cards

    def _mark_task_scope_present(self) -> None:
        """Record a successful task-memory write without waiting for cache expiry."""
        if not self.session_id:
            return
        key = self._scope_presence_key("task", self.session_id, self.agent_id)
        self._scope_presence[key] = (True, time.monotonic())

    def _invalidate_task_scope_presence(self) -> None:
        """Force the next task-scope retrieval to re-check after a mutation."""
        if not self.session_id:
            return
        key = self._scope_presence_key("task", self.session_id, self.agent_id)
        self._scope_presence.pop(key, None)

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
                    "🔎 [long-term memory] query rewrite completed: sampler={} original_chars={} "
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
                "🔎 [long-term memory] query rewrite failed: sampler={} fail_open={} "
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
        self._stats["update_count"] = 0
        self._stats["reinforcement_count"] = 0
        self._stats["search_count"] = 0
        self._stats["last_error"] = None
        self._stats["last_search_elapsed_ms"] = None
        self._stats["last_search_scope_hits"] = {}
        self._stats["last_injected_chars"] = 0
        self._scope_presence.clear()

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
                "memory_type": card.type.value,
                "structured_allowed_property_names": [card.type.value, "name", "tags"],
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
            "structured_allowed_property_names": [card.type.value, "name", "tags"],
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
            filters={
                "entity_type": LLM4AD_MEMORY_ENTITY_TYPE,
                "property_name": LLM4AD_MEMORY_CARD_PROPERTY_FILTER,
            },
        )
        cards: list[MemoryCard] = []
        for item in getattr(result, "memories", []) or []:
            card = _card_from_hit(item)
            if card is not None:
                cards.append(card)
        return cards


def _read_pinned_file(path: Path) -> list[str] | None:
    """Read pinned memory ids from the runtime pinned-memory file.

    Args:
        path: Path to the pinned-memory JSON file.

    Returns:
        The list of pinned ids, or ``None`` when the file is missing or invalid
        (so the caller can fall back to the config snapshot).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    raw = data.get("pinned_card_ids") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return None
    return [str(cid) for cid in raw if str(cid)]


def _write_pinned_file(path: Path, pinned_card_ids: list[str]) -> None:
    """Atomically write pinned memory ids to the runtime pinned-memory file.

    Writes to a temporary file then renames, so a concurrent reader never sees a
    half-written file.

    Args:
        path: Path to the pinned-memory JSON file.
        pinned_card_ids: Pinned memory card ids to persist.
    """
    payload = {"pinned_card_ids": [str(cid) for cid in pinned_card_ids if str(cid)]}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp_path.replace(path)
    except OSError:
        logger.warning("Failed to write pinned memory file: {}", path)


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
    if not isinstance(metadata, dict):
        return {}
    # ``structured_add`` keeps each original block's lossless provenance in
    # ``source_documents``. Recover the latest source metadata for LLM4AD's
    # card view while preserving explicit aggregate fields as authoritative.
    source_metadata: dict[str, Any] = {}
    source_documents = metadata.get("source_documents")
    if isinstance(source_documents, list):
        for source_document in source_documents:
            if not isinstance(source_document, dict):
                continue
            value = source_document.get("metadata")
            if isinstance(value, dict):
                source_metadata.update(value)
    source_metadata.update(metadata)
    wrapped = _llm4ad_wrapped_fields(_raw_memory_text(hit))
    if wrapped:
        if wrapped.get("title") or wrapped.get("name"):
            source_metadata["title"] = wrapped.get("title") or wrapped.get("name")
        tags = _normalize_llm4ad_tags(wrapped.get("tags"))
        if tags:
            source_metadata["tags"] = tags
    return source_metadata


def _hit_is_enabled(hit: Any) -> bool:
    """Return whether a remote hit is active and eligible for injection."""
    metadata = _hit_metadata(hit)
    status = str(_hit_get(hit, "status", "") or metadata.get("status") or "active")
    return status == "active" and metadata.get("enabled", True) is not False


def _hit_timestamp(hit: Any) -> float | None:
    """Extract a recency timestamp (epoch seconds) from a search hit.

    Prefers ``last_update_at``, then ``event_time``, then ``source_timestamp``,
    all formatted as ``%Y-%m-%d %H:%M:%S`` by MindMemOS. Returns ``None`` when no
    field parses, so the candidate is treated as oldest by the weight selector.

    Args:
        hit: Raw MindMemOS search hit (dict or object).

    Returns:
        Epoch seconds as a float, or ``None`` when unavailable.
    """
    metadata = _hit_metadata(hit)
    for key in ("last_update_at", "event_time", "source_timestamp"):
        raw = _hit_get(hit, key, None) or metadata.get(key)
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
        except (TypeError, ValueError):
            continue
    return None


def _optional_hit_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _objective_score_from_hit(hit: Any) -> float | None:
    """Return the algorithm's measured score, separate from retrieval relevance."""
    metadata = _hit_metadata(hit)
    for value in (
        metadata.get("score"),
        metadata.get("objective_score"),
        metadata.get("evaluation_score"),
    ):
        score = _optional_hit_float(value)
        if score is not None:
            return score
    # Older card rows may expose only the original algorithm score at the top
    # level. Keep that compatibility fallback, but never use it to override an
    # explicit metadata score.
    return _optional_hit_float(_hit_get(hit, "score", None))


def _optional_hit_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _raw_memory_text(hit: Any) -> str:
    return str(_hit_get(hit, "memory", None) or _hit_get(hit, "content", None) or hit).strip()


def _parse_memory_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _llm4ad_wrapped_fields(value: Any) -> dict[str, Any]:
    parsed = _parse_memory_mapping(value)
    if parsed is None:
        return {}
    nested = parsed.get("dynamic_property")
    if isinstance(nested, dict):
        parsed = nested
    recognized = {*_MEMORY_EVENT_LABELS, "tags", "name", "title"}
    return {str(key): field_value for key, field_value in parsed.items() if str(key) in recognized}


def _normalize_llm4ad_tags(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else str(value).replace("，", ",").split(",")
    return list(dict.fromkeys(tag for raw in values if (tag := str(raw).strip())))


def _wrapped_memory_type_and_text(hit: Any) -> tuple[MemoryType | None, str | None]:
    fields = _llm4ad_wrapped_fields(_raw_memory_text(hit))
    property_name = str(
        _hit_get(hit, "property_name", None) or _hit_metadata_without_wrapper(hit).get("property_name") or ""
    )
    candidates = [property_name, *_MEMORY_EVENT_LABELS]
    for candidate in dict.fromkeys(candidates):
        try:
            memory_type = MemoryType(candidate)
        except ValueError:
            continue
        value = fields.get(candidate)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return memory_type, text
    return None, None


def _hit_metadata_without_wrapper(hit: Any) -> dict[str, Any]:
    metadata = _hit_get(hit, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _memory_text(hit: Any) -> str:
    _memory_type, content = _wrapped_memory_type_and_text(hit)
    return content or _raw_memory_text(hit)


def _memory_type_from_hit(hit: Any) -> MemoryType:
    wrapped_type, _content = _wrapped_memory_type_and_text(hit)
    if wrapped_type is not None:
        return wrapped_type
    metadata = _hit_metadata(hit)
    # MindMemOS labels first-order properties as the generic ``fact`` memory
    # type. Prefer the first value that is an actual LLM4AD card type,
    # including the property name selected by Structured extraction.
    for raw in (
        _hit_get(hit, "memory_type", None),
        metadata.get("memory_type"),
        _hit_get(hit, "property_name", None),
        metadata.get("property_name"),
    ):
        try:
            return MemoryType(str(raw))
        except ValueError:
            continue
    return MemoryType.GENERAL_INSIGHT


def _hit_key(hit: Any) -> str:
    memory_id = str(_hit_get(hit, "id", "") or _hit_get(hit, "memory_id", "") or "")
    if memory_id:
        return f"id:{memory_id}"
    return f"memory:{_memory_text(hit)}"


def _hit_code_artifacts(hit: Any) -> list[dict[str, Any]]:
    """Return complete code artifacts attached to a structured memory hit."""
    metadata = _hit_metadata(hit)
    structured_content = metadata.get("structured_content")
    artifacts = (
        structured_content.get("artifacts", [])
        if isinstance(structured_content, dict)
        else []
    )
    return [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and str(artifact.get("type") or "").strip() == "code"
        and str(artifact.get("content") or "").strip()
    ]


def _format_hit_for_prompt(
    hit: Any,
    scope: str,
    memory_type: MemoryType,
    *,
    include_artifacts: bool = True,
) -> str:
    metadata = _hit_metadata(hit)
    text = _memory_text(hit)
    title = str(
        metadata.get("title")
        or metadata.get("entity_name")
        or _hit_get(hit, "title", "")
        or ""
    ).strip()
    score = _objective_score_from_hit(hit)
    generation = _optional_hit_int(metadata.get("generation"))
    algorithm_id = str(metadata.get("algorithm_id") or "").strip()

    attrs = [f"scope: {scope}", f"type: {memory_type.value}"]
    if score is not None:
        attrs.append(f"objective_score: {score:.4f}")
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
        # Preserve the established evidence text consumed by existing logs and
        # tests; the attribute label above makes clear this is the measured
        # objective score rather than retrieval relevance.
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
    if memory_type == MemoryType.GOOD_ALGORITHM and include_artifacts:
        artifacts = _hit_code_artifacts(hit)
        rendered_artifacts: list[str] = []
        artifact_budget = 12000
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            content = str(artifact.get("content") or "")
            if not content.strip() or artifact_budget <= 0:
                continue
            artifact_type = str(artifact.get("type") or "").strip()
            artifact_id = str(artifact.get("artifact_id") or "source")
            language = str(artifact.get("language") or "")
            available = max(0, artifact_budget - len(artifact_id) - 32)
            body = content
            if len(body) > available:
                body = body[:available].rstrip() + "\n[artifact shortened for prompt budget]"
            if artifact_type == "code":
                rendered = f"  `{artifact_id}`\n```{language}\n{body}\n```"
            else:
                rendered = f"  `{artifact_id}` ({artifact_type or 'evidence'}):\n{body}"
            rendered_artifacts.append(rendered)
            artifact_budget -= len(rendered)
        if rendered_artifacts:
            lines.append("  Inherited implementation evidence:")
            lines.extend(rendered_artifacts)
    elif memory_type == MemoryType.ERROR_REFLECTION and include_artifacts:
        artifacts = _hit_code_artifacts(hit)
        rendered_artifacts = []
        artifact_budget = 2400
        for artifact in artifacts:
            if artifact_budget <= 0:
                break
            content = str(artifact.get("content") or "")
            artifact_id = str(artifact.get("artifact_id") or "source")
            language = str(artifact.get("language") or "")
            available = max(0, artifact_budget - len(artifact_id) - 80)
            body = content
            if len(body) > available:
                body = body[:available].rstrip() + "\n[failure evidence shortened]"
            rendered = f"  `{artifact_id}`\n```{language}\n{body}\n```"
            rendered_artifacts.append(rendered)
            artifact_budget -= len(rendered)
        if rendered_artifacts:
            lines.append("  Failure implementation evidence (do not inherit verbatim):")
            lines.extend(rendered_artifacts)
    return "\n".join(lines)


def _is_elite_code_enabled(context: dict[str, Any] | None) -> bool:
    """Use the dedicated code lane unless this is an explicitly memory-free island."""
    profile = context.get("island_strategy") if context else None
    if not isinstance(profile, dict):
        return True
    return str(profile.get("memory_policy", "")).strip().lower() != "none"


def _effective_elite_budget(
    *,
    total_budget: int,
    configured_budget: int,
    reserved_prefix: str,
) -> int:
    """Reserve code space inside the total context without displacing the policy note."""
    if configured_budget <= 0:
        return 0
    if total_budget <= 0:
        return configured_budget
    prefix_cost = len(reserved_prefix) + (2 if reserved_prefix else 0)
    return max(0, min(configured_budget, total_budget - prefix_cost))


def _artifact_source_block_id(
    artifact: dict[str, Any], source_block_ids: set[str]
) -> str:
    """Resolve an artifact to the structured source version that introduced it."""
    explicit = str(artifact.get("source_block_id") or "").strip()
    if explicit:
        return explicit
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    matches = [
        block_id
        for block_id in source_block_ids
        if artifact_id == block_id or artifact_id.startswith(f"{block_id}:")
    ]
    return max(matches, key=len) if matches else ""


def _elite_candidate_blocks(hit: Any, scope: str) -> list[tuple[str, str, int]]:
    """Render complete implementation versions from a possibly merged memory card."""
    artifacts = _hit_code_artifacts(hit)
    if not artifacts:
        return []
    metadata = _hit_metadata(hit)
    raw_metadata = _hit_get(hit, "metadata", None)
    source_documents = (
        raw_metadata.get("source_documents", []) if isinstance(raw_metadata, dict) else []
    )
    source_metadata: dict[str, dict[str, Any]] = {}
    for source_document in source_documents:
        if not isinstance(source_document, dict):
            continue
        block_id = str(source_document.get("block_id") or "").strip()
        if not block_id:
            continue
        document_metadata = source_document.get("metadata")
        source_metadata[block_id] = (
            dict(document_metadata) if isinstance(document_metadata, dict) else {}
        )

    source_block_ids = set(source_metadata)
    grouped_artifacts: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        block_id = _artifact_source_block_id(artifact, source_block_ids)
        if not block_id and len(source_block_ids) == 1:
            block_id = next(iter(source_block_ids))
        # Legacy cards have no source linkage. Their files still belong to one
        # implementation and therefore remain inseparable.
        grouped_artifacts.setdefault(block_id or "__legacy__", []).append(artifact)

    aggregate_title = str(
        metadata.get("title")
        or metadata.get("entity_name")
        or _hit_get(hit, "title", "")
        or "Historical elite"
    ).strip()
    aggregate_algorithm_id = str(
        metadata.get("algorithm_id") or _hit_get(hit, "id", "") or ""
    ).strip()
    aggregate_score = _objective_score_from_hit(hit)
    candidates: list[tuple[str, str, int, float | None, int]] = []
    for order, (block_id, version_artifacts) in enumerate(grouped_artifacts.items()):
        version_metadata = source_metadata.get(block_id, {})
        title = str(
            version_metadata.get("title")
            or version_metadata.get("name")
            or aggregate_title
        ).strip()
        algorithm_id = str(
            version_metadata.get("algorithm_id") or aggregate_algorithm_id
        ).strip()
        score = None
        for key in ("score", "objective_score", "evaluation_score"):
            score = _optional_hit_float(version_metadata.get(key))
            if score is not None:
                break
        if score is None:
            score = aggregate_score

        lines = [f"## {title}", f"Scope: {scope}"]
        if score is not None:
            lines.append(f"Objective score: {score:.6f}")
        if algorithm_id:
            lines.append(f"Algorithm ID: {algorithm_id}")
        lines.append("Inherited implementation evidence (complete):")
        code_chars = 0
        for artifact in version_artifacts:
            artifact_id = str(artifact.get("artifact_id") or "source")
            language = str(artifact.get("language") or "")
            content = str(artifact.get("content") or "")
            code_chars += len(content)
            lines.extend((f"`{artifact_id}`", f"```{language}", content.rstrip(), "```"))
        candidates.append(("\n".join(lines), algorithm_id, code_chars, score, order))

    # A structured merge may retain several historical implementations. Try
    # the strongest measured source first while keeping stable insertion order
    # for cards whose objective is unavailable.
    candidates.sort(
        key=lambda item: (
            item[3] is not None,
            item[3] if item[3] is not None else float("-inf"),
            -item[4],
        ),
        reverse=True,
    )
    return [(block, algorithm_id, code_chars) for block, algorithm_id, code_chars, _, _ in candidates]


def _format_elite_code_section(
    candidates: list[tuple[Any, str]],
    *,
    slots: int,
    char_budget: int,
) -> tuple[str, list[str], int]:
    """Consume selector-ordered candidates without re-ranking them by objective score."""
    if slots <= 0 or char_budget <= 0 or not candidates:
        return "", [], 0

    header = "# Historical Elite Implementation"
    rendered: list[str] = []
    algorithm_ids: list[str] = []
    code_chars = 0
    seen: set[str] = set()
    for hit, scope in candidates:
        key = _hit_key(hit)
        if key in seen:
            continue
        seen.add(key)
        versions = _elite_candidate_blocks(hit, scope)
        if not versions:
            continue
        selected_version: tuple[str, str, int] | None = None
        for version in versions:
            block, _algorithm_id, _candidate_code_chars = version
            proposed = "\n\n".join((header, *rendered, block))
            if len(proposed) <= char_budget:
                selected_version = version
                break
        if selected_version is None:
            # Never provide syntactically incomplete evidence. A later,
            # smaller selector-approved card may still fit the reserved slot.
            continue
        block, algorithm_id, candidate_code_chars = selected_version
        rendered.append(block)
        algorithm_ids.append(algorithm_id or key)
        code_chars += candidate_code_chars
        if len(rendered) >= slots:
            break
    if not rendered:
        return "", [], 0
    return "\n\n".join((header, *rendered)), algorithm_ids, code_chars


def _sampler_name(context: dict[str, Any] | None) -> str:
    if not context:
        return "unknown"
    sampler = context.get("sampler")
    return str(sampler).strip() or "unknown"


def _format_island_strategy_note(context: dict[str, Any] | None) -> str:
    """Render the island's concrete memory and search responsibility."""
    profile = context.get("island_strategy") if context else None
    if not isinstance(profile, dict):
        return ""
    memory_policy = str(profile.get("memory_policy", "")).strip().lower()
    if memory_policy == "success_only":
        return (
            "### Island search policy\n"
            "This island advances proven mechanisms aggressively. Use only successful "
            "algorithm memories as evidence and seek a measurable improvement rather than "
            "repeating them verbatim."
        )
    if memory_policy == "corrective":
        success_ratio = min(
            1.0, max(0.0, _strategy_number(profile, "success_memory_ratio", 0.6))
        )
        error_ratio = min(
            1.0, max(0.0, _strategy_number(profile, "error_memory_ratio", 0.4))
        )
        return (
            "### Island search policy\n"
            "This island improves proven mechanisms while correcting known failures. "
            f"The memory mix targets {success_ratio:.0%} successful evidence and "
            f"at most {error_ratio:.0%} error reflections; treat errors as bounded "
            "constraints, not as the main design objective."
        )
    if memory_policy == "none":
        return ""
    try:
        exploration = min(1.0, max(0.0, float(profile.get("exploration", 0.5))))
        exploitation = min(1.0, max(0.0, float(profile.get("exploitation", 0.5))))
    except (TypeError, ValueError):
        return ""
    return (
        "### Island search policy\n"
        f"Exploration weight: {exploration:.2f}; exploitation weight: {exploitation:.2f}. "
        "Use successful memories as evidence, error reflections as constraints, and vary the "
        "mechanism in proportion to the exploration weight instead of copying a recalled design."
    )


def _build_retrieval_query(query: str, context: dict[str, Any] | None) -> str:
    """Build a compact retrieval query from sampler context without an LLM call."""
    lines: list[str] = []
    background = " ".join(str(query or "").split())
    if background:
        lines.append(background[:800])
    if context:
        for key in (
            "sampler",
            "generation",
            "island_id",
            "parent_score",
            "parent_description",
            "parent_1_score",
            "parent_1_description",
            "parent_2_score",
            "parent_2_description",
            "parents",
            "cluster_id",
            "cluster_id_1",
            "cluster_id_2",
        ):
            value = context.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, dict | list | tuple):
                text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
            else:
                text = str(value)
            text = " ".join(text.split())
            if not text:
                continue
            lines.append(f"{key}: {text[:500]}")
    retrieval_query = "\n".join(lines).strip()
    return retrieval_query[:2400] or query


def _deduplicate_hits_by_content(hits: list[Any]) -> list[Any]:
    """Drop exact textual duplicates while preserving recall rank and memory type."""
    unique: list[Any] = []
    seen: set[str] = set()
    for hit in hits:
        normalized = " ".join(_memory_text(hit).casefold().split())
        key = f"{_memory_type_from_hit(hit).value}:{normalized}"
        if normalized and key in seen:
            continue
        if normalized:
            seen.add(key)
        unique.append(hit)
    return unique


def _select_balanced_task_candidates(
    ranked: list[TaskMemoryCandidate],
    limit: int,
) -> list[TaskMemoryCandidate]:
    """Keep relevance order while preventing one memory type from crowding out success."""
    if limit <= 0 or not ranked:
        return []

    selected: list[TaskMemoryCandidate] = []
    selected_keys: set[str] = set()
    type_counts: dict[MemoryType, int] = dict.fromkeys(MemoryType, 0)
    max_per_type = max(1, (limit + 1) // 2)

    # A successful design is the positive anchor for the next improvement. It
    # must not disappear merely because many failure reflections rank ahead of
    # it in a small raw Top-K window.
    successful = next(
        (
            candidate
            for candidate in ranked
            if _memory_type_from_hit(candidate.payload) is MemoryType.GOOD_ALGORITHM
        ),
        None,
    )
    if successful is not None:
        selected.append(successful)
        selected_keys.add(successful.key)
        type_counts[MemoryType.GOOD_ALGORITHM] += 1

    # First pass enforces type coverage. With the default limit of five, no
    # single type can consume more than three slots while alternatives exist.
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if candidate.key in selected_keys:
            continue
        memory_type = _memory_type_from_hit(candidate.payload)
        if type_counts[memory_type] >= max_per_type:
            continue
        selected.append(candidate)
        selected_keys.add(candidate.key)
        type_counts[memory_type] += 1

    # If the pool contains only one type, fill the remaining slots rather than
    # returning fewer memories than requested.
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if candidate.key in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(candidate.key)

    return selected


def _select_island_strategy_candidates(
    ranked: list[TaskMemoryCandidate],
    limit: int,
    profile: dict[str, Any],
) -> list[TaskMemoryCandidate]:
    """Select task memories using the island's explicit responsibility.

    New profiles use strict type quotas: success-only islands never backfill
    from errors, corrective islands cap the error share, and independent search
    receives no task memory. Profiles from older checkpoints fall back to the
    previous continuous ranking behavior below.
    """
    if limit <= 0 or not ranked:
        return []

    memory_policy = str(profile.get("memory_policy", "")).strip().lower()
    if memory_policy == "none":
        return []
    if memory_policy in {"success_only", "corrective"}:
        successful = [
            candidate
            for candidate in ranked
            if _memory_type_from_hit(candidate.payload) is MemoryType.GOOD_ALGORITHM
        ]
        if memory_policy == "success_only":
            return successful[:limit]

        success_ratio = min(
            1.0, max(0.0, _strategy_number(profile, "success_memory_ratio", 0.6))
        )
        error_ratio = min(
            1.0, max(0.0, _strategy_number(profile, "error_memory_ratio", 0.4))
        )
        ratio_total = success_ratio + error_ratio
        if ratio_total <= 0:
            return []
        success_slots = min(limit, round(limit * success_ratio / ratio_total))
        error_slots = max(0, limit - success_slots)
        errors = [
            candidate
            for candidate in ranked
            if _memory_type_from_hit(candidate.payload) is MemoryType.ERROR_REFLECTION
        ]
        # Do not backfill a missing success slot with another error. This keeps
        # a sparse positive pool from being overwhelmed by the failure tail.
        return successful[:success_slots] + errors[:error_slots]

    def number(key: str, default: float) -> float:
        try:
            return float(profile.get(key, default))
        except (TypeError, ValueError):
            return default

    exploration = min(1.0, max(0.0, number("exploration", 0.5)))
    exploitation = min(1.0, max(0.0, number("exploitation", 1.0 - exploration)))
    type_weights = {
        MemoryType.GOOD_ALGORITHM: number("success_memory_weight", 1.0),
        MemoryType.ERROR_REFLECTION: number("error_memory_weight", 1.0),
        MemoryType.DOMAIN_KNOWLEDGE: 1.0,
        MemoryType.GENERAL_INSIGHT: number("novelty_memory_weight", 1.0),
    }
    rank_by_key = {candidate.key: index for index, candidate in enumerate(ranked)}

    def tokens(candidate: TaskMemoryCandidate) -> set[str]:
        text = " ".join(_memory_text(candidate.payload).casefold().split())
        return {token.strip(".,:;()[]{}<>`'") for token in text.split() if token.strip()}

    token_map = {candidate.key: tokens(candidate) for candidate in ranked}
    selected: list[TaskMemoryCandidate] = []
    selected_keys: set[str] = set()

    # Retain one positive anchor so a novelty-heavy island does not learn only
    # from a long tail of failures.
    successful = next(
        (
            candidate
            for candidate in ranked
            if _memory_type_from_hit(candidate.payload) is MemoryType.GOOD_ALGORITHM
        ),
        None,
    )
    if successful is not None:
        selected.append(successful)
        selected_keys.add(successful.key)

    while len(selected) < min(limit, len(ranked)):
        remaining = [candidate for candidate in ranked if candidate.key not in selected_keys]
        if not remaining:
            break

        def value(candidate: TaskMemoryCandidate) -> tuple[float, int]:
            rank = rank_by_key[candidate.key]
            relevance = (len(ranked) - rank) / len(ranked)
            candidate_tokens = token_map[candidate.key]
            similarity = max(
                (
                    len(candidate_tokens & token_map[item.key])
                    / max(1, len(candidate_tokens | token_map[item.key]))
                    for item in selected
                ),
                default=0.0,
            )
            novelty = 1.0 - similarity
            memory_type = _memory_type_from_hit(candidate.payload)
            type_weight = type_weights[memory_type]
            combined = (
                0.55 * exploitation * relevance
                + 0.30 * exploration * novelty
                + 0.15 * type_weight
            )
            return combined, -rank

        chosen = max(remaining, key=value)
        selected.append(chosen)
        selected_keys.add(chosen.key)

    return selected


def _strategy_number(profile: dict[str, Any], key: str, default: float) -> float:
    """Read a numeric strategy field without letting malformed metadata fail recall."""
    try:
        return float(profile.get(key, default))
    except (TypeError, ValueError):
        return default


def _card_from_hit(hit: Any) -> MemoryCard | None:
    memory_id = str(_hit_get(hit, "id", "") or _hit_get(hit, "memory_id", "") or "")
    content = _memory_text(hit)
    if not memory_id or not content:
        return None
    metadata = _hit_metadata(hit)
    raw_metadata = _hit_metadata_without_wrapper(hit)
    memory_type = _memory_type_from_hit(hit)
    title = str(
        raw_metadata.get("entity_name")
        or metadata.get("entity_name")
        or raw_metadata.get("title")
        or metadata.get("title")
        or content.splitlines()[0][:80]
        or "MindMemOS memory"
    )
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


def _structured_operation_events(result: Any) -> list[dict[str, Any]]:
    """Return substantive LLM4AD card operations from a structured Add result."""
    memories = getattr(result, "memories", None)
    if memories is None and isinstance(result, dict):
        data = result.get("data") or {}
        memories = data.get("memories") if isinstance(data, dict) else result.get("memories")
    if not isinstance(memories, list | tuple):
        return []

    events: list[dict[str, Any]] = []
    for item in memories:
        def get_value(key: str, default: Any = None, *, event_item: Any = item) -> Any:
            if isinstance(event_item, dict):
                return event_item.get(key, default)
            return getattr(event_item, key, default)

        property_name = str(get_value("property_name", "") or "").strip()
        if property_name and property_name not in LLM4AD_MEMORY_CARD_PROPERTIES:
            continue
        memory_id = str(get_value("memory_id", "") or get_value("id", "") or "").strip()
        if not memory_id:
            continue
        operation = str(get_value("operation", "add") or "add").strip()
        if operation not in {"add", "update", "reinforcement"}:
            operation = "add"
        source_block_ids = [
            str(value)
            for value in (get_value("source_block_ids", []) or [])
            if str(value)
        ]
        related_memory_ids = [
            str(value)
            for value in (get_value("related_memory_ids", []) or [])
            if str(value)
        ]
        events.append(
            {
                "operation": operation,
                "memory_id": memory_id,
                "property_name": property_name or None,
                "source_block_ids": source_block_ids,
                "related_memory_ids": related_memory_ids,
            }
        )
    return events


class _HttpMindMemOSMemoryResource:
    """Minimal HTTP client for MindMemOS public memory APIs."""

    def __init__(self, parent: _HttpMindMemOSClient) -> None:
        self._parent = parent

    def add(self, **kwargs: Any) -> Any:
        payload = dict(kwargs)
        if "messages" in payload:
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
        # ``post`` already unwraps the ``data.memories`` envelope into a
        # SimpleNamespace with a ``.memories`` list, so return it directly (the
        # same as ``search``). Re-parsing here would double-unwrap and drop all
        # results, silently yielding zero memories.
        return self._parent.post("/v1/memory/list", kwargs)

    def delete(self, **kwargs: Any) -> Any:
        memory_id = kwargs.get("memory_id") or kwargs.get("id")
        return self._parent.post(
            "/v1/memory/delete",
            {"memory_id": memory_id, "hard": bool(kwargs.get("hard", False))},
        )

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


def _format_sections_with_budget(
    sections: dict[MemoryType, list[str]],
    char_budget: int,
) -> str:
    """Pack whole guidance cards into the remaining budget without cutting code blocks."""
    if char_budget <= 0:
        return ""
    labels = [
        (MemoryType.GOOD_ALGORITHM, "Successful Patterns"),
        (MemoryType.ERROR_REFLECTION, "Error Reflections"),
        (MemoryType.DOMAIN_KNOWLEDGE, "Domain Knowledge"),
        (MemoryType.GENERAL_INSIGHT, "General Insights"),
    ]
    parts: list[str] = []
    omitted = False
    for memory_type, label in labels:
        section_lines: list[str] = []
        for line in sections.get(memory_type) or []:
            candidate_lines = [*section_lines, line]
            candidate_section = f"# {label}\n" + "\n".join(candidate_lines)
            candidate_parts = [*parts, candidate_section]
            if len("\n\n".join(candidate_parts)) <= char_budget:
                section_lines.append(line)
                continue
            omitted = True
        if section_lines:
            parts.append(f"# {label}\n" + "\n".join(section_lines))

    rendered = "\n\n".join(parts)
    marker = "[Additional memory guidance omitted for prompt budget]"
    if omitted:
        proposed = "\n\n".join(part for part in (rendered, marker) if part)
        if len(proposed) <= char_budget:
            rendered = proposed
    return rendered


def _compose_memory_prompt_context(
    *,
    strategy_note: str,
    elite_section: str,
    sections: dict[MemoryType, list[str]],
    char_budget: int,
) -> str:
    """Compose protected elite code first, then fill only the remaining guidance budget."""
    fixed_parts = [part for part in (strategy_note, elite_section) if part]
    fixed = "\n\n".join(fixed_parts)
    if char_budget <= 0:
        guidance = _format_sections(sections)
        return "\n\n".join(part for part in (fixed, guidance) if part)

    # The elite renderer is already constrained by the budget remaining after
    # the policy note. Keep this guard for unusually tiny user budgets without
    # ever slicing an elite code block into invalid source.
    if len(fixed) > char_budget:
        if elite_section and len(elite_section) <= char_budget:
            fixed = elite_section
        else:
            return strategy_note[:char_budget].rstrip()

    separator_cost = 2 if fixed else 0
    remaining = max(0, char_budget - len(fixed) - separator_cost)
    guidance = _format_sections_with_budget(sections, remaining)
    return "\n\n".join(part for part in (fixed, guidance) if part)


def _trim_context(context: str, char_budget: int) -> str:
    if char_budget <= 0 or len(context) <= char_budget:
        return context
    return context[:char_budget].rstrip() + "\n\n[Memory context truncated]"
