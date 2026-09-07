"""Solver backend factory with lazy optional dependency loading."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class SolverDependencyError(RuntimeError):
    """Raised when an optional solver dependency is unavailable."""


class ScipBackend:
    """Small PySCIPOpt facade shared by problem adapters."""

    name = "scip"

    def __init__(self, options: Mapping[str, bool | int | float | str] | None = None) -> None:
        """Store trusted backend options supplied by the task configuration."""
        self.options = dict(options or {})

    def create_model(self, name: str, *, timeout: float) -> Any:
        """Create a quiet SCIP model with trusted runtime limits applied."""
        try:
            from pyscipopt import Model
        except ImportError as exc:
            raise SolverDependencyError(
                "The configured mathematical solver backend is not installed"
            ) from exc

        model = Model(name)
        model.hideOutput()
        model.setParam("limits/time", float(timeout))
        for parameter, value in self.options.items():
            model.setParam(parameter, value)
        return model


def create_backend(
    name: str,
    options: Mapping[str, bool | int | float | str] | None = None,
) -> ScipBackend:
    """Create a configured mathematical solver backend."""
    if name == "scip":
        return ScipBackend(options)
    raise ValueError(f"Unsupported solver backend: {name}")
