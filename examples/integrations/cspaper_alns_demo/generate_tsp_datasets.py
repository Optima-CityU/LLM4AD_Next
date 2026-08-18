"""Generate deterministic TSPLIB instances for ALNS evolution experiments."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPECS = {
    "data/train": [
        ("train_uniform_020", 20, "uniform", 1101),
        ("train_clustered_030", 30, "clustered", 1102),
        ("train_ring_040", 40, "ring", 1103),
        ("train_grid_049", 49, "grid", 1104),
        ("train_mixed_060", 60, "mixed", 1105),
    ],
    "data/validation": [
        ("validation_uniform_025", 25, "uniform", 2101),
        ("validation_clustered_045", 45, "clustered", 2102),
        ("validation_ring_070", 70, "ring", 2103),
    ],
    "private-test": [
        ("private_grid_036", 36, "grid", 3101),
        ("private_mixed_065", 65, "mixed", 3102),
        ("private_uniform_100", 100, "uniform", 3103),
    ],
}


def _clip(value: float) -> int:
    return max(0, min(1000, round(value)))


def _uniform(size: int, rng: random.Random) -> list[tuple[int, int]]:
    return [(rng.randint(0, 1000), rng.randint(0, 1000)) for _ in range(size)]


def _clustered(size: int, rng: random.Random) -> list[tuple[int, int]]:
    centers = [(220, 220), (780, 240), (500, 780), (760, 760)]
    points = []
    for index in range(size):
        center_x, center_y = centers[index % len(centers)]
        points.append((_clip(rng.gauss(center_x, 75)), _clip(rng.gauss(center_y, 75))))
    rng.shuffle(points)
    return points


def _ring(size: int, rng: random.Random) -> list[tuple[int, int]]:
    points = []
    for index in range(size):
        angle = 2 * math.pi * index / size + rng.uniform(-0.025, 0.025)
        radius = rng.gauss(380, 24)
        points.append((_clip(500 + radius * math.cos(angle)), _clip(500 + radius * math.sin(angle))))
    return points


def _grid(size: int, rng: random.Random) -> list[tuple[int, int]]:
    width = math.ceil(math.sqrt(size))
    spacing = 800 / max(1, width - 1)
    points = []
    for index in range(size):
        row, column = divmod(index, width)
        x = 100 + column * spacing + rng.uniform(-14, 14)
        y = 100 + row * spacing + rng.uniform(-14, 14)
        points.append((_clip(x), _clip(y)))
    return points


def _mixed(size: int, rng: random.Random) -> list[tuple[int, int]]:
    uniform_size = size // 2
    points = _uniform(uniform_size, rng) + _clustered(size - uniform_size, rng)
    rng.shuffle(points)
    return points


GENERATORS = {
    "uniform": _uniform,
    "clustered": _clustered,
    "ring": _ring,
    "grid": _grid,
    "mixed": _mixed,
}


def _make_unique(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    unique = []
    used = set()
    for x, y in points:
        while (x, y) in used:
            x = (x + 1) % 1001
            if (x, y) in used:
                y = (y + 1) % 1001
        used.add((x, y))
        unique.append((x, y))
    return unique


def _render(name: str, distribution: str, seed: int, points: list[tuple[int, int]]) -> str:
    lines = [
        f"NAME: {name}",
        "TYPE: TSP",
        f"COMMENT: synthetic {distribution} EUC_2D instance; seed={seed}",
        f"DIMENSION: {len(points)}",
        "EDGE_WEIGHT_TYPE: EUC_2D",
        "NODE_COORD_SECTION",
    ]
    lines.extend(f"{index} {x} {y}" for index, (x, y) in enumerate(points, start=1))
    lines.append("EOF")
    return "\n".join(lines) + "\n"


def main() -> None:
    """Generate all deterministic instances and their checksum manifest."""
    manifest = []
    for relative_dir, specs in SPECS.items():
        output_dir = ROOT / relative_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, size, distribution, seed in specs:
            rng = random.Random(seed)
            points = _make_unique(GENERATORS[distribution](size, rng))
            content = _render(name, distribution, seed, points)
            output_path = output_dir / f"{name}.tsp"
            output_path.write_bytes(content.encode("ascii"))
            manifest.append(
                {
                    "name": name,
                    "split": relative_dir,
                    "path": output_path.relative_to(ROOT).as_posix(),
                    "dimension": size,
                    "distribution": distribution,
                    "seed": seed,
                    "sha256": hashlib.sha256(content.encode("ascii")).hexdigest(),
                }
            )

    (ROOT / "dataset-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(manifest)} instances under {ROOT}")


if __name__ == "__main__":
    main()
