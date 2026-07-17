"""Population helper for the MEoH orchestrator."""

from __future__ import annotations

import importlib
import random

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from llm4ad.evaluator.base import MetricType
from llm4ad.planner.base import Algorithm


def compute_codebleu_similarity(left: str, right: str) -> float:
    """Compute CodeBLEU syntax similarity for two code strings.

    Falls back to 0.0 on any error (e.g. tree-sitter version mismatch).
    """
    if not left or not right:
        return 0.0
    try:
        syntax_match = importlib.import_module("codebleu.syntax_match")
        score = syntax_match.calc_syntax_match([left], right, "python")
        if isinstance(score, (float, int)):
            return float(score)
        if hasattr(score, "__float__"):
            return float(score)
    except Exception as exc:
        logger.warning(f"[MEoH] CodeBLEU similarity failed: {exc}")
    return 0.0


def get_non_dominated_indices(objective_matrix):
    """Compute the non-dominated front using pymoo."""
    sorting_module = importlib.import_module("pymoo.util.nds.non_dominated_sorting")
    sorting = sorting_module.NonDominatedSorting()
    return sorting.do(objective_matrix, only_non_dominated_front=True)


def get_domination_relation(a, b):
    """Return Pareto domination relation using pymoo.

    Returns 1 if *a* dominates *b*, -1 if *b* dominates *a*, 0 otherwise.
    Both vectors must be in **minimize** space.
    """
    dominator_mod = importlib.import_module("pymoo.util.dominator")
    return dominator_mod.Dominator.get_relation(a, b)


def compute_hypervolume(obj_matrix, ref_point):
    """Compute hypervolume indicator via pymoo.

    Args:
        obj_matrix: numpy array of shape ``(n, d)`` in minimize space.
        ref_point: numpy array of shape ``(d,)``.

    Returns:
        Hypervolume value as float.
    """
    hv_module = importlib.import_module("pymoo.indicators.hv")
    hv_indicator = hv_module.HV(ref_point=ref_point)
    return float(hv_indicator(obj_matrix))


class MEoHPopulation(BaseModel):
    """MEoH population state and Pareto-based survival logic."""

    population: list[Algorithm] = Field(default_factory=list)
    next_pop: list[Algorithm] = Field(default_factory=list)
    elitist_archive: list[Algorithm] = Field(default_factory=list)
    generation: int = 0
    pop_size: int = 8
    num_operators: int = 4
    objective_metrics: list[str] = Field(default_factory=list)
    metric_directions: dict[str, MetricType] = Field(default_factory=dict)
    last_hv: float = 0.0

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def register_individual(self, algorithm: Algorithm) -> tuple[bool, list[Algorithm]]:
        """Register a new candidate into next_pop and trigger survival when full.

        New candidates are added to ``next_pop``. When ``next_pop`` reaches
        ``pop_size * num_operators``, survival merges ``population + next_pop``,
        keeps the top ``pop_size`` by dominance score, and clears ``next_pop``.

        Returns:
            Tuple of (survival_triggered, eliminated_individuals).
        """
        if self.has_duplicate(algorithm):
            return False, []
        self.next_pop.append(algorithm)
        if len(self.next_pop) >= self.pop_size * self.num_operators:
            eliminated = self.survival()
            return True, eliminated
        return False, []

    def register_init_individual(self, algorithm: Algorithm) -> bool:
        """Register during initialization phase — adds directly to population."""
        if self.has_duplicate(algorithm):
            return False
        self.population.append(algorithm)
        return True

    def survival(self) -> list[Algorithm]:
        """Run one survival event and advance the MEoH generation.

        Merges ``population + next_pop``, updates the elitist archive with
        non-dominated solutions, then selects the best ``pop_size`` individuals
        by dominance score to form the next generation. Clears ``next_pop``.

        Returns:
            List of individuals eliminated during this survival event.
        """
        if not self.next_pop:
            return []

        combined = self.population + self.next_pop
        pool_size = len(combined)
        logger.info(
            f"[MEoH-Survival] gen={self.generation} -> {self.generation + 1} | "
            f"pop={len(self.population)} + next={len(self.next_pop)} = {pool_size} -> {self.pop_size} | "
            f"archive_before={len(self.elitist_archive)}"
        )

        archive_source = self.elitist_archive + combined
        self.elitist_archive = self._non_dominated_front(archive_source)

        dominance_scores = self._dominance_scores(combined)
        ranked_indices = sorted(
            range(len(combined)),
            key=lambda index: dominance_scores[index],
            reverse=True,
        )
        survivor_indices = set(ranked_indices[: self.pop_size])
        self.population = [combined[index] for index in ranked_indices[: self.pop_size]]
        eliminated = [
            combined[index]
            for index in range(len(combined))
            if index not in survivor_indices
        ]
        self.next_pop = []
        self.generation += 1
        self.last_hv = self._compute_hypervolume()

        logger.info(
            f"[MEoH-Survival] gen={self.generation} complete | "
            f"survivors={len(self.population)} eliminated={len(eliminated)} "
            f"archive={len(self.elitist_archive)} "
            f"hv={self.last_hv:.6f}"
        )
        return eliminated

    def selection(self, count: int = 1) -> list[Algorithm]:
        """Sample parents from the active population.

        Prefers successfully evaluated individuals weighted by dominance score.
        Falls back to all population members (uniform random) when no
        evaluated individuals exist, so that operators can still produce
        offspring even when early generations have only failing candidates.
        """
        valid = [algorithm for algorithm in self.population if algorithm.is_evaluated()]
        if not valid:
            # Fallback: use all population members so evolution can continue
            if self.population:
                return random.sample(self.population, min(count, len(self.population)))
            return []

        dominance_scores = self._dominance_scores(valid)
        exp_scores = [pow(2.718281828459045, score) for score in dominance_scores]
        total = sum(exp_scores)
        if total <= 0:
            return random.sample(valid, min(count, len(valid)))
        weights = [score / total for score in exp_scores]
        return random.choices(valid, weights=weights, k=count)

    def has_duplicate(self, candidate: Algorithm) -> bool:
        """Check whether a candidate is a duplicate of an existing individual.

        A candidate is a duplicate only when its code is byte-for-byte identical
        to an existing individual's code. This matches the legacy MEoH behaviour
        (``has_duplicate_function`` compared ``str(f) == str(func)`` only).

        Note: objective-vector deduplication was intentionally removed. Many
        semantically distinct heuristics can produce the identical objective
        vector (e.g. multiple different implementations that both degenerate to
        the per-instance baseline cost). Rejecting them by objective vector
        collapses the population — observed on CVRP where 31/32 candidates were
        wrongly rejected, leaving a single baseline individual. Diversity among
        equal-scoring individuals is instead handled downstream by the AST
        similarity penalty in ``_dominance_scores``.
        """
        candidate_code = self._code_signature(candidate)
        if not candidate_code:
            # No code to compare against — treat as unique so evolution proceeds.
            return False

        code_len = len(candidate_code)
        has_artifacts = bool(candidate.code_artifacts)
        for existing in self.population + self.next_pop + self.elitist_archive:
            existing_code = self._code_signature(existing)
            if candidate_code == existing_code:
                logger.debug(
                    f"[Dup-Code] candidate={candidate.id} matches={existing.id} "
                    f"code_len={code_len} artifacts={has_artifacts}"
                )
                return True
        return False

    def get_objective_vector(self, algorithm: Algorithm) -> tuple[float, ...]:
        """Project evaluator metrics into maximize-space objective values."""
        vector: list[float] = []
        metrics = algorithm.metrics
        is_valid = algorithm.is_evaluated()
        for metric_name in self.objective_metrics:
            direction = self.metric_directions.get(metric_name, MetricType.MAXIMIZE)
            if not is_valid or metric_name not in metrics:
                vector.append(float("-inf"))
                continue
            value = metrics[metric_name]
            vector.append(-value if direction == MetricType.MINIMIZE else value)
        return tuple(vector)

    def _non_dominated_front(self, population: list[Algorithm]) -> list[Algorithm]:
        if not population:
            return []
        import numpy as np

        # get_objective_vector returns maximize-space; negate for pymoo minimize-space
        objective_matrix = np.array(
            [[-value for value in self.get_objective_vector(algorithm)] for algorithm in population],
            dtype=float,
        )
        indices = get_non_dominated_indices(objective_matrix)
        front = [population[int(index)] for index in indices]

        seen: set[tuple[float, ...]] = set()
        unique: list[Algorithm] = []
        for alg in front:
            vec = self.get_objective_vector(alg)
            if vec not in seen:
                seen.add(vec)
                unique.append(alg)
        if len(unique) < len(front):
            logger.info(
                f"[MEoH-Archive] Deduplicated {len(front)} -> {len(unique)} "
                f"(removed {len(front) - len(unique)} duplicate objective vectors)"
            )
        return unique

    def _dominance_scores(self, population: list[Algorithm]) -> list[float]:
        """Compute dominance-based scores penalized by code similarity.

        Uses pymoo ``Dominator.get_relation()`` for correct Pareto dominance
        (requires strict inequality in at least one objective).  Objective
        vectors from ``get_objective_vector()`` are in *maximize* space;
        they are negated before passing to ``Dominator`` which expects
        *minimize* space.
        """
        if not population:
            return []

        size = len(population)
        scores = [0.0 for _ in range(size)]
        max_vectors = [self.get_objective_vector(algorithm) for algorithm in population]
        # Negate to minimize space for pymoo Dominator
        min_vectors = [tuple(-v for v in vec) for vec in max_vectors]

        similarity_count = 0
        similarity_total = 0.0
        for left_index in range(size):
            for right_index in range(left_index + 1, size):
                relation = get_domination_relation(
                    min_vectors[left_index], min_vectors[right_index],
                )
                if relation == 1:
                    # left dominates right
                    sim = compute_codebleu_similarity(
                        self._code_signature(population[left_index]),
                        self._code_signature(population[right_index]),
                    )
                    scores[right_index] -= sim
                    similarity_count += 1
                    similarity_total += sim
                elif relation == -1:
                    # right dominates left
                    sim = compute_codebleu_similarity(
                        self._code_signature(population[right_index]),
                        self._code_signature(population[left_index]),
                    )
                    scores[left_index] -= sim
                    similarity_count += 1
                    similarity_total += sim
                else:
                    # mutually non-dominated
                    sim = compute_codebleu_similarity(
                        self._code_signature(population[left_index]),
                        self._code_signature(population[right_index]),
                    )
                    if sim > 0:
                        scores[left_index] -= sim * 0.5
                        scores[right_index] -= sim * 0.5
                        similarity_count += 1
                        similarity_total += sim
        avg_sim = similarity_total / similarity_count if similarity_count else 0.0
        logger.info(
            f"[MEoH-Dominance] pop={size} | "
            f"ast_comparisons={similarity_count} avg_similarity={avg_sim:.4f} | "
            f"scores=[{', '.join(f'{s:.4f}' for s in scores)}]"
        )
        return scores

    def _compute_hypervolume(self) -> float:
        """Compute normalized HV for the current elitist archive."""
        evaluated = [algorithm for algorithm in self.elitist_archive if algorithm.is_evaluated()]
        if len(evaluated) < 2:
            return 0.0

        vectors = []
        for algorithm in evaluated:
            vector = self.get_objective_vector(algorithm)
            if vector and all(value != float("-inf") for value in vector):
                vectors.append(vector)

        if len(vectors) < 2:
            return 0.0

        import numpy as np

        obj_matrix = np.array(vectors, dtype=float)
        mins = obj_matrix.min(axis=0)
        maxs = obj_matrix.max(axis=0)
        ranges = maxs - mins
        if np.any(ranges == 0):
            return 0.0

        normalized = (obj_matrix - mins) / ranges

        ref_point = np.full(obj_matrix.shape[1], 1.1, dtype=float)
        return compute_hypervolume(1.0 - normalized, ref_point)

    @staticmethod
    def _code_signature(algorithm: Algorithm) -> str:
        if not algorithm.code_artifacts:
            return ""
        return "\n".join(artifact.content for artifact in algorithm.code_artifacts)
