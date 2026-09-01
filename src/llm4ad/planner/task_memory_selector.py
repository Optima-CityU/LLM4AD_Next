"""Task-memory injection selectors.

After task-scoped memory is retrieved from a memory backend, a selector decides
how the retrieved candidates are ordered and trimmed before they are injected
into a sampler prompt. Retrieval always happens first; the selector only governs
ordering/sampling of the already-retrieved candidate pool.

The candidate pool arrives ordered by retrieval similarity (most similar first),
so a candidate's position in the pool is used as a similarity proxy — the
backend search API does not expose the raw similarity score, only the ranking.

Three strategies are provided:

- ``topk``: keep the most similar candidates (deterministic head of the pool).
- ``weight``: roulette-wheel (weighted random) sampling where each candidate's
  selection probability is proportional to a blended weight
  ``weight = lambda * similarity + (1 - lambda) * recency``. Both terms are
  rank-based linear decays in ``(0, 1]``: ``similarity`` from the retrieval order
  (most similar first) and ``recency`` from the candidate timestamp (most recent
  first). ``lambda`` (default ``0.5``) trades off relevance vs freshness.
  Selection stays stochastic so lower-weight memories still surface over time.
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


@dataclass
class TaskMemoryCandidate:
    """A retrieved task-memory candidate awaiting injection selection.

    Attributes:
        key: Stable dedup/identity key for the candidate.
        score: Retrieval relevance score if available, otherwise ``None``.
        objective_score: Original algorithm evaluation score. This is kept
            separate from retrieval relevance so an elite implementation can
            be selected by measured quality after semantic recall.
        timestamp: Epoch seconds for the candidate's recency (larger = newer).
            ``None`` when no timestamp is available; used by the ``weight``
            strategy's recency term.
        metadata: Arbitrary metadata associated with the candidate.
        payload: Opaque backend object (e.g. a raw search hit) carried through so
            the caller can render the selected candidates without re-lookup.
    """

    key: str
    score: float | None = None
    objective_score: float | None = None
    timestamp: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: Any = None


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
    """Roulette-wheel sampling weighted by blended similarity and recency.

    Each candidate's weight blends two rank-based linear decays in ``(0, 1]``::

        weight = lambda * similarity + (1 - lambda) * recency

    - ``similarity``: from the retrieval order (most similar first), since the
      backend exposes only ranking, not the raw score.
    - ``recency``: from the candidate timestamp (most recent first). Candidates
      without a timestamp are treated as oldest.

    ``lambda`` (config key ``lambda``, default ``0.5``) trades off relevance vs
    freshness. Candidates are sampled without replacement with probability
    proportional to the blended weight, so injection is biased toward relevant
    and fresh memories while staying stochastic.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the selector.

        Args:
            config: Optional configuration. ``seed`` makes sampling reproducible;
                ``lambda`` (0..1, default 0.5) weights similarity vs recency.
        """
        super().__init__(config)
        seed = self.config.get("seed")
        self._rng = random.Random(seed)
        self._lambda = _clamp_unit(self.config.get("lambda"), default=0.5)

    def select(
        self,
        candidates: list[TaskMemoryCandidate],
        limit: int,
    ) -> list[TaskMemoryCandidate]:
        """Roulette-wheel sample ``limit`` candidates by blended weight.

        Uses the Efraimidis-Spirakis A-Res scheme for weighted sampling without
        replacement: each candidate gets a key ``u ** (1 / w)`` with
        ``u ~ Uniform(0, 1]`` and ``w`` its blended weight, then the highest keys
        win. This yields the same distribution as repeatedly spinning a roulette
        wheel and removing the winner, in a single O(n log k) pass.

        Args:
            candidates: Retrieved task-memory candidates, ordered most-similar
                first.
            limit: Maximum number of candidates to inject.

        Returns:
            A weighted-random subset of the candidates, at most ``limit`` items.
        """
        if limit <= 0 or not candidates:
            return []
        n = len(candidates)
        # Similarity rank = pool position (most similar first). Recency rank is
        # derived by ordering candidates newest-first; missing timestamps sort
        # oldest. Both map to a linear decay in (0, 1].
        order = sorted(
            range(n),
            key=lambda i: (
                candidates[i].timestamp is not None,
                candidates[i].timestamp if candidates[i].timestamp is not None else 0.0,
            ),
            reverse=True,
        )
        recency_rank = {index: rank for rank, index in enumerate(order)}
        keyed: list[tuple[float, int, TaskMemoryCandidate]] = []
        for sim_rank, candidate in enumerate(candidates):
            similarity = (n - sim_rank) / n
            recency = (n - recency_rank[sim_rank]) / n
            weight = self._lambda * similarity + (1.0 - self._lambda) * recency
            # u in (0, 1] keeps the exponent well-defined; larger weight ->
            # smaller exponent -> key closer to 1 -> higher selection odds.
            u = 1.0 - self._rng.random()
            key = u ** (1.0 / weight)
            keyed.append((key, sim_rank, candidate))
        keyed.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        return [candidate for _, _, candidate in keyed[:limit]]


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


def _clamp_unit(value: Any, *, default: float) -> float:
    """Coerce ``value`` to a float in ``[0, 1]``, falling back to ``default``.

    Args:
        value: Raw configuration value (may be missing or invalid).
        default: Value returned when ``value`` is absent or not parseable.

    Returns:
        A float clamped to the closed unit interval ``[0.0, 1.0]``.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


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
