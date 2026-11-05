"""Shared wire contracts for whole-candidate remote evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from llm4ad.evaluator.base import EvaluationResult, Metric


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


class GatewayEvaluator:
    """Implement local evaluation; the SDK owns transport and persistence.

    Subclasses define ``contract`` and implement ``evaluate``. ``recover`` may
    reconnect an external scheduler using reporter.saved_state. Returning None
    leaves uncertain work waiting for explicit confirmation.
    """

    contract: EvaluationContract

    async def evaluate(self, job: RemoteJob, reporter: Any) -> EvaluationResult:
        """Run the user's local evaluation and return its aggregated result."""
        raise NotImplementedError

    async def recover(self, job: RemoteJob, reporter: Any) -> EvaluationResult | None:
        """Recover external work without blindly submitting a duplicate."""
        return None

    async def cancel(self, job: RemoteJob, saved_state: dict) -> bool:
        """Confirm external cancellation; False means it remains uncertain."""
        return False


def load_evaluator(spec: str) -> GatewayEvaluator:
    """Load a user evaluator from a local file and class name."""
    import importlib.util
    import sys

    filename, name = spec.rsplit(":", 1)
    path = Path(filename).expanduser().resolve()
    module_spec = importlib.util.spec_from_file_location("llm4ad_gateway_adapter", path)
    if module_spec is None or module_spec.loader is None:
        raise ValueError("Evaluator must be a Python file followed by :ClassName")
    module = importlib.util.module_from_spec(module_spec)
    sys.path.insert(0, str(path.parent))
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    adapter = getattr(module, name)()
    adapter.contract = EvaluationContract.model_validate(adapter.contract)
    return adapter
