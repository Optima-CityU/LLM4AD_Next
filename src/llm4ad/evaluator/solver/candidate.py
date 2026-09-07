"""Safe loading for structured mathematical candidates."""

from __future__ import annotations

import ast
import copy
import json
import pprint
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


class CandidateLoadError(ValueError):
    """Raised when a structured candidate is missing, unsafe, or malformed."""


class CandidateUpdateError(ValueError):
    """Raised when an evaluator patch cannot safely update a candidate."""


def _load_python_literal(path: Path, symbol: str) -> object:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise CandidateLoadError(f"Unable to parse candidate: {exc}") from exc

    value_node: ast.expr | None = None
    for node in tree.body:
        match node:
            case ast.Assign(targets=targets, value=value) if any(
                isinstance(target, ast.Name) and target.id == symbol for target in targets
            ):
                value_node = value
            case ast.AnnAssign(target=ast.Name(id=name), value=value) if (
                name == symbol and value is not None
            ):
                value_node = value

    if value_node is None:
        raise CandidateLoadError(f"Candidate symbol {symbol!r} was not found in {path.name}")

    try:
        return ast.literal_eval(value_node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise CandidateLoadError(
            f"Candidate symbol {symbol!r} must contain only Python literal values"
        ) from exc


def load_candidate(path: Path, *, symbol: str = "MODEL_SPEC") -> Mapping[str, Any]:
    """Load JSON, YAML, or a named Python literal assignment without executing code."""
    if not path.is_file():
        raise CandidateLoadError(f"Candidate file not found: {path}")

    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() in {".yaml", ".yml"}:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() == ".py":
            value = _load_python_literal(path, symbol)
        else:
            raise CandidateLoadError(
                f"Unsupported candidate format {path.suffix!r}; use .json, .yaml, .yml, or .py"
            )
    except CandidateLoadError:
        raise
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise CandidateLoadError(f"Unable to load candidate: {exc}") from exc

    if not isinstance(value, dict):
        raise CandidateLoadError("Candidate root must be an object/mapping")
    return value


def _merge_existing_fields(
    target: dict[str, Any],
    patch: Mapping[str, Any],
    *,
    path: tuple[str, ...] = (),
) -> None:
    """Deep-merge a trusted patch without allowing it to expand the schema."""
    for key, patch_value in patch.items():
        if not isinstance(key, str) or key not in target:
            location = ".".join((*path, str(key)))
            raise CandidateUpdateError(
                f"Candidate patch references unknown field {location!r}"
            )
        current = target[key]
        if isinstance(patch_value, Mapping):
            if not isinstance(current, dict):
                location = ".".join((*path, key))
                raise CandidateUpdateError(
                    f"Candidate patch field {location!r} is not an object"
                )
            _merge_existing_fields(current, patch_value, path=(*path, key))
        else:
            target[key] = patch_value


def _python_value_span(source: str, symbol: str) -> tuple[int, int]:
    """Return character offsets for a literal assignment's value expression."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CandidateUpdateError(f"Unable to parse candidate: {exc}") from exc

    value_node: ast.expr | None = None
    for node in tree.body:
        match node:
            case ast.Assign(targets=targets, value=value) if any(
                isinstance(target, ast.Name) and target.id == symbol for target in targets
            ):
                value_node = value
            case ast.AnnAssign(target=ast.Name(id=name), value=value) if (
                name == symbol and value is not None
            ):
                value_node = value
    if value_node is None or value_node.end_lineno is None or value_node.end_col_offset is None:
        raise CandidateUpdateError(f"Candidate symbol {symbol!r} was not found")

    lines = source.splitlines(keepends=True)

    def offset(line_number: int, byte_column: int) -> int:
        prefix = sum(len(line) for line in lines[: line_number - 1])
        line = lines[line_number - 1]
        column = len(line.encode("utf-8")[:byte_column].decode("utf-8"))
        return prefix + column

    return (
        offset(value_node.lineno, value_node.col_offset),
        offset(value_node.end_lineno, value_node.end_col_offset),
    )


def apply_candidate_patch(
    path: Path,
    patch: Mapping[str, Any],
    *,
    symbol: str = "MODEL_SPEC",
) -> bool:
    """Apply a trusted deep patch to an existing structured candidate.

    Python candidates keep text outside the literal assignment intact; JSON and
    YAML candidates retain their format.  Unknown paths are rejected so evaluator
    feedback cannot silently change the candidate schema.
    """
    if not patch:
        return False
    current = dict(load_candidate(path, symbol=symbol))
    updated = copy.deepcopy(current)
    _merge_existing_fields(updated, patch)
    if updated == current:
        return False

    suffix = path.suffix.lower()
    if suffix == ".py":
        source = path.read_text(encoding="utf-8")
        start, end = _python_value_span(source, symbol)
        rendered = pprint.pformat(updated, width=100, sort_dicts=False)
        new_source = f"{source[:start]}{rendered}{source[end:]}"
    elif suffix == ".json":
        new_source = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    elif suffix in {".yaml", ".yml"}:
        new_source = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False)
    else:
        raise CandidateUpdateError(
            f"Unsupported candidate format {path.suffix!r}; use .json, .yaml, .yml, or .py"
        )

    path.write_text(new_source, encoding="utf-8")
    load_candidate(path, symbol=symbol)
    return True
