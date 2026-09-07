"""Regression tests for island migration correctness and diversity controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from llm4ad.config.app import AppConfig
from llm4ad.config.evolution import (
    DiverseIslandGAConfig,
    IslandGAConfig,
    MigrationStrategy,
)
from llm4ad.infra.version_control.base import VersionControlResult, WorktreeInfo
from llm4ad.orchestrator.base import BaseOrchestrator
from llm4ad.orchestrator.island_diversity import (
    build_island_strategy,
    code_fingerprint,
    select_diverse_survivors,
)
from llm4ad.orchestrator.island_ga import (
    DiverseIslandGAOrchestrator,
    Island,
    IslandGAOrchestrator,
)
from llm4ad.planner.base import (
    Algorithm,
    BasePlanner,
    CodeArtifact,
    EvaluationResult,
    InsightType,
)
from llm4ad.planner.llm_evolution import LLMEvolutionPlanner
from llm4ad.planner.mindmemos_memory import _select_island_strategy_candidates
from llm4ad.planner.task_memory_selector import TaskMemoryCandidate


def _algorithm(
    algorithm_id: str,
    *,
    island_id: int,
    score: float,
    code: str,
) -> Algorithm:
    return Algorithm(
        id=algorithm_id,
        insight_type=InsightType.MUTATION,
        description=f"algorithm {algorithm_id}",
        island_id=island_id,
        evaluation=EvaluationResult(score=score),
        code_artifacts=[CodeArtifact(file_path="solution.py", content=code, content_mode="full")],
    )


def test_diverse_island_ga_has_independent_config_and_registry_entry():
    """The diversity variant must not silently change IslandGA configs."""
    from llm4ad.config import evolution as evolution_config
    from llm4ad.orchestrator import island_ga as island_module

    assert hasattr(evolution_config, "DiverseIslandGAConfig")
    diverse_config_type = evolution_config.DiverseIslandGAConfig
    assert diverse_config_type().type == "diverse_island_ga"
    assert diverse_config_type().adaptive_migration is True

    classic = IslandGAConfig()
    assert classic.type == "island_ga"
    assert "adaptive_migration" not in type(classic).model_fields
    assert "novelty_survivor_ratio" not in type(classic).model_fields
    assert "exploration_restart_ratio" not in type(classic).model_fields
    assert hasattr(island_module, "DiverseIslandGAOrchestrator")
    assert BaseOrchestrator.get("island_ga") is IslandGAOrchestrator
    assert BaseOrchestrator.get("diverse_island_ga") is island_module.DiverseIslandGAOrchestrator

    parsed = AppConfig.model_validate({"evolution": {"type": "diverse_island_ga", "num_islands": 3}})
    assert isinstance(parsed.evolution, diverse_config_type)


def test_diverse_island_ga_uses_bounded_defaults_without_changing_classic_islands():
    """The diversity variant should be useful by default without scheduling a huge run."""
    classic = IslandGAConfig()
    diverse = DiverseIslandGAConfig()

    assert classic.num_islands == 5
    assert classic.island_population_size == 20
    assert classic.max_generations == 100
    assert classic.max_llm_concurrency is None

    assert diverse.num_islands == 3
    assert diverse.island_population_size == 5
    assert diverse.max_generations == 10
    assert diverse.max_llm_concurrency == 5
    assert diverse.elite_ratio == pytest.approx(0.2)
    assert diverse.migration_interval == 3
    assert diverse.migration_rate == pytest.approx(0.2)

    classic_schema = IslandGAConfig.model_json_schema()["properties"]
    diverse_schema = DiverseIslandGAConfig.model_json_schema()["properties"]
    for field_name in (
        "num_islands",
        "island_population_size",
        "max_generations",
        "max_llm_concurrency",
        "elite_ratio",
        "migration_interval",
        "migration_rate",
    ):
        assert diverse_schema[field_name]["ui"] == classic_schema[field_name]["ui"]


def test_classic_island_ga_ignores_stale_diversity_fields_at_runtime():
    """Saved extra fields cannot activate features on the original algorithm."""
    classic = object.__new__(IslandGAOrchestrator)
    classic.config = IslandGAConfig.model_validate(
        {
            "type": "island_ga",
            "adaptive_migration": True,
            "elite_reevaluation_count": 2,
            "novelty_survivor_ratio": 0.5,
        }
    )

    assert classic._adaptive_migration_enabled() is False
    assert classic._elite_reevaluation_count() == 0
    assert classic._novelty_survivor_ratio() == 0.0

    diverse = object.__new__(DiverseIslandGAOrchestrator)
    diverse.config = DiverseIslandGAConfig(
        adaptive_migration=True,
        elite_reevaluation_count=2,
        novelty_survivor_ratio=0.5,
    )
    assert diverse._adaptive_migration_enabled() is True
    assert diverse._elite_reevaluation_count() == 2
    assert diverse._novelty_survivor_ratio() == 0.5


def test_classic_island_ga_keeps_legacy_duplicate_and_migration_policy():
    """Only the diversity variant may deduplicate code or limit lineage spread."""
    duplicate_low = _algorithm(
        "duplicate-low", island_id=0, score=1.0, code="return 1"
    )
    duplicate_high = _algorithm(
        "duplicate-high", island_id=0, score=3.0, code="return 1"
    )
    population = [duplicate_low, duplicate_high]

    classic = object.__new__(IslandGAOrchestrator)
    diverse = object.__new__(DiverseIslandGAOrchestrator)

    assert classic._prepare_population(population) == population
    assert classic._migration_lineage_limit_enabled() is False
    assert diverse._prepare_population(population) == [duplicate_high]
    assert diverse._migration_lineage_limit_enabled() is True


def test_island_can_accept_duplicate_code_for_classic_migration():
    """Identity isolation must not force code de-duplication on classic IslandGA."""
    source = _algorithm("source", island_id=0, score=2.0, code="return 2")
    existing = _algorithm("existing", island_id=1, score=1.0, code="return 2")
    target = Island(
        island_id=1,
        population=[existing],
        island_config={"population_size": 3},
    )

    accepted = target.receive_migrants(
        [source],
        generation=2,
        replace_worst=False,
        deduplicate_code=False,
    )

    assert len(accepted) == 1
    assert accepted[0].id != source.id
    assert accepted[0].island_id == 1
    assert source.island_id == 0


def test_island_can_reexport_migrated_lineage_for_classic_policy():
    """Classic IslandGA retains repeated migration while keeping clone identities isolated."""
    source = _algorithm("source", island_id=0, score=2.0, code="return 2")
    target = Island(island_id=1, island_config={"population_size": 3})
    migrated = target.receive_migrants(
        [source], generation=2, replace_worst=False
    )[0]

    migrants = target.get_migrants(
        1,
        MigrationStrategy.BEST,
        deduplicate_code=False,
        restrict_lineage=False,
    )

    assert migrants == [migrated]


def test_classic_island_ga_uses_legacy_strategy_and_interval_migration():
    """IslandGA keeps fixed migration without adaptive strategy gates."""
    classic = object.__new__(IslandGAOrchestrator)
    classic.config = IslandGAConfig(
        num_islands=3,
        max_generations=8,
        migration_interval=2,
    )
    classic.current_generation = 2
    classic._last_migration_gen = 0
    classic.islands = [Island(island_id=index) for index in range(3)]

    assert classic._strategy_for_island(0) is None
    assert classic._strategy_for_island(2) is None
    assert classic._should_migrate() is True


def test_island_variants_expose_clear_schema_labels_and_separate_fields():
    """The dynamic task form can distinguish both island algorithms."""
    definitions = AppConfig.model_json_schema()["$defs"]
    classic_schema = definitions["IslandGAConfig"]
    diverse_schema = definitions["DiverseIslandGAConfig"]

    assert classic_schema["ui"]["label"] == {
        "zh": "Island GA",
        "en": "Island GA",
    }
    assert diverse_schema["ui"]["label"] == {
        "zh": "Diverse Island GA",
        "en": "Diverse Island GA",
    }
    assert "adaptive_migration" not in classic_schema["properties"]
    assert "adaptive_migration" in diverse_schema["properties"]


def test_diverse_island_numeric_controls_expose_slider_metadata():
    """Advanced island controls render as bounded, readable sliders."""
    properties = AppConfig.model_json_schema()["$defs"]["DiverseIslandGAConfig"]["properties"]

    expected = {
        "num_islands": (1, 12, 1),
        "island_population_size": (1, 100, 1),
        "migration_interval": (0, 20, 1),
        "migration_rate": (0, 1, 0.05),
        "elite_reevaluation_count": (0, 5, 1),
        "migration_stagnation_threshold": (1, 10, 1),
        "short_task_generation_threshold": (1, 50, 1),
        "short_task_max_migrations": (0, 10, 1),
        "novelty_survivor_ratio": (0, 0.5, 0.05),
        "island_strategy_strength": (0, 1, 0.05),
        "exploration_restart_ratio": (0, 1, 0.05),
    }
    for field_name, (minimum, maximum, step) in expected.items():
        slider = properties[field_name]["ui"]["slider"]
        assert properties[field_name]["ui"]["widget"] == "slider"
        assert slider == {"min": minimum, "max": maximum, "step": step}


def test_island_variants_keep_separate_reproduction_parent_pools():
    """Only the diversity variant exposes the full island to every offspring."""
    population = [
        _algorithm("a", island_id=0, score=3.0, code="a"),
        _algorithm("b", island_id=0, score=2.0, code="b"),
        _algorithm("c", island_id=0, score=1.0, code="c"),
    ]

    classic = object.__new__(IslandGAOrchestrator)
    classic.config = IslandGAConfig(elite_ratio=0.34)
    classic.planner = MagicMock()
    classic.planner.select_parents.return_value = [population[0]]

    diverse = object.__new__(DiverseIslandGAOrchestrator)
    diverse.config = DiverseIslandGAConfig(elite_ratio=0.34)
    diverse.planner = MagicMock()

    assert classic._select_reproduction_parents(population) == [population[0]]
    classic.planner.select_parents.assert_called_once_with(
        population,
        1,
        deduplicate_code=False,
    )
    assert diverse._select_reproduction_parents(population) == population
    diverse.planner.select_parents.assert_not_called()


def test_receive_migrants_clones_identity_and_skips_duplicate_code():
    """A received migrant is isolated and exact target code is rejected."""
    source = _algorithm("source", island_id=0, score=2.0, code="return 2")
    duplicate = _algorithm("target-existing", island_id=1, score=1.0, code="return 2")
    target = Island(
        island_id=1,
        population=[duplicate],
        island_config={"population_size": 3},
    )

    assert target.receive_migrants([source], generation=4) == []
    assert source.id == "source"
    assert source.island_id == 0

    source.code_artifacts[0].content = "return 3"
    source.worktree = WorktreeInfo(
        name="source",
        path="/tmp/source",
        branch="source",
        commit_hash="source-commit",
        created_at=0,
        last_used_at=0,
    )
    accepted = target.receive_migrants([source], generation=5, replace_worst=False)

    assert len(accepted) == 1
    migrant = accepted[0]
    assert migrant is not source
    assert migrant.id != source.id
    assert migrant.island_id == 1
    assert source.island_id == 0
    assert migrant.worktree is None
    assert migrant.custom_metadata["migration_source_id"] == "source"
    assert migrant.custom_metadata["migration_lineage_root"] == "source"
    assert migrant.custom_metadata["migration_source_commit"] == "source-commit"
    assert code_fingerprint(migrant) == code_fingerprint(source)


def test_migrated_individual_is_not_selected_for_immediate_reexport():
    """A migrated clone cannot propagate through several islands in sequence."""
    original = _algorithm("source", island_id=0, score=2.0, code="return 2")
    target = Island(island_id=1, island_config={"population_size": 3})
    migrated = target.receive_migrants([original], generation=2, replace_worst=False)[0]

    assert target.get_migrants(1, MigrationStrategy.BEST) == []
    assert migrated.custom_metadata["migration_hops"] == 1


@pytest.mark.asyncio
async def test_migration_emits_structured_lineage_event():
    """Accepted migrants must be observable by the live and persisted graph."""
    left_best = _algorithm("left-best", island_id=0, score=2.0, code="return 2")
    right_best = _algorithm("right-best", island_id=1, score=1.0, code="return 1")
    left = Island(
        island_id=0,
        population=[left_best],
        best_individual=left_best,
        island_config={"population_size": 2},
    )
    right = Island(
        island_id=1,
        population=[right_best],
        best_individual=right_best,
        island_config={"population_size": 2},
    )
    orchestrator = object.__new__(DiverseIslandGAOrchestrator)
    orchestrator.config = DiverseIslandGAConfig(
        num_islands=2,
        island_population_size=2,
        migration_rate=0.5,
        migration_topology="ring",
        adaptive_migration=False,
    )
    orchestrator.current_generation = 3
    orchestrator.islands = [left, right]
    orchestrator._exported_migrant_ids = set()
    orchestrator._last_migration_gen = 0
    orchestrator._migration_events = 0
    orchestrator.global_best_individual = None
    orchestrator.monitor = MagicMock()
    orchestrator.state_tracker = MagicMock()

    records = []
    sink_id = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        await orchestrator._perform_migration()
    finally:
        logger.remove(sink_id)

    events = [
        record["extra"]
        for record in records
        if record["extra"].get("event_type") == "migration"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["generation"] == 3
    assert event["topology"] == "ring"
    assert len(event["migrants"]) == 2
    assert {
        (
            migrant["source_id"],
            migrant["source_island_id"],
            migrant["target_island_id"],
        )
        for migrant in event["migrants"]
    } == {("left-best", "0", "1"), ("right-best", "1", "0")}
    assert all(migrant["id"] != migrant["source_id"] for migrant in event["migrants"])


def test_migration_candidates_deduplicate_code_and_keep_best():
    """A source island must not export two IDs for the same implementation."""
    island = Island(
        island_id=0,
        population=[
            _algorithm("duplicate-low", island_id=0, score=1.0, code="return 1"),
            _algorithm("duplicate-high", island_id=0, score=3.0, code="return 1"),
            _algorithm("distinct", island_id=0, score=2.0, code="return 2"),
        ],
    )

    migrants = island.get_migrants(3, MigrationStrategy.BEST)

    assert {item.id for item in migrants} == {"duplicate-high", "distinct"}


def test_parent_selection_never_returns_duplicate_algorithm_ids():
    """Every parent selection strategy samples without replacement."""
    population = [
        _algorithm("a", island_id=0, score=3.0, code="a"),
        _algorithm("b", island_id=0, score=2.0, code="b"),
        _algorithm("c", island_id=0, score=1.0, code="c"),
    ]

    for strategy in ("tournament", "roulette", "rank", "random"):
        parents = BasePlanner.select_parents(None, population, 2, strategy)
        assert len(parents) == 2
        assert len({parent.id for parent in parents}) == 2


def test_parent_selection_deduplicates_identical_code_and_keeps_best():
    """Different IDs with the same code must not become two parents."""
    population = [
        _algorithm("duplicate-low", island_id=0, score=1.0, code="return 1"),
        _algorithm("duplicate-high", island_id=0, score=3.0, code="return 1"),
        _algorithm("distinct", island_id=0, score=2.0, code="return 2"),
    ]

    for strategy in ("tournament", "roulette", "rank", "random"):
        parents = BasePlanner.select_parents(None, population, 3, strategy)
        assert {parent.id for parent in parents} == {"duplicate-high", "distinct"}
        assert len({code_fingerprint(parent) for parent in parents}) == 2


def test_parent_selection_can_keep_duplicate_code_for_classic_island_ga():
    """Classic IslandGA may select distinct IDs even when their code is identical."""
    population = [
        _algorithm("duplicate-low", island_id=0, score=1.0, code="return 1"),
        _algorithm("duplicate-high", island_id=0, score=3.0, code="return 1"),
    ]

    parents = BasePlanner.select_parents(
        None,
        population,
        2,
        "random",
        deduplicate_code=False,
    )

    assert {parent.id for parent in parents} == {"duplicate-low", "duplicate-high"}


def test_diverse_survival_deduplicates_code_but_preserves_codeless_insights():
    """Exact code duplicates share one slot while codeless insights stay distinct."""
    duplicate_low = _algorithm("duplicate-low", island_id=0, score=1.0, code="return 1")
    duplicate_high = _algorithm("duplicate-high", island_id=0, score=3.0, code="return 1")
    codeless_a = Algorithm(
        id="codeless-a",
        insight_type=InsightType.REFLECTION,
        description="first reflection",
        island_id=0,
    )
    codeless_b = Algorithm(
        id="codeless-b",
        insight_type=InsightType.REFLECTION,
        description="second reflection",
        island_id=0,
    )

    survivors = select_diverse_survivors(
        [duplicate_low, duplicate_high, codeless_a, codeless_b],
        count=4,
        novelty_ratio=0,
    )

    assert {item.id for item in survivors} == {
        "duplicate-high",
        "codeless-a",
        "codeless-b",
    }


def test_strategy_spectrum_has_distinct_memory_and_search_anchor_roles():
    """Three islands map to success, correction, and memory-free exploration."""
    exploit, correct, explore = [build_island_strategy(index, 3, sample_index=0, sample_count=1) for index in range(3)]

    assert exploit.memory_policy == "success_only"
    assert exploit.success_memory_ratio == 1.0
    assert exploit.error_memory_ratio == 0.0
    assert exploit.independent_exploration is False

    assert correct.memory_policy == "corrective"
    assert correct.success_memory_ratio == pytest.approx(0.6)
    assert correct.error_memory_ratio == pytest.approx(0.4)
    assert correct.independent_exploration is False

    assert explore.memory_policy == "none"
    assert explore.memory_injection_probability == 0.0
    assert explore.random_restart_probability == pytest.approx(0.3)

    explore_candidates = [
        build_island_strategy(island_id=2, num_islands=3, sample_index=sample, sample_count=10) for sample in range(10)
    ]
    assert sum(profile.independent_exploration for profile in explore_candidates) == 3


def test_strategy_spectrum_supports_any_island_count_and_adapts_to_stagnation():
    """Profiles remain distinct for arbitrary counts and add scheduled restarts."""
    profiles = [build_island_strategy(i, 7, stagnation_generations=0) for i in range(7)]

    assert [profile.position for profile in profiles] == sorted(profile.position for profile in profiles)
    assert len({profile.exploration for profile in profiles}) == 7
    assert len({profile.success_memory_ratio for profile in profiles[:4]}) == 4
    assert len({profile.random_restart_probability for profile in profiles}) >= 4

    stalled = build_island_strategy(0, 7, stagnation_generations=6, stagnation_threshold=2)
    assert stalled.random_restart_probability > profiles[0].random_restart_probability

    scheduled = [build_island_strategy(2, 4, sample_index=index, sample_count=10) for index in range(10)]
    assert sum(profile.independent_exploration for profile in scheduled) == 1


def test_exploration_restart_ratio_is_user_configurable_and_validated():
    """The task config exposes an independent restart ratio with a safe default."""
    assert DiverseIslandGAConfig().exploration_restart_ratio == pytest.approx(0.3)
    assert DiverseIslandGAConfig(exploration_restart_ratio=0.65).exploration_restart_ratio == 0.65
    with pytest.raises(ValueError):
        DiverseIslandGAConfig(exploration_restart_ratio=1.1)


def test_fractional_restart_ratio_accumulates_across_small_generations():
    """A small island still receives its configured restart share over time."""
    candidates = [
        build_island_strategy(
            island_id=2,
            num_islands=3,
            sample_index=0,
            sample_count=1,
            generation=generation,
        )
        for generation in range(10)
    ]

    assert sum(profile.independent_exploration for profile in candidates) == 3


def test_diverse_survival_reserves_a_novel_candidate():
    """Mixed truncation reserves its configured novelty slot."""
    common = [_algorithm(f"common-{i}", island_id=0, score=10 - i, code=f"x = {i}\nreturn x") for i in range(4)]
    novel = _algorithm(
        "novel",
        island_id=0,
        score=1.0,
        code="graph = build_delaunay(points)\nreturn optimize(graph)",
    )

    survivors = select_diverse_survivors(
        common + [novel],
        count=3,
        novelty_ratio=1 / 3,
        archive_fingerprints=[code_fingerprint(item) for item in common[:2]],
    )

    assert len(survivors) == 3
    assert novel in survivors


@pytest.mark.asyncio
async def test_planner_falls_back_to_mutation_with_one_distinct_parent():
    """Crossover degrades to mutation instead of self-crossing."""
    crossover = SimpleNamespace(name="crossover_sampler", n_parents=2, sample=AsyncMock())
    mutation_result = _algorithm("child", island_id=0, score=0.0, code="child")
    mutation = SimpleNamespace(
        name="mutation_sampler",
        n_parents=1,
        sample=AsyncMock(return_value=mutation_result),
    )
    planner = object.__new__(LLMEvolutionPlanner)
    planner.samplers = [crossover, mutation]
    planner.sampler_map = {"crossover_sampler": crossover, "mutation_sampler": mutation}
    planner.sampler_selector = MagicMock(select=MagicMock(return_value=[crossover]))
    planner.config = SimpleNamespace(parent_selection_strategy="tournament")
    planner.state_tracker = MagicMock()

    parent = _algorithm("only-parent", island_id=0, score=1.0, code="parent")
    result = await planner.plan([parent, parent], generation=2, background="test")

    assert result is mutation_result
    mutation.sample.assert_awaited_once()
    assert mutation.sample.await_args.kwargs["parents"] == [parent]
    crossover.sample.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_creates_descendant_worktree_from_parent_commit():
    """The selected parent commit, not the default branch, must seed a child worktree."""
    worktree = WorktreeInfo(
        name="child",
        path="/tmp/child",
        branch="child",
        commit_hash="parent-commit",
        created_at=0,
        last_used_at=0,
    )
    version_control = MagicMock()
    version_control.create_worktree.return_value = VersionControlResult(
        success=True,
        data={"worktree": worktree},
        message="ok",
    )
    planner = object.__new__(LLMEvolutionPlanner)
    planner.version_control = version_control

    result = await planner.init(
        island_id=1,
        generation_id=2,
        algorithm_id="child",
        base_commit="parent-commit",
    )

    assert result is worktree
    version_control.create_worktree.assert_called_once_with(
        name="island_1_gen_2_ind_child",
        base_commit="parent-commit",
    )


def test_memory_strategy_enforces_success_only_and_corrective_quotas():
    """Memory roles never fill success slots with an unbounded error tail."""
    hits = [
        SimpleNamespace(id="good-a", memory="hex lattice phase sweep", memory_type="good_algorithm"),
        SimpleNamespace(id="good-b", memory="hex lattice offset sweep", memory_type="good_algorithm"),
        SimpleNamespace(id="good-c", memory="adaptive radius sweep", memory_type="good_algorithm"),
        SimpleNamespace(id="error-a", memory="avoid overlap rounding", memory_type="error_reflection"),
        SimpleNamespace(id="error-b", memory="avoid invalid boundary", memory_type="error_reflection"),
        SimpleNamespace(id="error-c", memory="avoid uniform shrinking", memory_type="error_reflection"),
    ]
    ranked = [TaskMemoryCandidate(key=hit.id, score=1 - index / 10, payload=hit) for index, hit in enumerate(hits)]
    exploit = _select_island_strategy_candidates(
        ranked,
        5,
        {
            "memory_policy": "success_only",
            "success_memory_ratio": 1.0,
            "error_memory_ratio": 0.0,
        },
    )
    correct = _select_island_strategy_candidates(
        ranked,
        5,
        {
            "memory_policy": "corrective",
            "success_memory_ratio": 0.6,
            "error_memory_ratio": 0.4,
        },
    )
    explore = _select_island_strategy_candidates(
        ranked,
        5,
        {"memory_policy": "none"},
    )

    assert [candidate.key for candidate in exploit] == ["good-a", "good-b", "good-c"]
    assert [candidate.key for candidate in correct] == [
        "good-a",
        "good-b",
        "good-c",
        "error-a",
        "error-b",
    ]
    assert explore == []


@pytest.mark.asyncio
async def test_planner_forces_parentless_init_for_independent_exploration():
    """An exploration candidate bypasses parent samplers and disables memory."""
    init_result = _algorithm("fresh", island_id=2, score=0.0, code="fresh")
    init = SimpleNamespace(
        name="init_sampler",
        n_parents=0,
        sample=AsyncMock(return_value=init_result),
    )
    mutation = SimpleNamespace(name="mutation_sampler", n_parents=1, sample=AsyncMock())
    planner = object.__new__(LLMEvolutionPlanner)
    planner.samplers = [init, mutation]
    planner.sampler_map = {"init_sampler": init, "mutation_sampler": mutation}
    planner.sampler_selector = MagicMock()
    planner.config = SimpleNamespace(parent_selection_strategy="tournament")
    planner.state_tracker = MagicMock()

    parent = _algorithm("parent", island_id=2, score=1.0, code="parent")
    result = await planner.plan(
        [parent],
        generation=3,
        island_strategy={"independent_exploration": True, "memory_policy": "none"},
    )

    assert result is init_result
    init.sample.assert_awaited_once()
    assert init.sample.await_args.kwargs["parents"] == []
    assert init.sample.await_args.kwargs["disable_memory"] is True
    mutation.sample.assert_not_awaited()
    planner.sampler_selector.select.assert_not_called()


@pytest.mark.asyncio
async def test_planner_excludes_init_sampler_from_normal_evolution():
    """Parentless restarts occur only when the island strategy requests one."""
    init = SimpleNamespace(name="init_sampler", n_parents=0, sample=AsyncMock())
    child = _algorithm("child", island_id=0, score=0.0, code="child")
    mutation = SimpleNamespace(
        name="mutation_sampler",
        n_parents=1,
        sample=AsyncMock(return_value=child),
    )
    planner = object.__new__(LLMEvolutionPlanner)
    planner.samplers = [init, mutation]
    planner.sampler_map = {"init_sampler": init, "mutation_sampler": mutation}
    planner.sampler_selector = MagicMock(select=MagicMock(return_value=[mutation]))
    planner.config = SimpleNamespace(parent_selection_strategy="tournament")
    planner.state_tracker = MagicMock()

    parent = _algorithm("parent", island_id=0, score=1.0, code="parent")
    result = await planner.plan(
        [parent],
        generation=2,
        island_strategy={"independent_exploration": False},
    )

    assert result is child
    planner.sampler_selector.select.assert_called_once_with(
        n=1,
        population=[parent],
        exclude_sampler_names={"init_sampler"},
    )
    init.sample.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_free_exploration_can_derive_from_a_parent():
    """A non-restart exploration candidate keeps lineage while skipping memory."""
    init = SimpleNamespace(name="init_sampler", n_parents=0, sample=AsyncMock())
    child = _algorithm("child", island_id=2, score=0.0, code="child")
    mutation = SimpleNamespace(
        name="mutation_sampler",
        n_parents=1,
        sample=AsyncMock(return_value=child),
    )
    planner = object.__new__(LLMEvolutionPlanner)
    planner.samplers = [init, mutation]
    planner.sampler_map = {"init_sampler": init, "mutation_sampler": mutation}
    planner.sampler_selector = MagicMock(select=MagicMock(return_value=[mutation]))
    planner.config = SimpleNamespace(parent_selection_strategy="tournament")
    planner.state_tracker = MagicMock()
    parent = _algorithm("parent", island_id=2, score=1.0, code="parent")

    result = await planner.plan(
        [parent],
        generation=2,
        island_strategy={"independent_exploration": False, "memory_policy": "none"},
    )

    assert result is child
    mutation.sample.assert_awaited_once()
    assert mutation.sample.await_args.kwargs["parents"] == [parent]
    assert mutation.sample.await_args.kwargs["disable_memory"] is True
    init.sample.assert_not_awaited()


def test_short_run_migration_waits_for_stagnation_and_runs_once():
    """Adaptive short runs migrate once and only after island stagnation."""
    orchestrator = object.__new__(DiverseIslandGAOrchestrator)
    orchestrator.config = DiverseIslandGAConfig(
        num_islands=3,
        max_generations=8,
        migration_interval=2,
        migration_stagnation_threshold=2,
        short_task_generation_threshold=10,
        short_task_max_migrations=1,
    )
    orchestrator.current_generation = 2
    orchestrator._last_migration_gen = 0
    orchestrator._migration_events = 0
    orchestrator.islands = [Island(island_id=index) for index in range(3)]

    assert orchestrator._should_migrate() is False
    orchestrator.islands[1].stagnation_generations = 2
    assert orchestrator._should_migrate() is True
    orchestrator._migration_events = 1
    assert orchestrator._should_migrate() is False
