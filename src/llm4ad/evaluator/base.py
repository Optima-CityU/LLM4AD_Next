"""Base evaluator interface for LLM4AD."""

import asyncio
import re
import time
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from llm4ad.config.schema import EvalContext, ExecutableEvaluatorConfig
from llm4ad.evaluator.behavior import BehaviorData
from llm4ad.utils.registry import Registrable


class MetricType(Enum):
    """Types of evaluation metrics."""

    MINIMIZE = "minimize"  # Lower is better
    MAXIMIZE = "maximize"  # Higher is better


class Metric(BaseModel):
    """A single metric definition."""

    name: str
    type: MetricType = MetricType.MAXIMIZE
    weight: float = 1.0  # For multi-objective optimization
    description: str = ""

    model_config = ConfigDict(frozen=True)


class EvaluationResult(BaseModel):
    """Evaluation result container.

    Contains the computed score, detailed metrics, and any extra metadata
    for monitoring, logging, and algorithm analysis.

    Users only need to provide:
    - `metrics`: dict of metric name -> value (supports multiple objectives/metrics)
    - `score`: Primary aggregated score (computed by base class from metrics)
    Everything else is optional with good defaults.

    Extra information that is not used for evolution (monitoring, logs, analysis)
    should go into `metadata`.
    """
    score: float = 0.0  # Primary score for evolution (computed from metrics)
    metrics: dict[str, float]  # All evaluation metrics (objectives + secondary metrics)
    monitor_metrics: dict[str, float] = {}  # Metrics for monitoring (subset of metrics)
    metadata: dict[str, Any] = {}  # Extra info: monitoring, logs, analysis (optional)
    evolution_feedback: dict[str, Any] = Field(default_factory=dict)
    """Compact, safe evaluator findings that descendants may reuse."""
    success: bool = True  # Whether evaluation succeeded, defaults to True
    error_message: str | None = None  # Error message if failed, optional
    duration_ms: float = 0.0  # Evaluation duration in milliseconds, optional
    behavior: BehaviorData | None = None  # Behavior data from evaluation (optional)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class BaseEvaluator(Registrable, ABC, registry_name="evaluator"):
    """Abstract evaluator interface.

    Evaluators are responsible for measuring algorithm quality by running
    algorithms on evaluation tasks and computing scores based on metrics.

    The dataset is passed as a path (file or folder) to the user-provided
    evaluation function. Users define their own evaluation logic and handle
    data loading themselves.
    """

    def __init__(self):
        """Initialize evaluator with configuration.

        Args:
            config: Evaluation configuration
        """
        pass

    @abstractmethod
    async def evaluate(
        self, cfg: EvalContext
    ) -> EvaluationResult:
        """Evaluate a single algorithm.

        Args:
            cfg: Evaluation options

        Returns:
            Evaluation result with score and metrics
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Evaluator name.

        Returns:
            Unique name identifying this evaluator type
        """
        pass

    @property
    @abstractmethod
    def metrics(self) -> list[Metric]:
        """Supported metrics.

        Returns:
            List of metrics this evaluator can compute
        """
        pass

    def compute_score(
        self, metric_values: dict[str, float], weights: dict[str, float] | None = None
    ) -> float:
        """Compute overall score from metric values.

        Default implementation computes weighted sum. Override for
        custom scoring logic.

        Args:
            metric_values: Dict of metric name to value
            weights: Optional weights for each metric (defaults to metric.weight)

        Returns:
            Computed score
        """
        if not metric_values:
            return 0.0

        total = 0.0
        total_weight = 0.0

        for metric in self.metrics:
            if metric.name not in metric_values:
                continue

            value = metric_values[metric.name]
            weight = weights.get(metric.name, metric.weight) if weights else metric.weight

            # Normalize based on metric type
            if metric.type == MetricType.MINIMIZE:
                # Invert so lower is better
                value = -value if value != 0 else 0

            total += value * weight
            total_weight += weight

        return total / total_weight if total_weight > 0 else 0.0


class PythonEvaluator(BaseEvaluator, ABC):
    """Base class for Python-based evaluation.

    Handles importing and executing Python code directly, with timeout
    and exception capture. Subclasses should implement the evaluate
    method to handle the specific evaluation logic.
    """

    def __init__(self, config: EvalContext):
        """Initialize the Python evaluator with configuration.

        Args:
            config: Evaluation configuration.
        """
        super().__init__(config)


@BaseEvaluator.register("executable")
class ExecutableEvaluator(BaseEvaluator):
    """Generic evaluator for compiled/executable programs.

    Configured via ``ExecutableEvaluatorConfig``. Workflow:

    1. Resolve executable path relative to ``project_root``
    2. Build command string from template with placeholder substitution
    3. Execute command asynchronously with timeout
    4. Parse stdout/stderr with configured regex patterns
    5. Compute weighted score from parsed metrics
    """

    def __init__(self, config: ExecutableEvaluatorConfig):
        """Initialize the executable evaluator.

        Args:
            config: Executable evaluator configuration with command
                template, executable path, and metric patterns.
        """
        self._config = config
        self._metrics = [
            Metric(
                name=p.name,
                type=MetricType(p.type),
                weight=p.weight,
            )
            for p in config.metric_patterns
        ]

    @property
    def name(self) -> str:
        """Get the evaluator name."""
        return "executable_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Get the list of supported metrics."""
        return self._metrics

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Evaluate an algorithm by running its executable.

        Args:
            cfg: Evaluation configuration with project_root and data_path.

        Returns:
            Evaluation result with parsed metrics and computed score.
        """
        start_time = time.time()

        try:
            # 1. Resolve executable path
            executable_path = Path(cfg.project_root) / self._config.executable
            if not executable_path.exists():
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Executable not found: {executable_path}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # 2. Build command from template
            timeout = self._config.timeout or cfg.timeout
            command_str = self._config.command.format(
                executable=str(executable_path),
                data_file=cfg.data_path,
                timeout=str(timeout),
                project_root=cfg.project_root,
            )

            # 3. Execute asynchronously
            proc = await asyncio.create_subprocess_shell(
                command_str,
                cwd=cfg.project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=f"Execution timed out after {timeout}s",
                    duration_ms=timeout * 1000,
                )

            duration_ms = (time.time() - start_time) * 1000
            stdout_text = stdout_bytes.decode(errors="replace")
            stderr_text = stderr_bytes.decode(errors="replace")

            if proc.returncode != 0:
                return EvaluationResult(
                    score=0.0,
                    metrics={},
                    success=False,
                    error_message=(
                        f"Execution failed (exit code {proc.returncode}): "
                        f"{stderr_text.strip() or stdout_text.strip()}"
                    ),
                    duration_ms=duration_ms,
                )

            # 4. Parse metrics using configured regex patterns
            parsed_metrics = self._parse_metrics(stdout_text, stderr_text)

            # 5. Compute score
            score = self.compute_score(parsed_metrics)

            return EvaluationResult(
                score=score,
                metrics=parsed_metrics,
                success=True,
                duration_ms=duration_ms,
                metadata={"dataset": cfg.data_path},
            )

        except Exception as e:
            logger.warning(f"Executable evaluation error: {e}")
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message=f"Evaluation error: {e}",
                duration_ms=(time.time() - start_time) * 1000,
            )

    def _parse_metrics(self, stdout: str, stderr: str) -> dict[str, float]:
        """Parse metrics from process output using configured regex patterns.

        Each pattern in ``metric_patterns`` is searched against the combined
        stdout and stderr. The first capture group is taken as the metric value.

        Args:
            stdout: Standard output from the process.
            stderr: Standard error from the process.

        Returns:
            Dictionary mapping metric names to parsed float values.
        """
        combined = stdout + "\n" + stderr
        result: dict[str, float] = {}
        for pattern_cfg in self._config.metric_patterns:
            match = re.search(pattern_cfg.pattern, combined)
            if match:
                try:
                    result[pattern_cfg.name] = float(match.group(1))
                except (ValueError, IndexError):
                    logger.warning(
                        f"Failed to parse metric '{pattern_cfg.name}' "
                        f"from pattern '{pattern_cfg.pattern}'"
                    )
        return result


class BenchmarkEvaluator(BaseEvaluator, ABC):
    """Base class for standard benchmark datasets.

    Provides common functionality for loading benchmark datasets,
    preprocessing, and aggregating results across multiple problem instances.
    """

    def __init__(self, config: EvalContext):
        """Initialize the benchmark evaluator with configuration.

        Args:
            config: Evaluation configuration.
        """
        super().__init__(config)
        self._problem_instances: list[Any] = []

    @abstractmethod
    def load_dataset(self, dataset_path: str) -> list[Any]:
        """Load problem instances from dataset.

        Args:
            dataset_path: Path to the dataset directory or file

        Returns:
            List of loaded problem instances
        """
        pass

    @property
    def problem_instances(self) -> list[Any]:
        """Get the loaded problem instances."""
        return self._problem_instances

    def aggregate_results(self, results: list[EvaluationResult]) -> EvaluationResult:
        """Aggregate results across multiple problem instances.

        Args:
            results: List of evaluation results for each problem instance

        Returns:
            Aggregated evaluation result with average score across all problems
        """
        if not results:
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message="No problem instances to evaluate",
            )

        # Aggregate metrics by averaging across instances
        aggregated_metrics: dict[str, float] = {}
        all_success = all(r.success for r in results)
        total_score = 0.0
        count = 0

        for result in results:
            if result.success:
                total_score += result.score
                count += 1
                for name, value in result.metrics.items():
                    if name not in aggregated_metrics:
                        aggregated_metrics[name] = 0.0
                    aggregated_metrics[name] += value

        # Average the metrics
        if count > 0:
            for name in aggregated_metrics:
                aggregated_metrics[name] /= count

        avg_score = total_score / count if count > 0 else 0.0

        return EvaluationResult(
            score=avg_score,
            metrics=aggregated_metrics,
            success=all_success,
            metadata={"num_instances": len(results), "successful_instances": count},
        )
