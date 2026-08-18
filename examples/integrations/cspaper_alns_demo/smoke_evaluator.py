"""Run the baseline task evaluator without making any LLM API calls."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

from llm4ad.config.schema import EvalContext


def load_evaluator(task_dir: Path):
    """Load the copied task's evaluator class without importing candidate code."""
    module_path = task_dir / "task_evaluator.py"
    spec = importlib.util.spec_from_file_location("cspaper_alns_evaluator", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AlnsTspDestroyEvaluator()


async def run(task_dir: Path, include_validation: bool) -> list[dict[str, object]]:
    """Evaluate the untouched baseline on each selected public instance."""
    evaluator = load_evaluator(task_dir)
    data_paths = sorted((task_dir / "data" / "train").glob("*.tsp"))
    if include_validation:
        data_paths.extend(sorted((task_dir / "data" / "validation").glob("*.tsp")))
    if not data_paths:
        raise RuntimeError("no TSP instances found for the evaluator smoke test")

    records: list[dict[str, object]] = []
    for data_path in data_paths:
        result = await evaluator.evaluate(
            EvalContext(
                project_root=str(task_dir / "algorithm" / "ALNS-master"),
                data_path=str(data_path),
                timeout=60.0,
                candidate_id="baseline",
            )
        )
        record = {
            "instance": data_path.name,
            "success": result.success,
            "score": result.score,
            "metrics": result.metrics,
            "error_message": result.error_message,
        }
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))
        if not result.success:
            raise RuntimeError(f"baseline failed on {data_path.name}: {result.error_message}")
    return records


def main() -> None:
    """Parse command-line arguments and persist the dry-run records."""
    parser = argparse.ArgumentParser()
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("--include-validation", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    task_dir = args.task_dir.resolve()
    records = asyncio.run(run(task_dir, args.include_validation))
    output = args.output or task_dir / "dry-run-results.json"
    output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Dry-run evaluator results: {output.resolve()}")


if __name__ == "__main__":
    main()
