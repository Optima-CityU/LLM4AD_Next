"""Deterministic protections for paper revision candidates."""

from __future__ import annotations

import re
from collections import Counter

from llm4ad.evaluator.paper_revision.schemas import (
    CandidateRevision,
    PaperRevisionTask,
    StaticCheckResult,
)

_CITATION_RE = re.compile(
    r"\\(?:cite\w*|autocite\w*|parencite\w*|textcite\w*|footcite\w*)"
    r"\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![\w\\])[-+]?\d+(?:[.,]\d+)*(?:\s*(?:%|\\%))?")
_BEGIN_ENV_RE = re.compile(r"\\begin\{([^}]+)\}")
_END_ENV_RE = re.compile(r"\\end\{([^}]+)\}")


def _citation_keys(text: str) -> Counter[str]:
    """Extract LaTeX citation keys with occurrence counts."""
    keys: list[str] = []
    for match in _CITATION_RE.finditer(text):
        keys.extend(key.strip() for key in match.group(1).split(",") if key.strip())
    return Counter(keys)


def _numbers(text: str) -> Counter[str]:
    """Extract normalized numeric claims for conservative comparison."""
    return Counter(re.sub(r"\s+", "", match.group(0)) for match in _NUMBER_RE.finditer(text))


def _balanced_braces(text: str) -> bool:
    """Check unescaped LaTeX braces without attempting full TeX parsing."""
    depth = 0
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def validate_revision(
    task: PaperRevisionTask,
    candidate: CandidateRevision,
) -> StaticCheckResult:
    """Validate objective invariants before spending LLM calls."""
    original = task.original_text
    revised = candidate.revised_text
    constraints = task.constraints
    errors: list[str] = []
    warnings: list[str] = []

    if candidate.section_id and candidate.section_id != task.section_id:
        errors.append(
            f"Candidate section_id '{candidate.section_id}' does not match '{task.section_id}'."
        )

    original_len = max(1, len(original.strip()))
    length_ratio = len(revised.strip()) / original_len
    if length_ratio < constraints.min_length_ratio:
        errors.append(
            f"Revision length ratio {length_ratio:.2f} is below "
            f"{constraints.min_length_ratio:.2f}."
        )
    if length_ratio > constraints.max_length_ratio:
        errors.append(
            f"Revision length ratio {length_ratio:.2f} exceeds "
            f"{constraints.max_length_ratio:.2f}."
        )

    original_citations = _citation_keys(original)
    revised_citations = _citation_keys(revised)
    removed_citations = original_citations - revised_citations
    added_citations = revised_citations - original_citations
    if constraints.preserve_citations and removed_citations:
        errors.append(f"Removed citation keys: {sorted(removed_citations.elements())}")
    if not constraints.allow_new_citations and added_citations:
        errors.append(f"Added unapproved citation keys: {sorted(added_citations.elements())}")

    original_numbers = _numbers(original)
    revised_numbers = _numbers(revised)
    if constraints.preserve_numbers and original_numbers != revised_numbers:
        removed_numbers = sorted((original_numbers - revised_numbers).elements())
        added_numbers = sorted((revised_numbers - original_numbers).elements())
        errors.append(
            "Numeric claims changed" f" (removed={removed_numbers}, added={added_numbers})."
        )

    for term in constraints.locked_terms:
        if original.count(term) != revised.count(term):
            errors.append(f"Locked term occurrence changed: {term!r}.")
    for claim in constraints.locked_claims:
        if claim not in revised:
            errors.append(f"Locked claim is missing: {claim!r}.")

    if constraints.preserve_latex_structure:
        if not _balanced_braces(revised):
            errors.append("Revision contains unbalanced LaTeX braces.")
        if Counter(_BEGIN_ENV_RE.findall(revised)) != Counter(_END_ENV_RE.findall(revised)):
            errors.append("Revision contains unmatched LaTeX environments.")

    if revised.strip() == original.strip():
        warnings.append("Revision is identical to the original text.")

    return StaticCheckResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        penalty=float(len(warnings)),
        details={
            "length_ratio": length_ratio,
            "original_citations": sorted(original_citations.elements()),
            "revised_citations": sorted(revised_citations.elements()),
            "original_numbers": sorted(original_numbers.elements()),
            "revised_numbers": sorted(revised_numbers.elements()),
        },
    )
