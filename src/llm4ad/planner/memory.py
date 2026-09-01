"""Memory system for the planner layer.

Provides persistent, card-based memory that supports:
- Static memory cards loaded from config or YAML files
- Auto-extraction of insights from evaluated algorithms (good & bad)
- Prompt context generation for injecting knowledge into sampler prompts
- YAML-based persistence to a dedicated memory/ subdirectory
"""

import hashlib
import importlib
import importlib.util
import re
import sys
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from llm4ad.utils.registry import Registrable


class MemoryType(Enum):
    """Types of memory content."""

    GOOD_ALGORITHM = "good_algorithm"  # Successful algorithm designs
    ERROR_REFLECTION = "error_reflection"  # Coding errors and fixes
    DOMAIN_KNOWLEDGE = "domain_knowledge"  # Task background
    GENERAL_INSIGHT = "general_insight"  # General algorithm insights


class MemoryEntry(BaseModel):
    """A single memory entry (runtime representation).

    Represents a piece of information stored in the memory system,
    which can be retrieved later for generating new insights.
    """

    id: str
    memory_type: MemoryType
    content: str  # Text content (description, reflection, etc.)
    embedding: list[float] | None = None  # For semantic search (serialized)
    score: float | None = None  # Associated score if applicable
    generation: int = 0  # Generation when this was added
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_hash(self) -> str:
        """Get a hash of this entry's content for deduplication."""
        content = f"{self.memory_type.value}:{self.content}"
        return hashlib.md5(content.encode()).hexdigest()


class MemoryCard(BaseModel):
    """A persistent memory card that can be stored as YAML and loaded at init.

    MemoryCard is the serialization/persistence layer for memory entries.
    Each card is stored as an individual YAML file in the memory/ directory.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: MemoryType
    title: str  # Short human-readable title
    content: str  # Main textual content
    enabled: bool = True  # Whether this card can be injected into prompts
    source: Literal["static", "auto"] = "static"

    # Optional structured fields
    score: float | None = None  # Associated fitness score
    generation: int | None = None  # Generation when extracted
    algorithm_id: str | None = None  # Linked algorithm ID
    tags: list[str] = Field(default_factory=list)

    # Metadata
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_entry(self) -> MemoryEntry:
        """Convert this card to a runtime MemoryEntry."""
        return MemoryEntry(
            id=self.id,
            memory_type=self.type,
            content=self.content,
            score=self.score,
            generation=self.generation or 0,
            metadata={
                "title": self.title,
                "source": self.source,
                "enabled": self.enabled,
                "tags": self.tags,
                "algorithm_id": self.algorithm_id,
                **self.metadata,
            },
        )

    @classmethod
    def from_entry(cls, entry: MemoryEntry, source: str = "auto") -> "MemoryCard":
        """Create a MemoryCard from a runtime MemoryEntry.

        Args:
            entry: Runtime memory entry.
            source: Origin of the card ("static" or "auto").

        Returns:
            MemoryCard instance.
        """
        title = entry.metadata.get("title", entry.content[:60].strip())
        tags = entry.metadata.get("tags", [])
        algorithm_id = entry.metadata.get("algorithm_id")
        enabled = entry.metadata.get("enabled", True)
        # Remove keys that are stored as top-level fields
        extra_metadata = {
            k: v for k, v in entry.metadata.items()
            if k not in ("title", "source", "enabled", "tags", "algorithm_id")
        }

        return cls(
            id=entry.id,
            type=entry.memory_type,
            title=title,
            content=entry.content,
            source=source,
            enabled=bool(enabled),
            score=entry.score,
            generation=entry.generation,
            algorithm_id=algorithm_id,
            tags=tags,
            metadata=extra_metadata,
        )

    @classmethod
    def from_yaml_file(cls, path: Path) -> "MemoryCard":
        """Load a single card from a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            MemoryCard instance.
        """
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Convert string type to MemoryType enum
        if isinstance(data.get("type"), str):
            data["type"] = MemoryType(data["type"])

        return cls.model_validate(data)

    def to_yaml_file(self, directory: Path) -> Path:
        """Save this card as a YAML file.

        Args:
            directory: Directory to save the YAML file in.

        Returns:
            Path to the written file.
        """
        directory.mkdir(parents=True, exist_ok=True)

        # Build filename: {type}_{sanitized_title}_{id}.yaml
        sanitized_title = re.sub(r"[^a-zA-Z0-9]+", "_", self.title).strip("_").lower()
        sanitized_title = sanitized_title[:40]  # Limit length
        filename = f"{self.type.value}_{sanitized_title}_{self.id}.yaml"
        filepath = directory / filename

        # Serialize with type as string value
        data = self.model_dump(mode="json")
        data["type"] = self.type.value

        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return filepath


class BaseMemoryExtractor(ABC, Registrable):
    """Abstract interface for memory card extraction modules."""

    def __init__(self, provider: Any, config: Any):
        """Initialize extractor.

        Args:
            provider: LLM provider or extraction backend dependency.
            config: Extractor configuration object.
        """
        self.provider = provider
        self.config = config

    @abstractmethod
    def reset_generation(self) -> None:
        """Reset any per-generation extractor state."""
        ...

    @abstractmethod
    async def extract_from_good(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        """Extract a memory card from a well-performing algorithm."""
        ...

    @abstractmethod
    async def extract_from_bad(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        """Extract a memory card from a poorly-performing algorithm."""
        ...

    @abstractmethod
    async def extract_from_failure(
        self,
        algorithm: Any,
        error: str,
        generation: int,
        background: str = "",
    ) -> MemoryCard | None:
        """Extract a memory card from an evaluation failure."""
        ...


class BaseMemory(ABC, Registrable):
    """Abstract interface for pluggable memory modules."""

    def __init__(self, config: dict[str, Any]):
        """Initialize memory module.

        Args:
            config: Memory configuration dictionary.
        """
        self.config = config
        self.extractor: BaseMemoryExtractor | None = None

    @abstractmethod
    def set_memory_dir(self, memory_dir: Path) -> None:
        """Set the persistence directory for memory data."""
        ...

    @abstractmethod
    def load_static_cards(self, inline_cards: list[Any]) -> None:
        """Load static memory cards from configuration."""
        ...

    @abstractmethod
    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
        """Add a memory card to the module."""
        ...

    async def add_cards(
        self,
        cards: list[MemoryCard],
        persist: bool | None = None,
    ) -> None:
        """Add a group of cards, preserving legacy backends through a serial fallback."""
        for card in cards:
            await self.add_card(card, persist=persist)

    @abstractmethod
    def list_cards(self) -> list[MemoryCard]:
        """Return memory cards managed by this module."""
        ...

    @abstractmethod
    async def upsert_card(self, card: MemoryCard, persist: bool | None = None) -> MemoryCard:
        """Create or update a memory card."""
        ...

    @abstractmethod
    async def delete_card(self, card_id: str) -> None:
        """Delete a memory card by id."""
        ...

    @abstractmethod
    async def set_card_enabled(self, card_id: str, enabled: bool) -> MemoryCard:
        """Enable or disable a memory card."""
        ...

    @abstractmethod
    def get_prompt_context(self, query: str = "", max_cards: int | None = None) -> str:
        """Build a prompt-ready memory context string."""
        ...

    async def aget_prompt_context(
        self,
        query: str = "",
        max_cards: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build prompt-ready memory context from async sampler paths.

        Local memory remains synchronous; remote implementations can override
        this to perform async query planning before retrieval.
        """
        del context
        return self.get_prompt_context(query=query, max_cards=max_cards)

    def set_query_provider(self, provider: Any) -> None:
        """Attach an optional LLM provider for remote query rewriting."""
        del provider

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Clear all runtime memory state."""
        ...


class NullMemory(BaseMemory):
    """No-op memory used when a task explicitly disables memory.

    Keeping the regular memory interface lets planners and samplers remain
    unaware of the ablation mode while guaranteeing that no cards are loaded,
    persisted, retrieved, injected, or extracted.
    """

    @property
    def extractor(self) -> None:
        """Return no extractor while memory is disabled."""
        return None

    @extractor.setter
    def extractor(self, value: Any) -> None:
        del value

    def set_memory_dir(self, memory_dir: Path) -> None:
        """Ignore persistence directory configuration."""
        del memory_dir

    def load_static_cards(self, inline_cards: list[Any]) -> None:
        """Ignore static cards so they cannot enter disabled tasks."""
        del inline_cards

    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
        """Discard a card instead of storing it."""
        del card, persist

    def list_cards(self) -> list[MemoryCard]:
        """Return an empty card collection."""
        return []

    async def upsert_card(
        self,
        card: MemoryCard,
        persist: bool | None = None,
    ) -> MemoryCard:
        """Return the supplied card without storing it."""
        del persist
        return card

    async def delete_card(self, card_id: str) -> None:
        """Ignore deletion because disabled memory stores no cards."""
        del card_id

    async def set_card_enabled(self, card_id: str, enabled: bool) -> MemoryCard:
        """Reject updates because disabled memory has no cards."""
        del enabled
        raise KeyError(f"Memory is disabled; card '{card_id}' is unavailable")

    def get_prompt_context(self, query: str = "", max_cards: int | None = None) -> str:
        """Return no prompt context."""
        del query, max_cards
        return ""

    def get_stats(self) -> dict[str, Any]:
        """Report the disabled, empty state."""
        return {"enabled": False, "total_cards": 0}

    def clear(self) -> None:
        """Keep the empty state unchanged."""
        return None


@BaseMemoryExtractor.register("llm_card_extractor")
class MemoryExtractor(BaseMemoryExtractor):
    """LLM-based extraction of memory cards from evaluated algorithms.

    Extracts two types of insights:
    - From good algorithms: what worked and why (GOOD_ALGORITHM cards)
    - From bad algorithms: what to avoid (ERROR_REFLECTION cards)
    """

    def __init__(self, provider: Any, config: Any):
        """Initialize the memory extractor.

        Args:
            provider: LLM provider for extraction calls.
            config: AutoExtractionConfig instance.
        """
        super().__init__(provider, config)
        self._best_score: float = float("-inf")
        self._cards_this_gen: int = 0

    def _is_good(self, score: float, population_scores: list[float]) -> bool:
        """Check if an algorithm's score qualifies as 'good'.

        Args:
            score: The algorithm's score.
            population_scores: Sorted list of all population scores.

        Returns:
            True if the score meets the good threshold.
        """
        # Absolute threshold takes precedence
        if self.config.good_score_threshold is not None:
            return score >= self.config.good_score_threshold

        # Relative threshold: percentile-based
        if not population_scores:
            return False
        threshold_idx = int(len(population_scores) * self.config.good_relative_threshold)
        threshold_idx = min(threshold_idx, len(population_scores) - 1)
        threshold_score = sorted(population_scores)[threshold_idx]
        return score >= threshold_score

    def _is_bad(self, score: float, population_scores: list[float]) -> bool:
        """Check if an algorithm's score qualifies as 'bad'.

        Args:
            score: The algorithm's score.
            population_scores: Sorted list of all population scores.

        Returns:
            True if the score meets the bad threshold.
        """
        # Absolute threshold takes precedence
        if self.config.bad_score_threshold is not None:
            return score <= self.config.bad_score_threshold

        # Relative threshold: percentile-based
        if not population_scores:
            return False
        threshold_idx = int(len(population_scores) * self.config.bad_relative_threshold)
        threshold_idx = max(threshold_idx, 0)
        threshold_score = sorted(population_scores)[threshold_idx]
        return score <= threshold_score

    def _budget_available(self) -> bool:
        """Check if per-generation extraction budget is available."""
        return self._cards_this_gen < self.config.max_cards_per_generation

    def _is_new_global_best(self, score: float, population_scores: list[float]) -> bool:
        """Return whether score is a strictly improved population-best score."""
        if not population_scores or score != max(population_scores):
            return False
        if score <= self._best_score:
            return False
        self._best_score = score
        return True

    async def extract_from_good(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> "MemoryCard | None":
        """Extract positive insight from a well-performing algorithm.

        Args:
            algorithm: The evaluated algorithm (Algorithm instance).
            population: Current population for percentile calculation.
            generation: Current generation number.
            background: Problem background description.

        Returns:
            MemoryCard with positive insight, or None if skipped.
        """
        if not self.config.enabled or not self.config.extract_good:
            return None
        if not self._budget_available():
            return None
        if not algorithm.is_evaluated():
            return None

        population_scores = [
            a.score for a in population if a.is_evaluated() and a.score is not None
        ]
        if not self._is_good(algorithm.score, population_scores):
            return None
        if not self._is_new_global_best(algorithm.score, population_scores):
            return None

        from llm4ad.planner.sampler.prompt_templates import EXTRACT_GOOD_MEMORY

        evaluation_summary = (
            f"Score: {algorithm.score:.4f}"
        )
        if algorithm.evaluation and algorithm.evaluation.metrics:
            metrics_str = ", ".join(
                f"{k}: {v:.4f}" for k, v in algorithm.evaluation.metrics.items()
            )
            evaluation_summary += f"\nMetrics: {metrics_str}"

        prompt = EXTRACT_GOOD_MEMORY.format(
            background=background,
            algorithm_name=algorithm.name,
            score=f"{algorithm.score:.4f}",
            generation=generation,
            description=algorithm.description,
            evaluation_summary=evaluation_summary,
        )

        try:

            class _InsightSchema(BaseModel):
                title: str = Field(
                    ...,
                    description="Concise title (3-8 words)",
                    validation_alias=AliasChoices("title", "name"),
                )
                content: str = Field(
                    ...,
                    description="Actionable insight (2-5 sentences)",
                    validation_alias=AliasChoices("content", "description"),
                )

            response = await self.provider.generate(
                prompt,
                temperature=self.config.extraction_temperature,
                max_tokens=512,
                schema=_InsightSchema,
            )

            if not response.parsed:
                return None

            schema: _InsightSchema = response.parsed  # type: ignore

            card = MemoryCard(
                type=MemoryType.GOOD_ALGORITHM,
                title=schema.title,
                content=schema.content,
                source="auto",
                score=algorithm.score,
                generation=generation,
                algorithm_id=algorithm.id,
                tags=["auto_extracted", "good_pattern"],
            )
            self._cards_this_gen += 1
            logger.info(f"Extracted good memory card: {schema.title}")
            return card

        except Exception as e:
            logger.warning(f"Failed to extract good memory from {algorithm.name}: {e}")
            return None

    async def extract_from_bad(
        self,
        algorithm: Any,
        population: list[Any],
        generation: int,
        background: str = "",
    ) -> "MemoryCard | None":
        """Extract avoidance lesson from a poorly-performing algorithm.

        Args:
            algorithm: The evaluated algorithm (Algorithm instance).
            population: Current population for percentile calculation.
            generation: Current generation number.
            background: Problem background description.

        Returns:
            MemoryCard with avoidance lesson, or None if skipped.
        """
        if not self.config.enabled or not self.config.extract_bad:
            return None
        if not self._budget_available():
            return None
        if not algorithm.is_evaluated():
            return None

        population_scores = [
            a.score for a in population if a.is_evaluated() and a.score is not None
        ]
        if not self._is_bad(algorithm.score, population_scores):
            return None

        from llm4ad.planner.sampler.prompt_templates import EXTRACT_BAD_MEMORY

        evaluation_summary = (
            f"Score: {algorithm.score:.4f}"
        )
        if algorithm.evaluation and algorithm.evaluation.metrics:
            metrics_str = ", ".join(
                f"{k}: {v:.4f}" for k, v in algorithm.evaluation.metrics.items()
            )
            evaluation_summary += f"\nMetrics: {metrics_str}"

        prompt = EXTRACT_BAD_MEMORY.format(
            background=background,
            algorithm_name=algorithm.name,
            score=f"{algorithm.score:.4f}",
            generation=generation,
            description=algorithm.description,
            evaluation_summary=evaluation_summary,
        )

        try:

            class _LessonSchema(BaseModel):
                title: str = Field(
                    ...,
                    description="Concise title (3-8 words)",
                    validation_alias=AliasChoices("title", "name"),
                )
                content: str = Field(
                    ...,
                    description="Actionable lesson (2-5 sentences)",
                    validation_alias=AliasChoices("content", "description"),
                )

            response = await self.provider.generate(
                prompt,
                temperature=self.config.extraction_temperature,
                max_tokens=512,
                schema=_LessonSchema,
            )

            if not response.parsed:
                return None

            schema: _LessonSchema = response.parsed  # type: ignore

            card = MemoryCard(
                type=MemoryType.ERROR_REFLECTION,
                title=schema.title,
                content=schema.content,
                source="auto",
                score=algorithm.score,
                generation=generation,
                algorithm_id=algorithm.id,
                tags=["auto_extracted", "bad_pattern"],
            )
            self._cards_this_gen += 1
            logger.info(f"Extracted bad memory card: {schema.title}")
            return card

        except Exception as e:
            logger.warning(f"Failed to extract bad memory from {algorithm.name}: {e}")
            return None

    async def extract_from_failure(
        self,
        algorithm: Any,
        error: str,
        generation: int,
        background: str = "",
    ) -> "MemoryCard | None":
        """Extract error reflection from an algorithm that failed evaluation.

        Args:
            algorithm: The failed algorithm.
            error: Error message or description.
            generation: Current generation number.
            background: Problem background description.

        Returns:
            MemoryCard with error reflection, or None if skipped.
        """
        if not self.config.enabled or not self.config.extract_on_failure:
            return None
        if not self._budget_available():
            return None

        from llm4ad.planner.sampler.prompt_templates import EXTRACT_EXECUTION_FAILURE_MEMORY

        evaluation_summary = f"FAILED\nError: {error}"

        prompt = EXTRACT_EXECUTION_FAILURE_MEMORY.format(
            background=background,
            algorithm_name=algorithm.name,
            score="N/A (evaluation failed before scoring)",
            generation=generation,
            description=algorithm.description,
            evaluation_summary=evaluation_summary,
        )

        try:

            class _FailureSchema(BaseModel):
                title: str = Field(
                    ...,
                    description="Concise title (3-8 words)",
                    validation_alias=AliasChoices("title", "name"),
                )
                content: str = Field(
                    ...,
                    description="Actionable lesson (2-5 sentences)",
                    validation_alias=AliasChoices("content", "description"),
                )

            response = await self.provider.generate(
                prompt,
                temperature=self.config.extraction_temperature,
                max_tokens=512,
                schema=_FailureSchema,
            )

            if not response.parsed:
                return None

            schema: _FailureSchema = response.parsed  # type: ignore

            card = MemoryCard(
                type=MemoryType.ERROR_REFLECTION,
                title=schema.title,
                content=schema.content,
                source="auto",
                generation=generation,
                algorithm_id=algorithm.id,
                tags=["auto_extracted", "failure_pattern"],
                metadata={"error": error},
            )
            self._cards_this_gen += 1
            logger.info(f"Extracted failure memory card: {schema.title}")
            return card

        except Exception as e:
            logger.warning(f"Failed to extract failure memory from {algorithm.name}: {e}")
            return None

    def reset_generation(self) -> None:
        """Reset per-generation card counter. Called at start of each generation."""
        self._cards_this_gen = 0


@BaseMemory.register("local_yaml")
class Memory(BaseMemory):
    """Experience memory system for storing and retrieving insights.

    The memory system serves as the "long-term memory" of the evolution process,
    storing:
    1. Good Algorithm Archive: Successful algorithms with their scores
    2. Error Reflection: What went wrong and how it was fixed
    3. Domain Knowledge: Task-specific constraints and background

    Supports:
    - Static memory cards from config
    - Auto-extraction via MemoryExtractor
    - YAML persistence to memory/ directory
    - Prompt context generation for sampler integration
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize memory.

        Args:
            config: Memory configuration dict.
        """
        super().__init__(config)
        self.entries: dict[str, MemoryEntry] = {}  # id -> entry
        self._type_index: dict[MemoryType, set[str]] = {t: set() for t in MemoryType}
        self._generation_index: dict[int, set[str]] = {}

        # Memory card tracking
        self._cards: list[MemoryCard] = []
        self.memory_dir: Path | None = None
        self.max_prompt_cards: int = config.get("max_prompt_cards", 5)
        self.include_task_memory: bool = bool(config.get("include_task_memory", True))
        self.task_memory_limit: int = int(config.get("task_memory_limit", self.max_prompt_cards))
        self.persist_enabled: bool = config.get("persist", True)

    def set_memory_dir(self, memory_dir: Path) -> None:
        """Set the persistence directory and load any existing cards.

        Args:
            memory_dir: Path to the memory/ directory.
        """
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._load_persisted_cards()

    def _load_persisted_cards(self) -> None:
        """Load all YAML card files from the memory directory."""
        if not self.memory_dir or not self.memory_dir.exists():
            return

        loaded = 0
        for yaml_file in sorted(self.memory_dir.glob("*.yaml")):
            try:
                card = MemoryCard.from_yaml_file(yaml_file)
                entry = card.to_entry()
                # Store directly without deduplication check (persisted cards are canonical)
                self.entries[entry.id] = entry
                self._type_index[entry.memory_type].add(entry.id)
                if entry.generation not in self._generation_index:
                    self._generation_index[entry.generation] = set()
                self._generation_index[entry.generation].add(entry.id)
                self._cards.append(card)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load memory card from {yaml_file}: {e}")

        if loaded > 0:
            logger.info(f"Loaded {loaded} persisted memory cards from {self.memory_dir}")

    def load_static_cards(
        self,
        inline_cards: list[Any],
    ) -> None:
        """Load static memory cards from config inline definitions.

        Args:
            inline_cards: List of MemoryCardConfig instances from config.
        """
        loaded = 0

        # Load inline cards from config
        for card_config in inline_cards:
            try:
                card = MemoryCard(
                    type=MemoryType(card_config.type),
                    title=card_config.title,
                    content=card_config.content,
                    source="static",
                    enabled=getattr(card_config, "enabled", True),
                    tags=card_config.tags,
                    score=card_config.score,
                    metadata=card_config.metadata,
                )
                entry = card.to_entry()
                self.entries[entry.id] = entry
                self._type_index[entry.memory_type].add(entry.id)
                self._cards.append(card)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load inline memory card '{card_config.title}': {e}")

        if loaded > 0:
            logger.info(f"Loaded {loaded} static memory cards")

    async def add(self, entry: MemoryEntry) -> None:
        """Add a new entry to memory.

        Automatically generates embedding for semantic search if not provided.

        Args:
            entry: Memory entry to add
        """
        # Check for duplicates
        entry_hash = entry.get_hash()
        for existing in self.entries.values():
            if existing.get_hash() == entry_hash:
                # Duplicate found, don't add
                return

        # Store entry
        self.entries[entry.id] = entry

        # Update indexes
        self._type_index[entry.memory_type].add(entry.id)

        if entry.generation not in self._generation_index:
            self._generation_index[entry.generation] = set()
        self._generation_index[entry.generation].add(entry.id)

    async def add_algorithm(
        self, insight: Any, memory_type: MemoryType = MemoryType.GOOD_ALGORITHM  # AlgorithmInsight
    ) -> None:
        """Add an algorithm insight to memory.

        Args:
            insight: Algorithm insight to add
            memory_type: Type of memory entry
        """
        # Import here to avoid circular imports
        from llm4ad.planner.base import Algorithm

        if not isinstance(insight, Algorithm):
            raise TypeError(f"Expected Algorithm, got {type(insight)}")

        entry = MemoryEntry(
            id=insight.id,
            memory_type=memory_type,
            content=insight.description,
            score=insight.score,
            generation=insight.generation,
            metadata={
                "code": insight.code,
                "code_file": insight.code_file,
                "metrics": insight.metrics,
                "insight_type": insight.insight_type.value,
            },
        )

        await self.add(entry)

    async def add_error(
        self, error: str, fix: str, context: dict[str, Any], generation: int = 0
    ) -> None:
        """Add error reflection to memory.

        Args:
            error: Description of the error
            fix: Description of how it was fixed
            context: Additional context (code snippet, task, etc.)
            generation: Generation when this occurred
        """
        content = f"Error: {error}\n\nFix: {fix}"

        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.ERROR_REFLECTION,
            content=content,
            generation=generation,
            metadata={
                "error": error,
                "fix": fix,
                "context": context,
            },
        )

        await self.add(entry)

    async def add_card(self, card: MemoryCard, persist: bool | None = None) -> None:
        """Add a MemoryCard to runtime store and optionally persist to disk.

        Args:
            card: The memory card to add.
            persist: Whether to persist to disk. None uses self.persist_enabled.
        """
        await self.upsert_card(card, persist=persist)

    def list_cards(self) -> list[MemoryCard]:
        """Return all memory cards in insertion order."""
        return list(self._cards)

    async def upsert_card(self, card: MemoryCard, persist: bool | None = None) -> MemoryCard:
        """Create or replace a memory card and optionally persist it."""
        existing_index = next((idx for idx, item in enumerate(self._cards) if item.id == card.id), None)
        if existing_index is not None:
            await self.delete_card(card.id)

        entry = card.to_entry()
        await self.add(entry)
        self._cards.append(card)

        should_persist = persist if persist is not None else self.persist_enabled
        if should_persist:
            await self.persist_card(card)
        return card

    async def delete_card(self, card_id: str) -> None:
        """Delete a memory card from runtime indexes and disk."""
        entry = self.entries.pop(card_id, None)
        if entry is not None:
            self._type_index[entry.memory_type].discard(card_id)
            if entry.generation in self._generation_index:
                self._generation_index[entry.generation].discard(card_id)
                if not self._generation_index[entry.generation]:
                    del self._generation_index[entry.generation]

        self._cards = [card for card in self._cards if card.id != card_id]
        if self.memory_dir:
            for yaml_file in self.memory_dir.glob(f"*_{card_id}.yaml"):
                yaml_file.unlink(missing_ok=True)

    async def set_card_enabled(self, card_id: str, enabled: bool) -> MemoryCard:
        """Enable or disable a memory card."""
        for card in self._cards:
            if card.id == card_id:
                updated = card.model_copy(update={"enabled": enabled})
                return await self.upsert_card(updated)
        raise KeyError(card_id)

    async def persist_card(self, card: MemoryCard) -> Path | None:
        """Save a MemoryCard to the memory directory as YAML.

        Args:
            card: The memory card to persist.

        Returns:
            Path to the written file, or None if no memory_dir is set.
        """
        if not self.memory_dir:
            return None

        try:
            filepath = card.to_yaml_file(self.memory_dir)
            logger.debug(f"Persisted memory card to {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"Failed to persist memory card '{card.title}': {e}")
            return None

    async def get_relevant(
        self,
        query: str,
        k: int = 5,
        memory_types: list[MemoryType] | None = None,
        min_score: float | None = None,
    ) -> list[MemoryEntry]:
        """Retrieve relevant memories using semantic search.

        Uses embedding similarity to find the most relevant memories.
        This is a placeholder implementation - real implementation would
        use vector database or embedding similarity search.

        Args:
            query: Query string to search for
            k: Number of results to return
            memory_types: Optional filter by memory types
            min_score: Optional minimum score filter

        Returns:
            List of relevant memory entries
        """
        # Filter by type and score first
        candidates = []
        for entry in self.entries.values():
            if memory_types and entry.memory_type not in memory_types:
                continue
            if min_score is not None and (entry.score is None or entry.score < min_score):
                continue
            candidates.append(entry)

        # TODO: Implement actual embedding-based semantic search
        # For now, just do simple keyword matching as a placeholder
        query_words = set(query.lower().split())

        def relevance_score(entry: MemoryEntry) -> float:
            content_words = set(entry.content.lower().split())
            overlap = len(query_words & content_words)
            return overlap / max(len(query_words), 1)

        # Sort by relevance and return top k
        candidates.sort(key=relevance_score, reverse=True)
        return candidates[:k]

    async def get_good_algorithms(
        self, top_k: int = 10, generation: int | None = None
    ) -> list[MemoryEntry]:
        """Get top-performing algorithms from memory.

        Args:
            top_k: Number of top algorithms to return
            generation: Optional filter by generation

        Returns:
            List of memory entries with top scores
        """
        candidates = []
        for entry in self.entries.values():
            if entry.memory_type != MemoryType.GOOD_ALGORITHM:
                continue
            if entry.score is None:
                continue
            if generation is not None and entry.generation != generation:
                continue
            candidates.append(entry)

        # Sort by score (descending)
        candidates.sort(key=lambda e: e.score or 0, reverse=True)
        return candidates[:top_k]

    async def get_error_reflections(self, context: str, k: int = 5) -> list[MemoryEntry]:
        """Get relevant error reflections for the context.

        Args:
            context: Context to search for relevant errors
            k: Number of results to return

        Returns:
            List of error reflection entries
        """
        return await self.get_relevant(
            query=context, k=k, memory_types=[MemoryType.ERROR_REFLECTION]
        )

    def get_prompt_context(
        self,
        query: str = "",
        max_cards: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build formatted memory context for injection into sampler prompts.

        Organizes cards into three sections:
        - Successful Patterns (good algorithms)
        - Pitfalls to Avoid (error reflections)
        - Domain Knowledge

        Args:
            query: Optional query for relevance filtering.
            max_cards: Maximum cards to include. None uses self.max_prompt_cards.
            context: Optional sampler and island strategy metadata.

        Returns:
            Formatted memory context string. Empty string if no cards.
        """
        profile = context.get("island_strategy") if context else None
        memory_policy = (
            str(profile.get("memory_policy", "")).strip().lower()
            if isinstance(profile, dict)
            else ""
        )
        if memory_policy == "none" or not self._cards:
            return ""

        if not self.include_task_memory:
            return ""

        max_cards = max_cards if max_cards is not None else self.max_prompt_cards
        max_cards = min(max_cards, self.task_memory_limit)
        if max_cards <= 0:
            return ""

        # Categorize cards
        good_cards: list[MemoryCard] = []
        bad_cards: list[MemoryCard] = []
        domain_cards: list[MemoryCard] = []

        explicit_island_policy = memory_policy in {"success_only", "corrective"}
        for card in self._cards:
            if not card.enabled:
                continue
            if card.type == MemoryType.GOOD_ALGORITHM or (
                not explicit_island_policy and card.type == MemoryType.GENERAL_INSIGHT
            ):
                good_cards.append(card)
            elif card.type == MemoryType.ERROR_REFLECTION:
                bad_cards.append(card)
            elif card.type == MemoryType.DOMAIN_KNOWLEDGE:
                domain_cards.append(card)

        # Sort good by score descending, bad by score ascending
        good_cards.sort(key=lambda c: c.score if c.score is not None else 0.0, reverse=True)
        bad_cards.sort(key=lambda c: c.score if c.score is not None else float("inf"))

        if memory_policy == "success_only":
            n_domain = 0
            n_good = min(len(good_cards), max_cards)
            n_bad = 0
        elif memory_policy == "corrective" and isinstance(profile, dict):
            try:
                success_ratio = max(0.0, float(profile.get("success_memory_ratio", 0.6)))
                error_ratio = max(0.0, float(profile.get("error_memory_ratio", 0.4)))
            except (TypeError, ValueError):
                success_ratio, error_ratio = 0.6, 0.4
            ratio_total = success_ratio + error_ratio
            if ratio_total <= 0:
                return ""
            success_slots = min(max_cards, round(max_cards * success_ratio / ratio_total))
            error_slots = max(0, max_cards - success_slots)
            n_domain = 0
            n_good = min(len(good_cards), success_slots)
            n_bad = min(len(bad_cards), error_slots)
        else:
            # Preserve the legacy balanced local-memory layout when no island
            # strategy is supplied by older orchestrators or direct callers.
            remaining = max_cards
            n_domain = min(len(domain_cards), max(1, remaining // 3))
            remaining -= n_domain
            n_good = min(len(good_cards), max(1, remaining // 2))
            n_bad = min(len(bad_cards), remaining - n_good)

        sections: list[str] = []

        # Good patterns
        if good_cards[:n_good]:
            lines = ["### Successful Patterns (what works)"]
            for card in good_cards[:n_good]:
                score_str = f", score: {card.score:.4f}" if card.score is not None else ""
                gen_str = f", gen {card.generation}" if card.generation is not None else ""
                lines.append(f"- **{card.title}** ({score_str}{gen_str}): {card.content}")
            sections.append("\n".join(lines))

        # Bad patterns
        if bad_cards[:n_bad]:
            lines = ["### Pitfalls to Avoid (what doesn't work)"]
            for card in bad_cards[:n_bad]:
                score_str = f", score: {card.score:.4f}" if card.score is not None else ""
                gen_str = f", gen {card.generation}" if card.generation is not None else ""
                lines.append(f"- **{card.title}** ({score_str}{gen_str}): {card.content}")
            sections.append("\n".join(lines))

        # Domain knowledge
        if domain_cards[:n_domain]:
            lines = ["### Domain Knowledge"]
            for card in domain_cards[:n_domain]:
                lines.append(f"- **{card.title}**: {card.content}")
            sections.append("\n".join(lines))

        if not sections:
            return ""

        prompt_context = "\n\n".join(sections)
        # ⏳ marks short-term (local) memory. Local memory only injects task-scope
        # cards ranked by score, so the strategy is fixed to score-topk.
        logger.info(
            "⏳ [short-term memory] injection completed: task_injection={} "
            "good={} bad={} domain={} injected_chars={}",
            memory_policy or "score-topk",
            len(good_cards[:n_good]),
            len(bad_cards[:n_bad]),
            len(domain_cards[:n_domain]),
            len(prompt_context),
        )
        logger.bind(
            event_type="local_memory_injected",
            memory_kind="short_term",
            task_injection_mode=memory_policy or "score-topk",
            good_cards=len(good_cards[:n_good]),
            bad_cards=len(bad_cards[:n_bad]),
            domain_cards=len(domain_cards[:n_domain]),
            injected_chars=len(prompt_context),
        ).info("⏳ short-term memory injection event")
        return prompt_context

    async def aget_prompt_context(
        self,
        query: str = "",
        max_cards: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build local prompt context with the same island policy as remote memory."""
        return self.get_prompt_context(
            query=query,
            max_cards=max_cards,
            context=context,
        )

    async def summarize(self, max_tokens: int = 2000) -> str:
        """Summarize memory into a prompt for LLM.

        Creates a concise summary of:
        - Top performing algorithms
        - Recent error patterns
        - Key domain knowledge

        Args:
            max_tokens: Maximum tokens for summary

        Returns:
            Summarized memory as text
        """
        parts = []

        # Top algorithms
        top_algorithms = await self.get_good_algorithms(top_k=5)
        if top_algorithms:
            parts.append("## Top Performing Algorithms")
            for i, algo in enumerate(top_algorithms, 1):
                parts.append(f"{i}. Score: {algo.score:.4f}")
                parts.append(f"   Description: {algo.description[:200]}...")

        # Recent errors
        recent_errors = [
            e for e in self.entries.values() if e.memory_type == MemoryType.ERROR_REFLECTION
        ]
        recent_errors.sort(key=lambda e: e.created_at, reverse=True)

        if recent_errors[:3]:
            parts.append("\n## Common Errors to Avoid")
            for error in recent_errors[:3]:
                error_text = error.metadata.get("error", error.content[:100])
                parts.append(f"- {error_text}")

        summary = "\n".join(parts)

        # Rough token estimation (4 chars per token)
        if len(summary) > max_tokens * 4:
            summary = summary[: max_tokens * 4] + "..."

        return summary

    def get_stats(self) -> dict[str, Any]:
        """Get memory statistics.

        Returns:
            Dictionary with memory statistics
        """
        stats: dict[str, Any] = {
            "total_entries": len(self.entries),
            "total_cards": len(self._cards),
            "by_type": {},
            "by_source": {"static": 0, "auto": 0},
            "by_generation": {},
            "algorithms_with_scores": 0,
            "average_score": 0.0,
        }

        scores = []
        for entry in self.entries.values():
            # Count by type
            type_name = entry.memory_type.value
            stats["by_type"][type_name] = stats["by_type"].get(type_name, 0) + 1

            # Count by generation
            gen = entry.generation
            stats["by_generation"][gen] = stats["by_generation"].get(gen, 0) + 1

            # Track scores
            if entry.score is not None:
                scores.append(entry.score)

        # Count by source
        for card in self._cards:
            stats["by_source"][card.source] = stats["by_source"].get(card.source, 0) + 1

        if scores:
            stats["algorithms_with_scores"] = len(scores)
            stats["average_score"] = sum(scores) / len(scores)
            stats["best_score"] = max(scores)
            stats["worst_score"] = min(scores)

        return stats

    def clear(self) -> None:
        """Clear all entries from memory."""
        self.entries.clear()
        self._type_index = {t: set() for t in MemoryType}
        self._generation_index.clear()
        self._cards.clear()

    async def merge(self, other: "Memory") -> None:
        """Merge another memory into this one.

        Args:
            other: Another memory instance to merge from
        """
        for entry in other.entries.values():
            await self.add(entry)
        for card in other._cards:
            if card.id not in {c.id for c in self._cards}:
                self._cards.append(card)


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    """Read a config key from either dict-like or attribute-based config."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def import_memory_module(module: str | None) -> None:
    """Import an optional user module so it can register memory components.

    Args:
        module: Dotted module name or path to a Python file.

    Raises:
        ImportError: If the module cannot be imported.
        ValueError: If a file module cannot be loaded.
    """
    if not module:
        return

    module_path = Path(module).expanduser()
    if module.endswith(".py") or module_path.exists():
        resolved_path = module_path.resolve()
        module_name = f"llm4ad_user_memory_{resolved_path.stem}_{uuid.uuid4().hex[:8]}"
        spec = importlib.util.spec_from_file_location(module_name, resolved_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Unable to load memory module from '{module}'")
        loaded_module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = loaded_module
        spec.loader.exec_module(loaded_module)
        return

    importlib.import_module(module)


def create_memory(config: Any) -> BaseMemory:
    """Create a registered memory implementation from config.

    Args:
        config: MemoryConfig instance or plain dictionary.

    Returns:
        Registered memory implementation.
    """
    config_dict = config if isinstance(config, dict) else config.model_dump()
    if not bool(_config_get(config, "enabled", True)):
        return NullMemory(config=config_dict)

    import_memory_module(_config_get(config, "module"))
    memory_type = _config_get(config, "type", "local_yaml")
    if memory_type == "mindmemos_cloud":
        import_memory_module("llm4ad.planner.mindmemos_memory")
    return BaseMemory.create(memory_type, config=config_dict)


def create_memory_extractor(provider: Any, config: Any) -> BaseMemoryExtractor:
    """Create a registered memory extractor implementation from config.

    Args:
        provider: Provider passed to the extractor.
        config: AutoExtractionConfig instance or plain dictionary.

    Returns:
        Registered memory extractor implementation.
    """
    import_memory_module(_config_get(config, "module"))
    extractor_type = _config_get(config, "type", "llm_card_extractor")
    return BaseMemoryExtractor.create(extractor_type, provider=provider, config=config)
