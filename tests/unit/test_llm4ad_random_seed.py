"""Tests for applying the configured local random seed."""

import random

import numpy as np

from llm4ad.llm4ad import _seed_local_random_generators


def test_seed_local_random_generators_replays_python_and_numpy_sequences() -> None:
    _seed_local_random_generators(1729)
    first = (random.random(), np.random.random())

    _seed_local_random_generators(1729)
    second = (random.random(), np.random.random())

    assert second == first
