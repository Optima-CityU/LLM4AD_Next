"""Tests for multimodal sampler operators.

Verifies that:
- MultimodalMutationSampler calls provider.chat() when parent has behavior data,
  and falls back to provider.generate() when it does not.
- MultimodalCrossoverSampler calls provider.chat() when both parents have behavior,
  and falls back when either parent lacks behavior data.
- Text-only MutationSampler / CrossoverSampler always use provider.generate(),
  regardless of behavior data.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from llm4ad.evaluator.behavior import BehaviorData, BehaviorVisualization
from llm4ad.planner.base import Algorithm, EvaluationResult, InsightType
from llm4ad.planner.sampler.crossover_sampler import CrossoverSampler
from llm4ad.planner.sampler.multimodal_crossover_sampler import (
    MultimodalCrossoverSampler,
)
from llm4ad.planner.sampler.multimodal_mutation_sampler import (
    MultimodalMutationSampler,
)
from llm4ad.planner.sampler.mutation_sampler import MutationSampler


class AlgorithmSchema(BaseModel):
    """Schema for mock LLM response."""

    name: str
    description: str


def _mock_response():
    """Create a mock provider response with parsed AlgorithmSchema."""
    resp = MagicMock()
    resp.parsed = AlgorithmSchema(
        name="ImprovedAlgorithm",
        description="An improved version",
    )
    resp.prompt_tokens = 100
    resp.completion_tokens = 50
    return resp


def _mock_provider():
    """Create a mock provider with both generate and chat as AsyncMock."""
    provider = MagicMock()
    provider.generate = AsyncMock(return_value=_mock_response())
    provider.chat = AsyncMock(return_value=_mock_response())
    provider.model = "mock-model"
    return provider


def _make_algorithm_with_behavior(alg_id: str = "alg-1", score: float = 0.8) -> Algorithm:
    """Create an Algorithm with behavior data (has_behavior() returns True)."""
    return Algorithm(
        id=alg_id,
        insight_type=InsightType.INITIAL,
        name="TestAlgorithm",
        description="Test algorithm",
        generation=0,
        evaluation=EvaluationResult(score=score),
        behavior_data=[
            BehaviorData(
                observation="Seed=6 | Reward=172.7 | State=Crashed",
                visualizations=[
                    BehaviorVisualization(
                        label="Test Trajectory",
                        media_type="image/png",
                        data_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                    )
                ],
                instance_id="test_instance",
            )
        ],
    )


def _make_algorithm_no_behavior(alg_id: str = "alg-2", score: float = 0.5) -> Algorithm:
    """Create an Algorithm without behavior data."""
    return Algorithm(
        id=alg_id,
        insight_type=InsightType.INITIAL,
        name="TestAlgorithm",
        description="Test algorithm",
        generation=0,
        evaluation=EvaluationResult(score=score),
    )


MULTIMODAL_CONFIG = {
    "multimodal": {
        "max_images_per_prompt": 3,
        "image_max_size_kb": 512,
        "include_observation_text": True,
    },
}


class TestMultimodalMutationSampler:
    """Tests for the standalone MultimodalMutationSampler operator."""

    @pytest.mark.asyncio
    async def test_uses_chat_when_parent_has_behavior(self):
        """When parent has behavior data, should use chat() for multimodal."""
        provider = _mock_provider()
        memory = MagicMock()
        parent = _make_algorithm_with_behavior()

        sampler = MultimodalMutationSampler(
            provider=provider, memory=memory, config=MULTIMODAL_CONFIG,
        )

        algorithm = await sampler.sample(
            parents=[parent], generation=1, background="Test background",
        )

        assert algorithm is not None
        assert algorithm.insight_type == InsightType.MUTATION
        provider.chat.assert_called_once()
        provider.generate.assert_not_called()

        # Verify multimodal message structure
        call_args = provider.chat.call_args
        messages = call_args[0][0]
        assert len(messages) == 1
        assert isinstance(messages[0].content, list)

    @pytest.mark.asyncio
    async def test_falls_back_when_parent_has_no_behavior(self):
        """When parent has no behavior data, should fall back to generate()."""
        provider = _mock_provider()
        memory = MagicMock()
        parent = _make_algorithm_no_behavior()

        sampler = MultimodalMutationSampler(
            provider=provider, memory=memory, config=MULTIMODAL_CONFIG,
        )

        algorithm = await sampler.sample(
            parents=[parent], generation=1, background="Test background",
        )

        assert algorithm is not None
        provider.generate.assert_called_once()
        provider.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_records_operator_name(self):
        """Generation metadata should record the correct operator name."""
        provider = _mock_provider()
        memory = MagicMock()
        parent = _make_algorithm_with_behavior()

        sampler = MultimodalMutationSampler(
            provider=provider, memory=memory, config=MULTIMODAL_CONFIG,
        )

        algorithm = await sampler.sample(
            parents=[parent], generation=1, background="Test",
        )

        assert algorithm.generation_meta.operator == "multimodal_mutation_sampler"


class TestMultimodalCrossoverSampler:
    """Tests for the standalone MultimodalCrossoverSampler operator."""

    @pytest.mark.asyncio
    async def test_uses_chat_when_both_parents_have_behavior(self):
        """When both parents have behavior data, should use chat()."""
        provider = _mock_provider()
        memory = MagicMock()
        parent1 = _make_algorithm_with_behavior("p1", 0.8)
        parent2 = _make_algorithm_with_behavior("p2", 0.9)

        sampler = MultimodalCrossoverSampler(
            provider=provider, memory=memory, config=MULTIMODAL_CONFIG,
        )

        algorithm = await sampler.sample(
            population=[], generation=1,
            parents=[parent1, parent2], background="Test background",
        )

        assert algorithm is not None
        assert algorithm.insight_type == InsightType.CROSSOVER
        provider.chat.assert_called_once()
        provider.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_when_one_parent_lacks_behavior(self):
        """When only one parent has behavior, should fall back to generate()."""
        provider = _mock_provider()
        memory = MagicMock()
        parent1 = _make_algorithm_with_behavior("p1", 0.8)
        parent2 = _make_algorithm_no_behavior("p2", 0.9)

        sampler = MultimodalCrossoverSampler(
            provider=provider, memory=memory, config=MULTIMODAL_CONFIG,
        )

        algorithm = await sampler.sample(
            population=[], generation=1,
            parents=[parent1, parent2], background="Test background",
        )

        assert algorithm is not None
        provider.generate.assert_called_once()
        provider.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_records_operator_name(self):
        """Generation metadata should record the correct operator name."""
        provider = _mock_provider()
        memory = MagicMock()
        parent1 = _make_algorithm_with_behavior("p1", 0.8)
        parent2 = _make_algorithm_with_behavior("p2", 0.9)

        sampler = MultimodalCrossoverSampler(
            provider=provider, memory=memory, config=MULTIMODAL_CONFIG,
        )

        algorithm = await sampler.sample(
            population=[], generation=1,
            parents=[parent1, parent2], background="Test",
        )

        assert algorithm.generation_meta.operator == "multimodal_crossover_sampler"


class TestTextOnlySamplersIgnoreBehavior:
    """Verify that text-only samplers always use generate(), never chat()."""

    @pytest.mark.asyncio
    async def test_mutation_sampler_always_uses_generate(self):
        """MutationSampler should use generate() even when parent has behavior."""
        provider = _mock_provider()
        memory = MagicMock()
        memory.aget_prompt_context = AsyncMock(return_value="")
        parent = _make_algorithm_with_behavior()

        sampler = MutationSampler(
            provider=provider, memory=memory, config={},
        )

        await sampler.sample(
            generation=1, parents=[parent], background="Test",
        )

        provider.generate.assert_called_once()
        provider.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_crossover_sampler_always_uses_generate(self):
        """CrossoverSampler should use generate() even when parents have behavior."""
        provider = _mock_provider()
        memory = MagicMock()
        memory.aget_prompt_context = AsyncMock(return_value="")
        parent1 = _make_algorithm_with_behavior("p1", 0.8)
        parent2 = _make_algorithm_with_behavior("p2", 0.9)

        sampler = CrossoverSampler(
            provider=provider, memory=memory, config={},
        )

        await sampler.sample(
            population=[], generation=1,
            parents=[parent1, parent2], background="Test",
        )

        provider.generate.assert_called_once()
        provider.chat.assert_not_called()
