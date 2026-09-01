"""Sampler selector with dynamic weight adjustment based on fitness.

This selector chooses samplers based on their historical performance (fitness).
Weights are dynamically adjusted using exponential moving average.
"""

import random
from typing import Any

from llm4ad.planner.sampler.base import BaseSampler


class SamplerSelector:
    """Selector for choosing samplers based on dynamic weights.

    Maintains weights for each sampler and adjusts them based on the fitness
    of algorithms produced by each sampler. Uses exponential moving average
    for smooth weight updates.
    """

    def __init__(
        self,
        samplers: list[BaseSampler],
        config: dict[str, Any] | None = None,
    ):
        """Initialize the sampler selector.

        Args:
            samplers: List of available samplers
            config: Configuration with selection strategy and weight parameters
        """
        self.samplers = samplers
        self.config = config or {}
        self.selection_strategy = self.config.get("selection_strategy", "weighted")

        # Initialize weights - all samplers start with equal weight
        self.weights: dict[str, float] = {sampler.__class__.__name__: 1.0 for sampler in samplers}

        # Exponential moving average parameters
        self.ema_alpha = self.config.get("ema_alpha", 0.3)  # Weight for new fitness
        self.default_weight = self.config.get("default_weight", 1.0)

        # Track fitness history for each sampler
        self.fitness_history: dict[str, list[float]] = {
            sampler.__class__.__name__: [] for sampler in samplers
        }

    def select(self, n: int = 1, **kwargs) -> list[BaseSampler]:
        """Select one or more samplers based on the selection strategy.

        Args:
            n: Number of samplers to select
            **kwargs: Additional parameters (may include population for context)

        Returns:
            List of selected samplers
        """
        if not self.samplers:
            raise ValueError("No samplers available for selection")

        selected = []
        available: list[BaseSampler] = self.samplers.copy()

        excluded_names = {
            str(name) for name in kwargs.get("exclude_sampler_names", set())
        }
        if excluded_names:
            available = [sampler for sampler in available if sampler.name not in excluded_names]

        maximum_parents = len(kwargs.get("population", []))

        available = [s for s in available if s.n_parents <= maximum_parents]

        for _ in range(n):
            if not available:
                break

            if self.selection_strategy == "random":
                sampler = random.choice(available)
            elif self.selection_strategy == "weighted":
                sampler = self._weighted_select(available)
            elif self.selection_strategy == "tournament":
                sampler = self._tournament_select(available)
            elif self.selection_strategy == "roulette":
                sampler = self._roulette_select(available)
            else:
                sampler = random.choice(available)

            selected.append(sampler)
            if n == 1:
                break
            # For multi-selection, allow same sampler to be selected multiple times
            # Remove if we want unique selection: available.remove(sampler)

        return selected

    def _weighted_select(self, available: list[BaseSampler]) -> BaseSampler:
        """Select sampler based on weights (higher weight = higher probability).

        Args:
            available: List of available samplers

        Returns:
            Selected sampler
        """
        weights = [self.weights.get(s.__class__.__name__, self.default_weight) for s in available]
        total = sum(weights)

        if total == 0:
            return random.choice(available)

        # Normalize weights and use cumulative distribution
        r = random.random() * total
        cumulative = 0
        for i, sampler in enumerate(available):
            cumulative += weights[i]
            if r <= cumulative:
                return sampler

        return available[-1]

    def _tournament_select(self, available: list[BaseSampler]) -> BaseSampler:
        """Select sampler using tournament selection.

        Args:
            available: List of available samplers

        Returns:
            Selected sampler
        """
        tournament_size = min(self.config.get("tournament_size", 3), len(available))
        competitors = random.sample(available, tournament_size)

        # Return the one with highest weight
        return max(competitors, key=lambda s: self.weights.get(s.__class__.__name__, self.default_weight))

    def _roulette_select(self, available: list[BaseSampler]) -> BaseSampler:
        """Select sampler using roulette wheel selection.

        Similar to weighted but handles negative weights differently.

        Args:
            available: List of available samplers

        Returns:
            Selected sampler
        """
        weights = [self.weights.get(s.__class__.__name__, self.default_weight) for s in available]
        min_weight = min(weights)

        # Shift weights to be positive
        weights = [w - min_weight for w in weights] if min_weight < 0 else weights.copy()

        total = sum(weights)
        if total == 0:
            return random.choice(available)

        r = random.random() * total
        cumulative = 0
        for i, sampler in enumerate(available):
            cumulative += weights[i]
            if r <= cumulative:
                return sampler

        return available[-1]

    def update_weight(self, sampler: BaseSampler, fitness: float) -> None:
        """Update the weight for a sampler based on fitness.

        Uses exponential moving average: new_weight = alpha * fitness + (1 - alpha) * old_weight

        Args:
            sampler: The sampler that produced the algorithm
            fitness: The fitness value of the produced algorithm
        """
        name = sampler.__class__.__name__
        old_weight = self.weights.get(name, self.default_weight)

        # Update using exponential moving average
        new_weight = self.ema_alpha * fitness + (1 - self.ema_alpha) * old_weight
        self.weights[name] = new_weight

        # Track fitness history
        self.fitness_history[name].append(fitness)

    def get_weight(self, sampler: BaseSampler) -> float:
        """Get the current weight for a sampler.

        Args:
            sampler: The sampler

        Returns:
            Current weight
        """
        return self.weights.get(sampler.__class__.__name__, self.default_weight)

    def get_best_sampler(self) -> BaseSampler:
        """Get the sampler with the highest weight.

        Returns:
            Sampler with highest weight
        """
        if not self.samplers:
            raise ValueError("No samplers available")

        return max(self.samplers, key=lambda s: self.weights.get(s.__class__.__name__, self.default_weight))

    def reset_weights(self) -> None:
        """Reset all weights to default value."""
        for name in self.weights:
            self.weights[name] = self.default_weight
        self.fitness_history = {name: [] for name in self.fitness_history}
