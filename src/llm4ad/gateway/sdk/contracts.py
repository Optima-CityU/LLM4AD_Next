"""Shared wire contracts for whole-candidate remote evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from llm4ad.evaluator.models import EvaluationResult, Metric

if TYPE_CHECKING:
    from .journal import Reporter


class EvaluationContract(BaseModel):
    """Declare comparable evaluator and local-data versions."""

    evaluator_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    metrics: list[Metric] = Field(min_length=1)


class RemoteJob(BaseModel):
    """Describe one candidate evaluated entirely by a local adapter."""

    id: str
    attempt_id: str
    task_id: str
    project_root: str = ""
    timeout: float = Field(gt=0)
    evaluation_profile: Literal["standard", "elite"] = "standard"
    behavior_storage: Literal["rendered", "raw", "none"] = "none"
    parameters: dict[str, Any] = Field(default_factory=dict)

    @property
    def artifact_dir(self) -> str:
        """Return the SDK-owned directory for explicitly declared file outputs."""
        return str(Path(self.project_root) / ".evaluation-artifacts")


class GatewayEvaluator:
    """Implement local evaluation; the SDK owns transport and persistence.

    Ordinary subclasses define ``contract`` and implement only ``evaluate``.
    The SDK owns scheduling, local-worker recovery and cancellation. Optional
    before/after hooks extend operations without owning task state transitions.
    External backends may override recovery/cancellation integration.
    """

    contract: EvaluationContract
    manages_evidence_retention: bool = False

    @classmethod
    def create_runtime(cls, environment: dict, evaluator: str):
        """Optionally supply local backend hooks; normal evaluators require none."""
        return None

    async def evaluate(self, job: RemoteJob, reporter: Reporter) -> EvaluationResult | None:
        """Run the user's local evaluation and return its aggregated result."""
        raise NotImplementedError

    async def before_task(self, job: RemoteJob, reporter: Reporter, operation: str) -> None:
        """Optionally prepare resources before SDK-selected evaluate/recover/cancel."""

    async def after_task(
        self, job: RemoteJob, reporter: Reporter, operation: str,
        *, result: EvaluationResult | bool | None = None, error: BaseException | None = None,
    ) -> None:
        """Optionally finalize an operation, including preparation/evaluation errors.

        Forced worker termination is finalized through the SDK's cancel operation
        in the gateway process; worker-local finally blocks cannot survive SIGKILL.
        """

    async def recover(self, job: RemoteJob, reporter: Reporter) -> EvaluationResult | None:
        """Replay stopped local work; external handles require backend-specific recovery."""
        if reporter.saved_state:
            return None
        return await self.evaluate(job, reporter)

    async def cancel(self, job: RemoteJob, saved_state: dict) -> bool:
        """Confirm SDK-local cancellation after worker termination; guard external work."""
        return not saved_state


def normalize_spec(spec: str) -> str:
    """Resolve local file specifications while preserving installed module names."""
    filename, name = spec.rsplit(":", 1)
    if not filename or not name:
        raise ValueError("Evaluator must be FILE.py:Class or package.module:Class")
    if filename.endswith(".py") or "/" in filename or "\\" in filename:
        return f"{Path(filename).expanduser().resolve()}:{name}"
    return spec


def load_evaluator_class(spec: str):
    """Load an evaluator class without constructing device-dependent instances."""
    import hashlib
    import importlib
    import importlib.util
    import sys

    filename, name = normalize_spec(spec).rsplit(":", 1)
    if not filename.endswith(".py"):
        module = importlib.import_module(filename)
    else:
        path = Path(filename)
        module_name = "llm4ad_gateway_adapter_" + hashlib.sha256(str(path).encode()).hexdigest()
        module_spec = importlib.util.spec_from_file_location(module_name, path)
        if module_spec is None or module_spec.loader is None:
            raise ValueError("Evaluator must be a Python file followed by :ClassName")
        module = importlib.util.module_from_spec(module_spec)
        sys.path.insert(0, str(path.parent))
        sys.modules[module_spec.name] = module
        module_spec.loader.exec_module(module)
    return getattr(module, name)


def load_evaluator(spec: str) -> GatewayEvaluator:
    """Construct a custom evaluator and validate its comparison contract."""
    adapter = load_evaluator_class(spec)()
    adapter.contract = EvaluationContract.model_validate(adapter.contract)
    return adapter
