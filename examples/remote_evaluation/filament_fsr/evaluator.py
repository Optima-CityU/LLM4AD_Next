"""Evaluate Filament's mobile single-pass EASU on an Android Mali GPU."""

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import tarfile
import tempfile
from pathlib import Path

from llm4ad.gateway.sdk import (
    EvaluationConfigurationError,
    EvaluationContract,
    EvaluationError,
    EvaluationResult,
    EvaluationUnavailableError,
    GatewayEvaluator,
    Metric,
    MetricType,
    RemoteJob,
    Reporter,
)
from llm4ad.gateway.sdk.check import check_evaluator
from llm4ad.gateway.sdk.runtime import GatewayBinding, GatewayRuntime

HERE = Path(__file__).resolve().parent
SEED = HERE.parent.parent / "applications" / "filament_fsr1_single_pass" / "algorithm"
SOURCE_NAME = "ffx_fsr1_mobile.glsl"
SHADER_NAME = "ffx_fsr1_mobile.fs"
MATERIAL_NAME = "fsr_easu_mobile.mat"
START, END = "    // EVOLVE_START\n", "    // EVOLVE_END\n"
REMOTE_DIRECTORY = re.compile(r"/data/local/tmp/llm4ad-filament\.[a-zA-Z0-9]+")
DEVICE_FIXTURES = "/data/local/tmp/llm4ad-filament-fsr1-fixtures"
FRAME_TIMING = re.compile(r"GPU_FRAME_NS (\d+) (\d+)", re.MULTILINE)
FRAME_COUNT = 120
PAIR_COUNT = 4
QUALITY_PENALTY_CAP = -100.0
RELAXED_FLIP_TOLERANCE = 0.10
CAPTURED_FRAMES = tuple(f"frame-{index:03d}" for index in range(0, FRAME_COUNT, 10))
SCENES = {"DamagedHelmet": "FILAMENT_SCENE", "Fox": "FILAMENT_FOX_SCENE"}


def local_path(variable: str, *, directory: bool = False) -> Path:
    """Resolve a path provided by the gateway operator."""
    value = os.environ.get(variable)
    if not value:
        raise EvaluationConfigurationError(f"Set {variable}")
    path = Path(value).expanduser().resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise EvaluationConfigurationError(f"{variable} is missing or has the wrong file type")
    return path


def run(*arguments: str, timeout: int = 30, candidate: bool = False) -> str:
    """Run one bounded command and retain diagnostics for failed runs."""
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        command = Path(arguments[0]).name
        if len(arguments) > 5 and arguments[3] == "-s":
            command = f"{command} {arguments[5]}"
        raise EvaluationUnavailableError(f"{command} timed out after {timeout}s") from error
    except OSError as error:
        raise EvaluationConfigurationError(str(error)) from error
    if completed.returncode:
        detail = (completed.stdout + "\n" + completed.stderr).strip()
        if candidate and completed.returncode == 1 and not detail.startswith("bwrap:"):
            raise EvaluationError(f"Candidate material failed to compile: {detail[-3000:]}")
        raise EvaluationUnavailableError(detail[-4000:] or f"Process exited with {completed.returncode}")
    return completed.stdout


def adb(serial: str) -> tuple[str, ...]:
    """Address the authorized Android device using the configured ADB server."""
    return (os.environ.get("FILAMENT_ADB", "adb"), "-P", os.environ.get("FILAMENT_ADB_PORT", "5037"), "-s", serial)


def connect_device(serial: str) -> tuple[str, ...]:
    """Reconnect the configured wireless device if it has left the ADB server."""
    device = adb(serial)
    try:
        if run(*device, "get-state", timeout=10).strip() == "device":
            return device
    except EvaluationUnavailableError:
        if ":" not in serial:
            raise
    if ":" in serial:
        run(*device[:3], "connect", serial, timeout=15)
        if run(*device, "get-state", timeout=10).strip() == "device":
            return device
    raise EvaluationUnavailableError("The selected Android device is offline")


def prepare_device_fixtures(device: tuple[str, ...], remote: str, work: Path) -> None:
    """Install versioned immutable fixtures once and reuse them across evaluations."""
    assets = {
        "runner": local_path("FILAMENT_RUNNER"),
        "environment.ktx": local_path("FILAMENT_IBL"),
        **{f"{scene}.glb": local_path(variable) for scene, variable in SCENES.items()},
    }
    manifest = work / "sha256"
    fingerprints = {}
    for name, path in assets.items():
        with path.open("rb") as file:
            fingerprints[name] = hashlib.file_digest(file, "sha256").hexdigest()
    manifest.write_text("".join(f"{digest}  {name}\n" for name, digest in fingerprints.items()), encoding="ascii")
    cached = run(*device, "shell", f"cat {DEVICE_FIXTURES}/sha256 2>/dev/null || true", timeout=10)
    if cached == manifest.read_text(encoding="ascii"):
        return

    payload = work / "fixtures.tgz"
    with tarfile.open(payload, "w:gz", compresslevel=3) as transfer:
        for name, host in assets.items():
            transfer.add(host, arcname=name)
        transfer.add(manifest, arcname="sha256")
    run(*device, "push", str(payload), f"{remote}/fixtures.tgz", timeout=200)
    run(
        *device,
        "shell",
        f"cd {remote} && mkdir assets && tar -xzf fixtures.tgz -C assets && "
        f"cd assets && sha256sum -c sha256 && chmod 700 runner && cd .. && "
        f"rm -rf {DEVICE_FIXTURES} && mv assets {DEVICE_FIXTURES}",
        timeout=25,
    )


def split_source(source: str) -> tuple[str, str, str]:
    """Split the shader into its fixed context and its single editable body."""
    if source.count(START) != 1 or source.count(END) != 1:
        raise EvaluationError("Keep the original EVOLVE_START and EVOLVE_END markers")
    before, rest = source.split(START)
    body, after = rest.split(END)
    return before, body, after


def directives(source: str) -> list[str]:
    """Read preprocessor directives after normalizing line splicing and comments."""
    source = re.sub(r"\\\n", "", source)
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    return [" ".join(line.split()) for line in source.splitlines() if line.lstrip().startswith("#")]


def material_tool(tool: Path, workspace: Path, *arguments: str, candidate: bool = False) -> str:
    """Run the host material tool without network access or host project files."""
    sandbox = shutil.which("bwrap")
    if not sandbox:
        raise EvaluationConfigurationError("Install bubblewrap on the Linux gateway")
    return run(
        sandbox,
        "--unshare-all",
        "--die-with-parent",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(tool),
        "/tool",
        "--bind",
        str(workspace),
        "/work",
        "--chdir",
        "/work",
        "/tool",
        *arguments,
        timeout=30,
        candidate=candidate,
    )


def compile_material(source: Path, workspace: Path) -> bool:
    """Build the candidate against release templates and detect identical Vulkan code."""
    source_dir = local_path("FILAMENT_FSR_SOURCE", directory=True)
    tools = local_path("FILAMENT_HOST_TOOLS", directory=True)
    stock = source_dir / SHADER_NAME
    if not source.is_file() or source.stat().st_size > 64 * 1024:
        raise EvaluationError(f"Provide {SOURCE_NAME} in the candidate worktree (maximum 64 KiB)")
    try:
        before, body, after = split_source(source.read_text(encoding="utf-8"))
    except UnicodeError as error:
        raise EvaluationError("The candidate shader must be UTF-8") from error
    prefix, original, suffix = split_source((SEED / SOURCE_NAME).read_text(encoding="utf-8"))
    if stock.read_text(encoding="utf-8") != prefix + original + suffix:
        raise EvaluationConfigurationError("The bundled seed does not match the official Filament source")
    if (before, after) != (prefix, suffix) or directives(body) != directives(original):
        raise EvaluationError("Only edit the marked function body and preserve the original preprocessor directives")

    for name in (MATERIAL_NAME, "ffx_a.h", "ffx_fsr1.h"):
        shutil.copyfile(source_dir / name, workspace / name)
    (workspace / SHADER_NAME).write_text(before + body + after, encoding="utf-8")
    material_tool(
        tools / "matc",
        workspace,
        "-a",
        "opengl",
        "-a",
        "vulkan",
        "-p",
        "mobile",
        "-o",
        "candidate.filamat",
        MATERIAL_NAME,
        candidate=True,
    )
    shutil.copyfile(local_path("FILAMENT_STOCK_MATERIAL"), workspace / "stock.filamat")
    compiled = workspace / "candidate.filamat"
    if compiled.read_bytes() == (workspace / "stock.filamat").read_bytes():
        return True
    # The pinned material has opaque and translucent vertex/fragment variants.
    return all(
        material_tool(tools / "matinfo", workspace, "-s", str(index), "candidate.filamat")
        == material_tool(tools / "matinfo", workspace, "-s", str(index), "stock.filamat")
        for index in range(4)
    )


def gpu_timings(log: str, scene: str, variant: str) -> list[float]:
    """Read completed GPU frames and verify the intended EASU package was used."""
    samples = FRAME_TIMING.findall(log)
    expected = {"BACKEND Vulkan", "MODE fsr", f"SCENE {scene}", f"EASU_PACKAGE {variant}"}
    if not expected.issubset(log.splitlines()):
        raise EvaluationUnavailableError(f"Filament did not report Vulkan mobile EASU for {scene} ({variant})")
    if [int(frame) for frame, _ in samples] != list(range(32, 32 + FRAME_COUNT)):
        raise EvaluationUnavailableError(f"Expected {FRAME_COUNT} ordered GPU frame timings")
    if any(int(ns) <= 0 for _, ns in samples):
        raise EvaluationUnavailableError("Filament returned an invalid GPU frame duration")
    return [int(ns) / 1_000_000 for _, ns in samples]


def timing_summary(frames: list[float]) -> dict[str, float]:
    """Report the median and nearest-rank P95 GPU frame time in milliseconds."""
    ordered = sorted(frames)
    return {
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
    }


def summarize(evidence: Path) -> dict:
    """Summarize paired GPU timings and ImageDiff results per scene."""
    scenes = {}
    for scene in SCENES:
        rounds = []
        for pair in range(1, PAIR_COUNT + 1):
            timings = {
                variant: timing_summary(
                    gpu_timings((evidence / "timings" / f"{scene}-{pair}-{variant}.txt").read_text(), scene, variant)
                )
                for variant in ("stock", "candidate")
            }
            stock, candidate = timings["stock"]["p50_ms"], timings["candidate"]["p50_ms"]
            rounds.append({**timings, "improvement_pct": (stock - candidate) / stock * 100})
        scenes[scene] = {
            "pairs": rounds,
            "improvement_pct": statistics.median(pair["improvement_pct"] for pair in rounds),
        }
        frames = []
        for name in CAPTURED_FRAMES:
            diff = json.loads((evidence / "quality" / scene / f"{name}.json").read_text())
            frames.append({"frame": name, **diff})
        scenes[scene].update(frames=frames, frames_passed=sum(frame["passed"] for frame in frames))
    return scenes


def summarize_flip(evidence: Path, *, relaxed: bool = False) -> dict:
    """Compare scene means and worst frames against the same native references."""
    tolerance = RELAXED_FLIP_TOLERANCE if relaxed else 0.0
    scenes = {}
    for scene in SCENES:
        frames = []
        for name in CAPTURED_FRAMES:
            comparison = json.loads((evidence / "flip" / scene / f"{name}.json").read_text())
            stock, candidate = comparison["stock_mean_error"], comparison["candidate_mean_error"]
            if not (0 <= stock <= 1 and 0 <= candidate <= 1):
                raise EvaluationUnavailableError(f"Invalid LDR-FLIP result for {scene}/{name}")
            frames.append({"frame": name, **comparison, "improvement": stock - candidate})
        means = {variant: statistics.mean(row[f"{variant}_mean_error"] for row in frames) for variant in ("stock", "candidate")}
        worst = {variant: max(frames, key=lambda row: row[f"{variant}_mean_error"]) for variant in ("stock", "candidate")}
        worst_errors = {variant: frame[f"{variant}_mean_error"] for variant, frame in worst.items()}
        flat_frames = [row["frame"] for row in frames if row["candidate_flat_color"]] if relaxed else []
        error_increase = max(means["candidate"] - means["stock"], worst_errors["candidate"] - worst_errors["stock"])
        scenes[scene] = {
            "stock_mean_error": means["stock"],
            "candidate_mean_error": means["candidate"],
            "improvement": means["stock"] - means["candidate"],
            "stock_worst_frame": worst["stock"]["frame"],
            "candidate_worst_frame": worst["candidate"]["frame"],
            "stock_worst_frame_error": worst_errors["stock"],
            "candidate_worst_frame_error": worst_errors["candidate"],
            "error_increase": error_increase,
            "flat_frames": flat_frames,
            "passed": error_increase <= tolerance and not flat_frames,
            "frames": frames,
        }
    passed = all(result["passed"] for result in scenes.values())
    improvement = statistics.mean(result["improvement"] for result in scenes.values())
    if not passed:
        status = "regressed"
    elif any(result["error_increase"] > 0 for result in scenes.values()):
        status = "within_tolerance"
    else:
        status = "improved" if improvement > 0 else "matched"
    return {
        "method": "NVIDIA LDR-FLIP 1.7",
        "reference": "native 1920x1080",
        "pixels_per_degree": 67,
        "allowed_error_increase": tolerance,
        "mean_improvement": improvement,
        "status": status,
        "passed": passed,
        "scenes": scenes,
    }


def score_candidate(improvement: float, flip: dict | None, frames_passed: int, frames_total: int) -> tuple[float, dict]:
    """Apply a finite quality penalty while retaining the raw performance measure."""
    reasons = []
    regression = 0.0
    if flip is not None:
        tolerance = flip["allowed_error_increase"]
        for scene, values in flip["scenes"].items():
            if values["flat_frames"]:
                reasons.append(f"{scene} rendered {len(values['flat_frames'])} near-uniform color frames.")
            for key, label in (("mean_error", "mean"), ("worst_frame_error", "worst-frame")):
                stock, candidate = values[f"stock_{key}"], values[f"candidate_{key}"]
                increase = candidate - stock
                if increase > tolerance:
                    regression = max(regression, increase - tolerance)
                    reasons.append(
                        f"{scene} {label} FLIP rose from {stock:.6f} to {candidate:.6f} (allowed increase {tolerance:.2f})."
                    )
    elif frames_passed < frames_total:
        regression = (frames_total - frames_passed) / frames_total
        reasons.append(f"{frames_total - frames_passed} of {frames_total} frames differ from the official baseline.")

    if reasons:
        score = QUALITY_PENALTY_CAP * (1 + regression)
    elif improvement < 0:
        # Preserve the order of slow but acceptable candidates above the penalty band.
        score = max(math.nextafter(QUALITY_PENALTY_CAP, 0.0), improvement / (1 + improvement / QUALITY_PENALTY_CAP))
    else:
        score = improvement
    return score, {
        "status": "beyond_reference" if reasons else "within_reference",
        "penalized": bool(reasons),
        "penalty_score": QUALITY_PENALTY_CAP,
        "regression": regression,
        "reasons": reasons,
    }


class FilamentRuntime(GatewayRuntime):
    """Share one phone slot between the quality and performance contracts."""

    max_concurrency = 1

    def bindings(self) -> list[GatewayBinding]:
        """Keep the existing registration and queue when adding the performance contract."""
        primary = super().bindings()[0]
        filename, evaluator_name = primary.evaluator.rsplit(":", 1)
        if evaluator_name != "Evaluator":
            return [primary]
        primary.resource_key = primary.key
        return [
            primary,
            GatewayBinding(
                "performance",
                f"{primary.name} · Relaxed Quality",
                f"{filename}:PerformanceEvaluator",
                primary.directory / "performance",
                primary.capacity,
                resource_key=primary.resource_key,
            ),
        ]

    async def check(self, gateway) -> None:
        """Exercise the complete SDK worker and device path before registration."""
        relaxed = gateway.adapter.relaxed_quality
        root = gateway.root if relaxed else self.options["root"]
        parameters = {} if relaxed else {"quality_mode": os.environ.get("FILAMENT_STARTUP_QUALITY_MODE", "pixel_exact")}
        report = await asyncio.to_thread(
            check_evaluator,
            gateway.spec,
            SEED,
            root / "startup-check",
            600,
            parameters=parameters,
        )
        (root / "startup-check.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not report["ok"]:
            raise RuntimeError(f"Filament startup check failed: {report['result'].get('error_message')}")
        if not report["result"]["metadata"]["quality_passed"]:
            raise RuntimeError("Filament startup check rejected the original shader")
        print(f"{gateway.name} startup check passed on Android Vulkan", flush=True)


class Evaluator(GatewayEvaluator):
    """Compare one candidate's single-pass shader with the stock shader."""

    relaxed_quality = False
    contract = EvaluationContract(
        evaluator_id="filament-fsr1-android",
        version="1",
        data_version="filament-v1.75.0-mali-helmet-orbit-fox-walk",
        metrics=[
            Metric(name="improvement_pct", type=MetricType.MAXIMIZE),
            Metric(name="frames_passed", type=MetricType.MAXIMIZE),
            Metric(name="worst_frame_failing_pixels", type=MetricType.MINIMIZE),
        ],
    )

    @classmethod
    def create_runtime(cls, environment: dict, evaluator: str) -> GatewayRuntime:
        """Use one device slot and verify the Android path before registration."""
        return FilamentRuntime(environment, evaluator)

    async def cancel(self, job: RemoteJob, saved_state: dict) -> bool:
        """Remove the attempt's temporary files from the device."""
        serial, remote = saved_state.get("serial"), saved_state.get("remote", "")
        if serial and REMOTE_DIRECTORY.fullmatch(remote):
            run(*adb(serial), "shell", "rm", "-rf", remote, timeout=10)
        return True

    async def evaluate(self, job: RemoteJob, reporter: Reporter) -> EvaluationResult:
        """Run matched GPU timings and compare the rendered frames."""
        quality_mode = "flip" if self.relaxed_quality else job.parameters.get("quality_mode", "pixel_exact")
        if quality_mode not in ("pixel_exact", "flip"):
            raise EvaluationConfigurationError("quality_mode must be pixel_exact or flip")
        if quality_mode == "flip":
            try:
                from comparison_sheet import write_comparison_sheet
            except ModuleNotFoundError as error:
                if error.name != "PIL":
                    raise
                raise EvaluationConfigurationError("Install Pillow from the Filament evaluator requirements.txt") from error
        serial = os.environ.get("FILAMENT_DEVICE")
        if not serial:
            raise EvaluationConfigurationError("Set FILAMENT_DEVICE to an authorized ADB device")
        device = connect_device(serial)
        vulkan = run(*device, "shell", "cmd", "gpu", "vkjson", timeout=10)
        gpu = re.search(r'"deviceName"\s*:\s*"(Mali[^"]*)"', vulkan)
        if not gpu:
            raise EvaluationConfigurationError("This contract requires a Mali Vulkan GPU")

        output = Path(job.artifact_dir)
        output.mkdir(parents=True, exist_ok=True)
        reporter.progress("Compiling the candidate with Filament matc", completed=0, total=3)
        with tempfile.TemporaryDirectory(prefix="filament-", dir=output) as directory:
            work = Path(directory)
            identical = compile_material(Path(job.project_root) / SOURCE_NAME, work)
            remote = run(*device, "shell", "mktemp", "-d", "/data/local/tmp/llm4ad-filament.XXXXXXXX", timeout=10).strip()
            if not REMOTE_DIRECTORY.fullmatch(remote):
                raise EvaluationUnavailableError("ADB did not create the expected temporary directory")
            state = {"serial": serial, "remote": remote}
            reporter.save_state(state)
            try:
                prepare_device_fixtures(device, remote, work)
                run(*device, "push", str(work / "candidate.filamat"), f"{remote}/candidate.filamat", timeout=20)
                run(*device, "push", str(HERE / "measure.sh"), f"{remote}/measure.sh", timeout=20)
                reporter.progress("Running paired Vulkan timings and frame comparisons", completed=1, total=3)
                wake = "wake-screen" if os.environ.get("FILAMENT_WAKE_SCREEN") == "1" else "keep-display"
                run(
                    *device,
                    "shell",
                    "sh",
                    f"{remote}/measure.sh",
                    remote,
                    DEVICE_FIXTURES,
                    wake,
                    quality_mode,
                    timeout=270 if quality_mode == "flip" else 150,
                )
                archive = work / "evidence.tgz"
                run(*device, "pull", f"{remote}/evidence.tgz", str(archive), timeout=90)
                with tarfile.open(archive) as evidence_tar:
                    evidence_tar.extractall(work, filter="data")
            finally:
                if await self.cancel(job, state):
                    reporter.save_state({})

            evidence = work / "evidence"
            scenes = summarize(evidence)
            measured = statistics.mean(scene["improvement_pct"] for scene in scenes.values())
            improvement = 0.0 if identical else measured
            metadata = {
                "backend": "Android Vulkan",
                "gpu": gpu.group(1),
                "soc": (evidence / "soc.txt").read_text().strip(),
                "identical_vulkan_shader": identical,
                "measured_improvement_pct": measured,
                "quality_mode": "relaxed_flip" if self.relaxed_quality else quality_mode,
                "scenes": {
                    name: {key: value for key, value in scene.items() if key != "frames"} for name, scene in scenes.items()
                },
            }
            trace = output / "gpu-frame-timings.tgz"
            with tarfile.open(trace, "w:gz") as timings:
                timings.add(evidence / "timings", arcname="timings")
            reporter.artifact(trace.name, trace, "application/gzip", description="Raw Filament GPU frame durations")
            passed = sum(scene["frames_passed"] for scene in scenes.values())
            total = len(CAPTURED_FRAMES) * len(SCENES)
            flip = summarize_flip(evidence, relaxed=self.relaxed_quality) if quality_mode == "flip" else None
            metrics = {"improvement_pct": improvement}
            if not self.relaxed_quality:
                metrics.update(
                    frames_passed=passed,
                    worst_frame_failing_pixels=max(
                        frame["failingPixelCount"] for scene in scenes.values() for frame in scene["frames"]
                    ),
                )
            score, quality = score_candidate(improvement, flip, passed, total)
            metadata.update(frames_compared=total, quality_passed=not quality["penalized"], quality_assessment=quality)
            report = output / "imagediff.json"
            report.write_text(json.dumps({"passed": passed == total, "scenes": scenes}, indent=2) + "\n", encoding="utf-8")
            reporter.artifact(report.name, report, "application/json", description="Official ImageDiff and paired GPU timings")
            monitor_metrics = {}
            if flip:
                monitor_metrics = {
                    "flip_baseline_mean": statistics.mean(scene["stock_mean_error"] for scene in flip["scenes"].values()),
                    "flip_candidate_mean": statistics.mean(scene["candidate_mean_error"] for scene in flip["scenes"].values()),
                    "flip_improvement": flip["mean_improvement"],
                }
                flip_report = output / "flip.json"
                flip_report.write_text(json.dumps(flip, indent=2) + "\n", encoding="utf-8")
                reporter.artifact(
                    flip_report.name,
                    flip_report,
                    "application/json",
                    description="Frame-by-frame LDR-FLIP comparison with native references",
                )
                metadata["flip"] = {
                    **flip,
                    "scenes": {
                        name: {key: value for key, value in summary.items() if key != "frames"}
                        for name, summary in flip["scenes"].items()
                    },
                }
                comparison = output / "FLIP-comparison.jpg"
                write_comparison_sheet(evidence / "previews", flip["scenes"], comparison)
                reporter.artifact(
                    comparison.name,
                    comparison,
                    "image/jpeg",
                    description="Native, official and candidate renders with the same focus crop and FLIP heatmaps",
                )
            else:
                for image in sorted((evidence / "previews").glob("*.png")):
                    preview = output / image.name
                    shutil.move(image, preview)
                    reporter.artifact(preview.name, preview, "image/png", description="Matching timeline frame 060")
            summary = (
                f"Native-reference LDR-FLIP: {flip['status']}" if flip else f"Filament ImageDiff passed {passed}/{total} frames"
            )
            if quality["penalized"]:
                summary += f" (quality penalty, score {score:.3f})"
            reporter.progress(summary, completed=3, total=3)
            return EvaluationResult(
                score=score,
                metrics=metrics,
                monitor_metrics=monitor_metrics,
                metadata=metadata,
                evolution_feedback={"quality_assessment": quality, "metrics": metrics} if quality["penalized"] else {},
            )


class PerformanceEvaluator(Evaluator):
    """Rank candidates by GPU frame P50 improvement within a loose quality limit."""

    relaxed_quality = True
    contract = EvaluationContract(
        evaluator_id="filament-fsr1-android-performance",
        version=Evaluator.contract.version,
        data_version=Evaluator.contract.data_version,
        metrics=[Evaluator.contract.metrics[0]],
    )
