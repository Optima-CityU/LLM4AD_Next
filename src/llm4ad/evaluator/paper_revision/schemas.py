"""Data contracts for paper revision evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class RubricDimension(BaseModel):
    """One scored dimension shared by all paper judges."""

    name: str
    description: str
    weight: float = Field(default=1.0, gt=0)


def default_rubric() -> list[RubricDimension]:
    """Return the default academic-revision rubric."""
    return [
        RubricDimension(
            name="technical_fidelity",
            description="Preserves the original technical meaning, claims, and scope.",
            weight=0.25,
        ),
        RubricDimension(
            name="cspaper_resolution",
            description="Addresses the relevant CSPaper findings without blindly following them.",
            weight=0.20,
        ),
        RubricDimension(
            name="clarity_coherence",
            description="Improves clarity, logical flow, and fit with neighboring sections.",
            weight=0.20,
        ),
        RubricDimension(
            name="evidence_integrity",
            description="Preserves citations, numbers, evidence, and qualification of claims.",
            weight=0.20,
        ),
        RubricDimension(
            name="concision_style",
            description="Uses precise academic language without rewarding unnecessary length.",
            weight=0.15,
        ),
    ]


class CSPaperFinding(BaseModel):
    """A normalized CSPaper issue relevant to the selected section."""

    id: str = ""
    issue: str = Field(validation_alias=AliasChoices("issue", "description", "problem"))
    suggestion: str = ""
    severity: str = "medium"
    evidence: str = ""
    category: str = ""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RevisionConstraints(BaseModel):
    """Objective protections applied before subjective LLM review."""

    min_length_ratio: float = Field(default=0.5, gt=0)
    max_length_ratio: float = Field(default=2.0, gt=0)
    preserve_citations: bool = True
    allow_new_citations: bool = False
    preserve_numbers: bool = True
    preserve_latex_structure: bool = True
    locked_terms: list[str] = Field(default_factory=list)
    locked_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_length_range(self) -> RevisionConstraints:
        """Ensure the configured length interval is meaningful."""
        if self.min_length_ratio > self.max_length_ratio:
            raise ValueError("min_length_ratio cannot exceed max_length_ratio")
        return self


class PaperRevisionTask(BaseModel):
    """Normalized evaluator input produced by a parser or CSPaper adapter."""

    task_id: str
    document_id: str = ""
    section_id: str
    section_title: str = ""
    language: str = "en"
    original_text: str = Field(
        validation_alias=AliasChoices("original_text", "source_text", "baseline_text")
    )
    context_before: str = ""
    context_after: str = ""
    cspaper_findings: list[CSPaperFinding] = Field(default_factory=list)
    constraints: RevisionConstraints = Field(default_factory=RevisionConstraints)
    rubric: list[RubricDimension] = Field(default_factory=default_rubric)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("task_id", "section_id", "original_text")
    @classmethod
    def require_content(cls, value: str) -> str:
        """Reject identifiers and source text that contain only whitespace."""
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_rubric(self) -> PaperRevisionTask:
        """Require unique rubric names so judge output is unambiguous."""
        names = [dimension.name for dimension in self.rubric]
        if not names or len(names) != len(set(names)):
            raise ValueError("rubric dimensions must be non-empty and unique")
        return self


class CandidateRevision(BaseModel):
    """One proposed replacement for the selected paper section."""

    candidate_id: str = ""
    section_id: str = ""
    revised_text: str = Field(validation_alias=AliasChoices("revised_text", "text", "content"))
    parent_id: str | None = None
    generation: int = Field(default=0, ge=0)
    change_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("revised_text")
    @classmethod
    def require_revision(cls, value: str) -> str:
        """Reject an empty candidate before invoking any judges."""
        if not value.strip():
            raise ValueError("revised_text must not be blank")
        return value


class DimensionAssessment(BaseModel):
    """A judge's blinded A/B score for one rubric dimension."""

    dimension: str
    text_a_score: float = Field(ge=0, le=100)
    text_b_score: float = Field(ge=0, le=100)
    rationale: str


class PairwiseJudgeResponse(BaseModel):
    """Structured output requested from each independent panel judge."""

    assessments: list[DimensionAssessment]
    preferred: Literal["A", "B", "tie"]
    key_improvements: list[str] = Field(default_factory=list)
    key_regressions: list[str] = Field(default_factory=list)
    unresolved_cspaper_findings: list[str] = Field(default_factory=list)
    critical_issues: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class NormalizedDimensionAssessment(BaseModel):
    """A blinded assessment mapped back to baseline and revision."""

    dimension: str
    before_score: float = Field(ge=0, le=100)
    after_score: float = Field(ge=0, le=100)
    rationale: str


class JudgeReport(BaseModel):
    """A normalized report retained in ``EvaluationResult.metadata``."""

    provider: str
    candidate_id: str
    assessments: list[NormalizedDimensionAssessment]
    preferred: Literal["original", "revision", "tie"]
    key_improvements: list[str] = Field(default_factory=list)
    key_regressions: list[str] = Field(default_factory=list)
    unresolved_cspaper_findings: list[str] = Field(default_factory=list)
    critical_issues: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class DebateBallot(BaseModel):
    """Final ballot after judges inspect anonymous reviews and rebuttals."""

    candidate_scores: dict[str, float]
    ranking: list[str]
    rebuttals: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("candidate_scores")
    @classmethod
    def validate_scores(cls, value: dict[str, float]) -> dict[str, float]:
        """Keep all final ballot scores on the documented 0-100 scale."""
        if any(score < 0 or score > 100 for score in value.values()):
            raise ValueError("candidate scores must be between 0 and 100")
        return value


class JudgeSpec(BaseModel):
    """Reference to one top-level LLM provider used as a judge."""

    provider: str
    weight: float = Field(default=1.0, gt=0)


class PaperEvaluatorSettings(BaseModel):
    """Evaluator-specific settings read from custom evaluator YAML extras."""

    mode: Literal["panel", "debate"] = "panel"
    judges: list[JudgeSpec] = Field(default_factory=list)
    panel_size: int = Field(default=3, ge=1)
    min_judges: int = Field(default=2, ge=1)
    candidate_file: str = "candidate.json"
    random_seed: int = 42
    judge_temperature: float = Field(default=0.1, ge=0, le=2)
    judge_max_tokens: int = Field(default=4096, ge=256)
    max_concurrency: int = Field(default=8, ge=1)
    max_judge_retries: int = Field(default=1, ge=0)
    delta_weight: float = Field(default=0.2, ge=0)
    disagreement_weight: float = Field(default=0.1, ge=0)
    debate_rubric_weight: float = Field(default=0.60, ge=0)
    debate_pairwise_weight: float = Field(default=0.25, ge=0)
    debate_borda_weight: float = Field(default=0.15, ge=0)
    memory_min_delta: float = 3.0
    memory_max_disagreement: float = Field(default=12.0, ge=0)

    @field_validator("judges", mode="before")
    @classmethod
    def normalize_judges(cls, value: Any) -> Any:
        """Allow concise ``judges: [name_a, name_b]`` YAML syntax."""
        if value is None:
            return []
        return [{"provider": item} if isinstance(item, str) else item for item in value]

    @model_validator(mode="after")
    def validate_debate_weights(self) -> PaperEvaluatorSettings:
        """Require a non-zero election formula."""
        total = self.debate_rubric_weight + self.debate_pairwise_weight + self.debate_borda_weight
        if total <= 0:
            raise ValueError("at least one debate weight must be positive")
        return self


class StaticCheckResult(BaseModel):
    """Deterministic checks performed before LLM-based review."""

    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    penalty: float = Field(default=0.0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class MemoryCandidate(BaseModel):
    """Side-effect-free experience candidate emitted for the eventual winner."""

    kind: Literal["successful_pattern", "risk"]
    content: str
    candidate_id: str
    section_id: str
    score_delta: float
    recommended: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PanelAggregate(BaseModel):
    """Aggregated independent-panel outcome for one candidate."""

    score: float
    baseline_score: float
    revised_score: float
    score_delta: float
    disagreement: float
    agreement: float
    before_dimensions: dict[str, float]
    after_dimensions: dict[str, float]
    reports: list[JudgeReport]
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
