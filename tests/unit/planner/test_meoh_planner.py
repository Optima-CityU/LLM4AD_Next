"""Unit tests for the MEoH planner."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from llm4ad.infra.provider.base import GenerationResult
from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.infra.state import StateTracker
from llm4ad.infra.version_control.base import WorktreeInfo
from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.meoh_evolution import MEoHEvolutionPlanner


class MockSchema(BaseModel):
    """Mock structured output."""

    name: str
    description: str
    code: str = ""


class DummyProvider:
    """Provider stub for MEoH planner tests."""

    def __init__(self) -> None:
        """Initialize mock provider with two-step generation."""
        self.model = "mock-model"
        self.generate = AsyncMock(
            side_effect=[
                GenerationResult(
                    text="",
                    total_tokens=10,
                    parsed=MockSchema(name="Init", description="Init desc"),
                ),
                GenerationResult(text="return 42", total_tokens=4),
            ]
        )


class DirectCodeProvider:
    """Provider stub for direct code generation tests."""

    def __init__(self) -> None:
        """Initialize mock provider for direct code generation."""
        self.model = "mock-model"
        self.generate = AsyncMock(return_value=GenerationResult(text="return 42", total_tokens=4))


class DummyCoder:
    """Coder stub."""

    agent_type = MagicMock(value="custom")


class DummyMemory:
    """Memory stub."""


class DummyVersionControl:
    """Version control stub."""

    def create_worktree(self, name: str):
        """Create a mock worktree."""
        worktree = WorktreeInfo(
            name=name,
            path=str(Path.cwd()),
            branch="test",
            commit_hash="abc",
            created_at=0.0,
            last_used_at=0.0,
        )
        return MagicMock(success=True, data={"worktree": worktree})


@pytest.fixture
def analyzed_repository():
    """Create a mock analyzed repository."""
    block = EvolveBlock(
        file_path="solve.py",
        absolute_path=Path("/tmp/solve.py"),
        line_start=1,
        line_end=4,
        comment_style="#",
        block_name="solver",
        original_content="return 1",
        context_before="# EVOLVE START\n",
        context_after="\n# EVOLVE END",
        language="python",
    )
    repo = MagicMock()
    repo.evolvable_blocks = [block]
    return repo


@pytest.mark.asyncio
async def test_meoh_planner_routes_operator(analyzed_repository):
    """Planner should route operators to the registered samplers."""
    planner = MEoHEvolutionPlanner(
        provider=DummyProvider(),
        coder=DummyCoder(),
        memory=DummyMemory(),
        config={"selection_num": 2},
        analyzed_repository=analyzed_repository,
        version_control=DummyVersionControl(),
        state_tracker=StateTracker(),
    )

    algorithm = await planner.plan(population=[], generation=0, operator="i1", parents=[], background="task")

    assert algorithm.name == "Init"
    assert algorithm.custom_metadata["operator"] == "i1"


@pytest.mark.asyncio
async def test_meoh_planner_can_generate_direct_code(analyzed_repository):
    """Planner should support direct code generation."""
    tmp_path = Path("tests/.tmp") / f"meoh_planner_{uuid.uuid4().hex[:8]}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "solve.py"
    target.write_text("# EVOLVE START\nreturn 1\n# EVOLVE END\n", encoding="utf-8")
    analyzed_repository.evolvable_blocks[0].file_path = "solve.py"

    provider = DirectCodeProvider()
    planner = MEoHEvolutionPlanner(
        provider=provider,
        coder=DummyCoder(),
        memory=DummyMemory(),
        config={},
        analyzed_repository=analyzed_repository,
        version_control=DummyVersionControl(),
        state_tracker=StateTracker(),
    )
    algorithm = Algorithm(
        insight_type=InsightType.INITIAL,
        name="Direct",
        description="desc",
        worktree=WorktreeInfo(
            name="w",
            path=str(tmp_path),
            branch="b",
            commit_hash="c",
            created_at=0.0,
            last_used_at=0.0,
        ),
    )

    await planner.generate_direct_code(algorithm, task_description="task")

    assert "return 42" in target.read_text(encoding="utf-8")
