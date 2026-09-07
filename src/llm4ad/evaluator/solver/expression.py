"""Restricted arithmetic expression evaluation for model-authored formulas."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping


class ExpressionError(ValueError):
    """Raised when a formula uses unsafe syntax or produces an invalid number."""


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "cos": math.cos,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "min": min,
    "max": max,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


def validate_numeric_expression(
    expression: str,
    variable_names: set[str] | frozenset[str],
) -> None:
    """Validate formula syntax and symbols without evaluating a parameter point."""
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        location = f" at line {exc.lineno}, column {exc.offset}" if exc.offset else ""
        raise ExpressionError(f"Invalid expression syntax{location}: {exc.msg}") from exc

    allowed_names = set(variable_names) | set(_CONSTANTS)

    def _validate(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            _validate(node.body)
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return
        if isinstance(node, ast.Name):
            if node.id not in allowed_names:
                raise ExpressionError(f"Unknown symbol {node.id!r}")
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            _validate(node.left)
            _validate(node.right)
            return
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            _validate(node.operand)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in _FUNCTIONS or node.keywords:
                raise ExpressionError(f"Function {node.func.id!r} is not allowed")
            for argument in node.args:
                _validate(argument)
            return
        raise ExpressionError(f"Expression element {type(node).__name__} is not allowed")

    _validate(root)


def evaluate_numeric_expression(expression: str, variables: Mapping[str, float | int]) -> float:
    """Evaluate a numeric expression using a small, explicit arithmetic grammar."""
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Invalid expression syntax: {exc.msg}") from exc

    names = {**_CONSTANTS, **variables}

    def _evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ExpressionError(f"Unknown symbol {node.id!r}")
            return float(names[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            operation = _BINARY_OPERATORS[type(node.op)]
            return float(operation(_evaluate(node.left), _evaluate(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            operation = _UNARY_OPERATORS[type(node.op)]
            return float(operation(_evaluate(node.operand)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _FUNCTIONS.get(node.func.id)
            if function is None or node.keywords:
                raise ExpressionError(f"Function {node.func.id!r} is not allowed")
            return float(function(*(_evaluate(argument) for argument in node.args)))
        raise ExpressionError(f"Expression element {type(node).__name__} is not allowed")

    try:
        result = _evaluate(root)
    except ExpressionError:
        raise
    except (ArithmeticError, OverflowError, ValueError) as exc:
        raise ExpressionError(f"Expression could not be evaluated: {exc}") from exc

    if not math.isfinite(result):
        raise ExpressionError("Expression result must be finite")
    return result
