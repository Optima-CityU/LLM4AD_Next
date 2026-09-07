"""Debug entry point for TSP benchmark pipeline.

Usage:
    Run from any directory — the script auto-chdir to the task folder
    so that relative paths in the YAML resolve correctly.

This runs the full LLM4AD pipeline and requires LLM credentials:
- LLM_BASE_URL
- LLM_API_KEY
- LLM_MODEL
"""

import asyncio
import os
from pathlib import Path

from llm4ad import LLM4AD

# Ensure CWD is the task directory so YAML relative paths resolve correctly
TASK_DIR = Path(__file__).resolve().parent
os.chdir(TASK_DIR)


async def main():
    """Run the full LLM4AD pipeline for the TSP task."""
    llm4ad = LLM4AD("config.yaml")
    result = await llm4ad.run()

    if result.best_individual:
        print(f"\n[OK] Best score: {result.best_individual.score:.4f}")
        print(f"[OK] Best algorithm: {result.best_individual.name}")
    else:
        print("\n[FAIL] No valid individual found.")


if __name__ == "__main__":
    asyncio.run(main())
