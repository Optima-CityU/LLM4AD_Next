"""Native LLM4AD evaluator for the 26-circle unit-square benchmark."""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)

NUM_CIRCLES = 26


@BaseEvaluator.register("alphaevolve_circle_packing_evaluator")
class CirclePackingEvaluator(BaseEvaluator):
    """Evaluate the sum of radii for 26 disjoint circles in a unit square."""

    def __init__(self) -> None:
        """Define the objective and diagnostic metrics."""
        self._metrics = [
            Metric(
                name="sum_radii",
                type=MetricType.MAXIMIZE,
                weight=1.0,
                description="Sum of all 26 circle radii",
            ),
            Metric(
                name="validity",
                type=MetricType.MAXIMIZE,
                weight=0.0,
                description="Whether all geometric constraints are satisfied",
            ),
        ]

    @property
    def name(self) -> str:
        """Return the evaluator registry name."""
        return "alphaevolve_circle_packing_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Return supported objective and diagnostic metrics."""
        return self._metrics

    @staticmethod
    def _failure(message: str, started_at: float) -> EvaluationResult:
        return EvaluationResult(
            score=0.0,
            metrics={"validity": 0.0},
            success=False,
            error_message=message,
            duration_ms=(time.monotonic() - started_at) * 1000,
        )

    @staticmethod
    def _parse_output(stdout: str) -> dict[str, Any]:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise ValueError("Candidate produced no output")
        parsed = json.loads(lines[-1])
        if not isinstance(parsed, dict):
            raise ValueError("Candidate output must be a JSON object")
        return parsed

    @staticmethod
    def _validate_geometry(
        centers: np.ndarray,
        radii: np.ndarray,
    ) -> str | None:
        if centers.shape != (NUM_CIRCLES, 2):
            return f"centers must have shape ({NUM_CIRCLES}, 2), got {centers.shape}"
        if radii.shape != (NUM_CIRCLES,):
            return f"radii must have shape ({NUM_CIRCLES},), got {radii.shape}"
        if not np.isfinite(centers).all() or not np.isfinite(radii).all():
            return "centers and radii must contain only finite values"
        if np.any(radii < 0):
            return "radii must be non-negative"

        is_contained = ((radii[:, None] <= centers) & (centers <= 1.0 - radii[:, None])).all(axis=1)
        if not is_contained.all():
            return "one or more circles lie outside the unit square"

        for i in range(NUM_CIRCLES):
            for j in range(i + 1, NUM_CIRCLES):
                distance = float(np.linalg.norm(centers[i] - centers[j]))
                if float(radii[i] + radii[j]) > distance:
                    return f"circles {i} and {j} overlap"
        return None

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Execute one candidate and validate its circle geometry."""
        started_at = time.monotonic()
        solve_path = Path(cfg.project_root) / "solve.py"
        if not solve_path.is_file():
            return self._failure(f"solve.py not found in {cfg.project_root}", started_at)

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(solve_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(solve_path.parent),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=cfg.timeout)
            except TimeoutError:
                process.kill()
                await process.communicate()
                return self._failure(f"Evaluation timed out after {cfg.timeout:g}s", started_at)

            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            if process.returncode != 0:
                return self._failure(
                    f"Candidate execution failed: {stderr or f'exit code {process.returncode}'}",
                    started_at,
                )

            output = self._parse_output(stdout_bytes.decode("utf-8", errors="replace"))
            centers = np.asarray(output.get("centers"), dtype=float)
            radii = np.asarray(output.get("radii"), dtype=float)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return self._failure(f"Invalid candidate output: {exc}", started_at)
        except Exception as exc:
            return self._failure(f"Evaluation error: {exc}", started_at)

        validation_error = self._validate_geometry(centers, radii)
        if validation_error:
            return self._failure(f"Validation failed: {validation_error}", started_at)

        sum_radii = float(np.sum(radii))
        if not math.isfinite(sum_radii):
            return self._failure("Validation failed: objective is not finite", started_at)

        duration_ms = (time.monotonic() - started_at) * 1000
        return EvaluationResult(
            score=sum_radii,
            metrics={
                "sum_radii": sum_radii,
                "validity": 1.0,
            },
            monitor_metrics={
                "sum_radii": sum_radii,
                "validity": 1.0,
            },
            success=True,
            duration_ms=duration_ms,
            metadata={
                "benchmark": "packing_circle_in_unit_square_n26",
                "objective_direction": "maximize",
            },
        )
