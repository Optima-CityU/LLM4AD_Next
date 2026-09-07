"""Rule-based evolution parameter recommender.

Takes the LLM-inferred complexity_tier from analysis and produces
recommended evolution parameters via a lookup table. No LLM calls —
deterministic, zero-cost, and safe for automated pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class EvolParams:
    """Recommended evolution and evaluator runtime parameters."""

    num_islands: int = 1
    island_population_size: int = 8
    max_generations: int = 15
    elite_ratio: float = 0.1
    mutation_rate: float = 0.3
    crossover_rate: float = 0.5
    selection_strategy: str = "tournament"
    tournament_size: int = 3
    early_stop_patience: int = 5
    early_stop_threshold: float = 1e-6
    checkpoint_interval: int = 5
    max_checkpoints: int = 3
    evaluator_timeout: float = 60.0
    evaluator_batch_size: int = 5


# ---------------------------------------------------------------------------
# Rule table: complexity_tier -> EvolParams
# ---------------------------------------------------------------------------

COMPLEXITY_RULES: dict[str, EvolParams] = {
    "simple": EvolParams(
        num_islands=1,
        island_population_size=8,
        max_generations=15,
        elite_ratio=0.1,
        mutation_rate=0.3,
        crossover_rate=0.5,
        selection_strategy="tournament",
        tournament_size=3,
        early_stop_patience=5,
        early_stop_threshold=1e-6,
        checkpoint_interval=5,
        max_checkpoints=3,
        evaluator_timeout=30.0,
        evaluator_batch_size=5,
    ),
    "medium": EvolParams(
        num_islands=1,
        island_population_size=8,
        max_generations=30,
        elite_ratio=0.1,
        mutation_rate=0.3,
        crossover_rate=0.5,
        selection_strategy="tournament",
        tournament_size=3,
        early_stop_patience=10,
        early_stop_threshold=1e-6,
        checkpoint_interval=5,
        max_checkpoints=3,
        evaluator_timeout=60.0,
        evaluator_batch_size=5,
    ),
    "complex": EvolParams(
        num_islands=3,
        island_population_size=8,
        max_generations=50,
        elite_ratio=0.1,
        mutation_rate=0.3,
        crossover_rate=0.5,
        selection_strategy="tournament",
        tournament_size=3,
        early_stop_patience=20,
        early_stop_threshold=1e-6,
        checkpoint_interval=10,
        max_checkpoints=5,
        evaluator_timeout=120.0,
        evaluator_batch_size=10,
    ),
}

# Default params used when no complexity analysis is available (backward compat).
# Same as "simple" tier — the most conservative setting.
DEFAULT_PARAMS = COMPLEXITY_RULES["simple"]

_VALID_TIERS = frozenset(COMPLEXITY_RULES.keys())


# ---------------------------------------------------------------------------
# YAML rendering
# ---------------------------------------------------------------------------


def render_evolution_yaml(params: EvolParams) -> str:
    """Render the ``evolution:`` YAML block from EvolParams.

    Args:
        params: Recommended evolution parameters.

    Returns:
        Indented YAML string suitable for inserting into the config template.
    """
    return (
        "# ===== Evolution Configuration =====\n"
        "evolution:\n"
        f'  type: "island_ga"\n'
        f"  num_islands: {params.num_islands}\n"
        f"  island_population_size: {params.island_population_size}\n"
        f"  max_generations: {params.max_generations}\n"
        f"  elite_ratio: {params.elite_ratio}\n"
        f"  mutation_rate: {params.mutation_rate}\n"
        f"  crossover_rate: {params.crossover_rate}\n"
        f'  selection_strategy: "{params.selection_strategy}"\n'
        f"  tournament_size: {params.tournament_size}\n"
        f"  early_stop_patience: {params.early_stop_patience}\n"
        f"  early_stop_threshold: {params.early_stop_threshold}\n"
        f"  checkpoint_interval: {params.checkpoint_interval}\n"
        f"  max_checkpoints: {params.max_checkpoints}\n"
    )


def render_default_evolution_yaml() -> str:
    """Render the default evolution YAML block for backward compatibility.

    Returns the same hardcoded values that were previously inlined in
    ``CONFIG_YAML_TEMPLATE``.
    """
    return render_evolution_yaml(DEFAULT_PARAMS)


# ---------------------------------------------------------------------------
# Recommender
# ---------------------------------------------------------------------------


class ConfigRecommender:
    """Rule-based config parameter recommender.

    Takes the complexity tier from analysis and returns a complete
    ``EvolParams`` object with recommended evolution and evaluator
    runtime parameters.
    """

    @staticmethod
    def recommend(complexity_tier: str | None) -> EvolParams:
        """Recommend evolution parameters for a given complexity tier.

        Args:
            complexity_tier: One of ``"simple"``, ``"medium"``, ``"complex"``.
                May be ``None`` or an unrecognized value — in that case the
                ``"simple"`` tier is used as a safe fallback.

        Returns:
            ``EvolParams`` with recommended values.
        """
        tier = (complexity_tier or "").strip().lower()

        if tier not in _VALID_TIERS:
            if tier:
                logger.warning(
                    "Unrecognized complexity_tier '{}', falling back to 'simple'. "
                    "Valid tiers are: {}",
                    complexity_tier,
                    sorted(_VALID_TIERS),
                )
            else:
                logger.info("No complexity_tier provided, defaulting to 'simple'.")
            tier = "simple"

        logger.info(
            "ConfigRecommender: complexity_tier='{}' → num_islands={}, "
            "island_population_size={}, max_gens={}",
            tier,
            COMPLEXITY_RULES[tier].num_islands,
            COMPLEXITY_RULES[tier].island_population_size,
            COMPLEXITY_RULES[tier].max_generations,
        )
        return COMPLEXITY_RULES[tier]
