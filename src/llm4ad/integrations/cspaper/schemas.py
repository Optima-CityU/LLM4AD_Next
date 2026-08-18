"""Typed contracts between CSPaper reviews and LLM4AD algorithm evolution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PaperReference(BaseModel):
    """Source paper and CSPaper provenance."""

    title: str = ""
    source_path: str = ""
    source_url: str = ""
    cspaper_job_id: str = ""
    review_agent_id: str = ""
    review_path: str = ""
    review_sha256: str = ""


class ProblemDefinition(BaseModel):
    """Executable algorithm problem inferred from the paper and review."""

    name: str = "paper_algorithm"
    type: str = "algorithm_optimization"
    description: str = ""
    function_name: str = ""
    input_format: str = ""
    output_format: str = ""


class CandidateScope(BaseModel):
    """Code boundary that LLM4AD may evolve."""

    code_path: str = ""
    function_name: str = ""
    allowed_files: list[str] = Field(default_factory=list)
    notes: str = ""


class SuggestionEvidence(BaseModel):
    """One traceable review statement before semantic compilation."""

    id: str
    text: str
    category: str
    heading: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchDirection(BaseModel):
    """A design direction injected into planner and coder prompts."""

    id: str
    description: str
    priority: Literal["low", "medium", "high"] = "medium"
    rationale: str = ""
    source_suggestion_id: str


class ObjectiveSpec(BaseModel):
    """A metric that the local evaluator must calculate."""

    name: str
    direction: Literal["minimize", "maximize"]
    weight: float = Field(default=1.0, ge=0)
    measurement: str
    aggregation: str = "mean"
    unit: str = ""
    source_suggestion_id: str


class ConstraintSpec(BaseModel):
    """A hard or soft condition checked by the local evaluator."""

    name: str
    type: Literal["hard", "soft"] = "hard"
    check: str
    penalty: float | None = Field(default=None, ge=0)
    source_suggestion_id: str


class DatasetRequirement(BaseModel):
    """A dataset coverage requirement derived from review feedback."""

    description: str
    source_suggestion_id: str


class DatasetSpec(BaseModel):
    """Dataset paths and requested coverage."""

    train: str = ""
    validation: str = ""
    test: str = ""
    hidden_test: str = ""
    requirements: list[DatasetRequirement] = Field(default_factory=list)


class BaselineSpec(BaseModel):
    """A comparison baseline requested by the review."""

    name: str
    required: bool = True
    command: str = ""
    source_suggestion_id: str


class EvaluationBudget(BaseModel):
    """Resource limits applied to candidate evaluation."""

    timeout_seconds: float = Field(default=60.0, gt=0)
    max_memory_mb: int | None = Field(default=None, gt=0)
    repetitions: int = Field(default=1, ge=1)
    random_seeds: list[int] = Field(default_factory=lambda: [42])


class ExcludedSuggestion(BaseModel):
    """Review feedback intentionally excluded from algorithm evolution."""

    text: str
    reason: str
    source_suggestion_id: str


class PendingSuggestion(BaseModel):
    """Ambiguous feedback requiring review before automatic evolution."""

    text: str
    reason: str
    source_suggestion_id: str


class ConfirmationRecord(BaseModel):
    """Human confirmation of the compiled design specification."""

    status: Literal["pending", "confirmed"] = "pending"
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    notes: str = ""


class AlgorithmDesignSpec(BaseModel):
    """Stable handoff from CSPaper review to executable algorithm evolution."""

    schema_version: str = "1.0"
    paper: PaperReference = Field(default_factory=PaperReference)
    problem: ProblemDefinition = Field(default_factory=ProblemDefinition)
    candidate_scope: CandidateScope = Field(default_factory=CandidateScope)
    search_directions: list[SearchDirection] = Field(default_factory=list)
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    datasets: DatasetSpec = Field(default_factory=DatasetSpec)
    baselines: list[BaselineSpec] = Field(default_factory=list)
    evaluation_budget: EvaluationBudget = Field(default_factory=EvaluationBudget)
    excluded_suggestions: list[ExcludedSuggestion] = Field(default_factory=list)
    pending_suggestions: list[PendingSuggestion] = Field(default_factory=list)
    evidence: list[SuggestionEvidence] = Field(default_factory=list)
    confirmation: ConfirmationRecord = Field(default_factory=ConfirmationRecord)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: str) -> str:
        """Reject unknown major schema versions."""
        if value.split(".", 1)[0] != "1":
            raise ValueError(f"Unsupported AlgorithmDesignSpec version: {value}")
        return value

    @model_validator(mode="after")
    def unique_names_and_ids(self) -> AlgorithmDesignSpec:
        """Keep references and evaluator metric names unambiguous."""
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        objective_names = [item.name for item in self.objectives]
        if len(objective_names) != len(set(objective_names)):
            raise ValueError("objective names must be unique")
        constraint_names = [item.name for item in self.constraints]
        if len(constraint_names) != len(set(constraint_names)):
            raise ValueError("constraint names must be unique")
        return self

    @classmethod
    def load(cls, path: str | Path) -> AlgorithmDesignSpec:
        """Load a design spec from UTF-8 JSON."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> Path:
        """Write this design spec as stable, human-readable JSON."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output

    def confirm(self, confirmed_by: str, notes: str = "") -> None:
        """Record an explicit human confirmation in-place."""
        if not confirmed_by.strip():
            raise ValueError("confirmed_by must not be blank")
        self.confirmation = ConfirmationRecord(
            status="confirmed",
            confirmed_by=confirmed_by.strip(),
            confirmed_at=datetime.now(UTC).isoformat(),
            notes=notes.strip(),
        )


class SpecValidationReport(BaseModel):
    """Machine-readable audit of whether a spec can drive evolution."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_design_spec(
    spec: AlgorithmDesignSpec,
    *,
    strict: bool = False,
    check_paths: bool = False,
    base_dir: str | Path | None = None,
) -> SpecValidationReport:
    """Validate semantic requirements that Pydantic alone cannot express."""
    errors: list[str] = []
    warnings: list[str] = []

    if not spec.problem.description.strip():
        errors.append("problem.description is required")
    if not spec.objectives:
        errors.append("at least one measurable objective is required")
    for objective in spec.objectives:
        if not objective.measurement.strip():
            errors.append(f"objective '{objective.name}' has no measurement definition")
    if not spec.search_directions:
        warnings.append("no algorithm search directions were extracted")
    if spec.pending_suggestions:
        warnings.append(
            f"{len(spec.pending_suggestions)} suggestion(s) still require classification"
        )
    if spec.confirmation.status != "confirmed":
        warnings.append("AlgorithmDesignSpec has not been human-confirmed")
    if not any(
        [
            spec.datasets.train,
            spec.datasets.validation,
            spec.datasets.test,
            spec.datasets.hidden_test,
        ]
    ):
        warnings.append("no dataset path is recorded; the task evaluator must provide its own data")

    if check_paths:
        root = Path(base_dir or ".").expanduser().resolve()
        paths = {
            "paper.source_path": spec.paper.source_path,
            "candidate_scope.code_path": spec.candidate_scope.code_path,
            "datasets.train": spec.datasets.train,
            "datasets.validation": spec.datasets.validation,
            "datasets.test": spec.datasets.test,
            "datasets.hidden_test": spec.datasets.hidden_test,
        }
        for label, raw_path in paths.items():
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = root / path
            if not path.exists():
                errors.append(f"{label} does not exist: {path}")

    if strict:
        if spec.pending_suggestions:
            errors.append("pending suggestions must be resolved before strict validation")
        if spec.confirmation.status != "confirmed":
            errors.append("human confirmation is required for strict validation")

    return SpecValidationReport(valid=not errors, errors=errors, warnings=warnings)
