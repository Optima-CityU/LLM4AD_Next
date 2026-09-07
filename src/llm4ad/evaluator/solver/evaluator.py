"""Generic evaluator for structured candidates solved by mathematical backends."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import math
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from llm4ad.config.evaluator import SolverEvaluatorConfig
from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.base import BaseEvaluator, EvaluationResult, Metric, MetricType
from llm4ad.evaluator.solver.backend import create_backend
from llm4ad.evaluator.solver.base import BaseSolverAdapter, SolverContext
from llm4ad.evaluator.solver.candidate import load_candidate


@BaseEvaluator.register("solver")
class SolverEvaluator(BaseEvaluator):
    """Run a structured candidate through a pluggable solver problem adapter."""

    def __init__(self, config: SolverEvaluatorConfig, config_dir: str | None = None) -> None:
        """Initialize the evaluator from its generic backend and adapter contract."""
        self._config = config
        self._config_dir = Path(config_dir).resolve() if config_dir else None
        self._metrics = [
            Metric(
                name=metric.name,
                type=MetricType(metric.type),
                weight=metric.weight,
                description=metric.description,
            )
            for metric in config.metrics
        ]

    @property
    def name(self) -> str:
        """Return the evaluator registry name."""
        return "solver_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Return configured objective and monitoring metrics."""
        return self._metrics

    def _load_adapter_module(self) -> tuple[ModuleType, str]:
        module_spec, class_name = self._config.adapter.rsplit(":", 1)
        if "/" not in module_spec and "\\" not in module_spec and not module_spec.endswith(".py"):
            return importlib.import_module(module_spec), class_name

        adapter_path = Path(module_spec).expanduser()
        if not adapter_path.is_absolute():
            resolution_root = self._config_dir or Path.cwd()
            adapter_path = resolution_root / adapter_path
        adapter_path = adapter_path.resolve()
        if not adapter_path.is_file():
            raise FileNotFoundError(f"Solver adapter file not found: {adapter_path}")

        import_spec = importlib.util.spec_from_file_location(
            f"llm4ad_solver_adapter_{abs(hash(adapter_path))}", adapter_path
        )
        if import_spec is None or import_spec.loader is None:
            raise ImportError(f"Unable to load solver adapter: {adapter_path}")
        module = importlib.util.module_from_spec(import_spec)
        import_spec.loader.exec_module(module)
        return module, class_name

    def _create_adapter(self) -> BaseSolverAdapter:
        module, class_name = self._load_adapter_module()
        adapter_class = getattr(module, class_name)
        if not isinstance(adapter_class, type) or not issubclass(adapter_class, BaseSolverAdapter):
            raise TypeError(f"{self._config.adapter} must define a BaseSolverAdapter subclass")
        return adapter_class(self._config.adapter_config)

    @staticmethod
    def _failure(message: str, started_at: float, **metadata: Any) -> EvaluationResult:
        return EvaluationResult(
            score=0.0,
            metrics={},
            success=False,
            error_message=message,
            duration_ms=(time.monotonic() - started_at) * 1000,
            metadata=metadata,
        )

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Solve and independently validate one structured candidate artifact."""
        started_at = time.monotonic()
        try:
            project_root = Path(cfg.project_root).resolve()
            candidate_path = (project_root / self._config.candidate_file).resolve()
            if not candidate_path.is_relative_to(project_root):
                return self._failure("Candidate file must remain inside the project root", started_at)

            candidate = load_candidate(candidate_path, symbol=self._config.candidate_symbol)
            adapter = self._create_adapter()
            backend = create_backend(self._config.backend, self._config.solver_options)
            context = SolverContext(
                backend=backend,
                timeout=float(cfg.timeout),
                project_root=str(project_root),
                data_path=cfg.data_path,
                evaluation_profile=cfg.evaluation_profile,
            )
            run = await asyncio.to_thread(adapter.solve, candidate, context)
            if run.solution is None:
                return self._failure(
                    run.error_message or f"Solver returned no feasible solution ({run.status})",
                    started_at,
                    solver_status=run.status,
                    solver=run.metadata,
                )

            validation = await asyncio.to_thread(adapter.validate, candidate, run.solution, context)
            if not validation.valid:
                return self._failure(
                    validation.error_message or "Solver solution failed independent validation",
                    started_at,
                    solver_status=run.status,
                    solver=run.metadata,
                    validation=validation.metadata,
                )

            configured_names = {metric.name for metric in self._metrics}
            missing = configured_names.difference(validation.metrics)
            if missing:
                return self._failure(
                    f"Adapter did not return configured metrics: {', '.join(sorted(missing))}",
                    started_at,
                    solver_status=run.status,
                )
            if any(not math.isfinite(float(value)) for value in validation.metrics.values()):
                return self._failure(
                    "Adapter metrics must contain only finite numeric values",
                    started_at,
                    solver_status=run.status,
                )

            metrics = {name: float(value) for name, value in validation.metrics.items()}
            for name, value in run.metadata.items():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                numeric = float(value)
                if math.isfinite(numeric):
                    metrics.setdefault(f"solver_{name}", numeric)
            normalized_status = run.status.strip().lower()
            metrics.setdefault("solver_feasible", 1.0)
            metrics.setdefault("solver_optimal", 1.0 if normalized_status == "optimal" else 0.0)
            evolution_feedback = {
                "solver_status": run.status,
                "solver": run.metadata,
            }
            if run.candidate_patch:
                evolution_feedback["candidate_update"] = {
                    "candidate_file": self._config.candidate_file,
                    "candidate_symbol": self._config.candidate_symbol,
                    "patch": run.candidate_patch,
                }
            if validation.metadata:
                evolution_feedback["validation"] = validation.metadata
            return EvaluationResult(
                score=self.compute_score(metrics),
                metrics=metrics,
                monitor_metrics=metrics.copy(),
                success=True,
                duration_ms=(time.monotonic() - started_at) * 1000,
                metadata={
                    "solver_status": run.status,
                    "solver": run.metadata,
                    "validation": validation.metadata,
                    "candidate_file": self._config.candidate_file,
                },
                evolution_feedback=evolution_feedback,
            )
        except Exception as exc:
            return self._failure(f"Solver evaluation failed: {exc}", started_at)
