"""DyCA (Dynamic Clustering Algorithm) orchestrator for LLM4AD.

Implements instance-aware multi-pool evolution with three heterogeneous pools:
- Generalist pool: global best algorithms
- Specialist pools: per-cluster optimized algorithms (one per cluster)
- Complementary pool: cross-cluster diversity algorithms

Key features:
- Per-instance evaluation and cluster-specific fitness
- Dynamic reclustering based on ARI stability
- SOS (Save Our Specialist) inter-cluster collaboration
- Macro (ARI-based) and micro (gap-based) resource allocation
- Anchor algorithm selection for instance feature extraction
"""

import asyncio
import math
import random
import time
import uuid
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.schema import DyCAConfig
from llm4ad.evaluator import EvaluationDispatcher
from llm4ad.infra.state import EvolutionState, GenerationMetrics, StateTracker
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator.base import (
    BaseOrchestrator,
    EvolutionCheckpoint,
    EvolutionResult,
)
from llm4ad.orchestrator.clustering_utils import (
    build_feature_matrix,
    cluster_instances,
    cluster_score,
    compute_ari,
    find_best_k,
    get_cluster_instance_names,
    get_instance_scores,
    get_unique_cluster_ids,
    select_anchor_algorithms,
    set_cluster_id,
    set_instance_scores,
    set_pool_type,
)
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.planner.base import Algorithm, BasePlanner

# ---------------------------------------------------------------------------
# Pool data structures
# ---------------------------------------------------------------------------


class ClusterArchive:
    """Elite and failure archive for a specialist cluster.

    Maintains a bounded archive of the best algorithms (elite)
    and recently failed/poor algorithms (failure) for a cluster.
    """

    def __init__(
        self,
        cluster_id: int,
        instance_names: list[str],
        elite_size: int = 5,
    ):
        """Initialize the cluster archive.

        Args:
            cluster_id: Cluster this archive belongs to.
            instance_names: Instance names in this cluster.
            elite_size: Maximum elite archive size.
        """
        self.cluster_id = cluster_id
        self.instance_names = instance_names
        self.elite_size = elite_size
        self.elite: list[Algorithm] = []
        self.failure: list[Algorithm] = []

    def update(self, algorithm: Algorithm) -> None:
        """Update archive with a new algorithm.

        If the algorithm is good enough, it enters the elite archive
        (worst elite is demoted). Otherwise it goes to the failure archive.

        Args:
            algorithm: Algorithm to consider for archival.
        """
        score = cluster_score(algorithm, self.instance_names)

        if len(self.elite) < self.elite_size:
            self.elite.append(algorithm)
            self.elite.sort(
                key=lambda a: cluster_score(a, self.instance_names),
                reverse=True,
            )
        else:
            worst_elite_score = cluster_score(self.elite[-1], self.instance_names)
            if score > worst_elite_score:
                demoted = self.elite.pop()
                self.failure.append(demoted)
                self.elite.append(algorithm)
                self.elite.sort(
                    key=lambda a: cluster_score(a, self.instance_names),
                    reverse=True,
                )
            else:
                self.failure.append(algorithm)

        # Keep failure archive bounded
        max_failure = self.elite_size * 2
        if len(self.failure) > max_failure:
            self.failure = self.failure[-max_failure:]

    def get_elite_best(self) -> Algorithm | None:
        """Get the best algorithm in the elite archive.

        Returns:
            Best algorithm or None if empty.
        """
        return self.elite[0] if self.elite else None


class ClusterUnit:
    """Specialist pool for a single cluster.

    Manages a population of algorithms optimized for the instances
    in this cluster, with stagnation tracking and SOS triggering.
    """

    def __init__(
        self,
        cluster_id: int,
        instance_names: list[str],
        max_size: int = 10,
        elite_archive_size: int = 5,
        sos_stagnation_threshold: int = 3,
    ):
        """Initialize the cluster unit.

        Args:
            cluster_id: ID of this cluster.
            instance_names: Instance names in this cluster.
            max_size: Maximum population size.
            elite_archive_size: Elite archive size.
            sos_stagnation_threshold: Generations without improvement to trigger SOS.
        """
        self.cluster_id = cluster_id
        self.instance_names = instance_names
        self.max_size = max_size
        self.population: list[Algorithm] = []
        self.archive = ClusterArchive(cluster_id, instance_names, elite_archive_size)
        self.sos_stagnation_threshold = sos_stagnation_threshold

        # Tracking
        self.best_score: float = float("-inf")
        self.stagnation_count: int = 0
        self.gap: float = 1.0  # Distance to objective (1.0 = far, 0.0 = reached)

    def add_algorithm(self, algorithm: Algorithm) -> bool:
        """Add an algorithm to this specialist pool.

        Rejects algorithms with no valid scores on this cluster's instances
        (cluster_score is -inf), matching the original DyCA behavior.

        Args:
            algorithm: Algorithm to add.

        Returns:
            True if accepted, False if rejected.
        """
        score = cluster_score(algorithm, self.instance_names)
        if math.isinf(score):
            return False

        set_cluster_id(algorithm, self.cluster_id)
        set_pool_type(algorithm, "specialist")
        self.population.append(algorithm)
        self.archive.update(algorithm)

        # Update best score and stagnation
        if score > self.best_score:
            self.best_score = score
            self.stagnation_count = 0
        else:
            self.stagnation_count += 1

        # Trim population if over capacity
        if len(self.population) > self.max_size:
            self.population.sort(
                key=lambda a: cluster_score(a, self.instance_names),
                reverse=True,
            )
            self.population = self.population[:self.max_size]
        return True

    def select_parent(self, tournament_size: int = 3) -> Algorithm | None:
        """Select a parent from this cluster using tournament selection.

        Args:
            tournament_size: Tournament size.

        Returns:
            Selected parent or None if empty.
        """
        if not self.population:
            return self.archive.get_elite_best()

        evaluated = [a for a in self.population if a.is_evaluated()]
        if not evaluated:
            return random.choice(self.population) if self.population else None

        k = min(tournament_size, len(evaluated))
        tournament = random.sample(evaluated, k)
        return max(tournament, key=lambda a: cluster_score(a, self.instance_names))

    def needs_sos(self) -> bool:
        """Check if this cluster needs SOS (Save Our Specialist).

        Returns:
            True if stagnation exceeds threshold.
        """
        return self.stagnation_count >= self.sos_stagnation_threshold

    def update_gap(self, global_best_score: float) -> None:
        """Update gap metric based on global best.

        Uses absolute distance (matching original DyCA formula).

        Args:
            global_best_score: Best score across all populations.
        """
        if math.isinf(self.best_score):
            self.gap = 1.0
        else:
            self.gap = max(0.0, global_best_score - self.best_score)


class ComplementaryPool:
    """Cross-cluster diversity pool.

    Maintains algorithms that provide complementary coverage
    across different clusters. Uses structure-aware greedy
    marginal gain selection for survival.
    """

    def __init__(
        self,
        max_size: int = 10,
        cluster_labels: dict[str, int] | None = None,
    ):
        """Initialize the complementary pool.

        Args:
            max_size: Maximum pool size.
            cluster_labels: Instance-to-cluster mapping.
        """
        self.max_size = max_size
        self.cluster_labels = cluster_labels or {}
        self.population: list[Algorithm] = []

    def add_algorithm(self, algorithm: Algorithm) -> None:
        """Add an algorithm to the complementary pool.

        If over capacity, uses marginal gain selection to determine survival.

        Args:
            algorithm: Algorithm to add.
        """
        set_pool_type(algorithm, "complementary")
        self.population.append(algorithm)

        if len(self.population) > self.max_size:
            self._survival_selection()

    def _survival_selection(self) -> None:
        """Structure-aware greedy marginal gain survival selection.

        Greedily selects algorithms that maximize marginal coverage
        gain across all clusters. Prioritizes algorithms that are
        uniquely good on underrepresented clusters.
        """
        if not self.cluster_labels:
            # Fallback: keep top by aggregate score
            self.population.sort(key=lambda a: a.score, reverse=True)
            self.population = self.population[:self.max_size]
            return

        cluster_ids = sorted(set(self.cluster_labels.values()))
        cluster_instance_map: dict[int, list[str]] = {
            cid: [n for n, c in self.cluster_labels.items() if c == cid]
            for cid in cluster_ids
        }

        selected: list[Algorithm] = []
        remaining = self.population.copy()

        for _ in range(min(self.max_size, len(remaining))):
            best_candidate = None
            best_gain = -float("inf")

            for candidate in remaining:
                if candidate in selected:
                    continue
                # Marginal gain: sum of improvements on each cluster
                gain = 0.0
                for _cid, instances in cluster_instance_map.items():
                    candidate_cs = cluster_score(candidate, instances)
                    current_best = max(
                        (cluster_score(s, instances) for s in selected),
                        default=0.0,
                    )
                    # Gain is the improvement over current best
                    gain += max(0.0, candidate_cs - current_best)

                if gain > best_gain:
                    best_gain = gain
                    best_candidate = candidate

            if best_candidate:
                selected.append(best_candidate)
                remaining.remove(best_candidate)

        self.population = selected

    def select_pair(self) -> tuple[Algorithm, Algorithm] | None:
        """Select a complementary pair of algorithms.

        Selects two algorithms that are most different in their
        per-instance performance (maximize Manhattan distance).

        Returns:
            Tuple of two algorithms or None if insufficient population.
        """
        evaluated = [a for a in self.population if a.is_evaluated()]
        if len(evaluated) < 2:
            return None

        # Sample candidates to avoid O(n^2)
        sample_size = min(len(evaluated), 10)
        candidates = random.sample(evaluated, sample_size)

        best_pair = None
        best_dist = -1.0

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a_scores = get_instance_scores(candidates[i])
                b_scores = get_instance_scores(candidates[j])
                common = set(a_scores.keys()) & set(b_scores.keys())
                if not common:
                    continue
                dist = sum(abs(a_scores[k] - b_scores[k]) for k in common)
                if dist > best_dist:
                    best_dist = dist
                    best_pair = (candidates[i], candidates[j])

        return best_pair

    def update_cluster_labels(self, cluster_labels: dict[str, int]) -> None:
        """Update instance-to-cluster mapping.

        Args:
            cluster_labels: New cluster labels.
        """
        self.cluster_labels = cluster_labels


# ---------------------------------------------------------------------------
# DyCA Orchestrator
# ---------------------------------------------------------------------------


@BaseOrchestrator.register("dyca")
class DyCAOrchestrator(BaseOrchestrator):
    """DyCA orchestrator for instance-aware multi-pool evolution.

    Coordinates the evolution process using three heterogeneous pools
    (generalist, specialist, complementary) with dynamic clustering
    and adaptive resource allocation.
    """

    def __init__(
            self,
            planner: BasePlanner,
            coder: BaseCoder,
            dispatcher: EvaluationDispatcher,
            monitor: Any,
            config: DyCAConfig,
            version_control: BaseVersionControl,
            state_tracker: StateTracker,
            background: str = "",
            embedding_client: EmbeddingClient = None,
    ):
        """Initialize DyCA orchestrator.

        Args:
            planner: Planner for generating new algorithm insights.
            coder: Coder for implementing algorithms from insights.
            dispatcher: Dispatcher for scheduling evaluation tasks.
            monitor: Monitor for tracking evolution progress.
            config: DyCA configuration.
            version_control: Version control for isolated workspaces.
            state_tracker: Tracker for managing evolutionary state.
            background: Problem background description.
            embedding_client: EmbeddingClient for embedding generation.
        """
        super().__init__(planner, coder, dispatcher, monitor, config, state_tracker, background, embedding_client)
        self.config: DyCAConfig = config
        self.version_control = version_control

        # Data files (instances)
        self.data_files: list[str] = list(dispatcher._data_files)

        # Pool structures
        self.generalist_pool: list[Algorithm] = []
        self.specialist_pools: dict[int, ClusterUnit] = {}
        self.complementary_pool = ComplementaryPool(
            max_size=config.complementary_pool_size,
        )

        # Clustering state
        self.cluster_labels: dict[str, int] = {}
        self.previous_cluster_labels: dict[str, int] = {}
        self.anchor_algorithms: list[Algorithm] = []
        self.current_ari: float = 0.0

        # Global state
        self.global_best: Algorithm | None = None
        self.all_algorithms: list[Algorithm] = []  # Complete history

        # Per-generation token accumulators
        self._gen_tokens: int = 0
        self._gen_algo_count: int = 0

        # Concurrency limit for individual generation pipelines
        self._llm_semaphore: asyncio.Semaphore | None = None
        if config.max_llm_concurrency is not None:
            self._llm_semaphore = asyncio.Semaphore(config.max_llm_concurrency)
            logger.info(
                "DyCAOrchestrator: max_llm_concurrency=%d",
                config.max_llm_concurrency,
            )

        # Import DyCA samplers to trigger registration
        import llm4ad.planner.sampler.dyca_samplers  # noqa: F401

        # Ensure DyCA samplers are loaded into the planner's sampler_map.
        # The planner config merging in llm4ad.py only passes evolution config
        # to the planner, so the planner.samplers section is not seen and DyCA
        # samplers won't be auto-loaded. We fix this here by manually
        # instantiating any missing DyCA samplers.
        self._ensure_dyca_samplers_loaded()

    def _ensure_dyca_samplers_loaded(self) -> None:
        """Ensure all DyCA-specific samplers are in the planner's sampler_map.

        The planner may not have loaded these samplers because the config
        merging path in llm4ad.py does not include the ``planner`` YAML
        section in the dict passed to the planner.  We detect missing
        DyCA samplers and instantiate them directly from the registry.
        """
        dyca_sampler_names = [
            "e1_sampler",
            "e2_sampler",
            "m1_sampler",
            "m2_sampler",
            "summary_sampler",
            "complementary_cross_sampler",
        ]

        missing = [
            name for name in dyca_sampler_names
            if name not in self.planner.sampler_map
        ]

        if not missing:
            return

        logger.info(
            f"DyCA: manually loading missing samplers: {missing}"
        )

        from llm4ad.planner.sampler.base import BaseSampler

        for name in missing:
            try:
                sampler = BaseSampler.create(
                    name,
                    provider=self.planner.provider,
                    memory=self.planner.memory,
                    config={},
                    analyzed_repository=self.planner.analyzed_repository,
                )
                self.planner.sampler_map[name] = sampler
                self.planner.samplers.append(sampler)
                logger.info(f"DyCA: loaded sampler '{name}'")
            except Exception as e:
                logger.warning(f"DyCA: failed to load sampler '{name}': {e}")

    # ------------------------------------------------------------------
    # Core individual generation (follows island_ga pattern)
    # ------------------------------------------------------------------

    async def _limited_generate(
        self,
        parents: list[Algorithm],
        generation: int,
        sampler_kwargs: dict[str, Any] | None = None,
    ) -> Algorithm | None:
        """Wrap ``generate_new_individual`` with the optional LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await self.generate_new_individual(
                    parents, generation, sampler_kwargs
                )
        return await self.generate_new_individual(
            parents, generation, sampler_kwargs
        )

    async def _limited_coro(self, coro: Any) -> Any:
        """Wrap an arbitrary coroutine with the optional LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await coro
        return await coro

    async def generate_new_individual(
        self,
        parents: list[Algorithm],
        generation: int,
        sampler_kwargs: dict[str, Any] | None = None,
    ) -> Algorithm | None:
        """Generate a new individual following the planner → coder → evaluate pipeline.

        Args:
            parents: Parent algorithms for context.
            generation: Current generation number.
            sampler_kwargs: Additional kwargs passed to planner.plan().

        Returns:
            Evaluated algorithm or None on failure.
        """
        try:
            algorithm_id = uuid.uuid4().hex[:12]

            # 1. Create worktree
            worktree = await self.planner.init(
                island_id=0,
                generation_id=self.current_generation,
                algorithm_id=algorithm_id,
            )
            if not worktree:
                return None

            # 2. Generate algorithm insight
            kwargs: dict[str, Any] = {}
            if sampler_kwargs:
                kwargs.update(sampler_kwargs)

            algorithm: Algorithm = await self.planner.plan(
                population=parents,
                generation=generation,
                **kwargs,
            )
            algorithm.id = algorithm_id
            algorithm.worktree = worktree

            logger.info(
                f"Generated algorithm ({algorithm_id}) gen {generation}: {algorithm.name}"
            )

            # Export insight stage
            if self.state_tracker.generated_dir:
                algorithm.write(
                    self.state_tracker.generated_dir,
                    stage="insight",
                    generation=generation,
                )
                logger.info("Successfully written to the Generated directory")

            # 3. Implement
            algorithm = await self.planner.implement(
                algorithm=algorithm, worktree=worktree,
            )

            # 3.5 Build verification
            await self.planner.build(algorithm=algorithm, worktree=worktree)

            # 4. Commit
            self.version_control.commit_changes(
                worktree=worktree,
                message=(
                    f"feat: `{algorithm.name}` gen {generation}\n\n"
                    f"{algorithm.description}"
                ),
            )

            # 5. Get changed files
            changed_files = self.version_control.get_changed_files(
                commit_hash=worktree.commit_hash,
            )
            for file_info in changed_files:
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Populate diff-based workflow metadata
            algorithm.changed_files = [
                f.get("file_path", "") for f in changed_files
            ]
            diff_stats = self.version_control.get_diff_stats(
                commit_hash=worktree.commit_hash,
            )
            algorithm.lines_added = diff_stats.get("additions", 0)
            algorithm.lines_removed = diff_stats.get("deletions", 0)

            # 6. Evaluate per-instance
            await self._evaluate_per_instance(algorithm)

            # Export evaluation stage
            if self.state_tracker.generated_dir:
                algorithm.write(
                    self.state_tracker.generated_dir,
                    stage="evaluation",
                    generation=generation,
                )
                self._schedule_embedding_save(algorithm)

            # Accumulate token usage
            if algorithm.generation_meta and algorithm.generation_meta.tokens_used > 0:
                self._gen_tokens += algorithm.generation_meta.tokens_used
                self._gen_algo_count += 1

            return algorithm

        except Exception as e:
            logger.warning(f"Skipping individual in generation {generation}: {e}")
            return None

    # ------------------------------------------------------------------
    # Per-instance evaluation
    # ------------------------------------------------------------------

    async def _evaluate_per_instance(self, algorithm: Algorithm) -> None:
        """Evaluate algorithm on each data file separately.

        Stores per-instance scores in custom_metadata and sets
        the aggregate score as the mean.

        Args:
            algorithm: Algorithm to evaluate (must have worktree).
        """
        if algorithm.worktree is None:
            logger.warning(f"Algorithm {algorithm.name} has no worktree for evaluation")
            return

        results = await self.dispatcher.dispatch_batch(algorithms=[algorithm])

        per_instance: dict[str, float] = {}
        # dispatch_batch returns one *aggregated* result per algorithm,
        # but per-instance scores are preserved in metadata["per_instance"].
        agg = results[0] if results else None
        if agg and agg.metadata:
            per_instance_list = agg.metadata.get("per_instance", [])
            for data_file, inst in zip(self.data_files, per_instance_list, strict=False):
                if inst.get("success", False):
                    per_instance[data_file] = inst["score"]
                else:
                    # Track failed instances as -inf so clustering sees them
                    per_instance[data_file] = float("-inf")

        set_instance_scores(algorithm, per_instance)

        # Compute global score (all-or-nothing, matches original behavior):
        # If ALL instances passed → average of all scores.
        # If ANY instance failed → score = -inf (algorithm is penalised in
        # the generalist pool but can still join specialist clusters where
        # its cluster_score is finite).
        valid_scores = [v for v in per_instance.values() if not math.isinf(v)]
        if len(valid_scores) == len(self.data_files):
            avg_score = sum(valid_scores) / len(self.data_files)
            algorithm.set_evaluation_result(score=avg_score)
            logger.debug(
                f"Algorithm {algorithm.id}: avg={avg_score:.4f}, "
                f"instances={len(valid_scores)}/{len(self.data_files)}"
            )
        elif valid_scores:
            # Partial success — record -inf so generalist pool de-prioritises
            # this algorithm, but per-instance scores are still available for
            # specialist pool cluster_score computation.
            algorithm.set_evaluation_result(score=float("-inf"))
            logger.debug(
                f"Algorithm {algorithm.id}: score=-inf "
                f"(only {len(valid_scores)}/{len(self.data_files)} instances passed)"
            )
        else:
            # All instances failed — clean up worktree
            if algorithm.worktree:
                self.version_control.delete_worktree(algorithm.worktree, force=True)

    # ------------------------------------------------------------------
    # Pool registration
    # ------------------------------------------------------------------

    def _register_to_pools(self, algorithm: Algorithm) -> None:
        """Register a new algorithm to all three pools.

        - Generalist: top-N by aggregate score
        - Specialist: to matching cluster(s)
        - Complementary: if it provides cross-cluster value

        Args:
            algorithm: Evaluated algorithm with per_instance_scores.
        """
        if not algorithm.is_evaluated():
            return

        self.all_algorithms.append(algorithm)

        # Generalist pool: keep top by aggregate score
        self.generalist_pool.append(algorithm)
        self.generalist_pool.sort(key=lambda a: a.score, reverse=True)
        if len(self.generalist_pool) > self.config.generalist_pool_size:
            removed = self.generalist_pool[self.config.generalist_pool_size:]
            self.generalist_pool = self.generalist_pool[:self.config.generalist_pool_size]
            for r in removed:
                if r.worktree and r.id not in self._get_all_pool_members():
                    self.version_control.delete_worktree(r.worktree)

        # Specialist pools: try to register to ALL clusters (original DyCA
        # behaviour).  Each ClusterUnit.add_algorithm independently decides
        # whether to accept based on its own cluster_score for the algorithm.
        if self.cluster_labels:
            for _cid, pool in self.specialist_pools.items():
                pool.add_algorithm(algorithm)

        # Complementary pool: add all, let survival selection trim
        self.complementary_pool.add_algorithm(algorithm)

        # Update global best
        if self.global_best is None or algorithm.score > self.global_best.score:
            self.global_best = algorithm

    def _find_best_cluster(self, algorithm: Algorithm) -> int | None:
        """Find which cluster this algorithm is best suited for.

        Args:
            algorithm: Algorithm with per_instance_scores.

        Returns:
            Best cluster ID or None.
        """
        if not self.cluster_labels:
            return None

        cluster_ids = get_unique_cluster_ids(self.cluster_labels)
        best_cid = None
        best_score = -float("inf")

        for cid in cluster_ids:
            instances = get_cluster_instance_names(self.cluster_labels, cid)
            cs = cluster_score(algorithm, instances)
            if math.isinf(cs):
                continue
            if cs > best_score:
                best_score = cs
                best_cid = cid

        return best_cid

    def _get_all_pool_members(self) -> set[str]:
        """Get IDs of all algorithms currently in any pool.

        Returns:
            Set of algorithm IDs.
        """
        ids: set[str] = set()
        for a in self.generalist_pool:
            ids.add(a.id)
        for cu in self.specialist_pools.values():
            for a in cu.population:
                ids.add(a.id)
        for a in self.complementary_pool.population:
            ids.add(a.id)
        return ids

    # ------------------------------------------------------------------
    # Resource allocation
    # ------------------------------------------------------------------

    def _allocate_resources(self) -> list[dict[str, Any]]:
        """Allocate offspring generation resources across pools.

        Macro allocation: ARI-based (low ARI → more complementary).
        Micro allocation: gap-based (large gap → more resources).

        Returns:
            List of task specs: [{'pool': ..., 'cluster_id': ..., 'operator': ...}].
        """
        n_offspring = self.config.offspring_per_generation
        tasks: list[dict[str, Any]] = []

        # Macro: decide complementary vs specialist ratio
        if self.current_ari < self.config.ari_threshold:
            comp_ratio = min(0.5, self.config.base_complementary_ratio * 2)
        else:
            comp_ratio = self.config.base_complementary_ratio

        n_complementary = max(1, int(n_offspring * comp_ratio))
        n_specialist = n_offspring - n_complementary

        # Complementary tasks
        for _ in range(n_complementary):
            tasks.append({"pool": "complementary", "operator": "complementary_cross"})

        # Micro: distribute specialist resources by gap (with circuit breaker)
        if self.specialist_pools:
            gaps = {}
            for cid, cu in self.specialist_pools.items():
                raw_gap = cu.gap
                # Circuit breaker: decay weight for stagnating clusters
                # to prevent black-hole resource hoarding (original DyCA)
                stagnation = cu.stagnation_count
                decay = max(0.05, 0.95 ** stagnation) if stagnation > 0 else 1.0
                gaps[cid] = raw_gap * decay
            total_gap = sum(gaps.values())

            for cid, cu in self.specialist_pools.items():
                fraction = gaps[cid] / total_gap if total_gap > 0 else 1.0 / len(self.specialist_pools)

                n_for_cluster = max(1, round(n_specialist * fraction))

                # Check SOS
                if cu.needs_sos():
                    tasks.append({
                        "pool": "specialist",
                        "cluster_id": cid,
                        "operator": "sos",
                    })
                    n_for_cluster = max(0, n_for_cluster - 1)

                # Distribute operators
                for _i in range(n_for_cluster):
                    op = random.choice(["e1", "e2", "m1", "m2"])
                    tasks.append({
                        "pool": "specialist",
                        "cluster_id": cid,
                        "operator": op,
                    })

        return tasks

    # ------------------------------------------------------------------
    # Operator dispatch
    # ------------------------------------------------------------------

    async def _execute_task(
        self,
        task: dict[str, Any],
        generation: int,
        background: str,
    ) -> Algorithm | None:
        """Execute a single resource allocation task.

        Args:
            task: Task specification from _allocate_resources().
            generation: Current generation.
            background: Problem background.

        Returns:
            New algorithm or None on failure.
        """
        pool = task["pool"]
        operator = task["operator"]

        if pool == "complementary":
            return await self._execute_complementary_task(generation, background)
        elif pool == "specialist":
            cluster_id = task["cluster_id"]
            if operator == "sos":
                return await self._execute_sos_task(cluster_id, generation, background)
            return await self._execute_specialist_task(
                cluster_id, operator, generation, background,
            )
        return None

    async def _execute_specialist_task(
        self,
        cluster_id: int,
        operator: str,
        generation: int,
        background: str,
    ) -> Algorithm | None:
        """Execute a specialist pool task.

        Args:
            cluster_id: Target cluster.
            operator: Operator name ('e1', 'e2', 'm1', 'm2').
            generation: Current generation.
            background: Problem background.

        Returns:
            New algorithm or None.
        """
        cu = self.specialist_pools.get(cluster_id)
        if cu is None:
            return None

        parent = cu.select_parent()
        if parent is None:
            # Fallback to generalist
            if self.generalist_pool:
                parent = random.choice(self.generalist_pool)
            else:
                return None

        instances = cu.instance_names
        top_k = sorted(
            [a for a in cu.population if a.is_evaluated()],
            key=lambda a: cluster_score(a, instances),
            reverse=True,
        )[:3]

        # The planner needs to know which sampler to use.
        # For DyCA, we bypass the planner's internal sampler selection
        # and directly generate using the sampler.
        sampler_name = f"{operator}_sampler"
        sampler = self.planner.sampler_map.get(sampler_name)

        if sampler is None:
            logger.warning(f"Sampler {sampler_name} not found, falling back to mutation_sampler")
            sampler = self.planner.sampler_map.get("mutation_sampler")
            if sampler is None:
                return None

        try:
            algorithm_id = uuid.uuid4().hex[:12]
            worktree = await self.planner.init(
                island_id=0,
                generation_id=generation,
                algorithm_id=algorithm_id,
            )
            if not worktree:
                return None

            algorithm = await sampler.sample(
                population=cu.population,
                generation=generation,
                parent=parent,
                parents=[parent],
                cluster_id=cluster_id,
                cluster_instances=instances,
                background=background,
                top_k_algorithms=top_k,
            )
            algorithm.id = algorithm_id
            algorithm.worktree = worktree

            # Implement
            algorithm = await self.planner.implement(algorithm=algorithm, worktree=worktree)

            # Commit
            self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: `{algorithm.name}` [{operator}] cluster {cluster_id}\n\n{algorithm.description}",
            )

            # Get changed files
            changed_files = self.version_control.get_changed_files(commit_hash=worktree.commit_hash)
            for file_info in changed_files:
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Populate diff-based workflow metadata
            algorithm.changed_files = [
                f.get("file_path", "") for f in changed_files
            ]
            diff_stats = self.version_control.get_diff_stats(
                commit_hash=worktree.commit_hash,
            )
            algorithm.lines_added = diff_stats.get("additions", 0)
            algorithm.lines_removed = diff_stats.get("deletions", 0)

            # Evaluate
            await self._evaluate_per_instance(algorithm)

            # Accumulate token usage
            if algorithm.generation_meta and algorithm.generation_meta.tokens_used > 0:
                self._gen_tokens += algorithm.generation_meta.tokens_used
                self._gen_algo_count += 1

            return algorithm

        except Exception as e:
            logger.warning(f"Specialist task failed (cluster {cluster_id}, {operator}): {e}")
            return None

    async def _execute_complementary_task(
        self,
        generation: int,
        background: str,
    ) -> Algorithm | None:
        """Execute a complementary pool crossover task.

        Args:
            generation: Current generation.
            background: Problem background.

        Returns:
            New algorithm or None.
        """
        pair = self.complementary_pool.select_pair()
        if pair is None:
            # Not enough algorithms for crossover; try generalist
            if len(self.generalist_pool) >= 2:
                pair = tuple(random.sample(self.generalist_pool, 2))
            else:
                return None

        parent1, parent2 = pair
        cid1 = parent1.custom_metadata.get("cluster_id", 0)
        cid2 = parent2.custom_metadata.get("cluster_id", 1)

        sampler = self.planner.sampler_map.get("complementary_cross_sampler")
        if sampler is None:
            sampler = self.planner.sampler_map.get("crossover_sampler")
            if sampler is None:
                return None

        try:
            algorithm_id = uuid.uuid4().hex[:12]
            worktree = await self.planner.init(
                island_id=0,
                generation_id=generation,
                algorithm_id=algorithm_id,
            )
            if not worktree:
                return None

            ci1 = get_cluster_instance_names(self.cluster_labels, cid1) if self.cluster_labels else []
            ci2 = get_cluster_instance_names(self.cluster_labels, cid2) if self.cluster_labels else []

            algorithm = await sampler.sample(
                population=self.generalist_pool,
                generation=generation,
                parents=[parent1, parent2],
                parent1=parent1,
                parent2=parent2,
                background=background,
                cluster_id_1=cid1,
                cluster_id_2=cid2,
                cluster_instances_1=ci1,
                cluster_instances_2=ci2,
            )
            algorithm.id = algorithm_id
            algorithm.worktree = worktree

            algorithm = await self.planner.implement(algorithm=algorithm, worktree=worktree)

            self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: `{algorithm.name}` [complementary cross]\n\n{algorithm.description}",
            )

            changed_files = self.version_control.get_changed_files(commit_hash=worktree.commit_hash)
            for file_info in changed_files:
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Populate diff-based workflow metadata
            algorithm.changed_files = [
                f.get("file_path", "") for f in changed_files
            ]
            diff_stats = self.version_control.get_diff_stats(
                commit_hash=worktree.commit_hash,
            )
            algorithm.lines_added = diff_stats.get("additions", 0)
            algorithm.lines_removed = diff_stats.get("deletions", 0)

            await self._evaluate_per_instance(algorithm)

            # Accumulate token usage
            if algorithm.generation_meta and algorithm.generation_meta.tokens_used > 0:
                self._gen_tokens += algorithm.generation_meta.tokens_used
                self._gen_algo_count += 1

            return algorithm

        except Exception as e:
            logger.warning(f"Complementary crossover failed: {e}")
            return None

    async def _execute_sos_task(
        self,
        cluster_id: int,
        generation: int,
        background: str,
    ) -> Algorithm | None:
        """Execute SOS (Save Our Specialist) inter-cluster collaboration.

        Pulls a good algorithm from another cluster and tries to
        create a crossover to break the stagnation.

        Args:
            cluster_id: Stagnating cluster ID.
            generation: Current generation.
            background: Problem background.

        Returns:
            New algorithm or None.
        """
        cu = self.specialist_pools.get(cluster_id)
        if cu is None:
            return None

        # Find helper from another cluster
        other_clusters = [
            (cid, other_cu)
            for cid, other_cu in self.specialist_pools.items()
            if cid != cluster_id and other_cu.archive.get_elite_best() is not None
        ]
        if not other_clusters:
            # No helper available, try generalist
            if self.generalist_pool:
                helper = max(self.generalist_pool, key=lambda a: a.score)
            else:
                return None
        else:
            # Pick the best helper from another cluster
            helper_cid, helper_cu = max(
                other_clusters,
                key=lambda x: x[1].best_score,
            )
            helper = helper_cu.archive.get_elite_best()

        local_parent = cu.select_parent()
        if local_parent is None:
            return None

        # Use summary sampler for SOS
        sampler = self.planner.sampler_map.get("summary_sampler")
        if sampler is None:
            sampler = self.planner.sampler_map.get("crossover_sampler")
            if sampler is None:
                return None

        try:
            algorithm_id = uuid.uuid4().hex[:12]
            worktree = await self.planner.init(
                island_id=0,
                generation_id=generation,
                algorithm_id=algorithm_id,
            )
            if not worktree:
                return None

            algorithm = await sampler.sample(
                population=cu.population,
                generation=generation,
                parents=[local_parent, helper],
                parent=local_parent,
                background=background,
                cluster_id=cluster_id,
                cluster_instances=cu.instance_names,
            )
            algorithm.id = algorithm_id
            algorithm.worktree = worktree

            algorithm = await self.planner.implement(algorithm=algorithm, worktree=worktree)

            self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: `{algorithm.name}` [SOS] cluster {cluster_id}\n\n{algorithm.description}",
            )

            changed_files = self.version_control.get_changed_files(commit_hash=worktree.commit_hash)
            for file_info in changed_files:
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Populate diff-based workflow metadata
            algorithm.changed_files = [
                f.get("file_path", "") for f in changed_files
            ]
            diff_stats = self.version_control.get_diff_stats(
                commit_hash=worktree.commit_hash,
            )
            algorithm.lines_added = diff_stats.get("additions", 0)
            algorithm.lines_removed = diff_stats.get("deletions", 0)

            await self._evaluate_per_instance(algorithm)

            # Reset stagnation on SOS success
            if algorithm.is_evaluated():
                cu.stagnation_count = 0
                logger.info(f"SOS successful for cluster {cluster_id}")

            # Accumulate token usage
            if algorithm.generation_meta and algorithm.generation_meta.tokens_used > 0:
                self._gen_tokens += algorithm.generation_meta.tokens_used
                self._gen_algo_count += 1

            return algorithm

        except Exception as e:
            logger.warning(f"SOS failed for cluster {cluster_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Clustering and anchor management
    # ------------------------------------------------------------------

    def _update_anchors_and_recluster(self) -> None:
        """Update anchor algorithms and recluster instances if needed."""
        evaluated = [a for a in self.all_algorithms if a.is_evaluated()]
        if len(evaluated) < self.config.n_anchors:
            return

        # Select new anchors
        self.anchor_algorithms = select_anchor_algorithms(
            evaluated,
            self.data_files,
            n_anchors=self.config.n_anchors,
        )

        if not self.anchor_algorithms:
            return

        # Build feature matrix and cluster
        feature_matrix = build_feature_matrix(self.anchor_algorithms, self.data_files)

        # Determine number of clusters
        if self.config.n_clusters == 0:
            n_clusters = find_best_k(feature_matrix)
            logger.info(f"Auto-selected K={n_clusters} for instance clustering")
        else:
            n_clusters = self.config.n_clusters

        new_labels = cluster_instances(
            feature_matrix,
            self.data_files,
            method=self.config.clustering_method,
            n_clusters=n_clusters,
        )

        # Compute ARI against previous clustering
        if self.cluster_labels:
            self.current_ari = compute_ari(self.cluster_labels, new_labels)
            logger.info(f"Reclustering ARI: {self.current_ari:.4f}")

            if self.current_ari >= self.config.ari_threshold:
                logger.info("Clustering stable, keeping current labels")
                return

        # Apply new labels
        self.previous_cluster_labels = self.cluster_labels.copy()
        self.cluster_labels = new_labels
        self.complementary_pool.update_cluster_labels(new_labels)

        # Rebuild specialist pools
        self._rebuild_specialist_pools()

    def _rebuild_specialist_pools(self) -> None:
        """Rebuild specialist pools after reclustering.

        Creates new ClusterUnit for each cluster and redistributes
        existing algorithms based on their best-matching cluster.
        """
        cluster_ids = get_unique_cluster_ids(self.cluster_labels)

        new_pools: dict[int, ClusterUnit] = {}
        for cid in cluster_ids:
            instances = get_cluster_instance_names(self.cluster_labels, cid)
            new_pools[cid] = ClusterUnit(
                cluster_id=cid,
                instance_names=instances,
                max_size=self.config.specialist_pool_size,
                elite_archive_size=self.config.elite_archive_size,
                sos_stagnation_threshold=self.config.sos_stagnation_threshold,
            )

        # Redistribute existing algorithms
        all_in_pools: list[Algorithm] = []
        for cu in self.specialist_pools.values():
            all_in_pools.extend(cu.population)

        for algo in all_in_pools:
            for _cid, pool in new_pools.items():
                pool.add_algorithm(algo)

        self.specialist_pools = new_pools
        logger.info(
            f"Rebuilt {len(new_pools)} specialist pools: "
            + ", ".join(
                f"cluster {cid}: {len(cu.population)} algos"
                for cid, cu in new_pools.items()
            )
        )

    # ------------------------------------------------------------------
    # BaseOrchestrator interface
    # ------------------------------------------------------------------

    async def initialize_population(self) -> list[Algorithm]:
        """Initialize population and perform first clustering.

        1. Generate initial algorithms using init_sampler.
        2. Evaluate per-instance.
        3. Select anchors and cluster instances.
        4. Initialize pools.

        Returns:
            Initial population.
        """
        logger.info(
            f"Initializing DyCA population: {self.config.population_size} individuals, "
            f"{len(self.data_files)} instances"
        )

        # Generate initial population
        tasks = [
            self._limited_generate(
                parents=[], generation=0,
            )
            for _ in range(self.config.population_size)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        initial_pop: list[Algorithm] = [
            r for r in results
            if r is not None and not isinstance(r, BaseException)
        ]

        logger.info(f"Generated {len(initial_pop)} initial algorithms")

        if not initial_pop:
            raise RuntimeError("Failed to generate any initial algorithms")

        # Register all to pools (generalist only at this point)
        for algo in initial_pop:
            self.all_algorithms.append(algo)
            if algo.is_evaluated():
                self.generalist_pool.append(algo)

        self.generalist_pool.sort(key=lambda a: a.score, reverse=True)
        if len(self.generalist_pool) > self.config.generalist_pool_size:
            self.generalist_pool = self.generalist_pool[:self.config.generalist_pool_size]

        # Update global best
        evaluated = [a for a in initial_pop if a.is_evaluated()]
        if evaluated:
            self.global_best = max(evaluated, key=lambda a: a.score)

        # Initial clustering
        self._update_anchors_and_recluster()

        # Populate specialist pools from initial population
        for algo in evaluated:
            for _cid, pool in self.specialist_pools.items():
                pool.add_algorithm(algo)

        # Populate complementary pool
        for algo in evaluated:
            self.complementary_pool.add_algorithm(algo)

        self._print_pool_summary()
        return initial_pop

    async def run(self) -> EvolutionResult:
        """Run the complete DyCA evolution process.

        Executes the training flow (and optionally using flow):
        1. Initialize population and cluster
        2. Main evolution loop with resource allocation
        3. Periodic reclustering
        4. SOS for stagnating clusters

        Returns:
            EvolutionResult with final state and best individual.
        """
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        self.current_generation = 0
        self.state_tracker.start_run(self.config.max_generations)

        try:
            # Initialize
            await self.initialize_population()
            self.current_generation = 1

            # Main evolution loop
            while (
                self.current_generation <= self.config.max_generations
                and self.state == EvolutionState.RUNNING
            ):
                logger.info(f"=== DyCA Generation {self.current_generation} ===")

                should_continue, best = await self.step()

                if not should_continue:
                    break

                self.current_generation += 1

                # Periodic reclustering
                if self.current_generation % self.config.recluster_interval == 0:
                    self._update_anchors_and_recluster()

                # Update gaps
                if self.global_best:
                    for cu in self.specialist_pools.values():
                        cu.update_gap(self.global_best.score)

                # Checkpoint
                if self.should_checkpoint():
                    await self.save_checkpoint()
                    self._last_checkpoint_gen = self.current_generation

                # Early stopping
                should_stop, reason = self.check_early_stop()
                if should_stop:
                    self.monitor.log_info(f"Early stopping: {reason}")
                    break

            self.state = EvolutionState.COMPLETED

        except Exception as e:
            self.state = EvolutionState.FAILED
            self.monitor.log_error(f"DyCA evolution failed: {e}")
            raise

        finally:
            duration = time.time() - self.start_time
            self.state_tracker.end_run(self.state)

        return EvolutionResult(
            state=self.state,
            best_individual=self.global_best,
            final_generation=self.current_generation,
            total_evaluations=len([a for a in self.all_algorithms if a.is_evaluated()]),
            history=self.history,
            duration_seconds=duration,
            metadata={
                "n_clusters": len(self.specialist_pools),
                "final_ari": self.current_ari,
                "generalist_pool_size": len(self.generalist_pool),
            },
        )

    async def step(self) -> tuple[bool, Algorithm | None]:
        """Execute one generation step.

        1. Allocate resources
        2. Execute tasks (generate offspring)
        3. Register to pools
        4. Log stats

        Returns:
            (should_continue, best_individual).
        """
        if self.state != EvolutionState.RUNNING:
            return False, self.global_best

        # Reset per-generation token accumulators
        self._gen_tokens = 0
        self._gen_algo_count = 0

        background = getattr(self.config, "background", "")

        # Resource allocation
        tasks = self._allocate_resources()
        logger.info(
            f"Allocated {len(tasks)} tasks: "
            + str({t.get('operator', t.get('pool')): 1 for t in tasks})
        )

        # Execute tasks (with optional concurrency limit)
        if self._llm_semaphore is not None:
            async def _limited_task(
                task: dict[str, Any],
            ) -> Algorithm | None:
                async with self._llm_semaphore:
                    return await self._execute_task(
                        task, self.current_generation, background
                    )

            coros = [_limited_task(task) for task in tasks]
        else:
            coros = [
                self._execute_task(task, self.current_generation, background)
                for task in tasks
            ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        new_algorithms = [r for r in results if isinstance(r, Algorithm)]
        logger.info(
            f"Generation {self.current_generation}: "
            f"{len(new_algorithms)}/{len(tasks)} offspring successful"
        )

        # Register to pools
        for algo in new_algorithms:
            self._register_to_pools(algo)

        # Auto-extract memory cards from good and bad algorithms
        await self._extract_memory_cards(new_algorithms, background)

        # Log stats
        self._log_generation_stats()

        return True, self.global_best

    async def evolve_generation(self, parent_population: list[Any]) -> list[Any]:
        """Evolve one generation (delegated to step()).

        Args:
            parent_population: Current population (unused, pools manage their own).

        Returns:
            Updated global population.
        """
        await self.step()
        await self._finish_embedding_tasks()
        return self.all_algorithms

    async def pause(self) -> None:
        """Pause evolution."""
        self.state = EvolutionState.PAUSED
        self.monitor.log_info("DyCA evolution paused")

    async def resume(self) -> None:
        """Resume evolution."""
        if self.state == EvolutionState.PAUSED:
            self.state = EvolutionState.RUNNING
            self.monitor.log_info("DyCA evolution resumed")

    async def save_checkpoint(self, path: str | None = None) -> str:
        """Save DyCA state to checkpoint.

        Args:
            path: Optional checkpoint path.

        Returns:
            Path to saved checkpoint.
        """
        checkpoint = EvolutionCheckpoint(
            generation=self.current_generation,
            population=self.all_algorithms,
            best_individual=self.global_best,
            history=self.history,
            metadata={
                "cluster_labels": self.cluster_labels,
                "current_ari": self.current_ari,
                "state_tracker": self.state_tracker.to_checkpoint(),
            },
        )

        if path is None:
            path = (
                f"{self.state_tracker.checkpoints_dir}/dyca_checkpoint_gen_"
                f"{self.current_generation}_{uuid.uuid4().hex[:8]}.json"
            )

        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="UTF-8") as f:
            f.write(checkpoint.model_dump_json(indent=2))

        self.monitor.log_info(f"DyCA checkpoint saved to {path}")
        return path

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load DyCA state from checkpoint.

        Args:
            path: Path to checkpoint file.

        Returns:
            Loaded checkpoint.
        """
        import json

        with open(path, encoding="UTF-8") as f:
            data = json.load(f)

        checkpoint = EvolutionCheckpoint(**data)
        self.current_generation = checkpoint.generation
        self.global_best = checkpoint.best_individual
        self.history = checkpoint.history
        self.cluster_labels = checkpoint.metadata.get("cluster_labels", {})
        self.current_ari = checkpoint.metadata.get("current_ari", 0.0)

        if "state_tracker" in checkpoint.metadata:
            self.state_tracker = self.state_tracker.from_checkpoint(
                checkpoint.metadata["state_tracker"]
            )

        self.monitor.log_info(
            f"DyCA checkpoint loaded from {path}, generation {self.current_generation}"
        )
        return checkpoint

    def get_status(self) -> dict[str, Any]:
        """Get current DyCA evolution status.

        Returns:
            Status dictionary.
        """
        status = {
            "state": self.state.value,
            "current_generation": self.current_generation,
            "global_best_score": self.global_best.score if self.global_best else None,
            "n_clusters": len(self.specialist_pools),
            "current_ari": self.current_ari,
            "generalist_pool_size": len(self.generalist_pool),
            "complementary_pool_size": len(self.complementary_pool.population),
            "specialist_pools": {
                cid: {
                    "size": len(cu.population),
                    "best_score": cu.best_score,
                    "stagnation": cu.stagnation_count,
                    "gap": cu.gap,
                }
                for cid, cu in self.specialist_pools.items()
            },
            "total_algorithms": len(self.all_algorithms),
            "total_evaluated": len([a for a in self.all_algorithms if a.is_evaluated()]),
        }
        status.update(self.state_tracker.get_status_summary())
        return status

    # ------------------------------------------------------------------
    # Memory extraction
    # ------------------------------------------------------------------

    async def _extract_memory_cards(
        self, algorithms: list[Algorithm], background: str
    ) -> None:
        """Extract memory cards from newly evaluated algorithms.

        Checks each algorithm against good/bad thresholds and extracts
        insights via the MemoryExtractor. Extracted cards are added to
        the planner's memory (and persisted to disk when enabled).

        Args:
            algorithms: Newly evaluated algorithms from this generation.
            background: Problem background description.
        """
        memory = self.planner.memory
        if memory is None or memory.extractor is None:
            return

        extractor = memory.extractor
        extractor.reset_generation()

        # Collect the full population for percentile calculations
        population = self.all_algorithms

        coros = []
        for algo in algorithms:
            if algo.is_evaluated() and algo.score is not None:
                # Check good
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_good(
                            algo, population, self.current_generation, background
                        )
                    )
                )
                # Check bad
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_bad(
                            algo, population, self.current_generation, background
                        )
                    )
                )
            elif not algo.is_evaluated():
                # Failed evaluation
                error_msg = ""
                if algo.evaluation and algo.evaluation.error:
                    error_msg = algo.evaluation.error
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_failure(
                            algo, error_msg, self.current_generation, background
                        )
                    )
                )

        if not coros:
            return

        results = await asyncio.gather(*coros, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Memory extraction failed: {result}")
            elif result is not None:
                await memory.add_card(result)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _aggregate_generation_tokens(self) -> int:
        """Aggregate total tokens consumed by algorithms in the current generation.

        Returns:
            Total tokens used by algorithms created in this generation.
        """
        return self._gen_tokens

    def _log_token_summary(self) -> None:
        """Log token consumption summary for the current generation."""
        cumulative_tokens = sum(
            m.total_tokens for m in self.state_tracker.metrics_history
        )

        logger.info(
            f"Token Consumption (Generation {self.current_generation}): "
            f"{self._gen_tokens:,} tokens ({self._gen_algo_count} algorithms) | "
            f"Cumulative: {cumulative_tokens + self._gen_tokens:,} tokens"
        )

    def _log_generation_stats(self) -> None:
        """Log statistics for the current generation."""
        evaluated = [a for a in self.all_algorithms if a.is_evaluated()]
        scores = [a.score for a in evaluated] if evaluated else []

        stats = {
            "generation": self.current_generation,
            "global_best_score": self.global_best.score if self.global_best else None,
            "avg_score": sum(scores) / len(scores) if scores else None,
            "total_algorithms": len(self.all_algorithms),
            "total_evaluated": len(evaluated),
            "n_clusters": len(self.specialist_pools),
            "current_ari": self.current_ari,
        }
        self.history.append(stats)
        self.monitor.log_generation(stats)

        # Update state tracker
        best_score = max(scores) if scores else float("-inf")
        avg_score = sum(scores) / len(scores) if scores else 0.0
        worst_score = min(scores) if scores else float("inf")

        gen_tokens = self._aggregate_generation_tokens()

        metrics = GenerationMetrics(
            generation=self.current_generation,
            best_score=best_score,
            average_score=avg_score,
            worst_score=worst_score,
            total_evaluations=len(evaluated),
            successful_evaluations=len(evaluated),
            failed_evaluations=len(self.all_algorithms) - len(evaluated),
            total_tokens=gen_tokens,
        )
        self.state_tracker.update_generation(self.current_generation, metrics)

        # Log token consumption
        self._log_token_summary()

        self._print_pool_summary()

    def _print_pool_summary(self) -> None:
        """Print a summary of all pools."""
        logger.info("")
        logger.info(f"=== DyCA Pool Summary (Generation {self.current_generation}) ===")
        logger.info(
            f"Generalist: {len(self.generalist_pool)} | "
            f"Complementary: {len(self.complementary_pool.population)} | "
            f"ARI: {self.current_ari:.4f}"
        )

        for cid, cu in sorted(self.specialist_pools.items()):
            elite_best = cu.archive.get_elite_best()
            elite_score = cluster_score(elite_best, cu.instance_names) if elite_best else 0.0
            logger.info(
                f"  Cluster {cid}: {len(cu.population)} algos, "
                f"best={cu.best_score:.4f}, elite_best={elite_score:.4f}, "
                f"stagnation={cu.stagnation_count}, gap={cu.gap:.4f}, "
                f"instances={len(cu.instance_names)}"
            )

        if self.global_best:
            logger.info(f"Global best: {self.global_best.name} ({self.global_best.score:.4f})")
        logger.info("")
