"""Tests for MutationSampler and CrossoverSampler."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.planner.base import Algorithm, CodeArtifact, EvaluationResult, InsightType
from llm4ad.planner.sampler.crossover_sampler import CrossoverSampler
from llm4ad.planner.sampler.init_sampler import InitSampler
from llm4ad.planner.sampler.mutation_sampler import MutationSampler


class MockProviderForSamplers:
    """Mock provider for testing."""

    def __init__(self):
        """Initialize the mock provider."""
        self.generate = AsyncMock()

    @property
    def model(self):
        """Return the mock model name."""
        return "mock-model"


class AlgorithmSchema(BaseModel):
    """Schema for algorithm generation response."""

    name: str
    description: str


@pytest.fixture
def mock_provider():
    """Create a mock provider."""
    return MockProviderForSamplers()


@pytest.fixture
def mock_memory():
    """Create a mock memory."""
    memory = MagicMock()
    memory.search = MagicMock(return_value=[])
    memory.aget_prompt_context = AsyncMock(return_value="")
    return memory


@pytest.fixture
def mock_analyzed_repository():
    """Create a mock analyzed repository with an evolvable block."""
    from pathlib import Path

    block = EvolveBlock(
        file_path="test.py",
        absolute_path=Path("/test/test.py"),
        line_start=10,
        line_end=20,
        comment_style="#",
        block_name="sorting_algorithm",
        original_content="def sort(arr): return arr",
        context_before="# Before",
        context_after="# After",
        language="python",
    )
    repo = MagicMock()
    repo.evolvable_blocks = [block]
    return repo


def create_mock_algorithm(id: str, generation: int = 0, score: float | None = None) -> Algorithm:
    """Create a mock algorithm with optional evaluation score."""
    kwargs = {
        "id": id,
        "insight_type": InsightType.INITIAL,
        "name": "TestAlgorithm",
        "description": "Test algorithm description",
        "generation": generation,
    }
    if score is not None:
        kwargs["evaluation"] = EvaluationResult(score=score)
    return Algorithm(**kwargs)


@pytest.mark.asyncio
async def test_init_sampler_skips_memory_for_independent_exploration(
    mock_provider, mock_memory, mock_analyzed_repository
):
    """A parentless exploration candidate receives policy guidance but no memory."""
    mock_response = MagicMock()
    mock_response.parsed = AlgorithmSchema(
        name="IndependentAlgorithm",
        description="A mechanism proposed without recalled experience",
    )
    mock_response.prompt_tokens = 10
    mock_response.completion_tokens = 5
    mock_provider.generate.return_value = mock_response
    sampler = InitSampler(
        provider=mock_provider,
        memory=mock_memory,
        config={},
        analyzed_repository=mock_analyzed_repository,
    )

    await sampler.sample(
        parents=[],
        generation=2,
        disable_memory=True,
        island_strategy={"memory_policy": "none", "position": 1.0},
    )

    mock_memory.aget_prompt_context.assert_not_awaited()
    prompt = mock_provider.generate.await_args.args[0]
    assert "independent" in prompt.lower()


class TestMutationSampler:
    """Tests for MutationSampler."""

    @pytest.mark.asyncio
    async def test_mutation_sampler_initialization(self, mock_provider, mock_memory):
        """Test MutationSampler initialization."""
        config = {"temperature": 0.5, "max_tokens": 1024}
        sampler = MutationSampler(
            provider=mock_provider,
            memory=mock_memory,
            config=config,
        )

        assert sampler.temperature == 0.5
        assert sampler.max_tokens == 1024

    @pytest.mark.asyncio
    async def test_mutation_sampler_can_skip_memory(
        self, mock_provider, mock_memory, mock_analyzed_repository
    ):
        """Memory-free exploration still mutates a real parent without retrieval."""
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="MemoryFreeMutation",
            description="Mutate independently from the parent implementation",
        )
        mock_response.prompt_tokens = 10
        mock_response.completion_tokens = 5
        mock_provider.generate.return_value = mock_response
        sampler = MutationSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
            analyzed_repository=mock_analyzed_repository,
        )

        algorithm = await sampler.sample(
            parents=[create_mock_algorithm("parent", score=1.0)],
            generation=2,
            background="test",
            disable_memory=True,
        )

        assert algorithm.parent_ids == ["parent"]
        mock_memory.aget_prompt_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mutation_sampler_sample_without_parent(self, mock_provider, mock_memory):
        """Test that sample raises error without parent."""
        sampler = MutationSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        with pytest.raises(ValueError, match="parent"):
            await sampler.sample([], 1)

    @pytest.mark.asyncio
    async def test_mutation_sampler_sample_with_parent(
        self, mock_provider, mock_memory, mock_analyzed_repository
    ):
        """Test MutationSampler generates mutation from parent."""
        parent = create_mock_algorithm("parent-1", score=0.8)

        # Setup mock response
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="MutatedAlgorithm",
            description="A mutated version of the algorithm"
        )
        mock_response.prompt_tokens = 100
        mock_response.completion_tokens = 50
        mock_provider.generate.return_value = mock_response

        sampler = MutationSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
            analyzed_repository=mock_analyzed_repository,
        )

        algorithm = await sampler.sample(
            parents=[parent],
            generation=1,
            background="Sort numbers",
            island_id=2,
        )

        assert algorithm is not None
        assert algorithm.insight_type == InsightType.MUTATION
        assert algorithm.name == "MutatedAlgorithm"
        assert "parent-1" in algorithm.parent_ids
        assert algorithm.generation == 1
        assert algorithm.generation_meta is not None
        assert algorithm.generation_meta.operator == "mutation_sampler"
        assert algorithm.generation_meta.operation_params["parent_score"] == 0.8
        assert algorithm.generation_meta.operation_params["parent_description"] == parent.description
        assert algorithm.generation_meta.operation_params["parent_id"] == "parent-1"
        retrieval_context = mock_memory.aget_prompt_context.await_args.kwargs["context"]
        assert retrieval_context["generation"] == 1
        assert retrieval_context["island_id"] == 2

    @pytest.mark.asyncio
    async def test_mutation_prompt_contains_parent_code_and_evaluation_feedback(
        self, mock_provider, mock_memory, mock_analyzed_repository
    ):
        """A child plan must see the implementation and optimized state it inherits."""
        parent = create_mock_algorithm("parent-1", score=2.4)
        parent.code_artifacts = [
            CodeArtifact(
                file_path="model_spec.py",
                language="python",
                content="MODEL_SPEC = {'phase': 0.125}\n",
                content_mode="full",
            )
        ]
        assert parent.evaluation is not None
        parent.evaluation.evolution_feedback = {
            "optimized_parameters": {"phase": 0.375},
            "search_status": "evaluation_budget",
        }
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="InheritedMutation",
            description="Refine the inherited phase around the optimized value",
        )
        mock_response.prompt_tokens = 10
        mock_response.completion_tokens = 5
        mock_provider.generate.return_value = mock_response
        sampler = MutationSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
            analyzed_repository=mock_analyzed_repository,
        )

        await sampler.sample(parents=[parent], generation=2, background="packing")

        prompt = mock_provider.generate.await_args.args[0]
        assert "MODEL_SPEC = {'phase': 0.125}" in prompt
        assert '"phase": 0.375' in prompt
        assert "baseline repository snapshot" in prompt.lower()

    @pytest.mark.asyncio
    async def test_mutation_sampler_no_block(
        self, mock_provider, mock_memory
    ):
        """Test MutationSampler works without evolvable block."""
        parent = create_mock_algorithm("parent-1", score=0.8)

        # Setup mock response
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="MutatedAlgorithm",
            description="A mutated version"
        )
        mock_response.prompt_tokens = 100
        mock_response.completion_tokens = 50
        mock_provider.generate.return_value = mock_response

        sampler = MutationSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        algorithm = await sampler.sample(
            parents=[parent],
            generation=1,
            background="Sort numbers",
        )

        assert algorithm.insight_type == InsightType.MUTATION


class TestCrossoverSampler:
    """Tests for CrossoverSampler."""

    @pytest.mark.asyncio
    async def test_crossover_sampler_initialization(self, mock_provider, mock_memory):
        """Test CrossoverSampler initialization."""
        config = {"temperature": 0.6, "max_tokens": 2048}
        sampler = CrossoverSampler(
            provider=mock_provider,
            memory=mock_memory,
            config=config,
        )

        assert sampler.temperature == 0.6
        assert sampler.max_tokens == 2048

    @pytest.mark.asyncio
    async def test_crossover_sampler_can_skip_memory(
        self, mock_provider, mock_memory
    ):
        """Memory-free exploration can recombine parents without retrieval."""
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="MemoryFreeCrossover",
            description="Combine two island candidates without recalled experience",
        )
        mock_response.prompt_tokens = 10
        mock_response.completion_tokens = 5
        mock_provider.generate.return_value = mock_response
        sampler = CrossoverSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        algorithm = await sampler.sample(
            parents=[
                create_mock_algorithm("parent-1", score=1.0),
                create_mock_algorithm("parent-2", score=0.9),
            ],
            generation=2,
            background="test",
            disable_memory=True,
        )

        assert algorithm.parent_ids == ["parent-1", "parent-2"]
        mock_memory.aget_prompt_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_crossover_sampler_sample_without_parents(self, mock_provider, mock_memory):
        """Test that sample raises error without parents."""
        sampler = CrossoverSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        with pytest.raises(ValueError, match="parent"):
            await sampler.sample([], 1)

    @pytest.mark.asyncio
    async def test_crossover_sampler_sample_with_parents(
        self, mock_provider, mock_memory
    ):
        """Test CrossoverSampler combines two parent algorithms."""
        # Create two parent algorithms
        parent1 = create_mock_algorithm("parent-1", score=0.8)
        parent2 = create_mock_algorithm("parent-2", score=0.9)

        # Setup mock response
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="CombinedAlgorithm",
            description="Combined best of both parents"
        )
        mock_response.prompt_tokens = 150
        mock_response.completion_tokens = 75
        mock_provider.generate.return_value = mock_response

        sampler = CrossoverSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        algorithm = await sampler.sample(
            generation=1,
            parents=[parent1, parent2],
            background="Sort numbers",
            island_id=3,
        )

        assert algorithm is not None
        assert algorithm.insight_type == InsightType.CROSSOVER
        assert algorithm.name == "CombinedAlgorithm"
        assert "parent-1" in algorithm.parent_ids
        assert "parent-2" in algorithm.parent_ids
        assert algorithm.generation == 1
        assert algorithm.generation_meta is not None
        assert algorithm.generation_meta.operator == "crossover_sampler"
        assert algorithm.generation_meta.operation_params["parent_1_score"] == 0.8
        assert algorithm.generation_meta.operation_params["parent_1_description"] == parent1.description
        assert algorithm.generation_meta.operation_params["parent_2_score"] == 0.9
        assert algorithm.generation_meta.operation_params["parent_2_description"] == parent2.description
        retrieval_context = mock_memory.aget_prompt_context.await_args.kwargs["context"]
        assert retrieval_context["generation"] == 1
        assert retrieval_context["island_id"] == 3

    @pytest.mark.asyncio
    async def test_crossover_prompt_contains_both_parent_implementations(
        self, mock_provider, mock_memory
    ):
        """Crossover must combine real implementations instead of descriptions only."""
        parent1 = create_mock_algorithm("parent-1", score=0.8)
        parent2 = create_mock_algorithm("parent-2", score=0.9)
        parent1.code_artifacts = [
            CodeArtifact(file_path="solution.py", content="def solve(): return 'left'\n")
        ]
        parent2.code_artifacts = [
            CodeArtifact(file_path="solution.py", content="def solve(): return 'right'\n")
        ]
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(name="Combined", description="combine")
        mock_response.prompt_tokens = 10
        mock_response.completion_tokens = 5
        mock_provider.generate.return_value = mock_response
        sampler = CrossoverSampler(provider=mock_provider, memory=mock_memory, config={})

        await sampler.sample(parents=[parent1, parent2], generation=2, background="test")

        prompt = mock_provider.generate.await_args.args[0]
        assert "return 'left'" in prompt
        assert "return 'right'" in prompt

    @pytest.mark.asyncio
    async def test_crossover_sampler_with_parent_kwargs(
        self, mock_provider, mock_memory
    ):
        """Test CrossoverSampler works with parent1/parent2 kwargs."""
        parent1 = create_mock_algorithm("parent-1", score=0.8)
        parent2 = create_mock_algorithm("parent-2", score=0.9)

        # Setup mock response
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="CombinedAlgorithm",
            description="Combined algorithm"
        )
        mock_response.prompt_tokens = 100
        mock_response.completion_tokens = 50
        mock_provider.generate.return_value = mock_response

        sampler = CrossoverSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        algorithm = await sampler.sample(
            generation=1,
            parents=[parent1, parent2],
            background="Sort numbers",
        )

        assert algorithm.insight_type == InsightType.CROSSOVER
        assert "parent-1" in algorithm.parent_ids
        assert "parent-2" in algorithm.parent_ids

    @pytest.mark.asyncio
    async def test_crossover_sampler_generation_number(self, mock_provider, mock_memory):
        """Test that child generation is max(parent generations) + 1."""
        parent1 = create_mock_algorithm("parent-1", generation=2, score=0.8)
        parent2 = create_mock_algorithm("parent-2", generation=0, score=0.9)

        # Setup mock response
        mock_response = MagicMock()
        mock_response.parsed = AlgorithmSchema(
            name="CombinedAlgorithm",
            description="Combined"
        )
        mock_response.prompt_tokens = 100
        mock_response.completion_tokens = 50
        mock_provider.generate.return_value = mock_response

        sampler = CrossoverSampler(
            provider=mock_provider,
            memory=mock_memory,
            config={},
        )

        algorithm = await sampler.sample(
            generation=3,
            parents=[parent1, parent2],
            background="",
        )

        # Child should have generation
        assert algorithm.generation == 3
