"""Exercise planner-to-coder output contracts without external model calls."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from llm4ad.coder.custom_naive_coder import CustomNaiveCoder
from llm4ad.config.schema import CustomCoderConfig
from llm4ad.infra.provider.base import GenerationResult
from llm4ad.infra.version_control.base import WorktreeInfo
from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.llm_evolution import LLMEvolutionPlanner

EXAMPLE_DIR = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "applications"
    / "alphaevolve_math_benchmark"
)

CODE_CASES = (
    "circle_packing",
    "circle_rectangle",
    "first_autocorrelation",
    "heilbronn_square",
    "heilbronn_triangle",
    "hexagon_packing",
    "max_min_distance_ratio",
    "minimum_overlap",
    "second_autocorrelation",
    "sums_differences",
    "uncertainty_inequality",
)


def _planner_and_algorithm(tmp_path, config, response, parent_ids=()):
    """Create a real coder and planner with only the provider mocked.

    Args:
        tmp_path: Temporary worktree root.
        config: Custom coder configuration.
        response: Mocked final model content.
        parent_ids: Selected parent identifiers, if this is a descendant.

    Returns:
        The planner, algorithm, and mocked provider.
    """
    provider = MagicMock(model="local-test-model")
    provider.generate = AsyncMock(return_value=GenerationResult(text=response))
    planner = object.__new__(LLMEvolutionPlanner)
    planner.coder = CustomNaiveCoder(config, provider)
    planner.config = SimpleNamespace(mask_evolve_blocks=False)
    planner.state_tracker = MagicMock(coder_dir=None)
    algorithm = Algorithm(
        insight_type=InsightType.MUTATION if parent_ids else InsightType.INITIAL,
        name="Test Candidate",
        description="Return the immutable helper multiplied by three.",
        parent_ids=list(parent_ids),
        worktree=WorktreeInfo(
            name="candidate",
            path=str(tmp_path),
            branch="candidate",
            commit_hash="parent-commit",
            created_at=0,
            last_used_at=0,
        ),
    )
    return planner, algorithm, provider


@pytest.mark.parametrize("parent_ids", [(), ("selected-parent",)])
@pytest.mark.parametrize("response_format", ["named", "unnamed", "complete_file"])
def test_evolve_request_has_one_block_contract_and_preserves_fixed_code(
    tmp_path, parent_ids, response_format
):
    """Initial and descendant candidates request and apply one replacement block."""
    prefix = "def immutable_helper():\n    return 2\n\n# EVOLVE_START\n"
    suffix = "\n# EVOLVE_END\n\nRESULT = solve()\n"
    original = prefix + "def solve():\n    return immutable_helper()" + suffix
    replacement = "def solve():\n    return immutable_helper() * 3"
    candidate_file = tmp_path / "solve.py"
    candidate_file.write_text(original)
    if response_format == "named":
        response = f"```python:solve.py\n{replacement}\n```"
    elif response_format == "unnamed":
        response = f"```python\n{replacement}\n```"
    else:
        changed_prefix = prefix.replace("return 2", "return 999")
        changed_suffix = suffix.replace("RESULT = solve()", "RESULT = 999")
        response = f"```python:solve.py\n{changed_prefix}{replacement}{changed_suffix}\n```"
    planner, algorithm, provider = _planner_and_algorithm(
        tmp_path, CustomCoderConfig(), response, parent_ids
    )

    asyncio.run(planner.implement(algorithm, task_description="Maximize the task's measured score."))

    provider.generate.assert_awaited_once()
    request = provider.generate.await_args.args[0]
    assert "Maximize the task's measured score." in request
    assert algorithm.description in request
    assert "Return only the replacement block" in request
    assert "output the COMPLETE file content" not in request
    assert "Return ALL code files" not in request
    if parent_ids:
        assert "selected primary parent's exact commit" in request
    else:
        assert "The worktree contains the task baseline" in request
    updated = candidate_file.read_text()
    assert updated == prefix + replacement + suffix
    namespace = {}
    exec(compile(updated, str(candidate_file), "exec"), namespace)
    assert namespace["RESULT"] == 6


@pytest.mark.parametrize("parent_ids", [(), ("selected-parent",)])
@pytest.mark.parametrize("mask_evolve_blocks", [False, True])
def test_evolve_request_preserves_other_source_file_contracts(
    tmp_path, parent_ids, mask_evolve_blocks
):
    """Keep helper interfaces available without requesting full-file output."""
    helper_content = (
        "HELPER_CONTRACT = 7919\n\n"
        "def evaluate_path(path, *, closed=False):\n"
        "    return HELPER_CONTRACT - len(path)\n"
    )
    helper_file = tmp_path / "task_helpers.py"
    helper_file.write_text(helper_content)
    prefix = "from task_helpers import evaluate_path\n\n# EVOLVE_START\n"
    suffix = "\n# EVOLVE_END\n"
    candidate_file = tmp_path / "solve.py"
    candidate_file.write_text(prefix + "def solve():\n    return evaluate_path([])" + suffix)
    replacement = "def solve():\n    return evaluate_path([1], closed=True)"
    planner, algorithm, provider = _planner_and_algorithm(
        tmp_path,
        CustomCoderConfig(),
        f"```python:solve.py\n{replacement}\n```",
        parent_ids,
    )
    planner.config.mask_evolve_blocks = mask_evolve_blocks

    asyncio.run(planner.implement(algorithm, task_description="Improve the measured route score."))

    request = provider.generate.await_args.args[0]
    assert helper_content in request
    assert "Return only the replacement block" in request
    assert "Return ALL code files" not in request
    assert "output the COMPLETE file content" not in request
    assert helper_file.read_text() == helper_content
    assert candidate_file.read_text() == prefix + replacement + suffix


def test_evolve_request_keeps_explicit_mutation_template(tmp_path):
    """Custom mutation instructions still receive structured task and insight values."""
    candidate_file = tmp_path / "solve.py"
    candidate_file.write_text("# EVOLVE_START\ndef solve():\n    return 1\n# EVOLVE_END\n")
    config = CustomCoderConfig(
        mutation_prompt_template=(
            "CUSTOM MUTATION POLICY\n{task_description}\n{insight}\n"
            "Target: {file_path}\nOriginal:\n{original_block}"
        )
    )
    planner, algorithm, provider = _planner_and_algorithm(
        tmp_path, config, "```python:solve.py\ndef solve():\n    return 3\n```"
    )

    asyncio.run(planner.implement(algorithm, task_description="Preserve this task objective."))

    request = provider.generate.await_args.args[0]
    assert "CUSTOM MUTATION POLICY" in request
    assert "Preserve this task objective." in request
    assert "Target: solve.py" in request
    assert "output the COMPLETE file content" not in request
    assert "return 3" in candidate_file.read_text()


@pytest.mark.parametrize("custom_template", [False, True])
def test_no_evolve_files_preserve_complete_file_generation(tmp_path, custom_template):
    """Projects without EVOLVE markers retain initial multi-file generation."""
    (tmp_path / "helper.py").write_text("VALUE = 2\n")
    config = CustomCoderConfig(
        prompt_template=(
            "CUSTOM INITIAL POLICY\n{insight}\n{task_description}\n{project_context}"
            if custom_template
            else None
        )
    )
    planner, algorithm, provider = _planner_and_algorithm(
        tmp_path,
        config,
        "```python:solve.py\nfrom helper import VALUE\nRESULT = VALUE * 3\n```",
    )

    asyncio.run(planner.implement(algorithm, task_description="Keep this full-project objective."))

    request = provider.generate.await_args.args[0]
    assert "Return only the replacement block" not in request
    expected_instruction = "CUSTOM INITIAL POLICY" if custom_template else "Return ALL code files"
    assert expected_instruction in request
    assert (tmp_path / "solve.py").read_text() == "from helper import VALUE\nRESULT = VALUE * 3"
    assert (tmp_path / "helper.py").read_text() == "VALUE = 2\n"


@pytest.mark.parametrize("case_name", CODE_CASES)
def test_math_benchmark_actual_code_request_uses_block_contract(tmp_path, case_name):
    """Every bundled code task uses one coherent native replacement contract."""
    case_dir = EXAMPLE_DIR / case_name
    config_name = "code_config.yaml" if case_name == "circle_packing" else "config.yaml"
    task_config = yaml.safe_load((case_dir / config_name).read_text())
    original = (case_dir / "algorithm" / "solve.py").read_text()
    config = CustomCoderConfig(**task_config["coder"])
    temporary_coder = CustomNaiveCoder(config, MagicMock())
    blocks = temporary_coder._find_evolve_blocks(original)
    assert len(blocks) == 1
    original_block = blocks[0][3]
    replacement = "# Mocked candidate replacement.\n" + original_block.strip()
    response = f"```python:solve.py\n{replacement}\n```"
    (tmp_path / "solve.py").write_text(original)
    planner, algorithm, provider = _planner_and_algorithm(tmp_path, config, response)

    asyncio.run(planner.implement(algorithm, task_description=task_config["background"]))

    provider.generate.assert_awaited_once()
    request = provider.generate.await_args.args[0]
    assert task_config["background"] in request
    assert provider.generate.await_args.kwargs["temperature"] == task_config["providers"][0]["temperature"]
    assert "Return only the replacement block" in request
    assert "output the COMPLETE file content" not in request
    assert "Return ALL code files" not in request
    expected = original.replace(original_block, replacement + "\n", 1)
    updated = (tmp_path / "solve.py").read_text()
    assert updated == expected
    compile(updated, str(tmp_path / "solve.py"), "exec")
