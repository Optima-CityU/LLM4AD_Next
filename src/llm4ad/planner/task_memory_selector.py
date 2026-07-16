"""Task-memory injection selectors.

After task-scoped memory is retrieved from a memory backend, a selector decides
how the retrieved candidates are ordered and trimmed before they are injected
into a sampler prompt. Retrieval always happens first; the selector only governs
ordering/sampling of the already-retrieved candidate pool.

Three strategies are provided:

- ``topk``: keep the highest retrieval-scored candidates (default behaviour).
- ``weight``: order by a per-card injection weight stored in card metadata under
  ``injection_weight`` (default ``1.0``), so users can bias specific task
  memories via the memory-management UI.
- ``random``: sample uniformly at random from the candidate pool. An optional
  seed makes selection reproducible for tests.

Selectors are pure and backend-agnostic: they operate on
:class:`TaskMemoryCandidate` wrappers and never perform I/O, which keeps them
easy to unit-test in isolation.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from llm4ad.utils.registry import Registrable

# Metadata key holding a per-card injection weight for the ``weight`` strategy.
INJECTION_WEIGHT_KEY = "injection_weight"

# Default weight when a candidate carries no explicit injection weight.
DEFAULT_INJECTION_WEIGHT = 1.0


@dataclass
class TaskMemoryCandidate:
    """A retrieved task-memory candidate awaiting injection selection.

    Attributes:
        key: Stable dedup/identity key for the candidate.
        score: Retrieval relevance score if available, otherwise ``None``.
        metadata: Arbitrary metadata associated with the candidate. May carry an
            ``injection_weight`` used by the ``weight`` strategy.
        payload: Opaque backend object (e.g. a raw search hit) carried through so
            the caller can render the selected candidates without re-lookup.
    """

    key: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: Any = None

    def weight(self) -> float:
        """Return this candidate's injection weight (defaults to ``1.0``).

        Returns:
            The ``injection_weight`` from metadata coerced to ``float``; falls
            back to :data:`DEFAULT_INJECTION_WEIGHT` when absent or invalid.
        """
        raw = self.metadata.get(INJECTION_WEIGHT_KEY)
        if isinstance(raw, bool) or raw is None:
            return DEFAULT_INJECTION_WEIGHT
        try:
            return float(raw)
        except (TypeError, ValueError):
            return DEFAULT_INJECTION_WEIGHT


class BaseTaskMemorySelector(ABC, Registrable):
    """Abstract interface for task-memory injection selectors."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the selector.

        Args:
            config: Optional selector configuration dictionary.
        """
        self.config = config or {}

    @abstractmethod
    def select(
        self,
        candidates: list[TaskMemoryCandidate],
        limit: int,
    ) -> list[TaskMemoryCandidate]:
        """Order and trim retrieved candidates for injection.

        Args:
            candidates: Retrieved task-memory candidates.
            limit: Maximum number of candidates to inject. Non-positive values
                yield an empty result.

        Returns:
            The selected candidates, at most ``limit`` items.
        """
        ...


@BaseTaskMemorySelector.register("topk")
class TopKSelector(BaseTaskMemorySelector):
    """Keep the highest retrieval-scored candidates (default strategy)."""

    def select(
        self,
        candidates: list[TaskMemoryCandidate],
        limit: int,
    ) -> list[TaskMemoryCandidate]:
        """Select the top ``limit`` candidates by descending retrieval score.

        Candidates without a score are treated as lowest priority. Ordering is
        stable for equal scores, preserving the backend's original ordering.

        Args:
            candidates: Retrieved task-memory candidates.
            limit: Maximum number of candidates to inject.

        Returns:
            The highest-scored candidates, at most ``limit`` items.
        """
        if limit <= 0 or not candidates:
            return []
        ordered = sorted(
            candidates,
            key=lambda c: (c.score is not None, c.score if c.score is not None else 0.0),
            reverse=True,
        )
        return ordered[:limit]


@BaseTaskMemorySelector.register("weight")
class WeightSelector(BaseTaskMemorySelector):
    """Order candidates by a per-card injection weight from metadata."""

    def select(
        self,
        candidates: list[TaskMemoryCandidate],
        limit: int,
    ) -> list[TaskMemoryCandidate]:
        """Select the ``limit`` highest-weighted candidates.

        Weight is read from each candidate's ``injection_weight`` metadata
        (default ``1.0``). Ties break by descending retrieval score, then by the
        backend's original ordering.

        Args:
            candidates: Retrieved task-memory candidates.
            limit: Maximum number of candidates to inject.

        Returns:
            The highest-weighted candidates, at most ``limit`` items.
        """
        if limit <= 0 or not candidates:
            return []
        ordered = sorted(
            candidates,
            key=lambda c: (c.weight(), c.score if c.score is not None else 0.0),
            reverse=True,
        )
        return ordered[:limit]


@BaseTaskMemorySelector.register("random")
class RandomSelector(BaseTaskMemorySelector):
    """Sample candidates uniformly at random from the pool."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the selector.

        Args:
            config: Optional configuration. A ``seed`` key makes the random
                sampling reproducible (useful for tests).
        """
        super().__init__(config)
        seed = self.config.get("seed")
        self._rng = random.Random(seed)

    def select(
        self,
        candidates: list[TaskMemoryCandidate],
        limit: int,
    ) -> list[TaskMemoryCandidate]:
        """Randomly sample up to ``limit`` candidates from the pool.

        Args:
            candidates: Retrieved task-memory candidates.
            limit: Maximum number of candidates to inject.

        Returns:
            A random subset of the candidates, at most ``limit`` items. When the
            pool is smaller than ``limit`` all candidates are returned shuffled.
        """
        if limit <= 0 or not candidates:
            return []
        if limit >= len(candidates):
            shuffled = list(candidates)
            self._rng.shuffle(shuffled)
            return shuffled
        return self._rng.sample(candidates, limit)


def create_task_memory_selector(
    mode: str | None,
    config: dict[str, Any] | None = None,
) -> BaseTaskMemorySelector:
    """Create a task-memory selector for the given injection mode.

    Args:
        mode: Injection mode name (``topk``, ``weight``, or ``random``). Unknown
            or empty values fall back to ``topk``.
        config: Optional selector configuration.

    Returns:
        A :class:`BaseTaskMemorySelector` instance.
    """
    name = (mode or "topk").strip().lower()
    if name not in BaseTaskMemorySelector.list():
        name = "topk"
    return BaseTaskMemorySelector.create(name, config)
