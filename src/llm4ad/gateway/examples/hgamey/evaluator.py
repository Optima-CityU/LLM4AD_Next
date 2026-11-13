"""SDK adapter for Genshin-frame Shader measurements through HGamey."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import aiohttp

from llm4ad.evaluator.base import EvaluationResult
from llm4ad.gateway.examples.hgamey.benchmark import Benchmark, canonical, compiler_logs, digest, write_json
from llm4ad.gateway.sdk.errors import EvaluationError
from llm4ad.remote import GatewayEvaluator, RemoteJob


class CandidateCompilationError(EvaluationError):
    """Retain actionable candidate compiler feedback before device dispatch."""


class HGameyShaderEvaluator(GatewayEvaluator):
    """Bridge whole-candidate SDK jobs to the local serialized phone executor."""

    manages_evidence_retention = True

    @classmethod
    def create_runtime(cls, environment: dict, evaluator: str):
        """Attach device resources to the SDK lifecycle without a case-specific service."""
        from llm4ad.gateway.examples.hgamey.runtime import HGameyRuntime

        return HGameyRuntime(environment, evaluator)

    def __init__(self, filename: str | Path | None = None):
        """Load the operator-owned profile selected by the gateway environment."""
        filename = filename or os.environ.get("HGAMEY_GATEWAY_CONFIG")
        if not filename:
            raise ValueError("Set HGAMEY_GATEWAY_CONFIG to your local settings JSON file")
        self.benchmark = Benchmark(filename)
        self.contract = self.benchmark.contract

    async def rpc(self, session, method: str, path: str, body: dict | None = None, missing: bool = False):
        """Make one local HTTP request, distinguishing absent jobs from outages."""
        kwargs = {"json": body} if body is not None else {}
        async with session.request(method, self.benchmark.url + path, **kwargs) as response:
            if missing and response.status == 404:
                return None
            response.raise_for_status()
            return await response.json()

    async def retry_rpc(self, session, reporter, method: str, path: str, body: dict | None = None, missing: bool = False):
        """Keep device/transport interruptions out of algorithm failure samples."""
        disconnected = False
        while True:
            try:
                return await self.rpc(session, method, path, body, missing)
            except aiohttp.ClientResponseError as error:
                if error.status < 500 and error.status != 429:
                    raise
            except (TimeoutError, aiohttp.ClientError):
                pass
            if not disconnected:
                reporter.progress("Waiting for the local HGamey executor to reconnect")
                disconnected = True
            await asyncio.sleep(self.benchmark.poll_interval)

    async def check_executor(self, session, reporter, *, game_required: bool | None = None) -> None:
        """Confirm protocol capabilities and the configured physical device."""
        health = await self.retry_rpc(session, reporter, "GET", "/v1/health")
        if not {"idempotent_submit", "job_lookup", "cancel"}.issubset(health.get("features", [])):
            raise RuntimeError("Apply the documented HGamey executor compatibility patch before dispatch")
        if game_required is None:
            game_required = self.benchmark.game_check is not None
        if game_required and "game_ab" not in health.get("features", []):
            raise RuntimeError(
                "Configured game_check requires executor capability 'game_ab'; update the executor before dispatch"
            )
        devices = await self.retry_rpc(session, reporter, "GET", "/v1/devices")
        expected = self.benchmark.settings
        device = next((item for item in devices["devices"] if item["id"] == expected["device_id"]), None)
        if not device or not device["enabled"] or device["expected_serial"] != expected["expected_serial"]:
            raise RuntimeError("Executor device identity differs from the fixed local benchmark profile")

    def prepare(self, job: RemoteJob) -> dict:
        """Compile one candidate and freeze its idempotent external submission.

        Args:
            job: The SDK's isolated candidate worktree and attempt identity.

        Returns:
            Durable intent with the exact external request and evidence paths.
        """
        key = self.benchmark.attempt_key(job)
        game_required = self.benchmark.uses_game(job.evaluation_profile)
        folder = self.benchmark.profile / "jobs" / key
        folder.mkdir(parents=True, exist_ok=True)
        owner = {"job_id": job.id, "attempt_id": job.attempt_id, "task_id": job.task_id, "data_version": self.benchmark.version}
        ownership = folder / "owner.json"
        if ownership.exists() and json.loads(ownership.read_text()) != owner:
            raise RuntimeError("Staging directory belongs to another evaluation attempt")
        write_json(ownership, owner)
        if (folder / "submission.json").exists():
            state = json.loads((folder / "submission.json").read_text(encoding="utf-8"))
            self.validate_prepared(job, state)
            return state
        if game_required:
            self.benchmark.check_game_identity()
        candidate = Path(job.project_root) / "shader.comp"
        source = folder / "candidate.comp"
        shutil.copyfile(candidate, source)
        baseline = folder / "baseline.comp"
        baseline.write_bytes(self.benchmark.baseline.read_bytes())
        if digest(baseline.read_bytes()) != self.benchmark.manifest["baseline_sha256"]:
            raise RuntimeError("The fixed baseline changed after this benchmark was registered")
        try:
            self.benchmark.compile(baseline, folder / "baseline.spv")
        except (ValueError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"The operator-owned baseline could not be compiled: {error}") from error
        self.benchmark.compile(source, folder / "candidate.spv")
        request = self.benchmark.request_for(job.evaluation_profile)
        request["candidate_id"] = key
        request["payload"].update(
            shader_path=self.benchmark.container_path(source),
            spirv_path=self.benchmark.container_path(folder / "candidate.spv"),
            quality_reference=self.benchmark.container_path(self.benchmark.references),
        )
        body = {
            "idempotency_key": key,
            "device_id": self.benchmark.settings["device_id"],
            "operation": "eval",
            "pause_foreground": self.benchmark.settings.get("pause_foreground", False),
            "request": request,
        }
        state = {
            "idempotency_key": key,
            "data_version": self.benchmark.version,
            "evaluation_profile": job.evaluation_profile,
            "game_required": game_required,
            "remote_job_id": job.id,
            "remote_attempt_id": job.attempt_id,
            "evidence_directory": str(folder),
            "request": body,
            "request_sha256": digest(canonical(body)),
            "files": {
                path.name: digest(path.read_bytes())
                for path in (source, baseline, folder / "candidate.spv", folder / "baseline.spv")
            },
        }
        if game_required:
            baseline_layer = self.benchmark.build_game_layer(baseline, folder / "baseline-layer", "seed")
            candidate_layer = self.benchmark.build_game_layer(source, folder / "candidate-layer", "candidate")
            self.benchmark.check_game_identity()
            for name, expected in state["files"].items():
                if digest((folder / name).read_bytes()) != expected:
                    raise RuntimeError("Staged shader changed during the game layer build")
            for path in (baseline_layer, candidate_layer):
                state["files"][path.relative_to(folder).as_posix()] = digest(path.read_bytes())
            game_key = digest(canonical({"candidate_id": key, "operation": "game-ab"}))
            game_body = {
                "idempotency_key": game_key,
                "device_id": self.benchmark.settings["device_id"],
                "operation": "game-ab",
                "options": {
                    **self.benchmark.game_check["options"],
                    "baseline_layer_path": self.benchmark.container_path(baseline_layer),
                    "layer_path": self.benchmark.container_path(candidate_layer),
                    "candidate_id": key,
                    "candidate_source_sha256": state["files"]["candidate.comp"],
                    "baseline_source_sha256": state["files"]["baseline.comp"],
                },
            }
            state["game"] = {
                "idempotency_key": game_key,
                "request": game_body,
                "request_sha256": digest(canonical(game_body)),
                "submission_started": False,
            }
        write_json(folder / "submission.json", state)
        return state

    async def before_task(self, job: RemoteJob, reporter: Any, operation: str) -> None:
        """Freeze and compile fresh work, or validate a recovery without rebuilding.

        Args:
            job: Authoritative SDK attempt and evaluation profile.
            reporter: Durable intent, progress and private local diagnostics.
            operation: SDK-selected evaluate, recover or cancel operation.
        """
        if operation == "cancel":
            return
        state = reporter.saved_state
        submission = self.benchmark.profile / "jobs" / self.benchmark.attempt_key(job) / "submission.json"
        if not state and submission.is_file():
            state = json.loads(submission.read_text(encoding="utf-8"))
        if state:
            self.validate_prepared(job, state)
            reporter.save_state(state)
            return
        if operation != "evaluate":
            return
        reporter.progress("Compiling Genshin super-resolution Shader and fixed baseline")
        try:
            state = await asyncio.to_thread(self.prepare, job)
        except (ValueError, subprocess.TimeoutExpired) as error:
            raise CandidateCompilationError(
                f"Shader compilation/validation failed: {error}",
                metadata={
                    "error_type": "CandidateCompilationError",
                    "candidate_failure": True,
                    "evidence_directory": str(self.benchmark.profile / "jobs" / self.benchmark.attempt_key(job)),
                },
                evolution_feedback={"stage": "compile", "diagnostics": str(error)},
            ) from error
        # Persist intent before any HTTP request that can schedule a phone experiment.
        reporter.save_state(state)

    def validate_prepared(self, job: RemoteJob, state: dict) -> None:
        """Validate accepted ownership, frozen requests and binaries without device I/O."""
        key = self.benchmark.attempt_key(job)
        if state.get("idempotency_key") != key:
            raise RuntimeError("The saved job belongs to a different accepted attempt or profile")
        if state.get("evaluation_profile", job.evaluation_profile) != job.evaluation_profile:
            raise RuntimeError("The saved evaluation profile differs from the accepted job")
        if state.get("remote_job_id") != job.id or state.get("remote_attempt_id") != job.attempt_id:
            raise RuntimeError("The saved job belongs to a different accepted attempt")
        folder = self.benchmark.profile / "jobs" / key
        if Path(state["evidence_directory"]) != folder or folder.is_symlink():
            raise RuntimeError("The saved staging directory differs from the accepted attempt")
        owner = folder / "owner.json"
        expected = {
            "job_id": job.id,
            "attempt_id": job.attempt_id,
            "task_id": job.task_id,
            "data_version": self.benchmark.version,
        }
        if owner.is_symlink() or json.loads(owner.read_text()) != expected:
            raise RuntimeError("Staging directory belongs to another evaluation attempt")
        self.validate_frozen(state)

    async def evaluate(self, job: RemoteJob, reporter: Any) -> EvaluationResult | None:
        """Measure only hook-prepared work; never compile or freeze a new candidate."""
        state = reporter.saved_state
        if not state:
            raise RuntimeError("HGamey evaluation requires before_task preparation through the SDK lifecycle")
        self.validate_prepared(job, state)
        return await self.resume(state, reporter)

    async def recover(self, job: RemoteJob, reporter: Any) -> EvaluationResult | None:
        """Reattach by durable external identity without repeating experiments."""
        state = reporter.saved_state
        if not state:
            return None
        self.validate_prepared(job, state)
        return await self.resume(state, reporter)

    def validate_frozen(self, state: dict) -> None:
        """Check request hashes and immutable inputs before following existing work."""
        if state.get("data_version") != self.benchmark.version:
            raise RuntimeError("The saved job belongs to a different benchmark; restore its original profile")
        if state.get("request_sha256") and digest(canonical(state["request"])) != state["request_sha256"]:
            raise RuntimeError("Frozen operator submission changed")
        game_required = state.get("game_required", self.benchmark.game_check is not None)
        required_files = {"candidate.comp", "baseline.comp", "candidate.spv", "baseline.spv"}
        if "evaluation_profile" in state and game_required != self.benchmark.uses_game(state["evaluation_profile"]):
            raise RuntimeError("Frozen game obligation differs from the accepted evaluation profile")
        if game_required:
            required_files.update({"baseline-layer/libVkLayer_refknobs.so", "candidate-layer/libVkLayer_refknobs.so"})
            game = state.get("game")
            if not game or digest(canonical(game["request"])) != game["request_sha256"]:
                raise RuntimeError("Frozen game submission is missing or changed")
        if not required_files.issubset(state["files"]):
            raise RuntimeError("Frozen candidate/baseline file identities are missing")
        folder = Path(state["evidence_directory"])
        for name, expected in state["files"].items():
            path = folder / name
            if (
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or any(part.is_symlink() for part in (path, *path.parents))
                or digest(path.read_bytes()) != expected
            ):
                raise RuntimeError("Staged candidate/baseline changed; refusing to reuse the external submission")

    async def resume(self, state: dict, reporter: Any) -> EvaluationResult | None:
        """Recover a lost submit response or follow an existing executor job."""
        self.validate_frozen(state)
        game_required = state.get("game_required", self.benchmark.game_check is not None)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            await self.check_executor(session, reporter, game_required=game_required)
            report = await self.follow_job(session, reporter, state)
            if report is None:
                return None
            result = self.convert_report(report, state)
            if (
                result is not None
                and result.success
                and not result.metadata["evaluation_summary"]["operator_improvement_confirmed"]
            ):
                reporter.progress("Measurement completed with no confirmed advantage; retained as a valid evaluation")
            if not game_required or result is None or not result.success:
                return result
            game_report = await self.follow_job(session, reporter, state, game=True)
            return self.combine_game_report(result, game_report, state)

    async def after_task(self, job: RemoteJob, reporter: Any, operation: str, *, result=None, error=None) -> None:
        """Finalize diagnostics and completed evidence without deleting frozen work.

        Args:
            job: Authoritative SDK attempt.
            reporter: Attempt-scoped private logs and immutable evidence outbox.
            operation: SDK-selected evaluate, recover or cancel operation.
            result: Unchanged measurement result or cancellation confirmation.
            error: Original preparation or execution error, if any.
        """
        folder = self.benchmark.profile / "jobs" / self.benchmark.attempt_key(job)
        local_log = getattr(reporter, "local_log", None)
        if callable(local_log) and folder.is_dir():
            for name, path in compiler_logs(folder, job, self.benchmark.version).items():
                target = local_log(name, description="HGamey compiler and validator diagnostics")
                if isinstance(target, (str, os.PathLike)):
                    with path.open("rb") as source, Path(target).open("ab") as output:
                        shutil.copyfileobj(source, output)
        if error is not None or operation not in {"evaluate", "recover"}:
            return
        state = reporter.saved_state
        if not state or not state.get("game", {}).get("external_job_id"):
            return
        self.validate_prepared(job, state)
        receipt, report = folder / "game-executor-job.json", folder / "game-report.json"
        if receipt.is_symlink() or report.is_symlink():
            raise RuntimeError("Game evidence receipts must not be symlinks")
        if not receipt.is_file() or not report.is_file():
            return
        completed = json.loads(receipt.read_text())
        if (
            completed.get("id") != state["game"]["external_job_id"]
            or completed.get("state") != "completed"
            or completed.get("cleanup_status") != "completed"
        ):
            return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            errors = await self.retain_game_artifacts(session, reporter, state, json.loads(report.read_text()))
            if errors and isinstance(result, EvaluationResult):
                result.metadata.setdefault("artifact_errors", []).extend(errors)

    async def retain_game_artifacts(self, session, reporter, state: dict, report: dict) -> list[str]:
        """Retain declared evidence without letting optional images invalidate measurements.

        Args:
            session: Executor HTTP session.
            reporter: Attempt-scoped artifact reporter.
            state: Frozen executor identity and staging directory.
            report: Completed game report and declared artifacts.

        Returns:
            Diagnostics for optional screenshots that could not be retained.
        """
        errors = []
        for item in report.get("artifacts", []):
            optional = (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and re.fullmatch(r"shots/[AB][12]\.png", item["name"]) is not None
                and item.get("media_type") == "image/png"
            )
            try:
                await self.retain_game_artifact(session, reporter, state, item, optional=optional)
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError) as error:
                if not optional:
                    raise
                status = f" (HTTP {error.status})" if isinstance(error, aiohttp.ClientResponseError) else ""
                errors.append(f"game/{item['name']}: {type(error).__name__}{status}")
                reporter.progress(f"Optional screenshot {item['name']} unavailable; measurement result retained")
        return errors

    async def retain_game_artifact(self, session, reporter, state: dict, item: dict, *, optional: bool) -> None:
        """Retrieve one checksum-bound file, with bounded optional screenshot transport."""
        if not isinstance(item, dict):
            raise ValueError("Invalid executor evidence manifest")
        folder = Path(state["evidence_directory"]) / "game-artifacts"
        name = item.get("name", "")
        checksum = item.get("sha256", "")
        size = item.get("size")
        if (
            not name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or "\\" in name
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)
            or type(size) is not int
            or not 0 < size <= 64 * 1024 * 1024
        ):
            raise ValueError("Invalid executor evidence manifest")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / checksum
        if not target.exists():
            while True:
                temporary = target.with_suffix(".partial")
                try:
                    async with session.get(
                        self.benchmark.url + f"/v1/jobs/{state['game']['external_job_id']}/artifacts/{checksum}",
                        timeout=aiohttp.ClientTimeout(total=15 if optional else 120),
                    ) as response:
                        response.raise_for_status()
                        written = 0
                        with temporary.open("wb") as stream:
                            async for chunk in response.content.iter_chunked(65536):
                                written += len(chunk)
                                if written > size:
                                    raise ValueError("Executor evidence exceeds its declared size")
                                stream.write(chunk)
                    if written != size or digest(temporary.read_bytes()) != checksum:
                        raise ValueError("Executor evidence checksum mismatch")
                    temporary.replace(target)
                    break
                except aiohttp.ClientResponseError as error:
                    if optional or (error.status < 500 and error.status != 429):
                        raise
                    reporter.progress("Waiting to retrieve completed phone trace evidence")
                    await asyncio.sleep(self.benchmark.poll_interval)
                except (aiohttp.ClientError, TimeoutError):
                    if optional:
                        raise
                    reporter.progress("Waiting to retrieve completed phone trace evidence")
                    await asyncio.sleep(self.benchmark.poll_interval)
                finally:
                    original_error = sys.exception()
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        # Cleanup must not replace an in-flight cancellation or transport error.
                        if original_error is None:
                            raise
        if target.stat().st_size != size or digest(target.read_bytes()) != checksum:
            raise ValueError("Retained executor evidence changed")
        reporter.artifact(
            "game/" + name, target, item.get("media_type", "application/octet-stream"),
            description=item.get("description", ""),
        )

    async def follow_job(self, session, reporter, state: dict, game: bool = False) -> dict | None:
        """Follow one frozen external identity and retain its cleanup evidence."""
        intent = state["game"] if game else state
        prefix = "game-" if game else ""
        folder = Path(state["evidence_directory"])
        item = await self.retry_rpc(session, reporter, "GET", f"/v1/jobs/by-key/{intent['idempotency_key']}", missing=True)
        if (
            state.get("game_required", self.benchmark.game_check is not None)
            and intent.get("external_job_id")
            and (item is None or item["id"] != intent["external_job_id"])
        ):
            reporter.progress("Previously accepted executor job is missing or changed; refusing to replay it")
            return None
        if item is None:
            if game:
                # Persist the uncertainty boundary before a POST can reach the executor.
                intent["submission_started"] = True
                reporter.save_state(state)
            try:
                item = await self.retry_rpc(session, reporter, "POST", "/v1/jobs", intent["request"])
            except aiohttp.ClientResponseError as error:
                if not game:
                    raise
                reporter.progress(f"Game validation submission is unsupported or invalid (HTTP {error.status})")
                return None
        intent["external_job_id"] = item["id"]
        reporter.save_state(state)
        previous = None
        while True:
            phase = item["state"]
            if phase != previous:
                reporter.progress(f"HGamey {prefix}phone experiment: {phase} (job {item['id']})")
                previous = phase
            if phase in {"queued", "running", "cancel_requested"}:
                await asyncio.sleep(self.benchmark.poll_interval)
                item = await self.retry_rpc(session, reporter, "GET", f"/v1/jobs/{item['id']}")
                continue
            write_json(folder / f"{prefix}executor-job.json", item)
            if phase in {"interrupted", "uncertain", "cancelled"}:
                reporter.progress("Phone execution requires cancellation/recovery confirmation")
                return None
            if phase not in {"completed", "failed"}:
                raise RuntimeError(f"Unknown HGamey job state: {phase}")
            report = await self.retry_rpc(session, reporter, "GET", f"/v1/jobs/{item['id']}/result", missing=True)
            if report is None:
                reporter.progress("No valid phone measurement report; inspect local executor evidence")
                return None
            try:
                write_json(folder / f"{prefix}report.json", report)
            except (TypeError, ValueError):
                if not game:
                    raise
                reporter.progress("Invalid game report JSON; retaining the accepted job for inspection")
                return None
            if item.get("cleanup_status") != "completed":
                reporter.progress("Phone cleanup is unconfirmed; retaining the accepted job for recovery")
                return None
            if game and phase != "completed":
                return None
            return report

    def combine_game_report(self, result: EvaluationResult, report: dict | None, state: dict) -> EvaluationResult | None:
        """Require applied, restored game evidence without using FPS as fitness."""
        if (
            not isinstance(report, dict)
            or report.get("schema") != "llm4ad/hgamey-game-ab@1"
            or report.get("status") != "PASS"
            or report.get("errors") != []
            or report.get("candidate_applied") is not True
            or not isinstance(report.get("conditions_observed"), dict)
            or report["conditions_observed"].get("restored") is not True
            or report.get("candidate_id") != state["idempotency_key"]
            or report.get("candidate_layer_sha256") != state["files"]["candidate-layer/libVkLayer_refknobs.so"]
            or report.get("baseline_layer_sha256") != state["files"]["baseline-layer/libVkLayer_refknobs.so"]
            or not isinstance(report.get("metrics"), dict)
        ):
            return None
        metrics = {
            f"game_{key}": float(value)
            for key, value in report["metrics"].items()
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
        }
        if any(metrics.get(f"game_{side}_display_fps", 0) <= 0 for side in ("baseline", "comparison")):
            return None
        result.metrics.update(metrics)
        result.monitor_metrics.update(metrics)
        result.metadata.update(
            measurement_scope="operator_benchmark_with_game_validation",
            game_external_job_id=state["game"].get("external_job_id"),
            game_report=report,
            can_claim_game_improvement=False,
        )
        result.metadata.setdefault("evaluation_summary", {})["game_validation"] = "passed"
        result.evolution_feedback["assessment"] = dict(result.metadata["evaluation_summary"])
        result.evolution_feedback["game_validation"] = {"status": "PASS", "can_claim_game_improvement": False}
        return result

    def convert_report(self, report: dict, state: dict) -> EvaluationResult | None:
        """Preserve HGamey validity and provenance while exposing platform metrics."""
        if (
            report.get("source") != "phonefarm"
            or report.get("kind") != "shader"
            or report.get("candidate_id") != state["idempotency_key"]
        ):
            raise RuntimeError("Unexpected measurement source, kind or candidate identity")
        if report.get("conditions_observed", {}).get("restored") is not True:
            return None
        status = report.get("status")
        if status in {"ABORT", "ERROR", "REVERTED"}:
            return None
        metrics = {
            key: float(value)
            for key, value in report.get("metrics", {}).items()
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
        }
        metadata = {
            "game": "genshin",
            "measurement_scope": "operator_benchmark_only",
            "can_claim_game_improvement": False,
            "data_version": self.benchmark.version,
            "external_job_id": state.get("external_job_id"),
            "evidence_directory": state["evidence_directory"],
            "source_sha256": state["files"]["candidate.comp"],
            "report": report,
            "remote_job_id": state.get("remote_job_id"),
            "remote_attempt_id": state.get("remote_attempt_id"),
            "evaluation_profile": state.get("evaluation_profile", "standard"),
        }
        verdict = report.get("verdict", {})
        verdict = verdict if isinstance(verdict, dict) else {}
        p_value = verdict.get("p_value") if isinstance(verdict, dict) else None
        summary = {
            "measurement_valid": True,
            "quality_passed": status not in {"CRASH", "SLOW", "POOR_QUALITY"},
            "operator_improvement_confirmed": False,
            "min_operator_improvement_pct": self.benchmark.min_operator_improvement_pct,
            "operator_improvement_threshold_met": False,
            "game_validation": "pending"
            if state.get("game_required", self.benchmark.game_check is not None)
            else ("not_requested" if self.benchmark.game_check is not None else "not_configured"),
            "can_claim_game_improvement": False,
            "data_version": self.benchmark.version,
        }
        metadata["evaluation_summary"] = summary
        if status in {"CRASH", "SLOW", "POOR_QUALITY"}:
            metadata["candidate_failure"] = True
            return EvaluationResult(
                success=False,
                metrics=metrics,
                error_message=f"HGamey candidate verdict: {status}",
                metadata=metadata,
                evolution_feedback={"status": status, "verdict": report.get("verdict"), "metrics": metrics},
            )
        if status not in {"PASS", "REJECT"}:
            raise RuntimeError(f"Unknown measurement status: {status}")
        if metrics.get("operator_latency_ms", 0) <= 0 or "psnr_db" not in metrics:
            return None
        score = -metrics["operator_latency_ms"]
        baseline_metrics = report.get("baseline_metrics", {})
        baseline = baseline_metrics.get("operator_latency_ms") if isinstance(baseline_metrics, dict) else None
        if isinstance(baseline, (float, int)) and not isinstance(baseline, bool) and math.isfinite(baseline) and baseline > 0:
            metrics["baseline_operator_latency_ms"] = float(baseline)
            metrics["operator_improvement_pct"] = 100 * (baseline - metrics["operator_latency_ms"]) / baseline
            if not math.isfinite(metrics["operator_improvement_pct"]):
                return None
            summary["operator_improvement_threshold_met"] = (
                metrics["operator_improvement_pct"] > 0
                and metrics["operator_improvement_pct"] >= self.benchmark.min_operator_improvement_pct
            )
            summary["operator_improvement_confirmed"] = (
                status == "PASS"
                and verdict.get("is_pareto_improvement") is True
                and isinstance(p_value, (int, float))
                and not isinstance(p_value, bool)
                and math.isfinite(p_value)
                and 0 <= p_value <= state["request"]["request"]["protocol"]["p_threshold"]
                and summary["operator_improvement_threshold_met"]
            )
        if self.benchmark.score_mode == "relative_latency":
            if "operator_improvement_pct" not in metrics:
                return None
            score = metrics["operator_improvement_pct"]
        summary["score_mode"] = self.benchmark.score_mode
        summary["comparison_outcome"] = "improved" if summary["operator_improvement_confirmed"] else "no_advantage"
        primary = "operator_improvement_pct" if self.benchmark.score_mode == "relative_latency" else "operator_latency_ms"
        return EvaluationResult(
            score=score,
            metrics=metrics,
            metadata=metadata,
            monitor_metrics={key: value for key, value in metrics.items() if key != primary},
            evolution_feedback={
                "status": status,
                "verdict": report.get("verdict"),
                "metrics": metrics,
                "assessment": dict(summary),
            },
        )

    async def cancel(self, job: RemoteJob, saved_state: dict) -> bool:
        """Confirm cancellation only after executor-side execution and cleanup end."""
        if not saved_state:
            return False
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            confirmed = True
            intents = [saved_state]
            if "game" in saved_state:
                intents.append(saved_state["game"])
            elif saved_state.get("game_required", self.benchmark.game_check is not None):
                confirmed = False
            for intent in intents:
                try:
                    item = await self.rpc(session, "GET", f"/v1/jobs/by-key/{intent['idempotency_key']}", missing=True)
                    if item is None:
                        # Only an explicitly unsubmitted game arm can be absent safely.
                        confirmed &= intent.get("submission_started") is False and not intent.get("external_job_id")
                        continue
                    item = await self.rpc(session, "POST", f"/v1/jobs/{item['id']}/cancel", {})
                    confirmed &= (
                        item["state"] in {"cancelled", "completed", "failed"} and item.get("cleanup_status") == "completed"
                    )
                except (TimeoutError, aiohttp.ClientError):
                    if "game" not in saved_state:
                        raise
                    confirmed = False
            return confirmed


def main() -> int:
    """Run the standalone compatibility command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check local settings, tools and executor API without running a phone job"
    )
    arguments = parser.parse_args()
    if not arguments.check:
        parser.error("Use --check, or load HGameyShaderEvaluator with llm4ad evaluator-gateway start")
    evaluator = HGameyShaderEvaluator()

    class ConsoleReporter:
        """Print preflight transport status without a platform connection."""

        def progress(self, message, **kwargs):
            """Print one observed preflight status."""
            print(message)

    async def check():
        """Check the local baseline and read-only executor endpoints."""
        evaluator.benchmark.compile(evaluator.benchmark.baseline, evaluator.benchmark.profile / "preflight.spv")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            await asyncio.wait_for(evaluator.check_executor(session, ConsoleReporter()), timeout=15)

    asyncio.run(check())
    print(json.dumps(evaluator.contract.model_dump(mode="json"), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
