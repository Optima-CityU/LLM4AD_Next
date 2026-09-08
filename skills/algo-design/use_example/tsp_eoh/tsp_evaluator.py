import asyncio
import json
import sys
import time
from pathlib import Path
import numpy as np

from llm4ad.evaluator.base import BaseEvaluator, EvalContext, EvaluationResult, Metric, MetricType


@BaseEvaluator.register("python_tsp_evaluator")
class PythonTSPEvaluator(BaseEvaluator):
    def __init__(self):
        self._metrics = [Metric(name="tour_length", type=MetricType.MINIMIZE, weight=1.0)]

    @property
    def name(self) -> str:
        return "python_tsp_evaluator"

    @property
    def metrics(self):
        return self._metrics

    def _calculate_tour_length(self, nodes, tour):
        if len(tour) < 2:
            return 0.0
        total = 0.0
        nodes = np.array(nodes)
        for i in range(len(tour) - 1):
            total += np.linalg.norm(nodes[tour[i]] - nodes[tour[i + 1]])
        total += np.linalg.norm(nodes[tour[-1]] - nodes[tour[0]])
        return total

    def _validate_tour(self, n_nodes, tour):
        if not tour:
            return False, "Empty tour"
        if len(tour) != n_nodes:
            return False, f"Tour length {len(tour)} != {n_nodes}"
        if len(set(tour)) != n_nodes:
            return False, "Duplicate nodes"
        if any(i < 0 or i >= n_nodes for i in tour):
            return False, "Invalid index"
        return True, None

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        start_time = time.time()
        try:
            data_path = Path(cfg.data_path)
            with open(data_path) as f:
                test_data = json.load(f)
            nodes = test_data.get("nodes", [])
            n_nodes = len(nodes)

            input_json = json.dumps({"nodes": nodes})
            solve_script = Path(cfg.project_root) / "solve.py"

            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(solve_script), input_json,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=cfg.timeout)
            duration_ms = (time.time() - start_time) * 1000

            if proc.returncode != 0:
                return EvaluationResult(score=0.0, metrics={}, success=False,
                                       error_message=stderr_bytes.decode(), duration_ms=duration_ms)

            output_data = json.loads(stdout_bytes.decode().strip())
            tour = output_data.get("tour", [])

            is_valid, error_msg = self._validate_tour(n_nodes, tour)
            if not is_valid:
                return EvaluationResult(score=0.0, metrics={}, success=False,
                                       error_message=error_msg, duration_ms=duration_ms)

            tour_length = self._calculate_tour_length(nodes, tour)
            return EvaluationResult(score=-tour_length, metrics={"tour_length": tour_length},
                                   success=True, duration_ms=duration_ms)
        except Exception as e:
            return EvaluationResult(score=0.0, metrics={}, success=False,
                                   error_message=str(e), duration_ms=(time.time() - start_time) * 1000)
