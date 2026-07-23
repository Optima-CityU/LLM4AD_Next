"""Evaluation dispatcher for parallel/distributed evaluation.

This module provides the EvaluationDispatcher that handles distribution
of multiple candidate evaluation tasks according to parallel configuration.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from llm4ad.config.schema import (
    CustomEvaluatorConfig,
    EvalContext,
    EvaluatorConfig,
)
from llm4ad.evaluator.base import BaseEvaluator, EvaluationResult

if TYPE_CHECKING:
    from llm4ad.planner.base import Algorithm

logger = logging.getLogger(__name__)


class EvaluationDispatcher:
    """Dispatcher that handles parallel/distributed evaluation of multiple candidates.

    Takes an actual evaluator instance (implementing BaseEvaluator)
    and dispatches the batch evaluation according to the parallelization settings.
    Supports serial execution, parallel on single machine, and can be extended
    for distributed execution across multiple nodes.

    When multiple data files are discovered (e.g. TSP with 5 instance files),
    the dispatcher automatically aggregates per-file results into a single
    result per algorithm using mean aggregation.

    **Relationship with Task:**
    - Task/TaskConfig: Defines *what* to evaluate - problem description, dataset, task type
    - EvaluationDispatcher: Defines *how* to evaluate - handles distribution of multiple
      candidate evaluations according to parallelization settings
    """

    def __init__(
        self,
        config: EvaluatorConfig,
        behavior_storage: str = "rendered",
        config_dir: str | None = None,
        provider_config: Any = None,
    ):
        """Initialize the dispatcher with an evaluator instance.

        Args:
            config: Evaluation configuration containing parallel settings.
            behavior_storage: How evaluators should store behavior data.
                One of ``"rendered"`` (pre-rendered images), ``"raw"``
                (compact JSON data for deferred rendering), or ``"none"``
                (no behavior data). Passed through to each
                ``EvalContext``.
            config_dir: Directory of the config file. Relative paths in
                evaluator module specs and dataset configs are resolved
                against this directory instead of the current working
                directory.
            provider_config: Resolved provider configuration for custom
                evaluators that need LLM access. Passed through to the
                evaluator constructor.
        """
        self.config: EvaluatorConfig = config
        self._parallel = config.parallel
        self._batch_size = config.batch_size
        self._behavior_storage = behavior_storage
        self._config_dir = config_dir
        self._provider_config = provider_config

        # Concurrency limit for parallel evaluation subprocesses
        effective_workers = config.batch_size or os.cpu_count() or 4
        self._semaphore = asyncio.Semaphore(effective_workers)
        self._max_workers = effective_workers

        # Resolve evaluator class and register if needed
        self._eval_cls: type[BaseEvaluator] = self._resolve_evaluator_class()
        self._data_files = self._get_data_files()

        logger.info(
            "EvaluationDispatcher: max_workers=%d, parallel=%s",
            self._max_workers,
            self._parallel,
        )

    def _resolve_evaluator_class(self) -> type[BaseEvaluator]:
        """Resolve the evaluator class via the registry.

        For built-in types (e.g. ``executable``), the class is already
        registered.  For ``custom`` type, the class is dynamically imported
        from the ``module`` path and registered under a deterministic name
        so subsequent lookups hit the registry.

        Returns:
            Evaluator class.
        """
        eval_type = self.config.type

        # For custom evaluators, import and register on first use
        if isinstance(self.config, CustomEvaluatorConfig):
            registry_name = f"custom:{self.config.module}"
            if registry_name not in BaseEvaluator.list():
                cls = self._import_custom_evaluator(
                    self.config.module, config_dir=self._config_dir
                )
                BaseEvaluator.register(registry_name, cls)
            return BaseEvaluator.get(registry_name)

        # Built-in evaluator (e.g. "executable") — already registered
        return BaseEvaluator.get(eval_type)

    @staticmethod
    def _import_custom_evaluator(
        module_path: str, config_dir: str | None = None
    ) -> type[BaseEvaluator]:
        """Dynamically import a custom evaluator class.

        Args:
            module_path: Import path in format ``module.path:ClassName``
                or ``path/to/file.py:ClassName``.
            config_dir: Directory of the config file. Relative file paths
                are resolved against this directory.

        Returns:
            Evaluator class.
        """
        import importlib
        import sys
        from pathlib import Path

        module_spec, class_name = module_path.rsplit(":", 1)

        if "/" in module_spec or "\\" in module_spec or module_spec.endswith(".py"):
            spec_path = Path(module_spec).expanduser()
            if not spec_path.is_absolute() and config_dir:
                file_path = (Path(config_dir) / spec_path).resolve()
            else:
                file_path = spec_path.resolve()
            if file_path.suffix != ".py":
                file_path = file_path.with_suffix(".py")

            if not file_path.exists():
                raise FileNotFoundError(f"Custom evaluator file not found: {file_path}")

            module_dir = str(file_path.parent)
            if module_dir not in sys.path:
                sys.path.insert(0, module_dir)

            module_name = file_path.stem
            module = importlib.import_module(module_name)
        else:
            module = importlib.import_module(module_spec)

        return getattr(module, class_name)

    def _create_evaluator(self) -> BaseEvaluator:
        """Create an evaluator instance via the registry.

        Built-in evaluators always receive the config.  For custom
        evaluators, we inspect the constructor: if it accepts a
        parameter beyond ``self``, the config and provider_config
        are passed so the evaluator can access LLM credentials.
        Legacy evaluators with ``def __init__(self):`` keep working.

        Returns:
            Evaluator instance.
        """
        if isinstance(self.config, CustomEvaluatorConfig):
            import inspect

            sig = inspect.signature(self._eval_cls.__init__)
            params = list(sig.parameters.keys())
            if len(params) > 1:
                kwargs: dict[str, Any] = {"config": self.config}
                if "provider_config" in params and self._provider_config is not None:
                    kwargs["provider_config"] = self._provider_config
                return self._eval_cls(**kwargs)
            return self._eval_cls()
        return self._eval_cls(self.config)

    def _get_data_files(self) -> list[str]:
        """Get all the candidate data files to evaluate.

        Supports three dataset discovery modes:
        - files: Explicit list of specific dataset files
        - directory: Automatically traverse all files in a directory
        - glob: Glob pattern matching for dataset files

        Returns:
            List of absolute file paths to evaluate.
        """
        from pathlib import Path

        dataset = self.config.dataset
        mode = dataset.mode

        def _resolve(p: str) -> Path:
            """Resolve a path relative to config_dir if not absolute."""
            path = Path(p).expanduser()
            if not path.is_absolute() and self._config_dir:
                return (Path(self._config_dir) / path).resolve()
            return path.resolve()

        if mode == "files":
            # Explicit list of files
            if dataset.files is None:
                return []
            return [str(_resolve(f)) for f in dataset.files]

        elif mode == "directory":
            # Directory traversal
            if dataset.path is None:
                return []
            base_path = _resolve(dataset.path)
            if not base_path.exists():
                raise FileNotFoundError(f"Dataset directory not found: {base_path}")

            if dataset.recursive:
                # Recursive: get all files
                return [str(p) for p in base_path.rglob("*") if p.is_file()]
            else:
                # Non-recursive: only immediate children
                return [str(p) for p in base_path.iterdir() if p.is_file()]

        elif mode == "glob":
            # Glob pattern matching
            if dataset.pattern is None:
                return []
            base_path = _resolve(dataset.path) if dataset.path else _resolve(".")
            return [str(p) for p in base_path.glob(dataset.pattern)]

        else:
            raise ValueError(f"Unknown dataset mode: {mode}")

    def get_metric_definitions(self) -> dict[str, Any]:
        """Return evaluator metric definitions keyed by metric name.

        Instantiates the evaluator to read its ``metrics`` attribute
        and returns a dict mapping metric name to the metric definition.

        Returns:
            Dictionary of metric definitions.
        """
        evaluator = self._eval_cls()
        return {metric.name: metric for metric in evaluator.metrics}

    def _aggregate_results(
        self, results: list[EvaluationResult]
    ) -> EvaluationResult:
        """Aggregate multiple per-instance results into a single result.

        For a single result, returns it unchanged (zero overhead for evaluators
        that already aggregate internally, e.g. LunarLander).
        For multiple results, computes mean score and metrics across successful
        instances, preserving per-instance details in metadata.

        Behavior data aggregation: selects behavior from the best-scoring
        successful instance as the primary ``behavior`` field and stores all
        instance behaviors in ``metadata["all_behavior"]``.

        Args:
            results: List of per-instance evaluation results.

        Returns:
            Single aggregated EvaluationResult.
        """
        if len(results) == 1:
            return results[0]

        if not results:
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message="No evaluation results to aggregate",
            )

        successful = [r for r in results if r.success]

        if not successful:
            errors = [r.error_message for r in results if r.error_message]
            return EvaluationResult(
                score=0.0,
                metrics={},
                success=False,
                error_message=(
                    f"All {len(results)} instances failed. "
                    f"Errors: {'; '.join(errors[:3])}"
                ),
                metadata={
                    "num_instances": len(results),
                    "successful_instances": 0,
                },
            )

        # Mean aggregation across successful instances
        aggregated_metrics: dict[str, float] = {}
        total_score = 0.0
        total_duration = 0.0

        for r in successful:
            total_score += r.score
            total_duration += r.duration_ms
            for name, value in r.metrics.items():
                aggregated_metrics.setdefault(name, 0.0)
                aggregated_metrics[name] += value

        count = len(successful)
        avg_score = total_score / count
        for name in aggregated_metrics:
            aggregated_metrics[name] /= count

        # Build per-instance summary for metadata (lightweight)
        per_instance: list[dict[str, Any]] = []
        for r in results:
            per_instance.append({
                "score": r.score,
                "success": r.success,
                "error_message": r.error_message,
            })

        # Aggregate behavior data: pick the best instance's behavior as primary
        best_behavior = None
        all_behavior = []
        best_score = float("-inf")
        for r in successful:
            if r.behavior is not None:
                all_behavior.append(r.behavior)
                if r.score > best_score:
                    best_score = r.score
                    best_behavior = r.behavior

        metadata: dict[str, Any] = {
            "num_instances": len(results),
            "successful_instances": count,
            "per_instance": per_instance,
        }
        if all_behavior:
            metadata["all_behavior"] = all_behavior

        return EvaluationResult(
            score=avg_score,
            metrics=aggregated_metrics,
            success=True,
            duration_ms=total_duration,
            behavior=best_behavior,
            metadata=metadata,
        )

    async def dispatch_batch(
        self,
        algorithms: list[Algorithm],
        **kwargs,
    ) -> list[EvaluationResult]:
        """Dispatch a batch of algorithms for evaluation.

        Each algorithm is evaluated against all discovered data files.
        Per-file results are aggregated into a single result per algorithm
        using mean aggregation.

        Args:
            algorithms: List of algorithms to evaluate.
            **kwargs: Additional evaluation options.

        Returns:
            List of aggregated evaluation results, one per algorithm.

        TODO: Now → Means, maybe can update to more configuration（算术平均，几何平均，最大值，最小值之类的）
        """
        kwargs.setdefault("timeout", self.config.timeout)
        n_files = len(self._data_files)

        if n_files == 0:
            # No dataset configured — evaluate each algorithm once without data files.
            if self._parallel:
                tasks = []
                for alg in algorithms:
                    cfg = EvalContext(
                        data_path="",
                        project_root=alg.worktree.path if alg.worktree else "",
                        behavior_storage=self._behavior_storage,
                        **kwargs,
                    )
                    evaluator = self._create_evaluator()
                    tasks.append(evaluator.evaluate(cfg))
                return list(await asyncio.gather(*tasks))
            else:
                results = []
                for alg in algorithms:
                    cfg = EvalContext(
                        data_path="",
                        project_root=alg.worktree.path if alg.worktree else "",
                        behavior_storage=self._behavior_storage,
                        **kwargs,
                    )
                    evaluator = self._create_evaluator()
                    results.append(await evaluator.evaluate(cfg))
                return results

        # Collect raw per-instance results
        if self._parallel:
            semaphore = self._semaphore

            async def _limited_eval(
                evaluator: BaseEvaluator, cfg: EvalContext
            ) -> EvaluationResult:
                async with semaphore:
                    return await evaluator.evaluate(cfg)

            tasks = []
            for alg in algorithms:
                for data_path in self._data_files:
                    cfg = EvalContext(
                        data_path=data_path,
                        project_root=alg.worktree.path if alg.worktree else "",
                        behavior_storage=self._behavior_storage,
                        **kwargs,
                    )
                    evaluator = self._create_evaluator()
                    tasks.append(_limited_eval(evaluator, cfg))
            raw_results = list(await asyncio.gather(*tasks))
        else:
            raw_results = []
            for alg in algorithms:
                for data_path in self._data_files:
                    cfg = EvalContext(
                        data_path=data_path,
                        project_root=alg.worktree.path if alg.worktree else "",
                        behavior_storage=self._behavior_storage,
                        **kwargs,
                    )
                    evaluator = self._create_evaluator()
                    raw_results.append(await evaluator.evaluate(cfg))

        # Aggregate per-algorithm: group by chunks of n_files
        aggregated = []
        for i in range(len(algorithms)):
            start = i * n_files
            end = start + n_files
            alg_results = raw_results[start:end]
            aggregated.append(self._aggregate_results(alg_results))

        return aggregated
