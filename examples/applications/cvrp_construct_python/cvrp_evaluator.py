"""Custom evaluator for Python CVRP greedy route-construction solvers.

Migrated from the legacy LLM4AD ``cvrp_construct`` task. The legacy
``CVRPEvaluation.evaluate_program(program_str, callable_func)`` evaluated the
heuristic in-process; here we follow the LLM4AD_Next contract: the evolved
``select_next_node`` lives in a git worktree as ``solve.py``, and this
evaluator runs it in a subprocess (so multiple instances evaluate in parallel
via ``asyncio.gather``), then scores the result.

Score is the negative normalized gap to a per-instance baseline
(``-(cost - baseline) / baseline``); higher (closer to 0, or positive) is
better. When no baseline label is present, the raw negative cost is used.
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


@BaseEvaluator.register("cvrp_construct_evaluator")
class CVRPConstructEvaluator(BaseEvaluator):
    """Evaluator for Python CVRP greedy route-construction solvers.

    Runs the ``solve.py`` driver in the algorithm worktree against a single
    instance file and computes the normalized gap to baseline.
    """

    def __init__(self):
        """Initialize the CVRP evaluator with metric definitions."""
        self._metrics = [
            Metric(
                name="normalized_gap",
                type=MetricType.MAXIMIZE,
                weight=1.0,
                description=(
                    "Negative normalized gap to baseline: "
                    "-(total_cost - baseline) / baseline. Higher is better."
                ),
            ),
            Metric(
                name="total_cost",
                type=MetricType.MINIMIZE,
                weight=0.0,
                description="Total route distance (monitoring only).",
            ),
            Metric(
                name="valid_route",
                type=MetricType.MAXIMIZE,
                weight=0.0,
                description="Whether the route is valid (1.0) or not (0.0).",
            ),
        ]

    @property
    def name(self) -> str:
        """Get the evaluator name."""
        return "cvrp_construct_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Get the list of supported metrics."""
        return self._metrics

    def _find_solver(self, project_root: Path) -> Path | None:
        """Locate the ``solve.py`` driver in the worktree.

        Supports both the nested ``cvrp_algorithm/solve.py`` layout and a flat
        ``solve.py`` layout used by some worktree configurations.

        Args:
            project_root: Worktree root path.

        Returns:
            Path to ``solve.py`` if found, else ``None``.
        """
        candidates = [
            project_root / "cvrp_algorithm" / "solve.py",
            project_root / "solve.py",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Evaluate a CVRP solver against a single instance.

        Args:
            cfg: Evaluation context with ``project_root``, ``data_path`` and
                ``timeout``.

        Returns:
            Evaluation result with score and metrics.
        """
        start_time = time.time()

        try:
            project_root = Path(cfg.project_root)
            data_path = Path(cfg.data_path)

            if not data_path.exists():
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Data file not found: {data_path}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            solve_script = self._find_solver(project_root)
            if solve_script is None:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=(
                        f"solve.py not found in {project_root} "
                        f"(looked in cvrp_algorithm/ and root)"
                    ),
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # Run the solver in a subprocess, passing the instance file path.
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(solve_script),
                str(data_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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
                    error_message=f"Evaluation timed out after {cfg.timeout}s",
                    duration_ms=cfg.timeout * 1000,
                )

            duration_ms = (time.time() - start_time) * 1000
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=(
                        f"Solver failed (exit code {proc.returncode}): "
                        f"{stderr_text[:500] or stdout_text[:500]}"
                    ),
                    duration_ms=duration_ms,
                )

            try:
                output = json.loads(stdout_text.strip())
            except json.JSONDecodeError:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Invalid JSON output: {stdout_text[:300]}",
                    duration_ms=duration_ms,
                )

            total_cost = output.get("total_cost", float("inf"))
            route = output.get("routes", [])

            if total_cost == float("inf") or not route:
                return EvaluationResult(
                    score=0.0,
                    metrics={"normalized_gap": -1.0, "valid_route": 0.0},
                    success=False,
                    error_message="Solver produced no valid route",
                    duration_ms=duration_ms,
                )

            # Compute normalized gap using the per-instance baseline label.
            with open(data_path, encoding="utf-8") as f:
                instance_data = json.load(f)

            baseline = float(instance_data.get("label", 0.0))
            if baseline > 0:
                normalized_gap = -(total_cost - baseline) / baseline
            else:
                # No baseline available: fall back to raw negative cost.
                normalized_gap = -total_cost

            metrics = {
                "normalized_gap": normalized_gap,
                "total_cost": float(total_cost),
                "valid_route": 1.0,
            }

            return EvaluationResult(
                score=normalized_gap,
                metrics=metrics,
                success=True,
                duration_ms=duration_ms,
                metadata={
                    "dataset": str(data_path),
                    "instance": data_path.stem,
                    "total_cost": float(total_cost),
                    "baseline": baseline,
                },
            )

        except Exception as e:
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message=f"Evaluation error: {e}",
                duration_ms=(time.time() - start_time) * 1000,
            )
