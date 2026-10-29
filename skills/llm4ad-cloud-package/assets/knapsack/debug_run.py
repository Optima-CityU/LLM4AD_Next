"""Optionally run real LLM evolution using environment-only credentials."""

import asyncio
import os
from pathlib import Path

import yaml

from llm4ad import LLM4AD
from llm4ad.config import AppConfig


async def main() -> None:
    """Bind a local provider without modifying the portable cloud config."""
    root = Path(__file__).resolve().parent
    os.chdir(root)
    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    data["providers"] = [
        {
            "name": "default",
            "type": "openai_compatible",
            "base_url": os.environ["LLM_BASE_URL"],
            "api_key": os.environ["LLM_API_KEY"],
            "model": os.environ["LLM_MODEL"],
        }
    ]
    result = await LLM4AD(AppConfig.from_dict(data)).run()
    if result.best_individual is None:
        raise RuntimeError("No evaluated individual was produced")
    print(f"Best score: {result.best_individual.score}")


if __name__ == "__main__":
    asyncio.run(main())
