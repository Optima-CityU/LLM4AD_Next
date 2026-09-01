"""Evaluator template for Python design tasks in LLM4AD.

HOW TO USE THIS TEMPLATE:
    1. Copy this file and rename (e.g. my_task_evaluator.py)
    2. Search for "TODO" — each marks a location you MUST customize
    3. Lines without TODO are reusable boilerplate (don't change)
    4. Reference in YAML config: module: "my_evaluator.py:MyEvaluator"

SUBPROCESS ISOLATION (SEPARATE-SCRIPT CONTRACT):
    Every evaluation runs the algorithm file as its own subprocess:

        python <algorithm_file> '<instance_json>'

    The algorithm file's ``main()`` reads the FULL instance dict from
    ``sys.argv[1]``, runs the algorithm, and prints ONE JSON object to
    stdout. The evaluator spawns that process, parses the JSON, and turns
    it into a score. This is the single contract used across the whole
    platform — the algorithm file, this evaluator, and the build-time
    validator all invoke the algorithm exactly the same way.

    Fault isolation: segfaults, ``sys.exit()``, deadlocks, or memory leaks
    in generated algorithm code cannot crash the main orchestrator, because
    the algorithm only ever runs inside the spawned subprocess.

COMMON DATA FORMAT:
    One JSON file per instance. Each evaluate() call receives one JSON file
    via ``cfg.data_path``. The file's contents are passed verbatim to the
    algorithm subprocess as ``sys.argv[1]``.

WORKTREE COMPATIBILITY:
    In production, the algorithm file lives in a git worktree (flat layout):
    - Local:    project_root/my_algorithm/my_function.py
    - Worktree: project_root/my_function.py
    Always check both paths.
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


# TODO: Change register name to your evaluator name (must match YAML module field)
@BaseEvaluator.register("my_task_evaluator")
# TODO: Rename class
class MyTaskEvaluator(BaseEvaluator):
    """Evaluator template for a custom Python design task.

    Runs each evaluation by spawning the algorithm file as an isolated
    subprocess (``python <algorithm_file> '<instance_json>'``), then parses
    the JSON the subprocess prints to stdout and computes a score.

    Benefits:
    - Segfaults, sys.exit(), memory leaks in generated code cannot crash
      the main orchestrator process.
    - True multi-instance parallelism via async subprocess (no GIL).
    - Timeout protection via asyncio.wait_for + proc.kill().
    """

    def __init__(self):
        """Initialize evaluator with metric definitions."""
        # TODO: Define metrics for your task. Each Metric must match
        # the `metrics` list in your YAML config.
        # MetricType.MAXIMIZE = higher is better (reward, accuracy)
        # MetricType.MINIMIZE = lower is better (time, error, cost)
        self._metrics = [
            Metric(
                name="primary_score",
                type=MetricType.MAXIMIZE,
                weight=1.0,
                description="Primary evaluation metric",
            ),
            Metric(
                name="execution_time_ms",
                type=MetricType.MINIMIZE,
                weight=0.1,
                description="Execution time in milliseconds",
            ),
        ]

    @property
    def name(self) -> str:
        """Get the evaluator name."""
        # TODO: Return your evaluator name (should match register name)
        return "my_task_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Get the list of supported metrics."""
        return self._metrics

    # ====================================================================
    # Locate the algorithm file (worktree-compatible)
    # ====================================================================

    @staticmethod
    def _resolve_algorithm_file(project_root: Path) -> Path | None:
        """Find the algorithm file under either the nested or flat layout.

        Args:
            project_root: Root directory passed via ``cfg.project_root``.

        Returns:
            Path to the algorithm file, or ``None`` if it cannot be found.
        """
        # TODO: Change "my_algorithm" to your algorithm directory name and
        # "my_function.py" to your algorithm filename.
        nested = project_root / "my_algorithm" / "my_function.py"
        if nested.exists():
            return nested
        flat = project_root / "my_function.py"
        if flat.exists():
            return flat
        return None

    # ====================================================================
    # Main process side: spawn subprocess, parse result
    # ====================================================================

    async def evaluate(
        self,
        cfg: EvalContext,
    ) -> EvaluationResult:
        """Evaluate an algorithm implementation via subprocess.

        Spawns the algorithm file as a subprocess, passing the full instance
        JSON as ``sys.argv[1]``. The subprocess runs the algorithm and prints
        a single JSON object to stdout.

        Args:
            cfg: EvalContext with:
                - cfg.project_root: path to algorithm directory (worktree or local)
                - cfg.data_path: path to the JSON data file for this evaluation
                - cfg.timeout: max execution time in seconds

        Returns:
            EvaluationResult with score, metrics, and metadata.
        """
        start_time = time.time()

        try:
            project_root = Path(cfg.project_root)
            data_path = Path(cfg.data_path)

            # --- Step 1: Validate data file (boilerplate) ---
            if not data_path.exists():
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Data file not found: {data_path}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # --- Step 2: Locate the algorithm file (boilerplate) ---
            algo_file = self._resolve_algorithm_file(project_root)
            if algo_file is None:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Algorithm file not found in {project_root}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # --- Step 3: Spawn algorithm subprocess (boilerplate) ---
            # The full instance JSON is passed verbatim as argv[1]; the
            # algorithm's main() reads it with json.loads(sys.argv[1]).
            instance_json = data_path.read_text(encoding="utf-8").strip()

            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(algo_file), instance_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=cfg.timeout,
                )
            except asyncio.TimeoutError:
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
                    error_message=f"Subprocess failed (rc={proc.returncode}): {stderr_text[:500]}",
                    duration_ms=duration_ms,
                )

            # --- Step 4: Parse subprocess output (boilerplate) ---
            try:
                result = json.loads(stdout_text.strip())
            except json.JSONDecodeError:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Invalid JSON output: {stdout_text[:200]}",
                    duration_ms=duration_ms,
                )

            if isinstance(result, dict) and "error" in result:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=str(result["error"]),
                    duration_ms=duration_ms,
                )

            # --- Step 5: Compute score from subprocess result ---
            # TODO: Extract values from the result dict and compute your score.
            # The result dict is whatever the algorithm's process() returns.
            #
            # Score convention (higher is always better for evolution):
            #   MINIMIZE tasks (tour length, time): score = -value
            #   MAXIMIZE tasks (reward, accuracy):  score = value
            #   Composite: score = w1*metric1 + w2*metric2 + ...
            payload = result.get("result", result) if isinstance(result, dict) else result
            primary = result.get("primary_score", 0.0) if isinstance(result, dict) else 0.0
            score = float(primary)

            # TODO: Build metrics dict matching your _metrics definitions
            metrics = {
                "primary_score": score,
                "execution_time_ms": (
                    result.get("execution_time_ms", duration_ms)
                    if isinstance(result, dict)
                    else duration_ms
                ),
            }

            return EvaluationResult(
                score=score,
                metrics=metrics,
                success=True,
                duration_ms=duration_ms,
                metadata={
                    "dataset": str(data_path),
                    "result": payload,
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
