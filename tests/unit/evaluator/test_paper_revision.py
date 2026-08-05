"""Tests for the paper revision panel and debate evaluator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm4ad.config.evaluator import CustomEvaluatorConfig, EvalContext
from llm4ad.evaluator.dispatcher import EvaluationDispatcher
from llm4ad.evaluator.paper_revision import PaperRevisionEvaluator
from llm4ad.evaluator.paper_revision.schemas import (
    CandidateRevision,
    DebateBallot,
    DimensionAssessment,
    PairwiseJudgeResponse,
    PaperRevisionTask,
)
from llm4ad.evaluator.paper_revision.validation import validate_revision
from llm4ad.infra.provider.base import GenerationResult


class _RuleBasedJudge:
    """Deterministic provider that scores marker words in anonymous text."""

    def __init__(self, offset: float = 0.0) -> None:
        self.offset = offset

    async def generate(self, prompt: str, schema=None, **_kwargs):
        """Return typed pairwise reports or final debate ballots."""
        if schema is PairwiseJudgeResponse:
            text_a = prompt.split("<<<TEXT_A\n", 1)[1].split("\nTEXT_A", 1)[0]
            text_b = prompt.split("<<<TEXT_B\n", 1)[1].split("\nTEXT_B", 1)[0]
            score_a = self._score(text_a)
            score_b = self._score(text_b)
            dimensions = [
                "technical_fidelity",
                "cspaper_resolution",
                "clarity_coherence",
                "evidence_integrity",
                "concision_style",
            ]
            parsed = PairwiseJudgeResponse(
                assessments=[
                    DimensionAssessment(
                        dimension=dimension,
                        text_a_score=score_a,
                        text_b_score=score_b,
                        rationale=f"{dimension} comparison",
                    )
                    for dimension in dimensions
                ],
                preferred="A" if score_a > score_b else "B" if score_b > score_a else "tie",
                key_improvements=["Make the causal explanation explicit."],
                key_regressions=[],
                confidence=0.9,
            )
            return GenerationResult(text="", parsed=parsed)

        if schema is DebateBallot:
            payload = prompt.split("ANONYMOUS CANDIDATES\n", 1)[1].split(
                "\n\nANONYMOUS INDEPENDENT REVIEWS", 1
            )[0]
            candidates = json.loads(payload)
            scores = {label: self._score(text) for label, text in candidates.items()}
            ranking = sorted(scores, key=lambda label: (scores[label], label), reverse=True)
            parsed = DebateBallot(
                candidate_scores=scores,
                ranking=ranking,
                rebuttals=["The preferred revision resolves the stated issue without new claims."],
                rationale="Ranked by fidelity and explanatory clarity.",
                confidence=0.85,
            )
            return GenerationResult(text="", parsed=parsed)

        raise AssertionError(f"Unexpected schema: {schema}")

    def _score(self, text: str) -> float:
        if "STRONG_REVISION" in text:
            return 88.0 + self.offset
        if "MEDIUM_REVISION" in text:
            return 72.0 + self.offset
        return 55.0 + self.offset


def _task() -> PaperRevisionTask:
    return PaperRevisionTask(
        task_id="paper-1-methods",
        document_id="paper-1",
        section_id="methods",
        section_title="Methods",
        original_text=(
            "Prior work reports 10% accuracy \\cite{smith}. "
            "The method is useful but its mechanism is not explained."
        ),
        context_before="This section introduces the method.",
        context_after="The next section reports experiments.",
        cspaper_findings=[
            {
                "id": "finding-1",
                "issue": "The mechanism is not explained.",
                "suggestion": "State the causal mechanism explicitly.",
            }
        ],
    )


def _revision(marker: str) -> str:
    return (
        "Prior work reports 10% accuracy \\cite{smith}. "
        f"{marker} The method is useful because its staged update links the input "
        "representation to the reported outcome."
    )


def _write_candidate(root: Path, candidate_id: str, text: str) -> EvalContext:
    root.mkdir()
    (root / "candidate.json").write_text(
        CandidateRevision(
            candidate_id=candidate_id,
            section_id="methods",
            revised_text=text,
        ).model_dump_json(),
        encoding="utf-8",
    )
    return EvalContext(project_root=str(root), data_path="")


def test_static_validation_rejects_changed_numbers_and_citations() -> None:
    """Objective evidence changes must fail before any LLM call."""
    task = _task()
    candidate = CandidateRevision(
        section_id="methods",
        revised_text="Prior work reports 20% accuracy \\cite{new}. The method is useful.",
    )

    result = validate_revision(task, candidate)

    assert result.passed is False
    assert any("Removed citation" in error for error in result.errors)
    assert any("Added unapproved citation" in error for error in result.errors)
    assert any("Numeric claims changed" in error for error in result.errors)


def test_dispatcher_injects_named_provider_pool() -> None:
    """Custom evaluator construction should reuse initialized top-level providers."""
    providers = {"judge_a": _RuleBasedJudge(), "judge_b": _RuleBasedJudge()}
    config = CustomEvaluatorConfig.model_validate(
        {
            "type": "custom",
            "provider": "judge_a",
            "module": "llm4ad.evaluator.paper_revision:PaperRevisionEvaluator",
            "judges": ["judge_a", "judge_b"],
            "panel_size": 2,
            "min_judges": 2,
        }
    )

    dispatcher = EvaluationDispatcher(config=config, providers=providers)
    evaluator = dispatcher._create_evaluator()

    assert isinstance(evaluator, PaperRevisionEvaluator)
    assert evaluator._providers == providers
    assert [judge.provider for judge in evaluator.settings.judges] == ["judge_a", "judge_b"]


@pytest.mark.asyncio
async def test_panel_scores_before_and_after_and_marks_one_winner(tmp_path: Path) -> None:
    """Panel mode should map blinded A/B scores and recommend only the winner."""
    task_path = tmp_path / "task.json"
    task_path.write_text(_task().model_dump_json(), encoding="utf-8")
    weak_cfg = _write_candidate(tmp_path / "weak", "weak", _revision("WEAK_REVISION"))
    strong_cfg = _write_candidate(tmp_path / "strong", "strong", _revision("STRONG_REVISION"))
    weak_cfg.data_path = str(task_path)
    strong_cfg.data_path = str(task_path)
    evaluator = PaperRevisionEvaluator(
        config={
            "mode": "panel",
            "judges": ["judge_a", "judge_b"],
            "panel_size": 2,
            "min_judges": 2,
            "memory_min_delta": 2,
        },
        providers={
            "judge_a": _RuleBasedJudge(),
            "judge_b": _RuleBasedJudge(offset=1),
        },
    )

    weak, strong = await evaluator.evaluate_batch([weak_cfg, strong_cfg])

    assert weak.success and strong.success
    assert strong.score > weak.score
    assert strong.metrics["baseline_score"] == pytest.approx(55.5)
    assert strong.metrics["revised_score"] == pytest.approx(88.5)
    assert strong.metrics["score_delta"] == pytest.approx(33.0)
    assert strong.metadata["selected_winner"] is True
    assert weak.metadata["selected_winner"] is False
    assert any(item["recommended"] for item in strong.metadata["memory_candidates"])
    assert not any(item["recommended"] for item in weak.metadata["memory_candidates"])


@pytest.mark.asyncio
async def test_debate_ranks_candidate_cohort(tmp_path: Path) -> None:
    """Debate mode should return aligned scores and elect the strongest candidate."""
    task_path = tmp_path / "task.json"
    task_path.write_text(_task().model_dump_json(), encoding="utf-8")
    cfgs = [
        _write_candidate(tmp_path / "weak", "weak", _revision("WEAK_REVISION")),
        _write_candidate(tmp_path / "medium", "medium", _revision("MEDIUM_REVISION")),
        _write_candidate(tmp_path / "strong", "strong", _revision("STRONG_REVISION")),
    ]
    for cfg in cfgs:
        cfg.data_path = str(task_path)
    evaluator = PaperRevisionEvaluator(
        config={
            "mode": "debate",
            "judges": ["judge_a", "judge_b"],
            "panel_size": 2,
            "min_judges": 2,
        },
        providers={
            "judge_a": _RuleBasedJudge(),
            "judge_b": _RuleBasedJudge(offset=1),
        },
    )

    results = await evaluator.evaluate_batch(cfgs)

    assert len(results) == 3
    assert all(result.success for result in results)
    assert all(result.metadata["mode"] == "debate" for result in results)
    assert results[2].score > results[1].score > results[0].score
    assert [result.metadata["selected_winner"] for result in results] == [False, False, True]
    assert results[2].metrics["pairwise_win_rate"] == pytest.approx(100.0)
