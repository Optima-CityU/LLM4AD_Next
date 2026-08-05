"""Multi-LLM evaluator for selected paper-section revisions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel

from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.base import (
    BaseBatchEvaluator,
    EvaluationResult,
    Metric,
    MetricType,
)
from llm4ad.evaluator.paper_revision.aggregation import (
    aggregate_debate,
    aggregate_panel,
    normalize_report,
)
from llm4ad.evaluator.paper_revision.prompts import (
    build_debate_prompt,
    build_pairwise_prompt,
)
from llm4ad.evaluator.paper_revision.schemas import (
    CandidateRevision,
    DebateBallot,
    JudgeReport,
    JudgeSpec,
    PairwiseJudgeResponse,
    PanelAggregate,
    PaperEvaluatorSettings,
    PaperRevisionTask,
    StaticCheckResult,
)
from llm4ad.evaluator.paper_revision.validation import validate_revision

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass
class _PreparedCandidate:
    """Internal candidate bundle kept aligned with dispatcher order."""

    index: int
    cfg: EvalContext
    task: PaperRevisionTask
    candidate: CandidateRevision
    static_check: StaticCheckResult


class PaperRevisionEvaluator(BaseBatchEvaluator):
    """Evaluate paper revisions with an independent panel or a debate vote.

    The evaluator consumes normalized JSON tasks from ``EvalContext.data_path``
    and one candidate file from each worktree. It deliberately does not parse
    PDFs, generate revisions, replace source text, or persist memory.
    """

    def __init__(
        self,
        config: Any = None,
        provider_config: Any = None,
        providers: dict[str, Any] | None = None,
    ) -> None:
        """Initialize evaluator settings and the shared provider pool."""
        raw = self._config_dict(config)
        self.settings = PaperEvaluatorSettings.model_validate(raw)
        self._providers = providers or {}

        default_provider = raw.get("provider") or getattr(provider_config, "name", "")
        if not self.settings.judges and default_provider:
            self.settings = self.settings.model_copy(
                update={"judges": [JudgeSpec(provider=str(default_provider))]}
            )
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrency)

    @staticmethod
    def _config_dict(config: Any) -> dict[str, Any]:
        """Convert typed custom config or a plain mapping into settings data."""
        if config is None:
            return {}
        if isinstance(config, dict):
            return dict(config)
        if hasattr(config, "model_dump"):
            return config.model_dump()
        raise TypeError(f"Unsupported evaluator config: {type(config)!r}")

    @property
    def name(self) -> str:
        """Return the evaluator registry name."""
        return "paper_revision"

    @property
    def metrics(self) -> list[Metric]:
        """Return stable cross-task metrics; rubric details live in metadata."""
        return [
            Metric(name="baseline_score", type=MetricType.MAXIMIZE),
            Metric(name="revised_score", type=MetricType.MAXIMIZE),
            Metric(name="score_delta", type=MetricType.MAXIMIZE),
            Metric(name="judge_agreement", type=MetricType.MAXIMIZE),
            Metric(name="static_valid", type=MetricType.MAXIMIZE),
        ]

    async def evaluate_batch(self, cfgs: list[EvalContext]) -> list[EvaluationResult]:
        """Evaluate an aligned candidate cohort in panel or debate mode."""
        started = time.time()
        if not cfgs:
            return []

        try:
            prepared = self._prepare_candidates(cfgs)
            judges = self._select_judges(prepared[0].task)
            if self.settings.mode == "debate":
                results = await self._evaluate_debate(prepared, judges)
            else:
                results = await self._evaluate_panel(prepared, judges)
            duration_ms = (time.time() - started) * 1000
            for result in results:
                result.duration_ms = duration_ms
            return results
        except Exception as exc:
            logger.exception("Paper revision evaluation failed: {}", exc)
            duration_ms = (time.time() - started) * 1000
            return [self._failure(str(exc), duration_ms=duration_ms) for _ in cfgs]

    def _prepare_candidates(self, cfgs: list[EvalContext]) -> list[_PreparedCandidate]:
        """Load one immutable task and one revision from each worktree."""
        task_paths = {str(Path(cfg.data_path).resolve()) for cfg in cfgs if cfg.data_path}
        if len(task_paths) != 1:
            raise ValueError("A paper evaluation batch must share exactly one data_path")
        task_path = Path(next(iter(task_paths)))
        task = PaperRevisionTask.model_validate_json(task_path.read_text(encoding="utf-8"))

        prepared: list[_PreparedCandidate] = []
        used_ids: set[str] = set()
        for index, cfg in enumerate(cfgs):
            candidate = self._load_candidate(cfg, task)
            candidate_id = candidate.candidate_id or cfg.candidate_id
            if not candidate_id:
                candidate_id = hashlib.sha256(candidate.revised_text.encode("utf-8")).hexdigest()[
                    :12
                ]
            if candidate_id in used_ids:
                candidate_id = f"{candidate_id}-{index + 1}"
            used_ids.add(candidate_id)
            candidate = candidate.model_copy(
                update={
                    "candidate_id": candidate_id,
                    "section_id": candidate.section_id or task.section_id,
                    "generation": candidate.generation or cfg.generation,
                    "parent_id": candidate.parent_id
                    or (cfg.parent_ids[0] if cfg.parent_ids else None),
                }
            )
            prepared.append(
                _PreparedCandidate(
                    index=index,
                    cfg=cfg,
                    task=task,
                    candidate=candidate,
                    static_check=validate_revision(task, candidate),
                )
            )
        return prepared

    def _load_candidate(
        self,
        cfg: EvalContext,
        task: PaperRevisionTask,
    ) -> CandidateRevision:
        """Load a normalized JSON candidate or a plain text/TeX fragment."""
        root = Path(cfg.project_root).resolve()
        configured = Path(self.settings.candidate_file)
        if configured.is_absolute():
            raise ValueError("candidate_file must be relative to project_root")
        path = (root / configured).resolve()
        if root not in path.parents and path != root:
            raise ValueError("candidate_file resolves outside project_root")
        if not path.is_file():
            raise FileNotFoundError(f"Candidate file not found: {path}")
        content = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return CandidateRevision.model_validate_json(content)
        return CandidateRevision(
            candidate_id=cfg.candidate_id,
            section_id=task.section_id,
            revised_text=content,
            generation=cfg.generation,
            parent_id=cfg.parent_ids[0] if cfg.parent_ids else None,
        )

    def _select_judges(self, task: PaperRevisionTask) -> list[JudgeSpec]:
        """Choose a reproducible, balanced panel for the whole cohort."""
        if not self.settings.judges:
            raise ValueError("No judges configured for PaperRevisionEvaluator")
        unique: dict[str, JudgeSpec] = {}
        for judge in self.settings.judges:
            unique.setdefault(judge.provider, judge)
        judges = list(unique.values())
        seed_material = f"{self.settings.random_seed}:{task.task_id}"
        seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
        random.Random(seed).shuffle(judges)
        selected = judges[: min(self.settings.panel_size, len(judges))]
        if len(selected) < self.settings.min_judges:
            raise ValueError(
                f"Judge quorum requires {self.settings.min_judges}, "
                f"but only {len(selected)} are configured"
            )
        missing = [judge.provider for judge in selected if judge.provider not in self._providers]
        if missing:
            raise ValueError(f"Configured judge providers are unavailable: {missing}")
        return selected

    async def _call_provider(
        self,
        provider_name: str,
        prompt: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        """Call one provider with structured-output parsing and bounded retries."""
        provider = self._providers[provider_name]
        last_error: Exception | None = None
        for _attempt in range(self.settings.max_judge_retries + 1):
            try:
                async with self._semaphore:
                    response = await provider.generate(
                        prompt,
                        schema=schema,
                        temperature=self.settings.judge_temperature,
                        max_tokens=self.settings.judge_max_tokens,
                        request_stage="evaluator",
                    )
                parsed = getattr(response, "parsed", None)
                if isinstance(parsed, schema):
                    return parsed
                if isinstance(parsed, BaseModel):
                    return schema.model_validate(parsed.model_dump())
                if isinstance(parsed, dict):
                    return schema.model_validate(parsed)
                text = getattr(response, "text", "")
                return schema.model_validate(self._extract_json(text))
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract one JSON object from providers without native schemas."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("Judge response did not contain a JSON object")
        value = json.loads(text[start:end])
        if not isinstance(value, dict):
            raise ValueError("Judge response JSON must be an object")
        return value

    def _orientation(
        self,
        task: PaperRevisionTask,
        candidate: CandidateRevision,
        provider_name: str,
    ) -> bool:
        """Deterministically randomize whether the original appears as text A."""
        material = (
            f"{self.settings.random_seed}:{task.task_id}:"
            f"{candidate.candidate_id}:{provider_name}"
        )
        return int(hashlib.sha256(material.encode()).hexdigest()[-2:], 16) % 2 == 0

    async def _collect_reports(
        self,
        prepared: list[_PreparedCandidate],
        judges: list[JudgeSpec],
    ) -> tuple[dict[int, list[JudgeReport]], dict[int, list[str]]]:
        """Run all independent blinded reviews and retain per-candidate errors."""
        reports: dict[int, list[JudgeReport]] = {item.index: [] for item in prepared}
        errors: dict[int, list[str]] = {item.index: [] for item in prepared}

        async def _review(
            item: _PreparedCandidate,
            judge: JudgeSpec,
        ) -> tuple[int, JudgeReport | None, str | None]:
            original_is_a = self._orientation(item.task, item.candidate, judge.provider)
            prompt = build_pairwise_prompt(
                item.task,
                item.candidate,
                original_is_a=original_is_a,
            )
            try:
                response = await self._call_provider(judge.provider, prompt, PairwiseJudgeResponse)
                report = normalize_report(
                    response,
                    provider=judge.provider,
                    candidate_id=item.candidate.candidate_id,
                    original_is_a=original_is_a,
                    task=item.task,
                )
                return item.index, report, None
            except Exception as exc:
                return item.index, None, f"{judge.provider}: {exc}"

        calls = [
            _review(item, judge)
            for item in prepared
            if item.static_check.passed
            for judge in judges
        ]
        for index, report, error in await asyncio.gather(*calls):
            if report is not None:
                reports[index].append(report)
            if error is not None:
                errors[index].append(error)
        return reports, errors

    async def _evaluate_panel(
        self,
        prepared: list[_PreparedCandidate],
        judges: list[JudgeSpec],
    ) -> list[EvaluationResult]:
        """Evaluate candidates independently and select the highest panel score."""
        reports, errors = await self._collect_reports(prepared, judges)
        aggregates: dict[int, PanelAggregate] = {}
        results: list[EvaluationResult] = [self._failure("uninitialized") for _ in prepared]

        for item in prepared:
            if not item.static_check.passed:
                results[item.index] = self._static_rejection(item)
                continue
            if len(reports[item.index]) < self.settings.min_judges:
                results[item.index] = self._failure(
                    "Judge quorum not reached: " + "; ".join(errors[item.index])
                )
                continue
            aggregate = aggregate_panel(
                reports[item.index],
                task=item.task,
                candidate=item.candidate,
                settings=self.settings,
                static_check=item.static_check,
            )
            aggregates[item.index] = aggregate
            results[item.index] = self._panel_result(item, aggregate, mode="panel")

        self._mark_winner(results, aggregates)
        return results

    async def _evaluate_debate(
        self,
        prepared: list[_PreparedCandidate],
        judges: list[JudgeSpec],
    ) -> list[EvaluationResult]:
        """Run independent reviews, cross-review rebuttals, and final ballots."""
        reports, errors = await self._collect_reports(prepared, judges)
        results: list[EvaluationResult] = [self._failure("uninitialized") for _ in prepared]
        valid_items: list[_PreparedCandidate] = []
        aggregates: dict[int, PanelAggregate] = {}

        for item in prepared:
            if not item.static_check.passed:
                results[item.index] = self._static_rejection(item)
                continue
            if len(reports[item.index]) < self.settings.min_judges:
                results[item.index] = self._failure(
                    "Judge quorum not reached: " + "; ".join(errors[item.index])
                )
                continue
            aggregate = aggregate_panel(
                reports[item.index],
                task=item.task,
                candidate=item.candidate,
                settings=self.settings,
                static_check=item.static_check,
            )
            aggregates[item.index] = aggregate
            valid_items.append(item)

        if len(valid_items) < 2:
            for item in valid_items:
                results[item.index] = self._panel_result(
                    item, aggregates[item.index], mode="panel_fallback"
                )
            self._mark_winner(results, aggregates)
            return results

        labels = {item.index: f"C{position + 1}" for position, item in enumerate(valid_items)}
        candidates_by_label = {labels[item.index]: item.candidate for item in valid_items}
        reports_by_label = {labels[item.index]: reports[item.index] for item in valid_items}
        prompt = build_debate_prompt(
            valid_items[0].task,
            candidates_by_label,
            reports_by_label,
        )

        async def _ballot(judge: JudgeSpec) -> tuple[str, DebateBallot | None, str | None]:
            try:
                ballot = await self._call_provider(judge.provider, prompt, DebateBallot)
                expected = set(candidates_by_label)
                if set(ballot.ranking) != expected or set(ballot.candidate_scores) != expected:
                    raise ValueError("Ballot does not include every anonymous candidate")
                return judge.provider, ballot, None
            except Exception as exc:
                return judge.provider, None, str(exc)

        ballot_calls = [_ballot(judge) for judge in judges]
        ballot_results = await asyncio.gather(*ballot_calls)
        ballots = [ballot for _provider, ballot, _error in ballot_results if ballot is not None]
        ballot_errors = [
            f"{provider}: {error}" for provider, ballot, error in ballot_results if ballot is None
        ]
        if len(ballots) < self.settings.min_judges:
            message = "Debate ballot quorum not reached: " + "; ".join(ballot_errors)
            for item in valid_items:
                results[item.index] = self._failure(message)
            return results

        aggregates_by_label = {labels[item.index]: aggregates[item.index] for item in valid_items}
        outcomes = aggregate_debate(aggregates_by_label, ballots, self.settings)
        winning_label = max(
            outcomes,
            key=lambda label: (
                outcomes[label]["score"],
                aggregates_by_label[label].revised_score,
                label,
            ),
        )
        for item in valid_items:
            label = labels[item.index]
            aggregate = aggregates[item.index]
            is_winner = label == winning_label
            if not is_winner:
                for memory in aggregate.memory_candidates:
                    memory.recommended = False
            results[item.index] = self._panel_result(
                item,
                aggregate,
                mode="debate",
                score_override=outcomes[label]["score"],
                extra_metrics={
                    "pairwise_win_rate": outcomes[label]["pairwise_win_rate"],
                    "borda_percentile": outcomes[label]["borda_percentile"],
                    "debate_ballot_score": outcomes[label]["ballot_score"],
                },
                extra_metadata={
                    "anonymous_label": label,
                    "selected_winner": is_winner,
                    "debate_ballots": [ballot.model_dump(mode="json") for ballot in ballots],
                    "debate_ballot_errors": ballot_errors,
                },
            )
        return results

    def _panel_result(
        self,
        item: _PreparedCandidate,
        aggregate: PanelAggregate,
        *,
        mode: str,
        score_override: float | None = None,
        extra_metrics: dict[str, float] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        """Convert a panel aggregate into the framework result envelope."""
        metrics: dict[str, float] = {
            "baseline_score": aggregate.baseline_score,
            "revised_score": aggregate.revised_score,
            "score_delta": aggregate.score_delta,
            "judge_agreement": aggregate.agreement,
            "static_valid": 1.0,
        }
        for name, score in aggregate.before_dimensions.items():
            metrics[f"before_{name}"] = score
        for name, score in aggregate.after_dimensions.items():
            metrics[f"after_{name}"] = score
        metrics.update(extra_metrics or {})

        metadata: dict[str, Any] = {
            "mode": mode,
            "task_id": item.task.task_id,
            "section_id": item.task.section_id,
            "candidate_id": item.candidate.candidate_id,
            "static_check": item.static_check.model_dump(mode="json"),
            "judge_reports": [report.model_dump(mode="json") for report in aggregate.reports],
            "judge_disagreement": aggregate.disagreement,
            "memory_candidates": [
                memory.model_dump(mode="json") for memory in aggregate.memory_candidates
            ],
            "selected_winner": False,
        }
        metadata.update(extra_metadata or {})
        return EvaluationResult(
            score=aggregate.score if score_override is None else score_override,
            metrics=metrics,
            metadata=metadata,
            success=True,
        )

    def _static_rejection(self, item: _PreparedCandidate) -> EvaluationResult:
        """Return a low but successfully evaluated result for invalid prose."""
        return EvaluationResult(
            score=0.0,
            metrics={
                "baseline_score": 0.0,
                "revised_score": 0.0,
                "score_delta": 0.0,
                "judge_agreement": 0.0,
                "static_valid": 0.0,
            },
            metadata={
                "mode": self.settings.mode,
                "task_id": item.task.task_id,
                "section_id": item.task.section_id,
                "candidate_id": item.candidate.candidate_id,
                "static_check": item.static_check.model_dump(mode="json"),
                "judge_reports": [],
                "memory_candidates": [],
                "selected_winner": False,
            },
            success=True,
        )

    @staticmethod
    def _mark_winner(
        results: list[EvaluationResult],
        aggregates: dict[int, PanelAggregate],
    ) -> None:
        """Mark only the best valid panel candidate as eligible for memory."""
        if not aggregates:
            return
        winner_index = max(aggregates, key=lambda index: (results[index].score, -index))
        for index, aggregate in aggregates.items():
            is_winner = index == winner_index
            results[index].metadata["selected_winner"] = is_winner
            if not is_winner:
                for memory in aggregate.memory_candidates:
                    memory.recommended = False
                results[index].metadata["memory_candidates"] = [
                    memory.model_dump(mode="json") for memory in aggregate.memory_candidates
                ]

    @staticmethod
    def _failure(message: str, *, duration_ms: float = 0.0) -> EvaluationResult:
        """Build a framework-level evaluation failure."""
        return EvaluationResult(
            score=0.0,
            metrics={},
            success=False,
            error_message=message,
            duration_ms=duration_ms,
        )
