"""Independent fixed-image and real-game evaluators over the shared HGamey backend."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import aiohttp

from llm4ad.evaluator.models import EvaluationResult
from llm4ad.gateway.sdk import EvaluationConfigurationError, RemoteJob

from .benchmark import canonical, digest
from .evaluator import HGameyShaderEvaluator
from .execution.static_measurement import COLLAGE_NAMES, REPORT_SCHEMA, score_static_dataset


class IndependentEvaluator(HGameyShaderEvaluator):
    """Reuse SDK preparation, recovery and evidence for one external experiment."""

    @classmethod
    def create_runtime(cls, environment: dict, evaluator: str):
        """Use the explicitly selected evaluator when starting a single-mode gateway."""
        from .runtime import HGameyRuntime

        return HGameyRuntime({**environment, "HGAMEY_EVALUATOR_MODES": cls.evaluation_mode}, evaluator)

    def finalize_preparation(self, job, state: dict) -> dict:
        """Identify one independently scored operation without changing its attempt."""
        state.update(evaluation_mode=self.evaluation_mode, artifact_scope=self.evaluation_mode, submission_started=False)
        return state

    def comparisons(self, report: dict) -> list[dict]:
        """Map executor evidence names to the common uploaded artifact namespace."""
        groups = copy.deepcopy(report.get("evidence_comparisons", []))
        if not isinstance(groups, list):
            return []
        result = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("images"), list):
                continue
            for image in group["images"]:
                if isinstance(image, dict) and isinstance(image.get("artifact"), str):
                    image["artifact"] = f"{self.evaluation_mode}/{image['artifact']}"
            result.append(group)
        return result

    async def after_task(self, job, reporter, operation, *, result=None, error=None) -> None:
        """Retain the completed experiment's images and reports through SDK uploads."""
        await super().after_task(job, reporter, operation, result=result, error=error)
        if error is not None or operation not in {"evaluate", "recover"}:
            return
        state = reporter.saved_state
        if not state or not state.get("external_job_id"):
            return
        self.validate_prepared(job, state)
        folder = Path(state["evidence_directory"])
        receipt, report = folder / "executor-job.json", folder / "report.json"
        if not receipt.is_file() or not report.is_file() or receipt.is_symlink() or report.is_symlink():
            return
        completed = json.loads(receipt.read_text())
        if (
            completed.get("id") != state["external_job_id"]
            or completed.get("state") not in {"completed", "failed"}
            or completed.get("cleanup_status") != "completed"
        ):
            return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            errors = await self.retain_game_artifacts(session, reporter, state, json.loads(report.read_text()))
        if errors and isinstance(result, EvaluationResult):
            result.metadata.setdefault("artifact_errors", []).extend(errors)


class HGameyStaticEvaluator(IndependentEvaluator):
    """Measure fixed-image GPU latency and quality without launching Genshin."""

    evaluation_mode = "static"

    async def before_task(self, job: RemoteJob, reporter, operation: str) -> None:
        """Reject invalid scoring settings before compilation or device allocation."""
        if operation != "cancel" and getattr(self.benchmark, "static_pairs", []):
            limit = job.parameters.get("max_psnr_drop_db", 0.0)
            if (
                isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or not math.isfinite(limit)
                or limit < 0
            ):
                raise EvaluationConfigurationError("max_psnr_drop_db must be a finite nonnegative number")
        await super().before_task(job, reporter, operation)

    def finalize_preparation(self, job: RemoteJob, state: dict) -> dict:
        """Record the selected tolerance in the job state used for scoring and recovery."""
        state = super().finalize_preparation(job, state)
        if getattr(self.benchmark, "static_pairs", []):
            state["max_psnr_drop_db"] = float(job.parameters.get("max_psnr_drop_db", 0.0))
        return state

    def convert_report(
        self, report: dict, state: dict, *, score_quality_rejection: bool = False
    ) -> EvaluationResult | None:
        """Keep official static measurements and associate actual GPU image evidence."""
        paired = bool(getattr(self.benchmark, "static_pairs", []))
        if paired and report.get("schema") != REPORT_SCHEMA and not report.get("preparation_failure_verified"):
            return None
        result = super().convert_report(report, state, score_quality_rejection=paired or score_quality_rejection)
        if result is not None:
            result.metadata.update(evaluation_mode="static", evidence_comparisons=self.comparisons(report))
            if report.get("artifact_errors"):
                result.metadata["artifact_errors"] = report["artifact_errors"]
            baseline = report.get("baseline_metrics", {})
            if isinstance(baseline, dict) and isinstance(baseline.get("psnr_db"), (int, float)):
                result.monitor_metrics["baseline_psnr_db"] = baseline["psnr_db"]
            if paired and result.success:
                scored = score_static_dataset(report, max_psnr_drop_db=state.get("max_psnr_drop_db", 0.0))
                if scored is None:
                    return None
                result.score, quality = scored
                gain = float(report["operator_improvement_pct"])
                result.metrics.update(static_score=result.score, operator_improvement_pct=gain)
                result.metadata.update(static_datasets=report["static_datasets"], quality_assessment=quality)
                summary = result.metadata["evaluation_summary"]
                summary["quality_passed"] = not quality["penalized"]
                summary["operator_improvement_threshold_met"] = (
                    gain > 0 and gain >= self.benchmark.min_operator_improvement_pct
                )
                if quality["penalized"]:
                    summary["comparison_outcome"] = "quality_penalty"
                result.evolution_feedback.update(
                    metrics=result.metrics, assessment=dict(summary), quality_assessment=quality
                )
        return result

    async def retain_game_artifacts(self, session, reporter, state: dict, report: dict) -> list[str]:
        """Apply the two-image upload allowlist to paired static evaluations."""
        if getattr(self.benchmark, "static_pairs", []):
            images: dict[str, dict] = {}
            for item in report.get("artifacts", []):
                if item.get("name") in COLLAGE_NAMES and item.get("media_type") == "image/png":
                    images.setdefault(item["name"], item)
            report = report | {"artifacts": list(images.values())}
        return await super().retain_game_artifacts(session, reporter, state, report)


class HGameyGameEvaluator(IndependentEvaluator):
    """Measure real-game performance and same-input GPU quality without elite reevaluation."""

    evaluation_mode = "game"

    def finalize_preparation(self, job, state: dict) -> dict:
        """Publish only the compiled game A/B request as this attempt's external intent."""
        state = super().finalize_preparation(job, state)
        game = state.pop("game")
        state["operation"] = "game-ab"
        state["request"] = game["request"]
        state["request"]["idempotency_key"] = state["idempotency_key"]
        state["request_sha256"] = digest(canonical(state["request"]))
        return state

    async def resume(self, state: dict, reporter) -> EvaluationResult | None:
        """Follow the original game job and derive fitness from its own measurements."""
        self.validate_frozen(state)
        if self.direct_store() is not None:
            direct_report = await self.follow_direct(state, reporter)
            return self.require_result(self.convert_report(direct_report, state), state)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            await self.check_executor(session, reporter, game_required=True)
            report = await self.follow_job(session, reporter, state)
        if report is None:
            return self.require_result(None, state)
        return self.require_result(self.convert_report(report, state), state)

    def convert_report(
        self, report: dict, state: dict, *, score_quality_rejection: bool = False
    ) -> EvaluationResult | None:
        """Expose weighted real-game fitness and each measured contribution."""
        from .scoring import score_game

        preparation = self.preparation_failure(report, state)
        if preparation is not None:
            preparation.metadata["evaluation_mode"] = "game"
            return preparation
        if (
            report.get("schema") != "llm4ad/hgamey-game-ab@1"
            or report.get("candidate_id") != state["idempotency_key"]
            or report.get("candidate_layer_sha256") != state["files"]["candidate-layer/libVkLayer_refknobs.so"]
            or report.get("baseline_layer_sha256") != state["files"]["baseline-layer/libVkLayer_refknobs.so"]
        ):
            raise RuntimeError("Game measurement report does not match the accepted candidate and baseline")
        if report.get("conditions_observed", {}).get("restored") is not True:
            return None
        scored = score_game(report, self.benchmark.game_scoring)
        metadata = {
            "evaluation_mode": "game", "measurement_scope": "game_candidate_evaluation",
            "data_version": self.benchmark.version, "external_job_id": state.get("external_job_id"),
            "evidence_directory": state["evidence_directory"], "source_sha256": state["files"]["candidate.comp"],
            "remote_job_id": state["remote_job_id"], "remote_attempt_id": state["remote_attempt_id"],
            "report": report, "scoring_policy": self.benchmark.game_scoring,
            "scoring_measurements": scored["metrics"],
            "score_components": scored["components"], "missing_scored_metrics": scored["missing"],
            "quality_assessment": scored.get("quality_assessment", {}),
            "evidence_comparisons": self.comparisons(report),
            "score_is_significance_test": False,
            "score_scope": "game_weighted_measurements",
            "can_claim_visual_quality_improvement": False,
        }
        observed = report.get("metrics", {})
        observed = observed if isinstance(observed, dict) else {}
        metrics = {
            f"game_{key}": float(value) for key, value in observed.items()
            if type(value) in (int, float) and math.isfinite(value)
        }
        unpenalized = scored.get("unpenalized_score")
        if (type(unpenalized) is int or type(unpenalized) is float) and math.isfinite(unpenalized):
            metrics["game_unpenalized_score"] = float(unpenalized)
        for name, component in scored["components"].items():
            for field in ("gain", "contribution"):
                value = component.get(field)
                if type(value) in (int, float) and math.isfinite(value):
                    metrics[f"game_{name}_{field}"] = float(value)
        if report.get("status") != "PASS" or report.get("errors") != [] or report.get("candidate_applied") is not True:
            metadata.update(infrastructure_failure=True, failure_type="GameMeasurementInvalid")
            return EvaluationResult(
                success=False, metrics=metrics, metadata=metadata,
                error_message="Game A/B measurement was not valid; inspect the retained report and device logs",
            )
        if scored["score"] is None:
            metadata.update(infrastructure_failure=True, failure_type="GameMeasurementUnavailable")
            return EvaluationResult(
                success=False, metrics=metrics,
                error_message="Game scoring observations unavailable: " + ", ".join(scored["missing"]),
                metadata=metadata,
            )
        metadata["evaluation_summary"] = {
            "measurement_valid": True, "score_basis": "real_game_weighted_measurements",
            "quality_penalized": bool(scored.get("quality_assessment", {}).get("penalized")),
            "comparison_outcome": (
                "quality_penalty" if scored.get("quality_assessment", {}).get("penalized")
                else "higher_weighted_score" if scored["score"] > 0 else "no_advantage"
            ),
        }
        return EvaluationResult(
            score=scored["score"], metrics={"game_score": scored["score"]}, monitor_metrics=metrics, metadata=metadata,
            evolution_feedback={
                "assessment": metadata["evaluation_summary"], "score_components": scored["components"],
                "quality_assessment": metadata["quality_assessment"],
            },
        )
