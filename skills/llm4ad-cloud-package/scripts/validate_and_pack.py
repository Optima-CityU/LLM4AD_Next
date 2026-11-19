#!/usr/bin/env python3
"""Validate an extracted cloud-task snapshot and deliver it as a ZIP."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from importlib.metadata import distributions, version
from pathlib import Path, PureWindowsPath
from typing import Any

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "__MACOSX",
}

EXCLUDED_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def package_files(root: Path) -> list[Path]:
    """Collect portable project files without environments, secrets, or caches.

    Args:
        root: Directory containing config.yaml.

    Returns:
        Sorted input files relative to root.
    """
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.name in EXCLUDED_FILES or path.name.startswith("._"):
            continue
        if relative.parts[0] == "runs":
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            continue
        if path.suffix in {".pyc", ".pyo"} or relative.as_posix() == "validation.json":
            continue
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Include a real file instead of a link: {relative}")
        if path.is_file():
            files.append(relative)
    if Path("config.yaml") not in files:
        raise ValueError("The project root must contain config.yaml")
    return files


def contained_path(root: Path, value: str) -> Path:
    """Resolve a portable path without escaping the project.

    Args:
        root: Allowed project directory.
        value: Relative configuration path.

    Returns:
        Resolved path inside root.
    """
    path = Path(value)
    if not value or "\\" in value or path.is_absolute() or PureWindowsPath(value).drive:
        raise ValueError(f"Use a package-relative path: {value!r}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes the project: {value!r}")
    return resolved


def select_build_script(build: Any, host_system: str) -> str:
    """Select an explicit build for the actual local verification platform.

    Args:
        build: Optional platform BuildConfig.
        host_system: Host name returned by platform.system().

    Returns:
        Local shell command, or an empty string when no build is needed.
    """
    if build is None:
        return ""
    host = host_system.lower()
    script = build.script if isinstance(build.script, str) else build.script.get(host)
    if not script or not script.strip():
        raise ValueError(f"planner.build.script has no build for {host}; add it or verify in the intended target environment")
    return script


def dataset_files(root: Path, dataset: Any) -> list[Path]:
    """Discover the configured instances and require included data.

    Args:
        root: Extracted project directory.
        dataset: Platform DatasetConfig.

    Returns:
        Nonempty instance list, preserving explicit file order and repetitions.
    """
    if dataset.mode == "files":
        files = [contained_path(root, value) for value in dataset.files or []]
    elif dataset.mode == "directory":
        directory = contained_path(root, dataset.path or "")
        files = list(directory.rglob("*") if dataset.recursive else directory.glob("*"))
        files = [path for path in files if path.is_file()]
    else:
        pattern = dataset.pattern or ""
        contained_path(root, pattern)
        directory = contained_path(root, dataset.path) if dataset.path else root
        files = list(directory.glob(pattern))
    if dataset.mode != "files":
        files = sorted(files)
    if not files:
        raise ValueError("Include at least one instance matching evaluator.dataset")
    for path in files:
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Dataset file is unavailable inside the package: {path}")
    return files


def check_requirements(root: Path) -> None:
    """Reject platform distribution names in auto-installed task requirements.

    Args:
        root: Extracted project directory.
    """
    requirements = root / "requirements.txt"
    if not requirements.exists():
        return
    for line in requirements.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match and re.sub(r"[-_.]+", "-", match[1]).lower() in {"llm4ad", "llm4ad-next"}:
            raise ValueError(
                "Install llm4ad-next in the verification environment separately; "
                "task requirements must not reinstall llm4ad or llm4ad-next"
            )


async def evaluate_snapshot(root: Path, config: Any, instances: list[Path]) -> dict[str, Any]:
    """Run baseline and empty-candidate checks through the platform dispatcher.

    Args:
        root: Extracted project root.
        config: Validated AppConfig.
        instances: Included dataset files.

    Returns:
        Actual baseline outcomes and candidate dependency check.
    """
    from llm4ad.config import DatasetConfig
    from llm4ad.evaluator.dispatcher import EvaluationDispatcher
    from llm4ad.infra.version_control import WorktreeInfo
    from llm4ad.planner.base import Algorithm, InsightType

    source = contained_path(root, config.version_control.local_path)
    with tempfile.TemporaryDirectory(prefix="llm4ad-cloud-candidates-") as temporary:
        workspace = Path(temporary)
        candidate = workspace / "candidate"
        shutil.copytree(source, candidate, ignore=shutil.ignore_patterns(*EXCLUDED_DIRS))
        build = config.planner.build
        script = select_build_script(build, platform.system())
        if script:
            result = subprocess.run(
                script,
                shell=True,
                cwd=candidate,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=build.timeout,
            )
            if result.returncode:
                raise ValueError(f"Candidate build failed:\n{result.stdout}\n{result.stderr}")

        async def dispatch(path: Path, instance: Path) -> tuple[Any, dict[str, Any]]:
            evaluator_config = config.evaluator.model_copy(
                update={
                    "dataset": DatasetConfig(mode="files", files=[str(instance)]),
                    "parallel": False,
                }
            )
            dispatcher = EvaluationDispatcher(
                evaluator_config,
                behavior_storage="none",
                config_dir=str(root),
            )
            definitions = dispatcher.get_metric_definitions()
            algorithm = Algorithm(
                insight_type=InsightType.INITIAL,
                name="cloud-package-baseline",
                description="Local verification of the packaged candidate",
                worktree=WorktreeInfo(
                    name="local-validation",
                    path=str(path),
                    branch="validation",
                    commit_hash="uncommitted-snapshot",
                    created_at=0,
                    last_used_at=0,
                ),
            )
            results = await dispatcher.dispatch_batch([algorithm])
            return results[0], definitions

        samples = []
        for instance in instances:
            result, definitions = await dispatch(candidate, instance)
            relative = instance.relative_to(root).as_posix()
            if not result.success or result.error_message:
                raise ValueError(f"Baseline failed on {relative}: {result.error_message}")
            configured = getattr(config.evaluator, "metrics", None) or list(definitions)
            expected = [metric if isinstance(metric, str) else metric.name for metric in configured]
            if not expected or set(expected) - definitions.keys():
                raise ValueError(f"Configured metrics are not declared by the evaluator: {expected}")
            missing = set(expected) - result.metrics.keys()
            if missing or not all(math.isfinite(value) for value in [result.score, *result.metrics.values()]):
                raise ValueError(f"Missing/nonfinite metrics on {relative}: {sorted(missing)}")
            samples.append({"path": relative, "score": result.score, "metrics": result.metrics})

        empty = workspace / "empty-candidate"
        empty.mkdir()
        result, _ = await dispatch(empty, instances[0])
        if result.success:
            raise ValueError(
                "Evaluator accepted an empty candidate. It may be evaluating a fixed "
                "original file instead of cfg.project_root; fix its candidate lookup."
            )
    return {"samples": samples, "empty_candidate_rejected": True, "build_executed": bool(script)}


def evaluate_gateway(source: Path, evaluator: Any, spec: str) -> dict[str, Any]:
    """Run the packaged baseline through the SDK offline in the gateway environment.

    Args:
        source: Candidate directory from the extracted ZIP.
        evaluator: RemoteEvaluatorConfig from the same snapshot.
        spec: Local gateway evaluator outside the project.

    Returns:
        Smoke-test summary without local paths, job identifiers, or artifacts.
    """
    from llm4ad.gateway.sdk.check import check_evaluator

    with tempfile.TemporaryDirectory(prefix="llm4ad-cloud-gateway-") as temporary:
        check = check_evaluator(
            spec,
            source,
            Path(temporary),
            evaluator.timeout,
            candidate_paths=evaluator.candidate_paths,
            parameters=evaluator.parameters,
        )
    result = check["result"]
    if not check["ok"] or result.get("error_message"):
        detail = result.get("error_message") or result.get("message") or check["state"]
        raise ValueError(f"Offline gateway evaluation failed: {detail}")
    score = result["score"]
    metrics = result["metrics"]
    if not all(math.isfinite(value) for value in [score, *metrics.values()]):
        raise ValueError("Offline gateway evaluation returned nonfinite values")
    return {
        "verification_scope": "offline_gateway_smoke_test",
        "samples": [{"path": ".", "score": score, "metrics": metrics}],
        "gateway_evaluator": {
            field: check["contract"][field] for field in ("evaluator_id", "version", "data_version")
        },
    }


def check_snapshot(root: Path, gateway_evaluator: str | None = None) -> dict[str, Any]:
    """Validate configuration, code, and actual evaluation in an isolated snapshot.

    Args:
        root: Isolated single-root task directory.
        gateway_evaluator: Local SDK evaluator for a remote task package.

    Returns:
        Machine-readable verification results.
    """
    import yaml

    from llm4ad.config import AppConfig
    from llm4ad.infra.repo_analyzer import BaseRepositoryAnalyzer
    from llm4ad.orchestrator import BaseOrchestrator
    from llm4ad.planner import BasePlanner
    from llm4ad.planner.sampler import BaseSampler

    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a mapping")
    for field in ("project_name", "evolution", "evaluator", "version_control"):
        if field not in data:
            raise ValueError(f"Missing required task configuration: {field}")
    config = AppConfig.from_dict(data)
    if config.providers:
        raise ValueError("Cloud packages use providers: []; bind the model in the web UI")
    if config.evaluator.type == "remote":
        if not gateway_evaluator:
            raise ValueError("Remote packages need --gateway-evaluator FILE.py:Class for offline verification")
        target = data["evaluator"]
        if "gateway_id" not in target or target["gateway_id"] is not None or target.get("gateway_ids"):
            raise ValueError("Use evaluator.gateway_id: null in the package; bind the gateway after import")
        if target.get("contract"):
            raise ValueError("Omit evaluator.contract in the package; the platform fills it after gateway selection")
    elif gateway_evaluator:
        raise ValueError("--gateway-evaluator requires evaluator.type: remote")
    if config.evolution.type not in BaseOrchestrator.list():
        raise ValueError(f"Unregistered platform method: {config.evolution.type}")
    expected = getattr(config.evolution, "planner_type", "llm_evolution")
    if config.planner.type != expected:
        raise ValueError(f"{config.evolution.type} requires planner.type: {expected}")
    BasePlanner.get(config.planner.type)
    if config.planner.type == "llm_evolution":
        if config.evolution.type == "dyca":
            importlib.import_module("llm4ad.planner.sampler.dyca_samplers")
        names = {sampler.name for sampler in config.planner.samplers}
        if "init_sampler" not in names:
            raise ValueError("planner.samplers must include init_sampler for initial generation")
        if config.evolution.type in {"island_ga", "diverse_island_ga"} and not names.intersection(
            {"mutation_sampler", "crossover_sampler", "multimodal_mutation_sampler", "multimodal_crossover_sampler"}
        ):
            raise ValueError("Island search requires a parent-based mutation or crossover sampler")
        for name in names:
            BaseSampler.get(name)
    if not config.version_control.enabled or config.version_control.type != "git_worktree":
        raise ValueError("Enable git_worktree version_control to supply candidate worktrees")
    source = contained_path(root, config.version_control.local_path)
    if not source.is_dir():
        raise ValueError("version_control.local_path must identify an included directory")
    if config.evaluator.type == "custom":
        module_path, _, class_name = config.evaluator.module.rpartition(":")
        if not module_path.endswith(".py") or not class_name:
            raise ValueError("Use a packaged evaluator module such as evaluator.py:MyEvaluator")
        if not contained_path(root, module_path).is_file():
            raise ValueError("The configured evaluator module is missing")
    if config.repo_analyzer is None:
        raise ValueError("Configure repo_analyzer to discover the candidate's EVOLVE blocks")
    if config.repo_analyzer.get("type", "evolve_detector") == "evolve_detector":
        for field in ("context_lines_before", "context_lines_after"):
            value = config.repo_analyzer.get(field)
            if type(value) is not int or value < 0:
                raise ValueError(f"Explicitly size repo_analyzer.{field} for the candidate code")
    if config.coder.type == "custom":
        for field in ("context_max_tokens", "max_gen_tokens"):
            if field not in data.get("coder", {}):
                raise ValueError(f"Explicitly size coder.{field} for the candidate code and selected model")
    analyzer = BaseRepositoryAnalyzer.create(
        config.repo_analyzer.get("type", "evolve_detector"),
        config=config.repo_analyzer,
    )
    blocks = analyzer.analyze(source).evolvable_blocks
    if not blocks:
        raise ValueError("No EVOLVE block matches repo_analyzer under version_control.local_path")
    for path in root.rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
    check_requirements(root)
    if config.evaluator.type == "remote":
        checks = evaluate_gateway(source, config.evaluator, gateway_evaluator)
    else:
        instances = dataset_files(root, config.evaluator.dataset)
        checks = asyncio.run(evaluate_snapshot(root, config, instances))
        checks["verification_scope"] = "baseline_in_copied_candidate_directory"
    return {
        "schema_version": 1,
        "status": "passed",
        "validated_at": datetime.now(UTC).isoformat(),
        "project_name": config.project_name,
        "config": "config.yaml",
        "evolution_type": config.evolution.type,
        "planner_type": config.planner.type,
        "environment": {
            "llm4ad_next": version("llm4ad-next"),
            "python": platform.python_version(),
            "os": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "installed_packages": dict(
                sorted((item.metadata["Name"], item.version) for item in distributions() if item.metadata["Name"])
            ),
        },
        "full_evolution_run": False,
        "evolve_blocks": len(blocks),
        **checks,
    }


def _verify_snapshot(
    snapshot: Path, files: list[Path], timeout: float, gateway_evaluator: str | None = None
) -> dict[str, Any]:
    """Run evaluator checks against one isolated directory snapshot."""
    hashes = {relative.as_posix(): hashlib.sha256((snapshot / relative).read_bytes()).hexdigest() for relative in files}
    with tempfile.TemporaryDirectory(prefix="llm4ad-project-check-") as temporary:
        workspace = Path(temporary)
        worker_report = workspace / "worker.json"
        started = time.monotonic()
        command = [
            sys.executable,
            "-X",
            "utf8",
            str(Path(__file__).resolve()),
            "--check-root",
            str(snapshot),
            "--worker-report",
            str(worker_report),
        ]
        if gateway_evaluator:
            command.extend(["--gateway-evaluator", gateway_evaluator])
        result = subprocess.run(
            command,
            cwd=None if gateway_evaluator else snapshot,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode:
            raise ValueError(f"Package validation failed:\n{result.stdout}\n{result.stderr}")
        report = json.loads(worker_report.read_text(encoding="utf-8"))
        report["files_sha256"] = hashes
        if report["verification_scope"] == "offline_gateway_smoke_test":
            return report
        tests = snapshot / "test_evaluator.py"
        if not tests.is_file():
            raise ValueError("Include test_evaluator.py with task-specific scoring and failure checks")
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("Verification timeout exhausted before task-specific evaluator tests")
        test_result = subprocess.run(
            [sys.executable, "-X", "utf8", str(tests)],
            cwd=snapshot,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=remaining,
        )
        if test_result.returncode:
            raise ValueError(f"Task-specific evaluator tests failed:\n{test_result.stdout}\n{test_result.stderr}")
        report["project_tests"] = {"script": "test_evaluator.py", "exit_code": 0}
    return report


def verify_project(root: Path, timeout: float) -> dict[str, Any]:
    """Verify an in-platform project directory without creating an archive.

    Args:
        root: Current project directory containing config.yaml.
        timeout: Time available for baseline and evaluator tests.

    Returns:
        Actual execution results and digests of the verified inputs.
    """
    files = package_files(root)
    with tempfile.TemporaryDirectory(prefix="llm4ad-project-snapshot-") as temporary:
        snapshot = Path(temporary) / root.name
        for relative in files:
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, destination)
        return _verify_snapshot(snapshot, files, timeout)


def pack(root: Path, output: Path, timeout: float, gateway_evaluator: str | None = None) -> dict[str, Any]:
    """Package a snapshot, verify its extracted contents, and deliver the ZIP.

    Args:
        root: Source task directory.
        output: New destination ZIP outside the task directory.
        timeout: Maximum seconds for the validation worker.
        gateway_evaluator: Local SDK evaluator file/class or installed module/class.

    Returns:
        Validation report embedded in the delivered ZIP.
    """
    report_path = output.with_suffix(".validation.json")
    if output.suffix.lower() != ".zip" or output.is_relative_to(root):
        raise ValueError("Choose a .zip output outside the source task directory")
    if output.exists() or report_path.exists():
        raise ValueError("Output already exists; choose a new archive/report filename")
    if gateway_evaluator:
        from llm4ad.gateway.sdk.contracts import normalize_spec

        gateway_evaluator = normalize_spec(gateway_evaluator)
        filename, _, _ = gateway_evaluator.rpartition(":")
        if filename.endswith(".py") and Path(filename).is_relative_to(root):
            raise ValueError("Keep the gateway evaluator and its local files outside the task directory")
    files = package_files(root)
    with tempfile.TemporaryDirectory(prefix="llm4ad-cloud-package-") as temporary:
        workspace = Path(temporary)
        archive_path = workspace / "task.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in files:
                archive.write(root / relative, f"{root.name}/{relative.as_posix()}")
        extracted = workspace / "extracted"
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extracted)
        snapshot = extracted / root.name
        report = _verify_snapshot(snapshot, files, timeout, gateway_evaluator)
        report_text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with zipfile.ZipFile(archive_path, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{root.name}/validation.json", report_text)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive_path, output)
        report_path.write_text(report_text, encoding="utf-8")
    return report


def main() -> int:
    """Run the packaging command or its isolated validation worker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--verify-only", action="store_true", help="Validate a project directory and write only a JSON report")
    parser.add_argument("--gateway-evaluator", help="Local SDK FILE.py:Class or package.module:Class for offline remote checks")
    parser.add_argument("--check-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.check_root and args.worker_report:
            report = check_snapshot(args.check_root.resolve(), args.gateway_evaluator)
            args.worker_report.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
        else:
            if not args.project or not args.output or args.timeout <= 0:
                parser.error("project, --output, and a positive --timeout are required")
            if args.verify_only:
                if args.gateway_evaluator:
                    parser.error("--gateway-evaluator is only supported when creating a ZIP")
                report = verify_project(args.project.resolve(), args.timeout)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                print(f"Validated {len(report['samples'])} instances; report: {args.output.resolve()}")
            else:
                report = pack(args.project.resolve(), args.output.resolve(), args.timeout, args.gateway_evaluator)
                if report["verification_scope"] == "offline_gateway_smoke_test":
                    print("Offline gateway smoke test passed")
                else:
                    print(f"Validated {len(report['samples'])} instances")
                print(f"ZIP: {args.output.resolve()}")
                print(f"Report: {args.output.resolve().with_suffix('.validation.json')}")
        return 0
    except Exception as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
