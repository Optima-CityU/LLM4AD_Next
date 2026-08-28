"""TSP evaluator for the LLM4AD platform.

This evaluator demonstrates the one-shot solver pattern:
- The algorithm is a standalone script that reads JSON input and prints JSON output
- The evaluator spawns the algorithm as a subprocess for each test instance
- The evaluator parses the output, validates the tour, and computes metrics
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)


@BaseEvaluator.register("python_tsp_evaluator")
class PythonTSPEvaluator(BaseEvaluator):
    """Evaluator for TSP algorithms.

    Spawns the solve.py script as a subprocess, validates the tour, and scores
    based on tour length (primary metric), execution time, and tour validity.
    """

    def __init__(self):
        """Initialize with TSP-specific metrics."""
        self._metrics = [
            Metric(
                name="tour_length",
                type=MetricType.MINIMIZE,
                weight=1.0,
                description="Total distance of the tour",
            ),
            Metric(
                name="execution_time_ms",
                type=MetricType.MINIMIZE,
                weight=0.3,
                description="Algorithm execution time in milliseconds",
            ),
            Metric(
                name="valid_tour",
                type=MetricType.MAXIMIZE,
                weight=10.0,
                description="Whether the tour is valid (visits all nodes exactly once)",
            ),
        ]

    @property
    def name(self) -> str:
        """Return evaluator name."""
        return "python_tsp_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Return supported metrics."""
        return self._metrics

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Evaluate a TSP algorithm on one problem instance.

        Args:
            cfg: Evaluation context with project_root, data_path, and timeout.

        Returns:
            EvaluationResult with score (negative tour length) and metrics.
        """
        start_time = time.time()

        try:
            # 1. Locate the algorithm script
            project_root = Path(cfg.project_root)
            solve_script = project_root / "solve.py"

            if not solve_script.exists():
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Algorithm script not found: {solve_script}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # 2. Load the problem instance
            with open(cfg.data_path, encoding="utf-8") as f:
                problem_data = json.load(f)

            nodes = problem_data.get("nodes", [])
            if not nodes:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message="No nodes in problem instance",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # 3. Prepare input JSON
            input_json = json.dumps({"nodes": nodes})

            # 4. Run the algorithm as a subprocess
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(solve_script),
                input_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_root),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=cfg.timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Algorithm timed out after {cfg.timeout}s",
                    duration_ms=cfg.timeout * 1000,
                )

            execution_time_ms = (time.time() - start_time) * 1000
            stdout_text = stdout_bytes.decode(errors="replace")
            stderr_text = stderr_bytes.decode(errors="replace")

            if proc.returncode != 0:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Algorithm crashed (exit code {proc.returncode}): {stderr_text.strip()}",
                    duration_ms=execution_time_ms,
                )

            # 5. Parse output JSON
            try:
                output_data = json.loads(stdout_text.strip())
            except json.JSONDecodeError as e:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Invalid JSON output: {e}",
                    duration_ms=execution_time_ms,
                )

            tour = output_data.get("tour", [])

            # 6. Validate the tour
            is_valid = self._validate_tour(tour, len(nodes))

            if not is_valid:
                return EvaluationResult(
                    score=-1e9,
                    metrics={
                        "tour_length": 1e9,
                        "execution_time_ms": execution_time_ms,
                        "valid_tour": 0.0,
                    },
                    success=True,
                    duration_ms=execution_time_ms,
                    metadata={"dataset": cfg.data_path, "reason": "invalid_tour"},
                )

            # 7. Calculate tour length
            tour_length = self._calculate_tour_length(tour, nodes)

            # 8. Compute score (negative length for minimization)
            score = -tour_length

            return EvaluationResult(
                score=score,
                metrics={
                    "tour_length": tour_length,
                    "execution_time_ms": execution_time_ms,
                    "valid_tour": 1.0,
                },
                success=True,
                duration_ms=execution_time_ms,
                metadata={"dataset": cfg.data_path},
            )

        except Exception as e:
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message=f"Evaluation error: {e}",
                duration_ms=(time.time() - start_time) * 1000,
            )

    def _validate_tour(self, tour: list[int], num_nodes: int) -> bool:
        """Check if tour visits all nodes exactly once."""
        if len(tour) != num_nodes:
            return False
        return set(tour) == set(range(num_nodes))

    def _calculate_tour_length(self, tour: list[int], nodes: list[list[float]]) -> float:
        """Calculate total Euclidean distance of the tour."""
        total_dist = 0.0
        for i in range(len(tour)):
            from_node = nodes[tour[i]]
            to_node = nodes[tour[(i + 1) % len(tour)]]
            dx = to_node[0] - from_node[0]
            dy = to_node[1] - from_node[1]
            total_dist += (dx**2 + dy**2) ** 0.5
        return total_dist
