"""Evaluator for the Hermite-polynomial uncertainty inequality."""

import math
from typing import Any

import numpy as np
import sympy
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator, MetricType


def _upper_bound(coefficients: np.ndarray) -> float:
    """Apply the benchmark's symbolic Hermite construction and root test."""
    x = sympy.symbols('x')
    degrees = np.arange(0, 4 * len(coefficients) + 4, 4)
    polynomials = [sympy.polys.orthopolys.hermite_poly(n=int(degree), x=x, polys=False) for degree in degrees]
    rational = [sympy.Rational(value) for value in coefficients]
    partial = sum((rational[index] * polynomials[index] for index in range(len(rational))))
    rational.append(sympy.Rational(-partial.subs(x, 0) / polynomials[-1].subs(x, 0)))
    expression = sum((rational[index] * polynomials[index] for index in range(len(rational))))
    if sympy.limit(expression, x, sympy.oo) < 0:
        expression = -expression
    if expression.subs(x, 0) != 0:
        return 0.0
    if sympy.limit(expression, x, sympy.oo) <= 0:
        return 0.0
    quotient = sympy.exquo(expression, x ** 2)
    # A real polynomial changes sign exactly at roots of odd multiplicity.
    # This exact test handles Rational/radical roots as well as CRootOf, and
    # cannot miss a sign change by sampling on the wrong side of a rounded root.
    sign_changes = [root for root, multiplicity in sympy.Poly(quotient, x).real_roots(multiple=False)
                    if multiplicity % 2 and root > 0]
    largest_sign_change = max(sign_changes, default=sympy.Integer(0))
    return float(sympy.N(largest_sign_change ** 2, 80)) / (2 * math.pi)


@BaseEvaluator.register("alphaevolve_uncertainty_evaluator")
class UncertaintyInequalityEvaluator(JsonBenchmarkEvaluator):
    metric_name = "c_upper_bound"
    metric_type = MetricType.MINIMIZE
    target_value = 0.3521
    score_mode = "target_over_objective"
    registry_key = "alphaevolve_uncertainty_evaluator"
    benchmark_key = "uncertainty_inequality"

    def measure(self, payload: dict[str, Any]) -> float:
        coefficients = finite_array(payload, "coefficients")
        if coefficients.ndim != 1 or len(coefficients) == 0:
            raise ValueError("coefficients must be a non-empty one-dimensional sequence")
        upper_bound = _upper_bound(coefficients)
        if not math.isfinite(upper_bound) or upper_bound <= 0:
            raise ValueError("coefficients must define a positive upper bound")
        return upper_bound
