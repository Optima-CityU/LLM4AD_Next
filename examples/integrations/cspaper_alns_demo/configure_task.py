"""Apply a reproducible, small evolution budget to the copied demo task."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> None:
    """Patch one copied task configuration with a small MEoH budget."""
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--population-size", type=int, default=2)
    parser.add_argument("--max-samples", type=int, default=3)
    args = parser.parse_args()

    if args.population_size < 1 or args.max_samples < 1:
        raise ValueError("population size and max samples must be positive")

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a mapping")

    for provider in config.get("providers", []):
        provider["timeout"] = max(float(provider.get("timeout", 0)), 300.0)

    evolution = config.setdefault("evolution", {})
    evolution.update(
        {
            "type": "meoh",
            "population_size": args.population_size,
            "max_sample_nums": args.max_samples,
            "num_samplers": 1,
        }
    )
    args.config.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Configured MEoH: population_size={args.population_size}, max_sample_nums={args.max_samples}, num_samplers=1")


if __name__ == "__main__":
    main()
