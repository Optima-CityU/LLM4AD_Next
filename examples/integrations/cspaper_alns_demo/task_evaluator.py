"""Evaluator for evolving the ALNS TSP destroy operator.

The candidate is executed in a separate Datawhale Python process. Tour length
is recomputed here from the TSPLIB instance; the objective reported by the
candidate is never trusted.

Audited hard constraints from AlgorithmDesignSpec:
valid_partial_state, effective_destruction, valid_tour, valid_node_ids,
finite_objective, and budget_compliance.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.base import BaseEvaluator, EvaluationResult, Metric, MetricType

EVALUATION_SEEDS = (42, 314, 2026)
MAX_ITERATIONS = 250
DEGREE_OF_DESTRUCTION = 0.1
DEFAULT_CANDIDATE_PYTHON = Path(sys.executable)

FORBIDDEN_MODULES = {
    "httpx",
    "os",
    "pathlib",
    "requests",
    "socket",
    "subprocess",
    "urllib",
}
FORBIDDEN_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "open",
}


RUNNER_CODE = r"""
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np

project_root = Path(sys.argv[1]).resolve()
instance_path = Path(sys.argv[2]).resolve()
seed = int(sys.argv[3])
max_iterations = int(sys.argv[4])
degree = float(sys.argv[5])
mode = sys.argv[6]

sys.path.insert(0, str(project_root))
solve_path = project_root / "solve.py"
spec = importlib.util.spec_from_file_location("candidate_tsp_solve", solve_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load candidate module: {solve_path}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixed_random_destroy(current, rng, degree_of_destruction=0.1, **_):
    destroyed = copy.deepcopy(current)
    remove_count = module.number_of_edges_to_remove(
        destroyed, degree_of_destruction
    )
    departures = list(destroyed.edges)
    selected = rng.choice(
        len(departures), size=remove_count, replace=False
    )
    for index in selected:
        del destroyed.edges[departures[int(index)]]
    return destroyed


probe = None
if mode == "baseline":
    module.destroy_operator = fixed_random_destroy
else:
    nodes, distances = module.load_instance(instance_path)
    rng = np.random.default_rng(seed)
    initial = module.repair_operator(
        module.TspState(nodes=nodes, edges={}, distances=distances), rng
    )
    original_edges = dict(initial.edges)
    destroyed = module.destroy_operator(
        initial,
        rng,
        degree_of_destruction=degree,
    )
    expected_max = module.number_of_edges_to_remove(initial, degree)
    removed = len(original_edges) - len(destroyed.edges)
    probe = {
        "is_tsp_state": isinstance(destroyed, module.TspState),
        "nodes_unchanged": destroyed.nodes == initial.nodes,
        "distances_unchanged": destroyed.distances == initial.distances,
        "edges_are_subset": all(
            original_edges.get(node) == target
            for node, target in destroyed.edges.items()
        ),
        "removed_edges": removed,
        "expected_max_removed": expected_max,
    }

started = time.perf_counter()
result = module.solve(
    instance_path,
    seed=seed,
    max_iterations=max_iterations,
    degree_of_destruction=degree,
)
runtime_ms = (time.perf_counter() - started) * 1000

print(json.dumps({
    "tour": result.get("tour"),
    "reported_objective": result.get("objective"),
    "runtime_ms": runtime_ms,
    "probe": probe,
}, separators=(",", ":")))
"""


class AlnsTspDestroyEvaluator(BaseEvaluator):
    """Compare an evolved destroy operator with fixed random edge removal."""

    def __init__(self) -> None:
        """Initialize the deterministic baseline cache."""
        super().__init__()
        self._baseline_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._baseline_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Return the evaluator registry name."""
        return "alns_tsp_destroy_operator"

    @property
    def metrics(self) -> list[Metric]:
        """Declare metrics that exactly match AlgorithmDesignSpec."""
        return [
            Metric(
                name="relative_tour_gap_pct",
                type=MetricType.MINIMIZE,
                weight=1.0,
                description="Mean tour-length gap against fixed random removal.",
            ),
            Metric(
                name="runtime_ms",
                type=MetricType.MINIMIZE,
                weight=0.05,
                description="Median candidate solver runtime over fixed seeds.",
            ),
        ]

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Compare one candidate with the fixed baseline on one TSP instance."""
        started = time.perf_counter()
        project_root = Path(cfg.project_root).resolve()
        instance_path = Path(cfg.data_path).resolve()

        try:
            solve_path = project_root / "solve.py"
            if not solve_path.is_file():
                raise ValueError(f"candidate solve.py does not exist: {solve_path}")
            if not instance_path.is_file():
                raise ValueError(f"TSP instance does not exist: {instance_path}")

            self._audit_candidate_source(solve_path)
            nodes, coordinates = self._load_euc_2d(instance_path)
            deadline = time.perf_counter() + cfg.timeout

            candidate_records = []
            baseline_records = []
            for seed in EVALUATION_SEEDS:
                baseline = await self._get_baseline(project_root, instance_path, seed, deadline)
                candidate = await self._run_solver(
                    project_root,
                    instance_path,
                    seed,
                    mode="candidate",
                    deadline=deadline,
                )
                self._validate_probe(candidate.get("probe"))

                candidate_length = self._validate_and_measure_tour(candidate.get("tour"), nodes, coordinates)
                baseline_length = self._validate_and_measure_tour(baseline.get("tour"), nodes, coordinates)
                candidate["verified_tour_length"] = candidate_length
                baseline["verified_tour_length"] = baseline_length
                candidate_records.append(candidate)
                baseline_records.append(baseline)

            gaps = [
                (candidate["verified_tour_length"] - baseline["verified_tour_length"]) / baseline["verified_tour_length"] * 100.0
                for candidate, baseline in zip(candidate_records, baseline_records, strict=True)
            ]
            candidate_runtime = statistics.median(record["runtime_ms"] for record in candidate_records)
            baseline_runtime = statistics.median(record["runtime_ms"] for record in baseline_records)
            relative_gap = statistics.mean(gaps)
            runtime_overhead_pct = (candidate_runtime - baseline_runtime) / max(baseline_runtime, 1e-9) * 100.0
            if abs(runtime_overhead_pct) <= 10.0:
                scored_runtime_overhead_pct = 0.0
            else:
                scored_runtime_overhead_pct = runtime_overhead_pct - math.copysign(10.0, runtime_overhead_pct)

            metrics = {
                "relative_tour_gap_pct": relative_gap,
                "runtime_ms": candidate_runtime,
            }
            score = -relative_gap - 0.05 * scored_runtime_overhead_pct
            duration_ms = (time.perf_counter() - started) * 1000.0
            return EvaluationResult(
                score=score,
                metrics=metrics,
                monitor_metrics=metrics,
                metadata={
                    "instance": instance_path.name,
                    "candidate_id": cfg.candidate_id,
                    "seeds": list(EVALUATION_SEEDS),
                    "max_iterations": MAX_ITERATIONS,
                    "degree_of_destruction": DEGREE_OF_DESTRUCTION,
                    "candidate_tour_lengths": [item["verified_tour_length"] for item in candidate_records],
                    "baseline_tour_lengths": [item["verified_tour_length"] for item in baseline_records],
                    "baseline_runtime_ms": baseline_runtime,
                    "runtime_overhead_pct": runtime_overhead_pct,
                    "scored_runtime_overhead_pct": scored_runtime_overhead_pct,
                },
                duration_ms=duration_ms,
                success=True,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            return EvaluationResult(
                score=-1_000_000_000.0,
                metrics={
                    "relative_tour_gap_pct": 1_000_000_000.0,
                    "runtime_ms": cfg.timeout * 1000.0,
                },
                success=False,
                error_message=str(exc),
                duration_ms=duration_ms,
                metadata={
                    "instance": instance_path.name,
                    "candidate_id": cfg.candidate_id,
                },
            )

    async def _get_baseline(
        self,
        project_root: Path,
        instance_path: Path,
        seed: int,
        deadline: float,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(instance_path.read_bytes()).hexdigest()
        key = (digest, seed, MAX_ITERATIONS)
        if key in self._baseline_cache:
            return self._baseline_cache[key]

        async with self._baseline_lock:
            if key not in self._baseline_cache:
                self._baseline_cache[key] = await self._run_solver(
                    project_root,
                    instance_path,
                    seed,
                    mode="baseline",
                    deadline=deadline,
                )
        return self._baseline_cache[key]

    async def _run_solver(
        self,
        project_root: Path,
        instance_path: Path,
        seed: int,
        *,
        mode: str,
        deadline: float,
    ) -> dict[str, Any]:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("budget_compliance failed: evaluation timed out")

        python = self._candidate_python()
        env = self._sanitized_environment()
        process = await asyncio.create_subprocess_exec(
            str(python),
            "-c",
            RUNNER_CODE,
            str(project_root),
            str(instance_path),
            str(seed),
            str(MAX_ITERATIONS),
            str(DEGREE_OF_DESTRUCTION),
            mode,
            cwd=str(project_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=remaining)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise TimeoutError(f"budget_compliance failed: {mode} run exceeded timeout") from exc

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{mode} solver failed: {error[-2000:]}")

        lines = [line for line in stdout.decode("utf-8").splitlines() if line.strip()]
        if not lines:
            raise ValueError(f"{mode} solver returned no JSON output")
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"{mode} solver returned invalid JSON") from exc
        if not isinstance(result.get("runtime_ms"), (int, float)):
            raise ValueError(f"{mode} solver did not report a numeric runtime")
        if not math.isfinite(float(result["runtime_ms"])):
            raise ValueError("finite_objective failed: runtime is not finite")
        return result

    @staticmethod
    def _candidate_python() -> Path:
        configured = os.getenv("TSP_EVALUATOR_PYTHON")
        candidate = Path(configured) if configured else DEFAULT_CANDIDATE_PYTHON
        if candidate.is_file():
            return candidate
        fallback = Path(sys.executable)
        if fallback.is_file():
            return fallback
        raise FileNotFoundError("no Python interpreter available for candidate execution")

    @staticmethod
    def _sanitized_environment() -> dict[str, str]:
        env = dict(os.environ)
        sensitive = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CSPAPER", "LLM_")
        for name in list(env):
            if any(marker in name.upper() for marker in sensitive):
                env.pop(name, None)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env.pop(name, None)
        env["PYTHONNOUSERSITE"] = "1"
        return env

    @staticmethod
    def _audit_candidate_source(solve_path: Path) -> None:
        source = solve_path.read_text(encoding="utf-8")
        if source.count("# EVOLVE_START") != 1 or source.count("# EVOLVE_END") != 1:
            raise ValueError("candidate must contain exactly one EVOLVE region")
        tree = ast.parse(source, filename=str(solve_path))
        function = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "destroy_operator"
            ),
            None,
        )
        if function is None:
            raise ValueError("destroy_operator function is missing")

        for node in ast.walk(function):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif node.module:
                    modules = [node.module.split(".")[0]]
                if FORBIDDEN_MODULES.intersection(modules):
                    raise ValueError("budget_compliance failed: forbidden module in destroy_operator")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                raise ValueError(f"budget_compliance failed: forbidden call {node.func.id}")

    @staticmethod
    def _validate_probe(probe: Any) -> None:
        if not isinstance(probe, dict):
            raise ValueError("valid_partial_state failed: missing destroy probe")
        required_true = (
            "is_tsp_state",
            "nodes_unchanged",
            "distances_unchanged",
            "edges_are_subset",
        )
        if not all(probe.get(name) is True for name in required_true):
            raise ValueError(f"valid_partial_state failed: {probe}")
        removed = probe.get("removed_edges")
        maximum = probe.get("expected_max_removed")
        if not isinstance(removed, int) or not isinstance(maximum, int):
            raise ValueError("effective_destruction failed: invalid removal count")
        if removed < 1 or removed > maximum:
            raise ValueError(f"effective_destruction failed: removed={removed}, maximum={maximum}")

    @staticmethod
    def _load_euc_2d(
        path: Path,
    ) -> tuple[list[int], dict[int, tuple[float, float]]]:
        lines = path.read_text(encoding="ascii").splitlines()
        edge_weight_type = ""
        dimension = None
        coordinates: dict[int, tuple[float, float]] = {}
        in_coordinates = False
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if in_coordinates:
                if line.upper() == "EOF":
                    break
                parts = line.split()
                if len(parts) != 3:
                    raise ValueError(f"invalid NODE_COORD_SECTION line: {line}")
                node = int(parts[0])
                coordinates[node] = (float(parts[1]), float(parts[2]))
                continue
            if line.upper() == "NODE_COORD_SECTION":
                in_coordinates = True
                continue
            if ":" in line:
                key, value = (part.strip() for part in line.split(":", 1))
                if key.upper() == "EDGE_WEIGHT_TYPE":
                    edge_weight_type = value.upper()
                elif key.upper() == "DIMENSION":
                    dimension = int(value)

        if edge_weight_type != "EUC_2D":
            raise ValueError(f"only EUC_2D instances are supported, got {edge_weight_type}")
        if dimension is None or len(coordinates) != dimension:
            raise ValueError("TSPLIB dimension does not match NODE_COORD_SECTION")
        return list(coordinates), coordinates

    @staticmethod
    def _validate_and_measure_tour(
        tour: Any,
        nodes: list[int],
        coordinates: dict[int, tuple[float, float]],
    ) -> float:
        if not isinstance(tour, list) or any(isinstance(node, bool) or not isinstance(node, int) for node in tour):
            raise ValueError("valid_node_ids failed: tour must be a list of integers")
        if len(tour) != len(nodes) or len(set(tour)) != len(tour):
            raise ValueError("valid_tour failed: every node must occur exactly once")
        if set(tour) != set(nodes):
            raise ValueError("valid_node_ids failed: tour node set differs from instance")

        total = 0.0
        for node_from, node_to in zip(tour, tour[1:] + tour[:1], strict=True):
            x1, y1 = coordinates[node_from]
            x2, y2 = coordinates[node_to]
            total += int(math.hypot(x1 - x2, y1 - y2) + 0.5)
        if not math.isfinite(total) or total <= 0:
            raise ValueError("finite_objective failed: verified tour length is invalid")
        return total
