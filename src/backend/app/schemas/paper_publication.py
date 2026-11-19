"""Small, discoverable publication contracts for the two research assistants."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.schemas.paper import ProposalContextPatch, RebuttalEntry, RebuttalGlobalResponse


class _Artifact(BaseModel):
    """Reject misspelled fields instead of silently ignoring agent input."""

    model_config = ConfigDict(extra="forbid")


class ProposalRevisionArtifact(_Artifact):
    """Optional document metadata; changed files are discovered by the platform."""

    type: Literal["proposal"] = "proposal"
    entry_path: str | None = None
    context_patch: ProposalContextPatch = Field(default_factory=ProposalContextPatch)
    findings: list[str] = Field(default_factory=list)


class ReviewSource(_Artifact):
    """An original review linked to the platform's existing review panel."""

    review_id: uuid.UUID
    reviewer_id: str
    display_label: str
    title: str | None = None
    source_system: str = "manual"
    external_id: str | None = None
    source_url: str | None = None
    review_markdown: str | None = None


class ReviewConstraints(_Artifact):
    """Author-provided submission context, without inferred requirements."""

    venue: str | None = None
    venue_year: int | None = None
    response_mode: Literal["per_reviewer", "shared_global"] = "per_reviewer"
    output_format: Literal["markdown", "text"] = "markdown"
    author_notes: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)


class ReviewIntake(_Artifact):
    """Only the intake fields used by the right-side reader."""

    summary: str = ""
    paper_summary: str = ""
    constraints: ReviewConstraints = Field(default_factory=ReviewConstraints)
    reviewer_sources: list[ReviewSource] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ReviewConcern(_Artifact):
    """Display metadata for one concern; scientific policy belongs to the Skill."""

    id: str
    reviewer_id: str
    label: Literal["W", "Q", "M"]
    concern: str
    concern_type: str = ""
    severity: str = ""
    answer_source: str = ""
    draft_move: str = ""
    source_refs: list[str] = Field(default_factory=list)


class ReviewerCard(_Artifact):
    """Optional reviewer analysis rendered alongside the original report."""

    reviewer_id: str
    sentiment: str = ""
    movability: str = ""
    attitude: str = ""
    primary_concerns: list[str] = Field(default_factory=list)
    concern_ids: list[str] = Field(default_factory=list)


class ReviewAnalysis(_Artifact):
    """The UI's concern and reviewer index, without completion gates."""

    summary: str = ""
    concerns: list[ReviewConcern] = Field(default_factory=list)
    reviewer_cards: list[ReviewerCard] = Field(default_factory=list)


class BaselineRevisionArtifact(_Artifact):
    """A baseline may be saved independently of a rebuttal draft."""

    type: Literal["baseline"] = "baseline"
    intake: ReviewIntake = Field(default_factory=ReviewIntake)
    analysis: ReviewAnalysis = Field(default_factory=ReviewAnalysis)
    findings: list[str] = Field(default_factory=list)


class RebuttalRevisionArtifact(_Artifact):
    """One current response, without duplicate strategy/draft/compliance bodies."""

    type: Literal["rebuttal"] = "rebuttal"
    entries: list[RebuttalEntry] = Field(default_factory=list)
    global_response: RebuttalGlobalResponse | None = None
    rendered_text: str = Field(min_length=1)
    output_format: Literal["markdown", "text"] = "markdown"
    findings: list[str] = Field(default_factory=list)
    open_placeholders: list[str] = Field(default_factory=list)


class ChairRevisionArtifact(_Artifact):
    """An independently editable message to the AC or chair."""

    type: Literal["ac_summary"] = "ac_summary"
    message: str = Field(min_length=1)


ResearchRevisionArtifact = Annotated[
    ProposalRevisionArtifact | BaselineRevisionArtifact | RebuttalRevisionArtifact | ChairRevisionArtifact,
    Field(discriminator="type"),
]


class ResearchRevisionPublication(BaseModel):
    """Save a revision against both its document and generated-output base."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    base_revision: str = Field(min_length=1)
    artifact: ResearchRevisionArtifact | None = None


def publication_schemas() -> dict:
    """Expose the exact tool contract to the assistant without a second schema."""
    return ResearchRevisionPublication.model_json_schema()


def parse_artifact(value: dict) -> ResearchRevisionArtifact:
    """Parse a legacy adapter payload using the same disclosed artifact union."""
    return TypeAdapter(ResearchRevisionArtifact).validate_python(value)
