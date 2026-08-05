"""Score aggregation for paper revision panel and debate modes."""

from __future__ import annotations

import statistics
from collections import defaultdict

from llm4ad.evaluator.paper_revision.schemas import (
    CandidateRevision,
    DebateBallot,
    JudgeReport,
    MemoryCandidate,
    NormalizedDimensionAssessment,
    PairwiseJudgeResponse,
    PanelAggregate,
    PaperEvaluatorSettings,
    PaperRevisionTask,
    StaticCheckResult,
)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    """Clamp a score to the public evaluator scale."""
    return max(minimum, min(maximum, value))


def weighted_total(scores: dict[str, float], task: PaperRevisionTask) -> float:
    """Compute a rubric-weighted score while requiring every dimension."""
    total_weight = sum(dimension.weight for dimension in task.rubric)
    return (
        sum(scores[dimension.name] * dimension.weight for dimension in task.rubric) / total_weight
    )


def normalize_report(
    response: PairwiseJudgeResponse,
    *,
    provider: str,
    candidate_id: str,
    original_is_a: bool,
    task: PaperRevisionTask,
) -> JudgeReport:
    """Map an anonymous A/B response back to original and revision fields."""
    expected = [dimension.name for dimension in task.rubric]
    actual = [assessment.dimension for assessment in response.assessments]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError(
            "Judge response dimensions do not match rubric: "
            f"expected={expected}, actual={actual}"
        )

    normalized = [
        NormalizedDimensionAssessment(
            dimension=assessment.dimension,
            before_score=(assessment.text_a_score if original_is_a else assessment.text_b_score),
            after_score=(assessment.text_b_score if original_is_a else assessment.text_a_score),
            rationale=assessment.rationale,
        )
        for assessment in response.assessments
    ]
    if response.preferred == "tie":
        preferred = "tie"
    elif (response.preferred == "A") == original_is_a:
        preferred = "original"
    else:
        preferred = "revision"

    return JudgeReport(
        provider=provider,
        candidate_id=candidate_id,
        assessments=normalized,
        preferred=preferred,
        key_improvements=response.key_improvements,
        key_regressions=response.key_regressions,
        unresolved_cspaper_findings=response.unresolved_cspaper_findings,
        critical_issues=response.critical_issues,
        confidence=response.confidence,
    )


def aggregate_panel(
    reports: list[JudgeReport],
    *,
    task: PaperRevisionTask,
    candidate: CandidateRevision,
    settings: PaperEvaluatorSettings,
    static_check: StaticCheckResult,
) -> PanelAggregate:
    """Aggregate independent judge reports with robust medians."""
    before_dimensions: dict[str, float] = {}
    after_dimensions: dict[str, float] = {}
    for dimension in task.rubric:
        before_dimensions[dimension.name] = statistics.median(
            assessment.before_score
            for report in reports
            for assessment in report.assessments
            if assessment.dimension == dimension.name
        )
        after_dimensions[dimension.name] = statistics.median(
            assessment.after_score
            for report in reports
            for assessment in report.assessments
            if assessment.dimension == dimension.name
        )

    baseline_score = weighted_total(before_dimensions, task)
    revised_score = weighted_total(after_dimensions, task)
    score_delta = revised_score - baseline_score
    judge_totals = [
        weighted_total(
            {assessment.dimension: assessment.after_score for assessment in report.assessments},
            task,
        )
        for report in reports
    ]
    disagreement = statistics.pstdev(judge_totals) if len(judge_totals) > 1 else 0.0
    agreement = _clamp(100.0 - disagreement)
    delta_bonus = settings.delta_weight * _clamp(score_delta, -10.0, 10.0)
    score = _clamp(
        revised_score
        + delta_bonus
        - settings.disagreement_weight * disagreement
        - static_check.penalty
    )

    persist_recommended = (
        score_delta >= settings.memory_min_delta
        and disagreement <= settings.memory_max_disagreement
        and static_check.passed
    )
    memory_candidates: list[MemoryCandidate] = []
    seen: set[tuple[str, str]] = set()
    for report in reports:
        for kind, points in (
            ("successful_pattern", report.key_improvements),
            ("risk", report.key_regressions + report.critical_issues),
        ):
            for point in points:
                normalized = " ".join(point.lower().split())
                key = (kind, normalized)
                if not normalized or key in seen:
                    continue
                seen.add(key)
                memory_candidates.append(
                    MemoryCandidate(
                        kind=kind,
                        content=point.strip(),
                        candidate_id=candidate.candidate_id,
                        section_id=task.section_id,
                        score_delta=score_delta,
                        recommended=(persist_recommended and kind == "successful_pattern"),
                        metadata={
                            "source_provider": report.provider,
                            "confidence": report.confidence,
                        },
                    )
                )

    return PanelAggregate(
        score=score,
        baseline_score=baseline_score,
        revised_score=revised_score,
        score_delta=score_delta,
        disagreement=disagreement,
        agreement=agreement,
        before_dimensions=before_dimensions,
        after_dimensions=after_dimensions,
        reports=reports,
        memory_candidates=memory_candidates,
    )


def aggregate_debate(
    aggregates_by_label: dict[str, PanelAggregate],
    ballots: list[DebateBallot],
    settings: PaperEvaluatorSettings,
) -> dict[str, dict[str, float]]:
    """Combine rubric quality, pairwise wins, and Borda election points."""
    labels = list(aggregates_by_label)
    expected = set(labels)
    for ballot in ballots:
        if set(ballot.ranking) != expected or len(ballot.ranking) != len(expected):
            raise ValueError("Debate ballot ranking must contain every candidate once")
        if set(ballot.candidate_scores) != expected:
            raise ValueError("Debate ballot scores must contain every candidate")

    wins: dict[str, float] = defaultdict(float)
    comparisons: dict[str, float] = defaultdict(float)
    borda: dict[str, float] = defaultdict(float)
    ballot_scores: dict[str, list[float]] = defaultdict(list)
    max_borda = max(1, len(labels) - 1)

    for ballot in ballots:
        positions = {label: index for index, label in enumerate(ballot.ranking)}
        for label in labels:
            borda[label] += max_borda - positions[label]
            ballot_scores[label].append(ballot.candidate_scores[label])
            for other in labels:
                if other == label:
                    continue
                comparisons[label] += 1
                if positions[label] < positions[other]:
                    wins[label] += 1

    total_weight = (
        settings.debate_rubric_weight
        + settings.debate_pairwise_weight
        + settings.debate_borda_weight
    )
    outcomes: dict[str, dict[str, float]] = {}
    for label, aggregate in aggregates_by_label.items():
        pairwise_rate = wins[label] / comparisons[label] if comparisons[label] else 1.0
        borda_percentile = borda[label] / (len(ballots) * max_borda)
        final_score = (
            settings.debate_rubric_weight * aggregate.revised_score
            + settings.debate_pairwise_weight * pairwise_rate * 100.0
            + settings.debate_borda_weight * borda_percentile * 100.0
        ) / total_weight
        outcomes[label] = {
            "score": _clamp(final_score),
            "pairwise_win_rate": pairwise_rate * 100.0,
            "borda_percentile": borda_percentile * 100.0,
            "ballot_score": statistics.mean(ballot_scores[label]),
        }
    return outcomes
