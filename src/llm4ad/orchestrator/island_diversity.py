"""Lightweight diversity primitives shared by the island orchestrator.

The helpers in this module deliberately avoid storing full ``Algorithm`` objects:
novelty is estimated from compact code token signatures while quality remains the
responsibility of the existing fitness/elite machinery.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from math import floor
from typing import Any

from llm4ad.planner.base import Algorithm, deduplicate_algorithms_by_code

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]")


@dataclass(frozen=True)
class IslandStrategyProfile:
    """Continuous exploitation/exploration position for one island."""

    position: float
    exploration: float
    exploitation: float
    memory_policy: str
    memory_injection_probability: float
    success_memory_ratio: float
    error_memory_ratio: float
    random_restart_probability: float
    independent_exploration: bool
    success_memory_weight: float
    error_memory_weight: float
    novelty_memory_weight: float

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON/prompt-friendly representation."""
        return asdict(self)


def build_island_strategy(
    island_id: int,
    num_islands: int,
    *,
    stagnation_generations: int = 0,
    stagnation_threshold: int = 2,
    strength: float = 1.0,
    exploration_restart_ratio: float = 0.3,
    sample_index: int = 0,
    sample_count: int = 1,
    generation: int = 0,
) -> IslandStrategyProfile:
    """Create a distinct, reproducible strategy for any island count.

    The spectrum has three exact memory anchors when three islands are configured:
    success-only reuse, success-plus-correction, and memory-free exploration.
    Parentless restarts are an independent dimension: only a configured share
    starts from scratch, while the remaining memory-free candidates still derive
    from parents. Counts above three interpolate between the anchors. Fractional
    restart probabilities are converted to a deterministic per-generation quota
    so parallel execution remains reproducible.
    """
    count = max(1, int(num_islands))
    index = min(max(0, int(island_id)), count - 1)
    raw_position = 0.5 if count == 1 else index / (count - 1)
    bounded_strength = min(1.0, max(0.0, float(strength)))
    # Strength zero collapses every island to the safe corrective midpoint;
    # strength one exposes the full success-to-independent-search spectrum.
    position = 0.5 + ((raw_position - 0.5) * bounded_strength)
    stalled_for = max(
        0,
        int(stagnation_generations) - max(0, int(stagnation_threshold)) + 1,
    )
    stagnation_boost = min(0.25, stalled_for * 0.05 * bounded_strength)
    exploration = min(1.0, position + stagnation_boost)
    exploitation = 1.0 - exploration

    # The first half interpolates from successful evidence only to a bounded
    # 60/40 success/correction mix. Islands past the midpoint are memory-free;
    # they explore either from an existing parent or, separately, from scratch.
    error_ratio = min(0.4, 0.8 * position)
    success_ratio = 1.0 - error_ratio
    bounded_restart_ratio = min(1.0, max(0.0, float(exploration_restart_ratio)))
    exploration_affinity = max(0.0, (2.0 * position) - 1.0)
    base_restart_probability = exploration_affinity * bounded_restart_ratio
    restart_probability = min(1.0, base_restart_probability + stagnation_boost)

    scheduled_count = max(1, int(sample_count))
    scheduled_index = min(max(0, int(sample_index)), scheduled_count - 1)
    scheduled_generation = max(0, int(generation))
    expected_restarts = restart_probability * scheduled_count
    restart_slots = min(
        scheduled_count,
        max(
            0,
            floor(((scheduled_generation + 1) * expected_restarts) + 1e-12)
            - floor((scheduled_generation * expected_restarts) + 1e-12),
        ),
    )
    if restart_probability > 0 and stalled_for > 0:
        restart_slots = max(1, restart_slots)
    phase = (scheduled_index + scheduled_generation + index) % scheduled_count
    independent_exploration = phase < restart_slots

    if position > 0.5:
        memory_policy = "none"
        success_ratio = 0.0
        error_ratio = 0.0
    elif error_ratio <= 0:
        memory_policy = "success_only"
    else:
        memory_policy = "corrective"

    return IslandStrategyProfile(
        position=round(position, 6),
        exploration=round(exploration, 6),
        exploitation=round(exploitation, 6),
        memory_policy=memory_policy,
        memory_injection_probability=0.0 if memory_policy == "none" else 1.0,
        success_memory_ratio=round(success_ratio, 6),
        error_memory_ratio=round(error_ratio, 6),
        random_restart_probability=round(restart_probability, 6),
        independent_exploration=independent_exploration,
        # Keep the legacy weights in checkpoints and logs for compatibility.
        # New selection uses the explicit ratios and memory_policy above.
        success_memory_weight=round(0.7 + 0.9 * exploitation, 6),
        error_memory_weight=round(0.8 + error_ratio, 6),
        novelty_memory_weight=round(0.7 + 1.1 * exploration, 6),
    )


def normalized_code(algorithm: Algorithm) -> str:
    """Return deterministic code text suitable for hashing and comparison."""
    artifacts = sorted(
        algorithm.code_artifacts,
        key=lambda artifact: (artifact.file_path, artifact.language),
    )
    return "\n".join(
        f"{artifact.file_path}\n{artifact.content.strip()}" for artifact in artifacts
    ).strip()


def code_fingerprint(algorithm: Algorithm) -> str | None:
    """Return a stable SHA-256 code fingerprint, or ``None`` without code."""
    return algorithm.code_fingerprint()


def code_tokens(algorithm: Algorithm) -> frozenset[str]:
    """Build a compact token signature for approximate code novelty."""
    return frozenset(token.lower() for token in _TOKEN_RE.findall(normalized_code(algorithm)))


def token_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Calculate Jaccard similarity between two compact signatures."""
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def select_diverse_survivors(
    population: list[Algorithm],
    *,
    count: int,
    novelty_ratio: float,
    archive_fingerprints: Iterable[str | None] = (),
) -> list[Algorithm]:
    """Keep quality leaders plus a small set of code-dissimilar candidates."""
    if count <= 0 or not population:
        return []
    unique = deduplicate_algorithms_by_code(population)
    target = min(count, len(unique))
    bounded_novelty_ratio = min(1.0, max(0.0, novelty_ratio))
    novelty_slots = (
        min(target, max(1, round(target * bounded_novelty_ratio)))
        if bounded_novelty_ratio > 0 and target > 1
        else 0
    )
    quality_slots = target - novelty_slots
    ranked = sorted(unique, key=lambda item: (item.is_evaluated(), item.score), reverse=True)
    selected = ranked[:quality_slots]
    remaining = [item for item in ranked if item.id not in {entry.id for entry in selected}]
    archived = {value for value in archive_fingerprints if value}

    while remaining and len(selected) < target:
        selected_tokens = [code_tokens(item) for item in selected]

        def novelty_key(
            item: Algorithm,
            reference_tokens: list[frozenset[str]] = selected_tokens,
        ) -> tuple[float, float]:
            signature = code_tokens(item)
            similarity = max(
                (token_similarity(signature, other) for other in reference_tokens),
                default=0.0,
            )
            archive_penalty = 0.25 if code_fingerprint(item) in archived else 0.0
            return (1.0 - similarity - archive_penalty, item.score)

        chosen = max(remaining, key=novelty_key)
        selected.append(chosen)
        remaining.remove(chosen)

    return selected


def population_similarity(left: list[Algorithm], right: list[Algorithm]) -> float:
    """Average nearest-neighbour code similarity between two populations."""
    left_tokens = [tokens for item in left if (tokens := code_tokens(item))]
    right_tokens = [tokens for item in right if (tokens := code_tokens(item))]
    if not left_tokens or not right_tokens:
        return 0.0
    left_to_right = [
        max(token_similarity(tokens, other) for other in right_tokens)
        for tokens in left_tokens
    ]
    right_to_left = [
        max(token_similarity(tokens, other) for other in left_tokens)
        for tokens in right_tokens
    ]
    directional = left_to_right + right_to_left
    return sum(directional) / len(directional)
