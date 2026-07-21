"""Unit tests for the standalone EoH planner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from llm4ad.infra.provider.base import GenerationResult
from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.infra.state import StateTracker
from llm4ad.infra.version_control.base import WorktreeInfo
from llm4ad.planner.base import BasePlanner
from llm4ad.planner.eoh_evolution import EoHEvolutionPlanner


class MockSchema(BaseModel):
    """Mock structured output (thought + code)."""

    name: str
    description: str
    code: str = ""


class DummyProvider:
    """Provider stub returning a parsed unified payload."""

    def __init__(self) -> None:
        """Initialize the mock provider."""
        self.model = "mock-model"
        self.config = {}
        self.generate = AsyncMock(
            return_value=GenerationResult(
                text="",
                total_tokens=10,
                parsed=MockSchema(name="Init", description="Init desc", code="return 1"),
            )
        )


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
    """Create a mock analyzed repository with one EVOLVE block."""
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


def _make_planner(analyzed_repository) -> EoHEvolutionPlanner:
    """Build an EoH planner with stubbed dependencies."""
    return EoHEvolutionPlanner(
        provider=DummyProvider(),
        coder=DummyCoder(),
        memory=DummyMemory(),
        config={"selection_num": 2},
        analyzed_repository=analyzed_repository,
        version_control=DummyVersionControl(),
        state_tracker=StateTracker(),
    )


def test_eoh_planner_registered_and_standalone():
    """EoH planner resolves by name and does not reuse MEoH's planner/samplers."""
    from llm4ad.planner.meoh_evolution import MEoHEvolutionPlanner

    assert BasePlanner.get("eoh_evolution") is EoHEvolutionPlanner
    assert not issubclass(EoHEvolutionPlanner, MEoHEvolutionPlanner)
    assert set(EoHEvolutionPlanner.OPERATOR_TO_SAMPLER.values()) == {
        "eoh_init_sampler",
        "eoh_e1_sampler",
        "eoh_e2_sampler",
        "eoh_m1_sampler",
        "eoh_m2_sampler",
    }


@pytest.mark.asyncio
async def test_eoh_planner_routes_operator(analyzed_repository):
    """Planner should route operators to the EoH samplers and tag metadata."""
    planner = _make_planner(analyzed_repository)

    algorithm = await planner.plan(population=[], generation=0, operator="i1", parents=[], background="task")

    assert algorithm.name == "Init"
    assert algorithm.custom_metadata["operator"] == "i1"
    assert algorithm.custom_metadata["unified_code"] == "return 1"


@pytest.mark.asyncio
async def test_eoh_planner_rejects_unknown_operator(analyzed_repository):
    """Planner should reject operators it does not support."""
    planner = _make_planner(analyzed_repository)

    with pytest.raises(ValueError, match="Unsupported EoH operator"):
        await planner.plan(population=[], generation=0, operator="s1", parents=[], background="task")
