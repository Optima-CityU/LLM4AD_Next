"""Base orchestrator interface for LLM4AD."""
import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from llm4ad.coder.base import BaseCoder
from llm4ad.config import EvolutionConfig
from llm4ad.evaluator import EvaluationDispatcher
from llm4ad.infra.state import EvolutionState, StateTracker
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.orchestrator.embedding_utils import save_algorithm_embeddings
from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.utils.registry import Registrable


def format_duration_ms(ms: float) -> str:
    """Format a duration in milliseconds to a human-readable string.

    Args:
        ms: Duration in milliseconds.

    Returns:
        Formatted string like "1h 2min 3s 456ms", omitting zero-valued
        leading components.
    """
    if ms < 0:
        return f"{ms:.0f}ms"

    total_ms = int(round(ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)

    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if seconds > 0:
        parts.append(f"{seconds}s")
    if millis > 0 or not parts:
        parts.append(f"{millis}ms")

    return " ".join(parts)


class EvolutionCheckpoint(BaseModel):
    """Checkpoint data for resuming evolution."""

    generation: int
    population: list[Any]  # List[AlgorithmInsight]
    best_individual: Any | None = None
    history: list[dict] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class EvolutionResult(BaseModel):
    """Final result of evolution process."""

    state: EvolutionState
    best_individual: Any | None = None  # AlgorithmInsight
    final_population: list[Any] = Field(default_factory=list)  # Final / elitist population
    best_individual: Algorithm | None = None  # Algorithm
    final_generation: int = 0
    total_evaluations: int = 0
    history: list[dict] = Field(default_factory=list)
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class BaseOrchestrator(Registrable, ABC, registry_name="orchestrator"):
    """Abstract orchestrator interface.

    Controls the main evolution workflow: Planner → Coder → Evaluator loop.
    Implements search algorithms (GA, Evolution Strategy) and manages the
    overall evolution process including checkpointing and resume functionality.
    """

    def __init__(
            self,
            planner: BasePlanner,  # BasePlanner
            coder: BaseCoder,  # BaseCoder
            dispatcher: EvaluationDispatcher,  # EvaluationDispatcher
            monitor: Any,  # BaseMonitor
            config: EvolutionConfig,
            state_tracker: StateTracker,  # StateTracker
            background: str = "",
            embedding_client: EmbeddingClient = None
    ):
        """Initialize orchestrator.

        Args:
            planner: Planner for generating new algorithm insights.
            coder: Coder for implementing algorithms from insights.
            dispatcher: Dispatcher for scheduling evaluation tasks.
            monitor: Monitor for tracking evolution progress.
            config: Evolution configuration.
            state_tracker: StateTracker for tracking evolution state.
            background: Problem background description from top-level config.
            embedding_client: EmbeddingClient for generating algorithm embeddings.
        """
        self.planner = planner
        self.coder = coder
        self.dispatcher = dispatcher
        self.monitor = monitor
        self.config = config
        self.state_tracker = state_tracker
        self.background = background
        self.embedding_client = embedding_client

        # State
        self.state = EvolutionState.IDLE
        self.current_generation = 0
        self.population: list[Any] = []  # List[AlgorithmInsight]
        self.best_individual: Any | None = None
        self.start_time: float = 0.0
        self.history: list[dict] = []
        self._embedding_tasks = set()

        # Checkpointing
        self._last_checkpoint_gen = 0
        self._checkpoint_count = 0

    @abstractmethod
    async def run(self) -> EvolutionResult:
        """Run the complete evolution process.

        Executes the full evolution loop from generation 0 to max_generations
        or until early stopping criteria are met.

        Returns:
            EvolutionResult with final state and best individual
        """
        pass

    @abstractmethod
    async def step(self) -> tuple[bool, Any | None]:
        """Execute one evolution step (one generation).

        Performs one iteration of the evolution loop:
        1. Generate new insights (or mutate/crossover)
        2. Code generation
        3. Evaluation
        4. Selection

        Returns:
            Tuple of (should_continue, best_individual)
        """
        pass

    @abstractmethod
    async def initialize_population(self) -> list[Any]:
        """Initialize the starting population.

        Creates the initial set of algorithm insights, either randomly,
        from seed solutions, or using domain-specific heuristics.

        Returns:
            Initial population of AlgorithmInsights
        """
        pass

    @abstractmethod
    async def evolve_generation(self, parent_population: list[Any]) -> list[Any]:
        """Evolve one generation.

        Creates offspring from the parent population using
        selection, mutation, and crossover operators.

        Args:
            parent_population: Current population

        Returns:
            New population after evolution
        """
        pass

    @abstractmethod
    async def pause(self) -> None:
        """Pause evolution at the next checkpoint.

        Sets a flag that will cause the evolution loop to pause
        after the current generation completes.
        """
        pass

    @abstractmethod
    async def resume(self) -> None:
        """Resume evolution from paused state.

        Continues the evolution loop from where it was paused.
        """
        pass

    @abstractmethod
    async def save_checkpoint(self, path: str | None = None) -> str:
        """Save current state to checkpoint.

        Persists the current evolution state (population, generation,
        best individual, etc.) to disk for later resumption.

        Args:
            path: Optional checkpoint path (default: auto-generated)

        Returns:
            Path to saved checkpoint
        """
        pass

    @abstractmethod
    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load state from checkpoint.

        Restores the evolution state from a previously saved checkpoint.

        Args:
            path: Path to checkpoint file

        Returns:
            Loaded checkpoint data
        """
        pass

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Get current evolution status.

        Returns:
            Dictionary with current status information
        """
        pass

    def should_checkpoint(self) -> bool:
        """Check if a checkpoint should be saved.

        Returns:
            True if checkpoint interval has been reached
        """
        if self.config.checkpoint_interval <= 0:
            return False
        return (
            self.current_generation - self._last_checkpoint_gen >= self.config.checkpoint_interval
        )

    def check_early_stop(self) -> tuple[bool, str]:
        """Check if early stopping criteria are met.

        Returns:
            Tuple of (should_stop, reason)
        """
        if len(self.history) < self.config.early_stop_patience:
            return False, ""

        # Check if no improvement for N generations
        recent = self.history[-self.config.early_stop_patience :]
        if len(recent) < 2:
            return False, ""

        best_scores = [gen.get("best_score", 0) for gen in recent]
        max_improvement = max(best_scores) - min(best_scores)

        if max_improvement < self.config.early_stop_threshold:
            return True, f"No improvement for {self.config.early_stop_patience} generations"

        return False, ""

    def _schedule_embedding_save(self, algorithm: Algorithm) -> asyncio.Task | None:
        """Schedule evaluation trace embedding generation for an evaluated algorithm."""
        if not self.embedding_client:
            logger.debug(
                "Embedding client is not configured; skip evaluation trace embedding for algorithm {}",
                getattr(algorithm, "id", "<unknown>"),
            )
            return None

        embedding_dir = getattr(self.state_tracker, "embedding_dir", None)
        if not embedding_dir:
            logger.warning(
                "Embedding client is configured but embedding_dir is missing; skip algorithm {}",
                getattr(algorithm, "id", "<unknown>"),
            )
            return None

        logger.info(
            "Scheduling evaluation trace embedding for algorithm {}, island {}, generation {}",
            getattr(algorithm, "id", "<unknown>"),
            getattr(algorithm, "island_id", None),
            getattr(algorithm, "generation", None),
        )
        task = asyncio.create_task(
            save_algorithm_embeddings(self.embedding_client, algorithm, embedding_dir)
        )
        self._embedding_tasks.add(task)

        def _log_embedding_task_result(done: asyncio.Task) -> None:
            self._embedding_tasks.discard(done)
            algorithm_id = getattr(algorithm, "id", "<unknown>")
            if done.cancelled():
                logger.warning("Evaluation trace embedding task was cancelled for algorithm {}", algorithm_id)
                return
            exc = done.exception()
            if exc:
                logger.warning(
                    "Evaluation trace embedding task failed for algorithm {}: {}",
                    algorithm_id,
                    exc,
                )

        task.add_done_callback(_log_embedding_task_result)
        return task

    async def _finish_embedding_tasks(self):
        if len(self._embedding_tasks) > 0:
            await asyncio.gather(*self._embedding_tasks, return_exceptions=True)
