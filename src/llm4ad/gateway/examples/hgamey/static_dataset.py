"""Prepare deterministic RGB pairs from extracted QRISP and GameIR-SR sources."""

import argparse
import hashlib
import re
import tempfile
from collections import defaultdict, deque
from io import BytesIO
from pathlib import Path

from .execution.storage import write_json

DATASETS = ("QRISP", "GameIR-SR")
SAMPLES_PER_DATASET = 8
SCHEMA = "hgamey/static-dataset@1"
LR_SIZE = (960, 540)
HR_SIZE = (1920, 1080)
QRISP_TEST_SCENES = {"abandonedschool", "spaceshipdemo", "seaport2"}


def matching_pairs(root: Path, dataset: str, split: str):
    """Yield stable group IDs, sample IDs and aligned source paths.

    Args:
        root: Directory containing the extracted official dataset layout.
        dataset: Dataset name from DATASETS.
        split: Official train or test split.

    Yields:
        Tuples of group ID, sample ID, low-resolution path and reference path.
    """
    if not root.is_dir():
        raise ValueError(f"Dataset directory is missing: {root}")
    if dataset == "QRISP":
        base = root / "QRISP" if (root / "QRISP").is_dir() else root
        if split == "test" and (base / "TestSet").is_dir():
            base /= "TestSet"
        for lr in sorted(base.glob("*/540p/Native/*/*.png")):
            scene, _, _, segment, frame = lr.relative_to(base).parts
            if (scene.casefold() in QRISP_TEST_SCENES) != (split == "test"):
                continue
            hr = base / scene / "1080p" / "Enhanced" / segment / frame
            if hr.is_file():
                group = f"{scene}/{segment}"
                yield group, f"{group}/{lr.stem}", lr, hr
    else:
        for lr in sorted(root.rglob("720p/*.rgb.png")):
            run = lr.parent.parent
            town = run.parent.name
            match = re.fullmatch(r"(?:static|dynamic)_town(0[1-8])", town, re.IGNORECASE)
            if match is None or (match[1] == "05") != (split == "test"):
                continue
            hr = run / "1440p" / lr.name
            if hr.is_file():
                group = f"{town}/{run.name}"
                yield group, f"{group}/{lr.name.removesuffix('.rgb.png')}", lr, hr


def _sample(root: Path, dataset: str, split: str):
    """Select across locations and then across sequences with stable ordering."""
    locations = defaultdict(lambda: defaultdict(list))
    ids = set()
    for group, sample, lr, hr in matching_pairs(root, dataset, split):
        if sample in ids:
            raise ValueError(f"Duplicate {dataset} sample {sample}")
        ids.add(sample)
        location, sequence = group.split("/")
        locations[location][sequence].append((sample, lr, hr))

    def rank(value):
        return hashlib.sha256(f"{SCHEMA}:{dataset}:{split}:42:{value}".encode()).digest()

    def interleave(groups):
        groups = deque(groups)
        while groups:
            group = groups.popleft()
            value = next(group, None)
            if value is not None:
                yield value
                groups.append(group)

    def rows(sequences):
        return interleave([iter(sorted(sequences[key], key=lambda row: rank(row[0]))) for key in sorted(sequences, key=rank)])

    selected = []
    for row in interleave([rows(locations[key]) for key in sorted(locations, key=rank)]):
        selected.append(row)
        if len(selected) == SAMPLES_PER_DATASET:
            return selected
    raise ValueError(f"{dataset} {split} needs at least {SAMPLES_PER_DATASET} complete image pairs")


def _prepare_image(source: Path, target: Path, size: tuple[int, int], source_size: tuple[int, int]) -> dict:
    """Write normalized PNG bytes and record the original image and crop identity."""
    from PIL import Image

    content = source.read_bytes()
    with Image.open(BytesIO(content)) as image:
        if image.size != source_size:
            raise ValueError(f"Unexpected image dimensions at {source}: {image.size}")
        x, y = (image.width - size[0]) // 2, (image.height - size[1]) // 2
        image = image.crop((x, y, x + size[0], y + size[1])).convert("RGBA")
        image.save(target, format="PNG")
    return {
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "source_size": list(source_size),
        "size": list(size),
        "crop_xy": [x, y],
    }


def prepare_dataset(output: Path, qrisp: Path, gameir: Path, revisions: tuple[str, str], split: str) -> Path:
    """Publish a complete, frozen dataset directory and return its manifest path.

    Args:
        output: New output directory for the generated PNGs and manifest.
        qrisp: Extracted Qualcomm dataset root.
        gameir: Extracted GameIR-SR dataset root.
        revisions: Operator-provided source revision for each dataset.
        split: Official train or test split to sample.

    Returns:
        Path to the published manifest.
    """
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if split not in {"train", "test"} or not all(revision.strip() for revision in revisions):
        raise ValueError("A train/test split and both source revisions are required")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hgamey-dataset-", dir=output.parent) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        samples = []
        for dataset, root, revision in zip(DATASETS, (qrisp, gameir), revisions, strict=True):
            for sample_id, lr, hr in _sample(root, dataset, split):
                sample = {"dataset": dataset, "id": sample_id, "revision": revision}
                for role, source, size in (("lr", lr, LR_SIZE), ("hr", hr, HR_SIZE)):
                    filename = f"{len(samples):02d}-{role}.png"
                    source_size = (size[0], (720 if role == "lr" else 1440)) if dataset == "GameIR-SR" else size
                    sample[role] = _prepare_image(source, stage / filename, size, source_size) | {"path": filename}
                samples.append(sample)
        write_json(stage / "manifest.json", {"schema": SCHEMA, "split": split, "samples": samples})
        stage.rename(output)
    return output / "manifest.json"


def validate_samples(samples: list) -> None:
    """Reject incomplete, duplicate or unsafe frame declarations before device work."""
    counts = dict.fromkeys(DATASETS, 0)
    ids = set()
    if not isinstance(samples, list):
        raise ValueError("Static samples must be a list")
    for sample in samples:
        name, sample_id = sample["dataset"], sample["id"]
        if name not in counts or not re.fullmatch(r"[\w.\-/]{1,96}", sample_id) or (name, sample_id) in ids:
            raise ValueError("Invalid or duplicate static dataset sample")
        if not isinstance(sample["revision"], str) or not sample["revision"].strip():
            raise ValueError("Static dataset source revision is required")
        counts[name] += 1
        ids.add((name, sample_id))
        for role, size in (("lr", LR_SIZE), ("hr", HR_SIZE)):
            file = sample[role]
            if file["size"] != list(size) or not re.fullmatch(r"[a-f0-9]{64}", file["sha256"]):
                raise ValueError(f"Invalid static {role} dimensions or hash")
    if any(count != SAMPLES_PER_DATASET for count in counts.values()):
        raise ValueError(f"Static benchmark requires {SAMPLES_PER_DATASET} samples from each dataset")


def main() -> None:
    """Provide the operator's offline dataset preparation command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrisp", type=Path, required=True)
    parser.add_argument("--gameir", type=Path, required=True)
    parser.add_argument("--qrisp-revision", required=True)
    parser.add_argument("--gameir-revision", required=True)
    parser.add_argument("--split", choices=("train", "test"), default="train")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(prepare_dataset(args.output, args.qrisp, args.gameir, (args.qrisp_revision, args.gameir_revision), args.split))


if __name__ == "__main__":
    main()
