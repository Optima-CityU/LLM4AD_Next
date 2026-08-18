from __future__ import annotations

from pathlib import Path

from llm4ad.integrations.cspaper.compiler import SuggestionCompiler
from llm4ad.integrations.cspaper.schemas import validate_design_spec

REVIEW = """---
paper: Adaptive Search
problem_name: adaptive_search
problem_description: Minimize route cost for feasible vehicle routes.
function_name: destroy_operator
---

## Algorithm
<!-- cspaper: category=search_direction; name=adaptive_destroy; priority=high -->
- Replace fixed operator selection with adaptive neighborhood selection.

## Metrics
<!-- cspaper: category=objective; name=solution_cost; direction=minimize; measurement=mean route cost -->
- Minimize solution cost over validation instances.
<!-- cspaper: category=objective; name=runtime_ms; direction=minimize; measurement=median wall-clock milliseconds -->
- Report runtime over repeated runs.

## Constraints
<!-- cspaper: category=constraint; name=visit_once; check=each customer occurs exactly once -->
- Every customer must be visited exactly once.

## Experiments
<!-- cspaper: category=dataset -->
- Add large-scale and out-of-distribution instances.
<!-- cspaper: category=baseline; name=random_removal -->
- Compare with random removal.

## Writing
- Expand the related work section.
"""


def test_compiles_annotated_review_with_traceability(tmp_path: Path) -> None:
    """Annotations compile to typed, source-linked requirements."""
    review = tmp_path / "review.md"
    review.write_text(REVIEW, encoding="utf-8")

    spec = SuggestionCompiler().compile_file(review)

    assert spec.problem.name == "adaptive_search"
    assert [item.name for item in spec.objectives] == ["solution_cost", "runtime_ms"]
    assert spec.objectives[0].source_suggestion_id == "suggestion-2"
    assert spec.search_directions[0].priority == "high"
    assert spec.constraints[0].name == "visit_once"
    assert len(spec.datasets.requirements) == 1
    assert spec.baselines[0].name == "random_removal"
    assert len(spec.excluded_suggestions) == 1
    assert not spec.pending_suggestions
    assert len(spec.paper.review_sha256) == 64


def test_ambiguous_objective_stays_pending() -> None:
    """An unmeasurable objective is never guessed into a fitness metric."""
    review = """## Review
<!-- cspaper: category=objective -->
- Improve the method substantially.
"""

    spec = SuggestionCompiler().compile_text(review)

    assert not spec.objectives
    assert len(spec.pending_suggestions) == 1
    report = validate_design_spec(spec)
    assert not report.valid
    assert "at least one measurable objective is required" in report.errors


def test_confirmation_enables_strict_validation() -> None:
    """Strict mode requires and accepts an explicit confirmation record."""
    spec = SuggestionCompiler().compile_text(REVIEW)
    assert not validate_design_spec(spec, strict=True).valid

    spec.confirm("team member", "Checked metrics and constraints.")

    report = validate_design_spec(spec, strict=True)
    assert report.valid
    assert spec.confirmation.confirmed_by == "team member"
