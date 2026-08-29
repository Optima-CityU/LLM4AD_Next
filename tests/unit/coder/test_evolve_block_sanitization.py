"""Regression tests for generated EVOLVE block sanitization."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm4ad.coder.custom_naive_coder import CustomNaiveCoder
from llm4ad.config.schema import CustomCoderConfig
from llm4ad.infra.provider.base import GenerationResult


@pytest.mark.asyncio
async def test_mutation_removes_future_import_from_replacement(tmp_path) -> None:
    """Module-only future imports returned by an LLM must not enter a nested block."""
    provider = MagicMock()
    provider.generate = AsyncMock(
        return_value=GenerationResult(
            text=(
                "```python:solve.py\n"
                '"""Generated module wrapper."""\n\n'
                "from __future__ import annotations\n\n"
                "def select_items(target: int, items: list[int]) -> list[int]:\n"
                "    return [0] if items and items[0] <= target else []\n"
                "```"
            ),
            total_tokens=40,
        )
    )
    coder = CustomNaiveCoder(
        config=CustomCoderConfig(type="custom", default_extension="py"),
        provider=provider,
    )
    parent_code = {
        "solve.py": (
            '"""Subset solver."""\n\n'
            "from __future__ import annotations\n\n"
            "# EVOLVE_START select_items\n"
            "def select_items(target: int, items: list[int]) -> list[int]:\n"
            "    return []\n"
            "# EVOLVE_END\n"
        )
    }

    result = await coder.generate(
        prompt="improve subset selection",
        context={"parent_code": parent_code, "language": "python"},
        working_dir=str(tmp_path),
    )

    assert result.is_success
    content = (tmp_path / "solve.py").read_text(encoding="utf-8")
    assert content.count("from __future__ import annotations") == 1
    compile(content, "solve.py", "exec")


@pytest.mark.asyncio
async def test_planner_replacement_updates_target_instead_of_implementation_file(
    tmp_path,
) -> None:
    """Unified planner code must replace the target block without another LLM call."""
    provider = MagicMock()
    provider.generate = AsyncMock()
    coder = CustomNaiveCoder(
        config=CustomCoderConfig(type="custom", default_extension="py"),
        provider=provider,
    )
    solve_path = tmp_path / "solve.py"
    solve_path.write_text(
        "from __future__ import annotations\n\n"
        "# EVOLVE_START\n"
        "def destroy_operator(value: int) -> int:\n"
        "    return value\n"
        "# EVOLVE_END\n",
        encoding="utf-8",
    )

    result = await coder.generate(
        prompt="unused because planner already supplied code",
        context={
            "language": "python",
            "replacement_code": (
                "def destroy_operator(value: int) -> int:\n"
                "    return value + 1"
            ),
            "targeted_files": ["solve.py"],
        },
        working_dir=str(tmp_path),
    )

    assert result.is_success
    assert result.generated_files == ["solve.py"]
    assert result.metadata["direct_replacement"] is True
    assert not (tmp_path / "implementation.py").exists()
    assert "return value + 1" in solve_path.read_text(encoding="utf-8")
    provider.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_replacement_rejects_unchanged_target(tmp_path) -> None:
    """An unchanged EVOLVE body must not be registered as a candidate."""
    provider = MagicMock()
    provider.generate = AsyncMock()
    coder = CustomNaiveCoder(
        config=CustomCoderConfig(type="custom", default_extension="py"),
        provider=provider,
    )
    original_block = "def solve(value: int) -> int:\n    return value"
    (tmp_path / "solve.py").write_text(
        f"# EVOLVE_START\n{original_block}\n# EVOLVE_END\n",
        encoding="utf-8",
    )

    result = await coder.generate(
        prompt="unused",
        context={
            "language": "python",
            "replacement_code": original_block,
            "targeted_files": ["solve.py"],
        },
        working_dir=str(tmp_path),
    )

    assert not result.is_success
    assert "did not change EVOLVE block" in (result.error_message or "")
    provider.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_replacement_rejects_target_outside_worktree(tmp_path) -> None:
    """Planner metadata cannot route generated code outside the candidate worktree."""
    provider = MagicMock()
    provider.generate = AsyncMock()
    coder = CustomNaiveCoder(
        config=CustomCoderConfig(type="custom", default_extension="py"),
        provider=provider,
    )

    result = await coder.generate(
        prompt="unused",
        context={
            "language": "python",
            "replacement_code": "def solve():\n    return 1",
            "targeted_files": ["../solve.py"],
        },
        working_dir=str(tmp_path),
    )

    assert not result.is_success
    assert "escapes worktree" in (result.error_message or "")
    provider.generate.assert_not_awaited()
