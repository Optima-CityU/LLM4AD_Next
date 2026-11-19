"""Regression coverage for paired dataset preparation and collage publication."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm4ad.gateway.examples.hgamey.evaluators import HGameyStaticEvaluator
from llm4ad.gateway.examples.hgamey.execution.static_measurement import (
    execute_static,
    recovery_directory,
    score_static_dataset,
)
from llm4ad.gateway.examples.hgamey.execution.storage import freeze_shader_inputs
from llm4ad.gateway.examples.hgamey.static_dataset import DATASETS, matching_pairs, prepare_dataset

pytestmark = pytest.mark.unit


def image_bytes(size):
    """Produce fixture bytes without a platform image upload."""
    image = pytest.importorskip("PIL.Image")
    storage = BytesIO()
    image.new("RGB", size, "blue").save(storage, format="PNG")
    return storage.getvalue()


def test_importer_freezes_aligned_crop_and_rejects_changed_input(tmp_path):
    """Prepare stable pairs and reject modified bytes at the executor boundary."""
    qrisp, gameir = tmp_path / "QRISP", tmp_path / "GameIR-SR"
    dimensions = {
        "540p/Native": (960, 540),
        "1080p/Enhanced": (1920, 1080),
        "720p": (960, 720),
        "1440p": (1920, 1440),
    }
    for mode, size in dimensions.items():
        image = image_bytes(size)
        for idx in range(8):
            base = qrisp / "CBApocalypse" / mode / f"{idx:04d}" if "/" in mode else gameir / "static_town08" / f"{idx:02d}" / mode
            base.mkdir(parents=True)
            filename = "0000.png" if "/" in mode else "00000000.rgb.png"
            (base / filename).write_bytes(image)
    first = prepare_dataset(tmp_path / "first", qrisp, gameir, ("qrisp-v1", "gameir-v1"), "train")
    second = prepare_dataset(tmp_path / "second", qrisp, gameir, ("qrisp-v1", "gameir-v1"), "train")
    assert first.read_bytes() == second.read_bytes()
    assert not list(matching_pairs(gameir, "GameIR-SR", "test"))
    samples = json.loads(first.read_text())["samples"]
    assert len(samples) == 16
    assert samples[8]["lr"]["crop_xy"] == [0, 90]
    assert samples[8]["hr"]["crop_xy"] == [0, 180]
    assert samples[0]["hr"]["crop_xy"] == [0, 0]
    for sample in samples:
        for role in ("lr", "hr"):
            sample[role]["path"] = str(first.parent / sample[role]["path"])
    Path(samples[0]["lr"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Static lr image changed"):
        freeze_shader_inputs(
            tmp_path / "job", {"submission": {"request": {"kind": "shader", "payload": {"static_pairs": samples}}}}
        )


@pytest.mark.parametrize("failure", [None, "one-collage", "arm", "dataset-quality"])
def test_static_job_requires_all_frames_and_publishes_two_collages(tmp_path, failure):
    """Drive the executor through all 16 pairs and verify artifact and score behavior."""
    samples = [
        {
            "dataset": dataset,
            "id": f"scene/{index}",
            "lr": {"path": f"lr-{dataset}-{index}"},
            "hr": {"path": f"hr-{dataset}-{index}"},
        }
        for dataset in DATASETS
        for index in range(8)
    ]
    request = {"candidate_id": "job", "protocol": {"rounds": 2}, "payload": {"static_pairs": samples}}
    (tmp_path / "job.json").write_text('{"state":"running"}')
    png = image_bytes((16, 9))
    calls = []

    def native(argv, *, pf, stdout, stderr):
        assert pf
        payload = json.loads(Path(argv[argv.index("--request") + 1]).read_text())["payload"]
        index = len(calls)
        assert payload == {"quality_input": samples[index]["lr"]["path"], "quality_reference": samples[index]["hr"]["path"]}
        folder = Path(argv[argv.index("--out") + 1])
        assert folder == recovery_directory(tmp_path)
        calls.append(folder)
        folder.mkdir()
        images, artifacts = [], []
        for role in ("reference", "baseline", "candidate"):
            name = f"{role}.png"
            if failure == "one-collage" and index == 0 and role == "candidate":
                continue
            (folder / name).write_bytes(png)
            images.append({"role": role, "artifact": name})
            artifacts.append(
                {"name": name, "sha256": hashlib.sha256(png).hexdigest(), "size": len(png), "media_type": "image/png"}
            )
        arms = [
            {
                "arm": arm,
                "runner_exit_status": 0,
                "psnr_db": (29 if index >= 8 else 30.01) if failure == "dataset-quality" and arm == "B" else 30,
                "ssim": 0.9,
                "operator_latency_ms": (2 if index % 2 else 4) if arm == "A" else 1,
            }
            for arm in ("A", "B")
            for _ in range(2)
        ]
        if failure == "arm" and index == 4:
            arms.pop()
        json.dump(
            {
                "candidate_id": "job",
                "source": "phonefarm",
                "kind": "shader",
                "status": "REJECT",
                "conditions_observed": {"restored": True, "arms": arms},
                "artifacts": artifacts,
                "evidence_comparisons": [{"id": "frame-00", "images": images}],
            },
            stdout,
        )
        return SimpleNamespace(returncode=1)

    code = execute_static(tmp_path, request, ["phonefarm", "eval", "--request", "placeholder", "--out", "placeholder"], native)
    report = json.loads((tmp_path / "result.json").read_text())
    if failure == "arm":
        assert code == 2 and report["status"] == "ABORT" and len(calls) == 5
        assert "Incomplete candidate" in report["error"]
        return
    assert len(report["static_frames"]) == len(calls) == 16
    assert report["operator_improvement_pct"] == 62.5
    assert report["status"] == ("POOR_QUALITY" if failure == "dataset-quality" else "PASS")
    expected = ["collages/GameIR-SR.png"] if failure == "one-collage" else ["collages/QRISP.png", "collages/GameIR-SR.png"]
    assert [file["name"] for file in report["artifacts"]] == expected
    assert bool(report["artifact_errors"]) == (failure == "one-collage")
    adapter = HGameyStaticEvaluator.__new__(HGameyStaticEvaluator)
    adapter.benchmark = SimpleNamespace(
        static_pairs=samples, version="fixture", game_check=None,
        score_mode="relative_latency", min_operator_improvement_pct=1,
    )
    state = {"idempotency_key": "job", "files": {"candidate.comp": "fixture"}, "evidence_directory": str(tmp_path)}
    result = adapter.convert_report(report, state)
    assert result is not None and result.success
    assert result.error_message is None
    assert result.metrics["operator_improvement_pct"] == 62.5
    assert result.metrics["static_score"] == result.score
    assessment = result.evolution_feedback["quality_assessment"]
    image = pytest.importorskip("PIL.Image")
    if failure == "dataset-quality":
        assert result.score == -138.5
        assert "failure_type" not in result.metadata and "candidate_failure" not in result.metadata
        assert result.metadata["evaluation_summary"]["quality_passed"] is False
        assert assessment["penalized"] and assessment["reasons"] == [
            "GameIR-SR: mean PSNR is 1.000000 dB below its same-run baseline."
        ]
        colors = {"QRISP": (156, 233, 169), "GameIR-SR": (255, 156, 156)}
        incomplete = {**report, "static_datasets": {"GameIR-SR": report["static_datasets"]["GameIR-SR"]}}
        assert adapter.convert_report(incomplete, state) is None
        assert adapter.convert_report({**report, "conditions_observed": {"restored": False}}, state) is None
    else:
        assert result.score == 62.5 and not assessment["penalized"]
        colors = {dataset: (213, 217, 223) for dataset in DATASETS}
    for dataset, color in colors.items():
        path = tmp_path / "evidence" / "collages" / f"{dataset}.png"
        if path.is_file():
            with image.open(path) as collage:
                header = collage.crop((1316, 76, 1968, 108))
                assert color in {rgb for _, rgb in header.getcolors(652 * 32)}


def test_static_fitness_keeps_qualified_candidates_above_quality_penalties():
    """Rank by speed within each band and preserve eligibility for measured regression."""
    qualified = {
        "status": "REJECT",
        "operator_improvement_pct": -500,
        "static_datasets": {dataset: {"samples": 8, "psnr_delta_db": 0} for dataset in DATASETS},
    }
    degraded = {
        "status": "POOR_QUALITY",
        "operator_improvement_pct": 90,
        "static_datasets": {**qualified["static_datasets"], "GameIR-SR": {"samples": 8, "psnr_delta_db": -0.0001}},
    }
    slow, _ = score_static_dataset(qualified)
    faster, _ = score_static_dataset({**qualified, "operator_improvement_pct": -100})
    penalized, _ = score_static_dataset(degraded)
    lower, _ = score_static_dataset({**degraded, "operator_improvement_pct": 80})
    assert faster > slow > -100 > penalized > lower


async def test_paired_upload_filters_individual_images_and_duplicates(monkeypatch):
    """The gateway forwards only the two named aggregate PNGs to the SDK."""
    evaluator = HGameyStaticEvaluator.__new__(HGameyStaticEvaluator)
    evaluator.benchmark = SimpleNamespace(static_pairs=[{}])
    seen = []

    async def retain(self, session, reporter, state, item, *, optional):
        seen.append(item["name"])
        assert optional
        if item["name"].endswith("QRISP.png"):
            raise OSError("Optional upload failed")

    monkeypatch.setattr(HGameyStaticEvaluator, "retain_game_artifact", retain)
    names = ["images/reference.png", "collages/QRISP.png", "collages/QRISP.png", "collages/GameIR-SR.png"]
    reporter = SimpleNamespace(progress=lambda _: None)
    errors = await evaluator.retain_game_artifacts(
        None,
        reporter,
        {"artifact_scope": "static"},
        {
            "artifacts": [{"name": name, "media_type": "image/png"} for name in names],
        },
    )
    assert seen == names[2:]
    assert errors == ["static/collages/QRISP.png: OSError"]
