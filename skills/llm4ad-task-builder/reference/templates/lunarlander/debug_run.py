"""Debug runner for LunarLander task package.

Run this script to test the complete LLM4AD pipeline on the LunarLander task.
"""

import asyncio
import os
from pathlib import Path

from llm4ad import LLM4AD

# Set working directory to task root
TASK_DIR = Path(__file__).resolve().parent
os.chdir(TASK_DIR)


async def main():
    """Run the LLM4AD pipeline with the LunarLander configuration."""
    llm4ad = LLM4AD("config.yaml")
    result = await llm4ad.run()
    print(f"Pipeline completed. Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
