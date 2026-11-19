"""Run paired static A/B evaluations under one device lease."""

import hashlib
import math
import os
import tempfile
from io import BytesIO
from pathlib import Path
from statistics import mean

from ..evidence import artifact_bytes
from ..scoring import _finite
from ..static_dataset import DATASETS, SAMPLES_PER_DATASET
from .storage import read_json, write_json

REPORT_SCHEMA = "hgamey/static-dataset-measurement@1"
COLLAGE_NAMES = {f"collages/{name}.png" for name in DATASETS}
ROLES = ("reference", "baseline", "candidate")
STATIC_QUALITY_SCORE_CAP = -100.0


def pair_request(request: dict, pair: dict) -> dict:
    """Build one native evaluation using both frozen input paths."""
    payload = request["payload"].copy()
    del payload["static_pairs"]
    payload.update(quality_input=pair["lr"]["path"], quality_reference=pair["hr"]["path"])
    return request | {"payload": payload}


def frame_directory(folder: Path, index: int) -> Path:
    """Resolve a frame's native evidence and restoration directory owner."""
    if type(index) is not int or not 0 <= index < len(DATASETS) * SAMPLES_PER_DATASET:
        raise ValueError("Invalid static frame index")
    return folder / "evidence" / "static-frames" / f"{index:02d}"


def recovery_directory(folder: Path) -> Path:
    """Find the last launched native operation for interrupted device recovery."""
    active = folder / "static-active.json"
    if active.is_file():
        return frame_directory(folder, read_json(active)["index"]) / "evidence"
    return folder / "evidence"


def _measurements(report: dict, rounds: int) -> dict:
    """Keep complete Vulkan measurements for both arms, independent of image export."""
    arms = report["conditions_observed"]["arms"]
    result = {}
    for arm, role in (("A", "baseline"), ("B", "candidate")):
        samples = [sample for sample in arms if sample.get("arm") == arm]
        if len(samples) != rounds or any(sample.get("runner_exit_status") != 0 for sample in samples):
            raise ValueError(f"Incomplete {role} A/B measurements")
        metrics = {}
        for key in ("psnr_db", "ssim", "operator_latency_ms"):
            values = [sample.get(key) for sample in samples]
            if any(not _finite(value) for value in values):
                raise ValueError(f"Missing {role} {key}")
            metrics[key] = mean(values)
        if metrics["operator_latency_ms"] <= 0 or not -1 <= metrics["ssim"] <= 1:
            raise ValueError(f"Invalid {role} measurement")
        result[role] = metrics
    result["psnr_delta_db"] = result["candidate"]["psnr_db"] - result["baseline"]["psnr_db"]
    baseline = result["baseline"]["operator_latency_ms"]
    result["operator_improvement_pct"] = 100 * (baseline - result["candidate"]["operator_latency_ms"]) / baseline
    return result


def aggregate_frames(frames: list[dict]) -> dict:
    """Require every planned frame and apply equally weighted dataset scoring."""
    groups = {}
    for dataset in DATASETS:
        rows = [frame for frame in frames if frame["dataset"] == dataset]
        if len(rows) != SAMPLES_PER_DATASET or len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"Incomplete measurements for {dataset}")
        groups[dataset] = {
            "samples": len(rows),
            "psnr_delta_db": mean(row["psnr_delta_db"] for row in rows),
            "operator_improvement_pct": mean(row["operator_improvement_pct"] for row in rows),
            **{role: {key: mean(row[role][key] for row in rows) for key in rows[0][role]} for role in ("baseline", "candidate")},
        }
    return {
        "datasets": groups,
        "quality_passed": all(group["psnr_delta_db"] >= 0 for group in groups.values()),
        "operator_improvement_pct": mean(group["operator_improvement_pct"] for group in groups.values()),
        **{
            role: {key: mean(group[role][key] for group in groups.values()) for key in frames[0][role]}
            for role in ("baseline", "candidate")
        },
    }


def score_static_dataset(report: dict) -> tuple[float, dict] | None:
    """Score complete paired measurements against each dataset's PSNR baseline.

    Args:
        report: Restored native benchmark report with per-dataset aggregates.

    Returns:
        A finite fitness and its quality assessment, or None for an invalid report.
    """
    gain = report.get("operator_improvement_pct")
    datasets = report.get("static_datasets")
    if not _finite(gain) or gain > 100 or not isinstance(datasets, dict) or set(datasets) != set(DATASETS):
        return None
    deltas = {}
    for dataset, group in datasets.items():
        if not isinstance(group, dict) or group.get("samples") != SAMPLES_PER_DATASET:
            return None
        delta = group.get("psnr_delta_db")
        if not _finite(delta):
            return None
        deltas[dataset] = delta
    regressions = {dataset: delta for dataset, delta in deltas.items() if delta < 0}
    if bool(regressions) != (report.get("status") == "POOR_QUALITY"):
        return None
    score = float(gain)
    if regressions:
        # Relative gain is at most 100 percent. The offset puts all regressions
        # at or below the cap while retaining the measured speed difference.
        score += STATIC_QUALITY_SCORE_CAP - 100 + min(regressions.values())
    elif score < 0:
        # Preserve latency ordering for slower candidates above the quality-penalty band.
        limit = math.nextafter(STATIC_QUALITY_SCORE_CAP, 0.0)
        score = max(limit, score / (1 + score / STATIC_QUALITY_SCORE_CAP))
    if not _finite(score):
        return None
    return score, {
        "status": "beyond_reference" if regressions else "within_reference",
        "penalized": bool(regressions),
        "penalty_score": STATIC_QUALITY_SCORE_CAP,
        "psnr_delta_by_dataset_db": deltas,
        "reasons": [
            f"{dataset}: mean PSNR is {abs(delta):.6f} dB below its same-run baseline."
            for dataset, delta in regressions.items()
        ],
    }


def _collage(folder: Path, dataset: str, frames: list[dict]) -> dict:
    """Render a checksum-bound strip from exact GPU evidence for three samples."""
    from PIL import Image, ImageDraw, ImageFont

    ordered = sorted(frames, key=lambda row: (row["psnr_delta_db"], row["id"]))
    selected = [ordered[index] for index in sorted({0, len(ordered) // 2, len(ordered) - 1})]
    width, height, gap = 640, 360, 12
    top, label = 76, 32
    canvas = Image.new("RGB", (width * 3 + 4 * gap, top + len(selected) * (height + label + gap)), "#15191e")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=21)
    draw.text((gap, 9), dataset, fill="white", font=font)
    for column, title in enumerate(("HR reference", "Bilinear baseline", "Candidate")):
        draw.text((gap + column * (width + gap), 42), title, fill="white", font=font)
    for row, sample in enumerate(selected):
        frame = frame_directory(folder, sample["index"])
        report = read_json(frame / "report.json")
        artifacts = {item["name"]: item for item in report["artifacts"]}
        group = next((group for group in report["evidence_comparisons"] if group["id"] == "frame-00"), None)
        if group is None:
            raise ValueError(f"No paired GPU images for {sample['id']}")
        images = {image["role"]: image["artifact"] for image in group["images"]}
        y = top + row * (height + label + gap)
        draw.text((gap, y), sample["id"], fill="white", font=font)
        for column, role in enumerate(ROLES):
            x = gap + column * (width + gap)
            if role != "reference":
                caption = f"PSNR  {sample[role]['psnr_db']:.2f} dB"
                if role == "candidate":
                    caption += "   |   Delta "
                draw.text((x, y), caption, fill="white", font=font)
                if role == "candidate":
                    delta = sample["psnr_delta_db"]
                    color = "#9ce9a9" if delta > 0 else "#ff9c9c" if delta < 0 else "#d5d9df"
                    number = f"{delta:+.6f}" if delta else "0.000000"
                    number_x = x + draw.textlength(caption, font=font)
                    draw.text((number_x, y), number, fill=color, font=font)
                    draw.text((number_x + draw.textlength(number, font=font), y), " dB", fill="white", font=font)
            data = artifact_bytes(frame, artifacts[images[role]])
            with Image.open(BytesIO(data)) as image:
                canvas.paste(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS), (x, y + label))
    name = f"collages/{dataset}.png"
    path = folder / "evidence" / name
    path.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            canvas.save(output, format="PNG")
            output.flush()
            os.fsync(output.fileno())
            content = temporary.read_bytes()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "name": name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "media_type": "image/png",
        "description": f"{dataset} static SR comparison",
    }


def execute_static(folder: Path, request: dict, argv: list[str], command) -> int:
    """Evaluate all pairs inside the current leased device worker.

    Args:
        folder: Durable executor job directory.
        request: Frozen shader request with the dataset manifest's ordered pairs.
        argv: Existing phonefarm command, before substituting each frame's paths.
        command: Device command runner that preserves the inherited lease.

    Returns:
        Executor status code for the published measurement report.
    """
    frames = []
    summary = {
        "schema": REPORT_SCHEMA,
        "source": "phonefarm",
        "kind": "shader",
        "candidate_id": request["candidate_id"],
        "status": "ABORT",
        "conditions_observed": {"restored": True},
        "static_frames": frames,
    }
    exit_code = 2
    for index, pair in enumerate(request["payload"]["static_pairs"]):
        if read_json(folder / "job.json").get("state") == "cancel_requested":
            break
        frame = frame_directory(folder, index)
        frame.mkdir(parents=True)
        job_request = frame / "request.json"
        write_json(job_request, pair_request(request, pair))
        frame_argv = argv.copy()
        frame_argv[argv.index("--request") + 1] = str(job_request)
        frame_argv[argv.index("--out") + 1] = str(frame / "evidence")
        write_json(folder / "static-active.json", {"index": index})
        with (frame / "stdout").open("x") as out, (frame / "stderr").open("x") as err:
            completed = command(frame_argv, pf=True, stdout=out, stderr=err)
        try:
            report = read_json(frame / "stdout")
            if not isinstance(report, dict):
                raise ValueError("Frame report must be an object")
        except (OSError, ValueError) as error:
            summary.update(error=str(error), conditions_observed={"restored": False})
            break
        write_json(frame / "report.json", report)
        observed = report.get("conditions_observed", {})
        restored = observed.get("restored") is True and observed.get("gpu_level_lock", {"restored": True}).get("restored") is True
        summary["conditions_observed"]["restored"] = restored
        if (
            not restored
            or report.get("candidate_id") != request["candidate_id"]
            or report.get("source") != "phonefarm"
            or report.get("kind") != "shader"
        ):
            summary["error"] = f"Invalid native receipt for {pair['id']}"
            break
        if completed.returncode not in (0, 1):
            summary["error"] = f"Native measurement aborted for {pair['id']}"
            break
        if report.get("status") == "CRASH":
            summary.update(status="CRASH", verdict={"reason": f"Candidate failed on {pair['id']}"})
            exit_code = 1
            break
        if report.get("status") not in {"PASS", "REJECT", "POOR_QUALITY", "SLOW"}:
            summary["error"] = f"Unknown native measurement for {pair['id']}"
            break
        try:
            metrics = _measurements(report, request["protocol"]["rounds"])
        except (KeyError, TypeError, ValueError) as error:
            summary["error"] = f"{pair['id']}: {error}"
            break
        frames.append({"index": index, "dataset": pair["dataset"], "id": pair["id"], **metrics})
    else:
        aggregate = aggregate_frames(frames)
        status = "PASS" if aggregate["operator_improvement_pct"] > 0 else "REJECT"
        if not aggregate["quality_passed"]:
            status = "POOR_QUALITY"
        summary.update(
            status=status,
            static_datasets=aggregate["datasets"],
            metrics=aggregate["candidate"],
            baseline_metrics=aggregate["baseline"],
            operator_improvement_pct=aggregate["operator_improvement_pct"],
            artifacts=[],
            artifact_errors=[],
        )
        for dataset in DATASETS:
            try:
                artifact = _collage(folder, dataset, [frame for frame in frames if frame["dataset"] == dataset])
            except (ImportError, KeyError, OSError, TypeError, ValueError) as error:
                summary["artifact_errors"].append(f"{dataset}: {error}")
            else:
                summary["artifacts"].append(artifact)
        exit_code = 0 if status == "PASS" else 1
    write_json(folder / "result.json", summary)
    return exit_code
