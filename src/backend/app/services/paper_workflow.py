"""Backend-owned workflow definitions for research workspaces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

ResearchWorkspaceMode = Literal["proposal", "manuscript", "algorithm"]
ResearchWorkflowStage = Literal[
    "formatting",
    "literature",
    "rationale",
    "objectives",
    "methods",
    "innovation_plan",
    "foundation_feasibility",
    "final_review",
    "reviews",
    "boundary",
    "targets",
    "branch",
]


@dataclass(frozen=True, slots=True)
class ResearchWorkflowDefinition:
    """Describe one extensible research workflow.

    Attributes:
        mode: Stable workspace mode identifier.
        available: Whether the workflow can currently start agent runs.
        stages: Ordered frontend stages supplied by the backend.
        skills_by_run_kind: Skills enabled for each supported agent run.
        run_kind_by_stage: Agent run assigned to each frontend stage.
        prerequisites_by_run_kind: Required completed stages for each run.
        writable_paths_by_run_kind: Source paths owned by each run. A trailing
            slash denotes an allowed directory prefix.
        prompt_preamble: Trusted mode context appended by the backend.
    """

    mode: ResearchWorkspaceMode
    available: bool
    stages: tuple[ResearchWorkflowStage, ...]
    skills_by_run_kind: Mapping[str, tuple[str, ...]]
    run_kind_by_stage: Mapping[ResearchWorkflowStage, str]
    prerequisites_by_run_kind: Mapping[str, tuple[ResearchWorkflowStage, ...]]
    writable_paths_by_run_kind: Mapping[str, tuple[str, ...]]
    prompt_preamble: str

    @property
    def run_kinds(self) -> tuple[str, ...]:
        """Return supported run kinds in declaration order."""
        return tuple(self.skills_by_run_kind)

    def stage_for_run_kind(self, run_kind: str) -> ResearchWorkflowStage:
        """Return the frontend stage assigned to a run kind."""
        for stage, candidate in self.run_kind_by_stage.items():
            if candidate == run_kind:
                return stage
        raise ValueError(f"Run kind is not mapped to a workflow stage: {run_kind}")

    def dependent_stages(
        self,
        stage: ResearchWorkflowStage,
        *,
        include_self: bool = False,
    ) -> tuple[ResearchWorkflowStage, ...]:
        """Return transitive workflow dependents in display order.

        Args:
            stage: Stage whose downstream dependencies should be resolved.
            include_self: Whether to include the supplied stage in the result.

        Returns:
            Stages invalidated when the supplied stage changes.
        """
        affected: set[ResearchWorkflowStage] = {stage}
        changed = True
        while changed:
            changed = False
            for candidate in self.stages:
                if candidate in affected:
                    continue
                run_kind = self.run_kind_by_stage[candidate]
                prerequisites = self.prerequisites_by_run_kind.get(run_kind, ())
                if any(prerequisite in affected for prerequisite in prerequisites):
                    affected.add(candidate)
                    changed = True
        return tuple(
            candidate for candidate in self.stages if candidate in affected and (include_self or candidate != stage)
        )


_WORKFLOWS: Mapping[ResearchWorkspaceMode, ResearchWorkflowDefinition] = MappingProxyType(
    {
        "proposal": ResearchWorkflowDefinition(
            mode="proposal",
            available=True,
            stages=(
                "formatting",
                "literature",
                "rationale",
                "objectives",
                "methods",
                "innovation_plan",
                "foundation_feasibility",
                "final_review",
            ),
            skills_by_run_kind=MappingProxyType(
                {
                    # Two skills, two concerns. `typst-author` (vendored, see its
                    # ATTRIBUTION.md) owns Typst syntax and ships a local copy of
                    # the official docs so the model reads the language instead of
                    # recalling it — the previous single skill named no list
                    # functions and a stage emitted `#numbered-list`, which does
                    # not exist. This one owns the proposal-specific part: the
                    # durable foundation and the write boundary for the stage.
                    "proposal_formatting": ("proposal-foundation-layout", "typst-author"),
                    "proposal_literature": ("proposal-literature-evidence",),
                    "proposal_rationale": ("proposal-rationale",),
                    "proposal_objectives": ("proposal-objectives",),
                    "proposal_methods": ("proposal-methods",),
                    "proposal_innovation_plan": ("proposal-innovation-plan",),
                    "proposal_foundation_feasibility": ("proposal-foundation-feasibility",),
                    "proposal_final_review": ("proposal-final-review",),
                }
            ),
            run_kind_by_stage=MappingProxyType(
                {
                    "formatting": "proposal_formatting",
                    "literature": "proposal_literature",
                    "rationale": "proposal_rationale",
                    "objectives": "proposal_objectives",
                    "methods": "proposal_methods",
                    "innovation_plan": "proposal_innovation_plan",
                    "foundation_feasibility": "proposal_foundation_feasibility",
                    "final_review": "proposal_final_review",
                }
            ),
            prerequisites_by_run_kind=MappingProxyType(
                {
                    "proposal_formatting": (),
                    "proposal_literature": ("formatting",),
                    "proposal_rationale": ("literature",),
                    "proposal_objectives": ("rationale",),
                    "proposal_methods": ("objectives",),
                    "proposal_innovation_plan": ("methods",),
                    "proposal_foundation_feasibility": ("methods",),
                    "proposal_final_review": ("innovation_plan", "foundation_feasibility"),
                }
            ),
            writable_paths_by_run_kind=MappingProxyType(
                {
                    "proposal_formatting": ("@entry", "styles/", "assets/"),
                    "proposal_literature": ("sections/02-literature.typ", "references.bib"),
                    "proposal_rationale": ("sections/01-rationale.typ", "sections/03-value.typ"),
                    "proposal_objectives": ("sections/04-objectives.typ",),
                    "proposal_methods": ("sections/05-methods.typ",),
                    "proposal_innovation_plan": ("sections/07-plan.typ",),
                    "proposal_foundation_feasibility": ("sections/06-feasibility.typ",),
                    "proposal_final_review": ("@entry",),
                }
            ),
            prompt_preamble=(
                "Work on a research proposal as one stage in a backend-governed workflow. Treat the persisted project "
                "foundation as authoritative when it is used. Project documents are optional context whose roles are "
                "described by the runtime; the agent decides whether and when to inspect them. Edit only the source "
                "paths explicitly assigned to the current stage. Preserve claims, evidence, citations, equations, and "
                "author intent. After every edit to .typ files, run the check_typst tool and fix all reported errors "
                "before calling publish_stage_result — publication is rejected while the document does not compile."
            ),
        ),
        "manuscript": ResearchWorkflowDefinition(
            mode="manuscript",
            available=False,
            stages=(),
            skills_by_run_kind=MappingProxyType({}),
            run_kind_by_stage=MappingProxyType({}),
            prerequisites_by_run_kind=MappingProxyType({}),
            writable_paths_by_run_kind=MappingProxyType({}),
            prompt_preamble="",
        ),
        "algorithm": ResearchWorkflowDefinition(
            mode="algorithm",
            available=False,
            stages=(),
            skills_by_run_kind=MappingProxyType({}),
            run_kind_by_stage=MappingProxyType({}),
            prerequisites_by_run_kind=MappingProxyType({}),
            writable_paths_by_run_kind=MappingProxyType({}),
            prompt_preamble="",
        ),
    }
)


def get_research_workflow(mode: str) -> ResearchWorkflowDefinition:
    """Return the registered workflow for a persisted workspace mode.

    Args:
        mode: Persisted research workspace mode.

    Returns:
        The immutable backend workflow definition.

    Raises:
        ValueError: If the persisted mode has no registered workflow.
    """
    definition = _WORKFLOWS.get(cast(ResearchWorkspaceMode, mode))
    if definition is None:
        raise ValueError(f"Unsupported research workspace mode: {mode}")
    return definition
