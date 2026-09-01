"""Tests for model-authored bounded parameter spaces."""

import pytest

from llm4ad.evaluator.solver import (
    ParameterSpace,
    maximize_bounded_parameters,
)


def test_parameter_space_accepts_model_defined_fixed_and_tunable_values() -> None:
    """The candidate chooses parameter names, starting points, and domains."""
    space = ParameterSpace.from_mapping(
        {
            "cx": 0.5,
            "step": {"initial": 0.18, "lower": 0.1, "upper": 0.24},
            "phase": {"initial": 0.0, "lower": -0.2, "upper": 0.3},
        },
        max_dimensions=8,
        reserved_names={"i", "count", "group"},
    )

    assert space.names == ("step", "phase")
    assert space.initial == pytest.approx((0.18, 0.0))
    assert space.bounds[0] == pytest.approx((0.1, 0.24))
    assert space.bounds[1] == pytest.approx((-0.2, 0.3))
    assert space.resolve((0.2, 0.1)) == pytest.approx(
        {"cx": 0.5, "step": 0.2, "phase": 0.1}
    )


def test_parameter_space_builds_initial_patch_for_optimized_values() -> None:
    """Optimized values should become reusable initials without changing bounds."""
    space = ParameterSpace.from_mapping(
        {
            "fixed": 1.0,
            "step": {"initial": 0.18, "lower": 0.1, "upper": 0.24},
            "phase": {"initial": 0.0, "lower": -0.2, "upper": 0.3},
        },
        max_dimensions=8,
    )

    patch = space.initial_value_patch({"step": 0.22, "phase": 0.1})

    assert patch == {
        "step": {"initial": 0.22},
        "phase": {"initial": 0.1},
    }


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"i": 0.5}, "reserved"),
        ({"step": {"initial": 0.2, "lower": 0.3, "upper": 0.1}}, "lower"),
        ({"step": {"initial": 0.4, "lower": 0.1, "upper": 0.3}}, "initial"),
        ({"step": {"initial": 0.2, "lower": 0.1}}, "exactly"),
    ],
)
def test_parameter_space_rejects_unsafe_or_ambiguous_domains(
    parameters: dict[str, object],
    message: str,
) -> None:
    """Malformed model-authored domains fail before invoking an optimizer."""
    with pytest.raises(ValueError, match=message):
        ParameterSpace.from_mapping(
            parameters,
            max_dimensions=8,
            reserved_names={"i", "count", "group"},
        )


def test_parameter_space_enforces_resource_dimension_cap() -> None:
    """Platform limits cap cost without prescribing mathematical bounds."""
    parameters = {
        f"p{index}": {"initial": 0.5, "lower": 0.0, "upper": 1.0}
        for index in range(3)
    }

    with pytest.raises(ValueError, match="at most 2"):
        ParameterSpace.from_mapping(parameters, max_dimensions=2)


def test_search_optimizes_over_the_candidate_declared_domain() -> None:
    """Search follows arbitrary candidate bounds instead of built-in geometry ranges."""
    space = ParameterSpace.from_mapping(
        {
            "x": {"initial": -4.0, "lower": -10.0, "upper": 20.0},
            "y": {"initial": 8.0, "lower": -3.0, "upper": 12.0},
        },
        max_dimensions=4,
    )

    result = maximize_bounded_parameters(
        space,
        lambda values: -((values["x"] - 7.5) ** 2) - ((values["y"] - 1.25) ** 2),
        max_evaluations=100,
        seed=17,
        population_size=6,
    )

    assert result is not None
    assert result.objective > -0.2
    assert result.parameters["x"] == pytest.approx(7.5, abs=0.4)
    assert result.parameters["y"] == pytest.approx(1.25, abs=0.4)
    assert result.evaluations <= 100


def test_search_refines_inside_a_very_narrow_feasible_region() -> None:
    """Adaptive local steps should shrink around a feasible anchor."""
    space = ParameterSpace.from_mapping(
        {
            "x": {"initial": 0.5, "lower": 0.0, "upper": 1.0},
        },
        max_dimensions=2,
    )

    def objective(values: dict[str, float]) -> float | None:
        x = values["x"]
        if abs(x - 0.5007) > 0.001:
            return None
        return 1.0 - abs(x - 0.5007) * 1000.0

    result = maximize_bounded_parameters(
        space,
        objective,
        max_evaluations=96,
        seed=23,
        population_size=6,
    )

    assert result is not None
    assert result.objective > 0.8
    assert result.parameters["x"] == pytest.approx(0.5007, abs=0.0002)
    assert result.invalid_evaluations > 0


def test_search_discovers_a_better_disconnected_feasible_component() -> None:
    """Global restarts should not remain trapped in the initial feasible island."""
    space = ParameterSpace.from_mapping(
        {
            "x": {"initial": 0.2, "lower": 0.0, "upper": 1.0},
            "y": {"initial": 0.2, "lower": 0.0, "upper": 1.0},
        },
        max_dimensions=2,
    )

    def objective(values: dict[str, float]) -> float | None:
        x, y = values["x"], values["y"]
        if (x - 0.2) ** 2 + (y - 0.2) ** 2 <= 0.02**2:
            return 0.1 - ((x - 0.2) ** 2 + (y - 0.2) ** 2)
        if (x - 0.78) ** 2 + (y - 0.72) ** 2 <= 0.12**2:
            return 1.0 - ((x - 0.78) ** 2 + (y - 0.72) ** 2)
        return None

    result = maximize_bounded_parameters(
        space,
        objective,
        max_evaluations=128,
        seed=11,
        population_size=6,
    )

    assert result is not None
    assert result.objective > 0.98
    assert result.parameters["x"] == pytest.approx(0.78, abs=0.12)
    assert result.parameters["y"] == pytest.approx(0.72, abs=0.12)
    assert result.invalid_evaluations > 0


def test_search_handles_high_dimension_with_less_than_one_population_budget() -> None:
    """Search must remain useful when the budget is smaller than dimension times population."""
    parameters = {
        f"p{index}": {
            "initial": 1000.0 + index * 1e-6,
            "lower": 1000.0 + index * 1e-6 - 5e-9,
            "upper": 1000.0 + index * 1e-6 + 5e-9,
        }
        for index in range(52)
    }
    space = ParameterSpace.from_mapping(parameters, max_dimensions=64)
    initial = space.resolve()

    result = maximize_bounded_parameters(
        space,
        lambda values: -sum((values[name] - initial[name]) ** 2 for name in values),
        max_evaluations=40,
        seed=31,
        population_size=6,
    )

    assert result is not None
    assert result.objective == pytest.approx(0.0)
    assert result.parameters == pytest.approx(initial)
    assert result.evaluations == 40
    assert result.strategy == "hybrid_qmc_pattern"
