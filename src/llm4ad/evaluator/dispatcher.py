"""Evaluation dispatcher for parallel/distributed evaluation.

This module provides the EvaluationDispatcher that handles distribution
of multiple candidate evaluation tasks according to parallel configuration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm4ad.config.schema import (
    CustomEvaluatorConfig,
    EvalContext,
    EvaluatorConfig,
    RemoteEvaluatorConfig,
    SolverEvaluatorConfig,
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
        records_dir: str | Path | None = None,
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
            records_dir: Durable record root, outside candidate worktrees.
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
        configured_records = os.environ.get("LLM4AD_EVALUATION_OUTBOX") or records_dir
        self._records_dir = Path(configured_records) if configured_records else None
        self._execution_id = os.environ.get("LLM4AD_EXECUTION_ID") or str(uuid.uuid4())

        # Concurrency limit for parallel evaluation subprocesses
        effective_workers = config.batch_size or os.cpu_count() or 4
        self._semaphore = asyncio.Semaphore(effective_workers)
        self._max_workers = effective_workers

        # Resolve evaluator class and register if needed
        self._eval_cls = None if isinstance(config, RemoteEvaluatorConfig) else self._resolve_evaluator_class()
        self._data_files = [] if isinstance(config, RemoteEvaluatorConfig) else self._get_data_files()

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
                cls = self._import_custom_evaluator(self.config.module, config_dir=self._config_dir)
                BaseEvaluator.register(registry_name, cls)
            return BaseEvaluator.get(registry_name)

        # Built-in evaluator (e.g. "executable") — already registered
        return BaseEvaluator.get(eval_type)

    @staticmethod
    def _import_custom_evaluator(module_path: str, config_dir: str | None = None) -> type[BaseEvaluator]:
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
            project_root = str(Path(config_dir).expanduser().resolve() if config_dir else Path.cwd().resolve())
            for import_path in (project_root, module_dir):
                if import_path not in sys.path:
                    sys.path.insert(0, import_path)

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
        if isinstance(self.config, SolverEvaluatorConfig):
            return self._eval_cls(self.config, config_dir=self._config_dir)
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
        if isinstance(self.config, RemoteEvaluatorConfig):
            from llm4ad.remote.contracts import EvaluationContract

            contract = EvaluationContract.model_validate(self.config.contract)
            return {metric.name: metric for metric in contract.metrics}
        evaluator = self._create_evaluator()
        return {metric.name: metric for metric in evaluator.metrics}

    def _aggregate_results(self, results: list[EvaluationResult]) -> EvaluationResult:
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
                error_message=(f"All {len(results)} instances failed. Errors: {'; '.join(errors[:3])}"),
                metadata={
                    "num_instances": len(results),
                    "successful_instances": 0,
                    "per_instance": [r.model_dump(mode="python", exclude={"behavior", "artifacts"}) for r in results],
                    "evaluation_records": [ref for r in results for ref in r.metadata.get("evaluation_records", [])],
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
            per_instance.append(r.model_dump(mode="python", exclude={"behavior", "artifacts"}))

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
            "evaluation_records": [ref for r in results for ref in r.metadata.get("evaluation_records", [])],
        }
        if all_behavior:
            metadata["all_behavior"] = all_behavior

        return EvaluationResult(
            score=avg_score,
            metrics=aggregated_metrics,
            monitor_metrics={
                name: sum(r.monitor_metrics[name] for r in successful if name in r.monitor_metrics)
                / sum(name in r.monitor_metrics for r in successful)
                for name in {key for r in successful for key in r.monitor_metrics}
            },
            success=True,
            duration_ms=total_duration,
            behavior=best_behavior,
            metadata=metadata,
            evolution_feedback=max(successful, key=lambda result: result.score).evolution_feedback,
        )

    async def _evaluate_one(self, alg, data_path: str, instance_id: str, options: dict) -> EvaluationResult:
        """Record one evaluation while preserving cancellation and scoring behavior."""
        from llm4ad.evaluator.records import atomic_json, complete_record, new_record

        evaluation_id = str(uuid.uuid4())
        root = self._records_dir / evaluation_id if self._records_dir else None
        fingerprint = alg.code_fingerprint() if hasattr(alg, "code_fingerprint") else ""
        cfg = EvalContext(
            **{
                **options,
                "project_root": alg.worktree.path if alg.worktree else "",
                "data_path": data_path,
                "behavior_storage": self._behavior_storage,
                "evaluation_id": evaluation_id,
                "algorithm_id": str(getattr(alg, "id", "")),
                "algorithm_name": getattr(alg, "name", ""),
                "source_fingerprint": fingerprint,
                "execution_id": self._execution_id,
                "instance_id": instance_id,
                "artifact_dir": str((root / "output").absolute()) if root else "",
            }
        )
        remote = isinstance(self.config, RemoteEvaluatorConfig)
        record = new_record(cfg, task_id=os.environ.get("LLM4AD_TASK_ID", ""), origin="remote" if remote else "local")
        if root and not remote:
            try:
                (root / "output").mkdir(parents=True, exist_ok=True)
                atomic_json(root / "record.json", record.model_dump(mode="json"))
            except OSError:
                logger.exception("Evaluation recording unavailable; continuing evaluation")
                root = None
        try:
            async with self._semaphore:
                if remote:
                    from llm4ad.remote.client import evaluate_remote

                    result = await evaluate_remote(self.config, cfg)
                else:
                    result = await self._create_evaluator().evaluate(cfg)
        except BaseException as exc:
            if root and not remote:
                failed = EvaluationResult(success=False, metrics={}, error_message=str(exc) or type(exc).__name__)
                try:
                    complete_record(root, record, failed)
                    if isinstance(exc, asyncio.CancelledError):
                        record.state = "cancelled"
                        atomic_json(root / "record.json", record.model_dump(mode="json"))
                except OSError:
                    logger.exception("Could not retain failed evaluation record")
            raise
        if remote:
            references = result.metadata.get("evaluation_records", [])
        elif root:
            try:
                record = await asyncio.to_thread(complete_record, root, record, result)
                result.artifacts = []
                references = [
                    {
                        "id": record.id,
                        "algorithm_id": cfg.algorithm_id,
                        "evaluation_profile": record.evaluation_profile,
                        "instance_id": instance_id,
                        "source_fingerprint": fingerprint,
                    }
                ]
            except Exception:
                logger.exception("Could not retain evaluation files; keeping the original score")
                references = []
        else:
            references = []
        result.metadata["evaluation_records"] = references
        if hasattr(alg, "custom_metadata"):
            previous = [
                ref for ref in alg.custom_metadata.get("evaluation_records", []) if ref.get("algorithm_id") == cfg.algorithm_id
            ]
            known = {ref["id"] for ref in previous}
            previous.extend(ref for ref in references if ref["id"] not in known)
            alg.custom_metadata = {**alg.custom_metadata, "evaluation_records": previous}
        return result

    async def dispatch_batch(self, algorithms: list[Algorithm], **kwargs) -> list[EvaluationResult]:
        """Evaluate candidates and retain each instance before aggregation.

        Args:
            algorithms: Candidates to evaluate.
            **kwargs: Evaluation context overrides, including fidelity and timeout.

        Returns:
            Aggregated results in candidate order.
        """
        kwargs.setdefault("timeout", self.config.timeout)
        paths = [""] if isinstance(self.config, RemoteEvaluatorConfig) else self._data_files or [""]
        calls = [
            (alg, path, f"{index}:{Path(path).name}" if path else "", kwargs)
            for alg in algorithms
            for index, path in enumerate(paths)
        ]
        if self._parallel:
            raw_results = list(await asyncio.gather(*(self._evaluate_one(*call) for call in calls)))
        else:
            raw_results = [await self._evaluate_one(*call) for call in calls]
        count = len(paths)
        return [self._aggregate_results(raw_results[i : i + count]) for i in range(0, len(raw_results), count)]
