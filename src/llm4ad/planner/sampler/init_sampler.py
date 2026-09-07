"""Initial sampler that generates algorithm insights from analyzed repository EVOLVE blocks.

This sampler uses the pre-analyzed repository information from the repository analyzer,
builds a prompt with the problem background and EVOLVE block context, and uses LLM
to generate a new/improved algorithm insight for the targeted evolvable region.
"""

import time
from typing import Any

from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository, EvolveBlock
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.prompt_templates import INITIAL_ALGORITHM_FROM_BLOCK


class MyResponseMock:
    """Mock response class for testing."""

    def __init__(self) -> None:
        """Initialize the mock response."""
        self.text = ""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.parsed: Any = None


@BaseSampler.register("init_sampler")
class InitSampler(BaseSampler):
    """Initial sampler that generates algorithm insights from pre-analyzed EVOLVE blocks.

    Uses the repository analyzer output to get the evolvable blocks with their
    surrounding context, builds a comprehensive prompt including the problem
    background and current implementation, and asks LLM to generate an improved
    algorithm insight for the targeted block.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository,
    ):
        """Initialize the InitSampler.

        Args:
            provider: LLM provider for generating insights
            memory: Memory system for storing/retrieving past experiences
            config: Sampler configuration:
                - prompt_template: Custom prompt template to use (optional)
                - temperature: Sampling temperature (default: 0.7)
                - max_tokens: Maximum tokens to generate (default: 2048)
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks
        """
        super().__init__(provider, memory, config, analyzed_repository)
        self.prompt_template = config.get("prompt_template", INITIAL_ALGORITHM_FROM_BLOCK)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)
    @property
    def n_parents(self) -> int:
        """Initial sampler does not require any parents."""
        return 0

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "init_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Sample a new initial algorithm insight from an EVOLVE block.

        Gets the analyzed repository from kwargs, selects an EVOLVE block,
        builds the prompt, calls LLM, and returns the new algorithm insight.

        Args:
            parents: Parent algorithms (empty for initial generation)
            generation: Current generation number
            **kwargs: Must contain 'analyzed_repository' with the analyzed repository

        Returns:
            New algorithm containing the generated insight (natural language description)
        """
        if self.analyzed_repository is None or len(self.analyzed_repository.evolvable_blocks) == 0:
            raise ValueError(
                "InitSampler requires analyzed_repository with at least one evolvable block "
                "in kwargs to sample from."
            )

        # For initial generation, pick the first (or any) evolvable block to evolve
        # In the future, we could support random selection or priority-based selection
        block: EvolveBlock = self.analyzed_repository.evolvable_blocks[0]

        # Get problem background from config or kwargs
        background: str = kwargs.get("background", "")

        # Independent exploration intentionally bypasses every memory scope.
        # It still receives a compact search-policy instruction so the absence
        # of recalled context is an explicit algorithmic role, not an accident.
        disable_memory = bool(kwargs.get("disable_memory", False))
        if self.memory and not disable_memory:
            memory_context = await self.memory.aget_prompt_context(
                query=background,
                context={
                    "sampler": "init",
                    "generation": generation,
                    "island_id": kwargs.get("island_id"),
                    "island_strategy": kwargs.get("island_strategy"),
                },
            )
        elif disable_memory:
            memory_context = (
                "### Independent exploration policy\n"
                "Propose an independent mechanism from the problem and source code only. "
                "Do not assume or imitate recalled solutions; prioritize a structurally "
                "different, testable approach."
            )
        else:
            memory_context = ""

        # Build the prompt
        prompt = self.prompt_template.format(
            background=background,
            memory_context=memory_context,
            file_path=block.file_path,
            language=block.language,
            block_name=block.block_name,
            context_before=block.context_before,
            current_content=block.original_content,
            context_after=block.context_after,
        )

        # Track generation time
        start_time = time.time()

        class AlgorithmSchema(BaseModel):
            name: str = Field(..., description="Concise name for the algorithm")
            description: str = Field(..., description="Detailed description of the algorithm")

        # Call LLM to generate the insight
        response = await self.provider.generate(
            prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            schema=AlgorithmSchema,
            request_stage="planner",
        )
#         response = MyResponseMock()
#         response.parsed = AlgorithmSchema(name="Optimized QuickSort", description="""
# ### Improved Algorithm Design: Hybrid Quick Sort (Introspective Sort)

# #### 1. Key Ideas of the New Approach

# The current implementation uses **Bubble Sort**, which has a time complexity of $O(n^2)$. To achieve high performance, I propose replacing this with a **Hybrid Quick Sort** (often the basis for Introsort).

# The key components of this new approach are:

# *   **Divide and Conquer Strategy:** Instead of comparing adjacent elements repeatedly (Bubble Sort), we will select a "pivot" element and partition the array into two sub-arrays: elements less than the pivot and elements greater than the pivot. We then recursively sort these sub-arrays.
# *   **Median-of-Three Pivot Selection:** To avoid the worst-case $O(n^2)$ scenario of Quick Sort (which happens when the pivot is always the smallest or largest element, e.g., in an already sorted array), we will select the pivot as the median of the first, middle, and last elements of the current sub-array.
# *   **Insertion Sort Fallback:** For very small sub-arrays (e.g., size less than 16), the overhead of recursive function calls in Quick Sort becomes inefficient. We will switch to **Insertion Sort** for these small partitions, as it is faster than Quick Sort for tiny datasets due to low constant factors and lack of recursion overhead.
# *   **In-Place Operation:** Like the original Bubble Sort, this algorithm will operate in-place on the `std::vector`, minimizing memory usage.

# #### 2. Why This Will Perform Better

# *   **Asymptotic Complexity:** The primary improvement is the reduction of time complexity from **$O(n^2)$** (Bubble Sort) to an average case of **$O(n \log n)$** (Quick Sort). For a dataset of 10,000 elements, Bubble Sort performs roughly 100,000,000 operations, whereas Quick Sort performs roughly 130,000 operations. This represents a massive performance gain.
# *   **Cache Locality:** Quick Sort processes data in a sequential manner during the partitioning phase, which is highly friendly to modern CPU caches. Bubble Sort, conversely, has poor cache behavior.
# *   **Adaptivity:** By using Insertion Sort for small subarrays, we eliminate the "recursive tail" problem where Quick Sort makes many expensive calls for tiny arrays. This hybrid approach is the standard strategy used in high-performance standard library implementations (like `std::sort` in C++).

# #### 3. Overall Algorithm Structure/Strategy

# The implementation will consist of a main public function `sort` and a private recursive helper function (e.g., `quickSortRecursive`).

# 1.  **Main Function (`sort`):**
#     *   Initialize `SortResult` to zero.
#     *   Check if the vector size is trivial (0 or 1); if so, return immediately.
#     *   Call the recursive helper function passing the vector, start index (0), end index (n-1), and a reference to the `SortResult` object.
#     *   Return the `SortResult`.

# 2.  **Recursive Helper Function (`quickSortRecursive`):**
#     *   **Base Case:** If the range size is below a threshold (e.g., 16 elements):
#         *   Perform an optimized **Insertion Sort** on this specific range.
#         *   Increment `result.comparisons` and `result.swaps` within the Insertion Sort logic.
#         *   Return.
#     *   **Pivot Selection:**
#         *   Calculate the middle index.
#         *   Compare the first, middle, and last elements to find the median value.
#         *   Swap the median element with the last element (to position the pivot conveniently for partitioning).
#         *   Update `result.comparisons` and `result` swaps accordingly.
#     *   **Partitioning:**
#         *   Use a pointer (index) `i` to track the boundary of elements smaller than the pivot.
#         *   Iterate through the array with pointer `j`.
#         *   If `data[j]` is less than the pivot, increment `i` and swap `data[i]` with `data[j]`.
#         *   Increment `result.comparisons` for every check. Increment `result.swaps` for every swap.
#         *   Finally, swap the pivot (at the end) into its correct position (at `i + 1`).
#     *   **Recursion:**
#         *   Recursively call `quickSortRecursive` for the left sub-array (start to pivot_index - 1).
#         *   Recursively call `quickSortRecursive` for the right sub-array (pivot_index + 1 to end).

# This structure maintains the required interface (accepting `std::vector<int>&` and returning `SortResult`) while drastically improving algorithmic efficiency.
#         """)
#         response.prompt_tokens = 0
#         response.completion_tokens = 0

        generation_time_ms = (time.time() - start_time) * 1000
        total_prompt_tokens = response.prompt_tokens
        total_completion_tokens = response.completion_tokens

        if not response.parsed:
            raise ValueError("LLM response could not be parsed into the expected schema for algorithm insight generation.")

        algorithm_schema: AlgorithmSchema = response.parsed # type: ignore

        # Create the new algorithm
        algorithm = Algorithm(
            insight_type=InsightType.INITIAL,
            description=algorithm_schema.description,
            name=algorithm_schema.name,
            generation=generation
        )

        # Fill in generation metadata
        algorithm.generation_meta = GenerationMetadata(
            operator=self.name,
            llm_provider=self.provider.__class__.__name__,
            llm_model=getattr(self.provider, "model", "unknown"),
            temperature=self.temperature,
            generation_time_ms=generation_time_ms,
            tokens_used=total_prompt_tokens + total_completion_tokens,
            agent_name="InitSampler",
            change_description=algorithm.description,
            targeted_files=[block.file_path],
        )

        return algorithm
