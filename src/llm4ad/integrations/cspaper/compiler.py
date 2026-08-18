"""Compile CSPaper Markdown reviews into auditable algorithm design specs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from llm4ad.integrations.cspaper.schemas import (
    AlgorithmDesignSpec,
    BaselineSpec,
    CandidateScope,
    ConstraintSpec,
    DatasetRequirement,
    DatasetSpec,
    ExcludedSuggestion,
    ObjectiveSpec,
    PaperReference,
    PendingSuggestion,
    ProblemDefinition,
    SearchDirection,
    SuggestionEvidence,
)

_ANNOTATION_RE = re.compile(r"<!--\s*cspaper:\s*(.*?)\s*-->", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_CATEGORY_ALIASES = {
    "direction": "search_direction",
    "search": "search_direction",
    "search_direction": "search_direction",
    "objective": "objective",
    "metric": "objective",
    "constraint": "constraint",
    "hard_constraint": "constraint",
    "dataset": "dataset",
    "data": "dataset",
    "baseline": "baseline",
    "writing": "writing",
    "excluded": "writing",
    "pending": "pending",
}

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "writing": (
        "writing",
        "clarity",
        "presentation",
        "related work",
        "citation",
        "typo",
        "rewrite",
        "写作",
        "表达",
        "相关工作",
        "引用",
    ),
    "dataset": (
        "dataset",
        "instance",
        "benchmark set",
        "out-of-distribution",
        "large-scale",
        "数据集",
        "实例规模",
        "分布外",
        "大规模实例",
    ),
    "baseline": (
        "baseline",
        "compare with",
        "comparison method",
        "state of the art",
        "基线",
        "对比算法",
        "比较方法",
    ),
    "constraint": (
        "must satisfy",
        "feasible",
        "feasibility",
        "constraint",
        "capacity",
        "valid solution",
        "exactly once",
        "必须满足",
        "可行解",
        "约束",
        "容量",
        "恰好一次",
    ),
    "objective": (
        "runtime",
        "latency",
        "memory",
        "accuracy",
        "cost",
        "distance",
        "gap",
        "throughput",
        "objective value",
        "运行时间",
        "内存",
        "准确率",
        "成本",
        "距离",
        "目标值",
    ),
    "search_direction": (
        "algorithm",
        "operator",
        "heuristic",
        "adaptive",
        "neighborhood",
        "restart",
        "selection strategy",
        "search strategy",
        "算法",
        "算子",
        "启发式",
        "自适应",
        "邻域",
        "重启",
        "搜索策略",
    ),
}

_MINIMIZE_WORDS = (
    "minimize",
    "reduce",
    "lower",
    "shorter",
    "runtime",
    "latency",
    "memory",
    "cost",
    "distance",
    "gap",
    "最小化",
    "减少",
    "降低",
    "运行时间",
    "内存",
    "成本",
    "距离",
)
_MAXIMIZE_WORDS = (
    "maximize",
    "increase",
    "improve accuracy",
    "accuracy",
    "quality",
    "throughput",
    "最大化",
    "提高准确",
    "提升质量",
    "吞吐",
)


class SuggestionCompiler:
    """Conservatively map review bullets to an ``AlgorithmDesignSpec``."""

    def compile_file(
        self,
        review_path: str | Path,
        *,
        paper_path: str | Path | None = None,
        code_path: str | Path | None = None,
        train_data: str | Path | None = None,
        validation_data: str | Path | None = None,
        test_data: str | Path | None = None,
        hidden_test_data: str | Path | None = None,
    ) -> AlgorithmDesignSpec:
        """Compile a saved CSPaper Markdown result."""
        path = Path(review_path).expanduser().resolve()
        text = path.read_text(encoding="utf-8")
        return self.compile_text(
            text,
            review_path=path,
            paper_path=paper_path,
            code_path=code_path,
            train_data=train_data,
            validation_data=validation_data,
            test_data=test_data,
            hidden_test_data=hidden_test_data,
        )

    def compile_text(
        self,
        review_text: str,
        *,
        review_path: str | Path | None = None,
        paper_path: str | Path | None = None,
        code_path: str | Path | None = None,
        train_data: str | Path | None = None,
        validation_data: str | Path | None = None,
        test_data: str | Path | None = None,
        hidden_test_data: str | Path | None = None,
    ) -> AlgorithmDesignSpec:
        """Compile Markdown while preserving every source statement."""
        frontmatter, body = _split_frontmatter(review_text)
        suggestions = _extract_suggestions(body)
        review_sha = hashlib.sha256(review_text.encode("utf-8")).hexdigest()

        paper = PaperReference(
            title=str(frontmatter.get("paper") or frontmatter.get("title") or ""),
            source_path=_path_string(paper_path or frontmatter.get("paper_path")),
            source_url=str(frontmatter.get("paper_url") or frontmatter.get("url") or ""),
            cspaper_job_id=str(frontmatter.get("job_id") or ""),
            review_agent_id=str(frontmatter.get("agent_id") or ""),
            review_path=_path_string(review_path),
            review_sha256=review_sha,
        )
        problem = ProblemDefinition(
            name=str(frontmatter.get("problem_name") or "paper_algorithm"),
            type=str(frontmatter.get("problem_type") or "algorithm_optimization"),
            description=str(
                frontmatter.get("problem_description")
                or frontmatter.get("description")
                or _first_paragraph(body)
            ),
            function_name=str(frontmatter.get("function_name") or ""),
            input_format=str(frontmatter.get("input_format") or ""),
            output_format=str(frontmatter.get("output_format") or ""),
        )
        allowed = frontmatter.get("allowed_files") or []
        if isinstance(allowed, str):
            allowed = [item.strip() for item in allowed.split(",") if item.strip()]
        scope = CandidateScope(
            code_path=_path_string(code_path or frontmatter.get("code_path")),
            function_name=str(
                frontmatter.get("candidate_function")
                or frontmatter.get("function_name")
                or ""
            ),
            allowed_files=list(allowed),
            notes=str(frontmatter.get("candidate_scope") or ""),
        )
        datasets = DatasetSpec(
            train=_path_string(train_data or frontmatter.get("train_data")),
            validation=_path_string(validation_data or frontmatter.get("validation_data")),
            test=_path_string(test_data or frontmatter.get("test_data")),
            hidden_test=_path_string(hidden_test_data or frontmatter.get("hidden_test_data")),
        )

        spec = AlgorithmDesignSpec(
            paper=paper,
            problem=problem,
            candidate_scope=scope,
            datasets=datasets,
            metadata={"compiler": "markdown-v1", "frontmatter": frontmatter},
        )
        for index, item in enumerate(suggestions, start=1):
            suggestion_id = str(item["metadata"].get("id") or f"suggestion-{index}")
            category = _normalize_category(
                str(item["metadata"].get("category") or ""),
                str(item["heading"]),
                str(item["text"]),
            )
            evidence = SuggestionEvidence(
                id=suggestion_id,
                text=str(item["text"]),
                category=category,
                heading=str(item["heading"]),
                metadata=dict(item["metadata"]),
            )
            spec.evidence.append(evidence)
            self._compile_evidence(spec, evidence)
        return spec

    def _compile_evidence(
        self,
        spec: AlgorithmDesignSpec,
        evidence: SuggestionEvidence,
    ) -> None:
        """Append one typed object for a normalized review statement."""
        metadata = evidence.metadata
        if evidence.category == "search_direction":
            spec.search_directions.append(
                SearchDirection(
                    id=str(metadata.get("name") or f"direction-{len(spec.search_directions) + 1}"),
                    description=evidence.text,
                    priority=_priority(metadata, evidence.text),
                    rationale=str(metadata.get("rationale") or ""),
                    source_suggestion_id=evidence.id,
                )
            )
        elif evidence.category == "objective":
            name = str(metadata.get("name") or _infer_metric_name(evidence.text))
            direction = str(metadata.get("direction") or _infer_direction(evidence.text))
            if not name or direction not in {"minimize", "maximize"}:
                spec.pending_suggestions.append(
                    PendingSuggestion(
                        text=evidence.text,
                        reason="objective requires an explicit metric name and direction",
                        source_suggestion_id=evidence.id,
                    )
                )
                return
            spec.objectives.append(
                ObjectiveSpec(
                    name=name,
                    direction=direction,
                    weight=float(metadata.get("weight") or 1.0),
                    measurement=str(metadata.get("measurement") or evidence.text),
                    aggregation=str(metadata.get("aggregation") or "mean"),
                    unit=str(metadata.get("unit") or ""),
                    source_suggestion_id=evidence.id,
                )
            )
        elif evidence.category == "constraint":
            spec.constraints.append(
                ConstraintSpec(
                    name=str(
                        metadata.get("name")
                        or _slug(evidence.text, f"constraint_{len(spec.constraints) + 1}")
                    ),
                    type=str(metadata.get("type") or "hard"),
                    check=str(metadata.get("check") or evidence.text),
                    source_suggestion_id=evidence.id,
                )
            )
        elif evidence.category == "dataset":
            spec.datasets.requirements.append(
                DatasetRequirement(
                    description=evidence.text,
                    source_suggestion_id=evidence.id,
                )
            )
        elif evidence.category == "baseline":
            spec.baselines.append(
                BaselineSpec(
                    name=str(
                        metadata.get("name")
                        or _slug(evidence.text, f"baseline_{len(spec.baselines) + 1}")
                    ),
                    required=_as_bool(metadata.get("required"), default=True),
                    command=str(metadata.get("command") or ""),
                    source_suggestion_id=evidence.id,
                )
            )
        elif evidence.category == "writing":
            spec.excluded_suggestions.append(
                ExcludedSuggestion(
                    text=evidence.text,
                    reason="writing-only feedback is outside algorithm evolution",
                    source_suggestion_id=evidence.id,
                )
            )
        else:
            spec.pending_suggestions.append(
                PendingSuggestion(
                    text=evidence.text,
                    reason="could not safely map review language to an executable requirement",
                    source_suggestion_id=evidence.id,
                )
            )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}, text
    loaded = yaml.safe_load("\n".join(lines[1:end])) or {}
    return (loaded if isinstance(loaded, dict) else {}), "\n".join(lines[end + 1 :])


def _extract_suggestions(body: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    heading = ""
    pending_metadata: dict[str, str] = {}
    for line in body.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            heading = heading_match.group(1).strip()
            continue
        annotation = _ANNOTATION_RE.search(line)
        if annotation:
            pending_metadata = _parse_annotation(annotation.group(1))
            line = _ANNOTATION_RE.sub("", line)
        list_match = _LIST_RE.match(line)
        if not list_match:
            continue
        text = list_match.group(1).strip()
        if not text:
            continue
        suggestions.append(
            {"text": text, "heading": heading, "metadata": pending_metadata}
        )
        pending_metadata = {}
    return suggestions


def _parse_annotation(raw: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        output[key.strip().lower()] = value.strip().strip('"\'')
    return output


def _normalize_category(explicit: str, heading: str, text: str) -> str:
    normalized = explicit.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[normalized]
    haystack = f"{heading} {text}".lower()
    scores = {
        category: sum(keyword in haystack for keyword in keywords)
        for category, keywords in _CATEGORY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    best_score = scores[best]
    ties = sum(score == best_score and score > 0 for score in scores.values())
    return best if best_score > 0 and ties == 1 else "pending"


def _infer_direction(text: str) -> str:
    lowered = text.lower()
    minimize = sum(word in lowered for word in _MINIMIZE_WORDS)
    maximize = sum(word in lowered for word in _MAXIMIZE_WORDS)
    if minimize > maximize:
        return "minimize"
    if maximize > minimize:
        return "maximize"
    return ""


def _infer_metric_name(text: str) -> str:
    lowered = text.lower()
    candidates = (
        ("optimality_gap_pct", ("optimality gap", "gap", "最优差距")),
        ("runtime_ms", ("runtime", "latency", "wall-clock", "运行时间", "耗时")),
        ("memory_mb", ("memory", "内存")),
        ("accuracy", ("accuracy", "准确率")),
        ("solution_cost", ("solution cost", "objective value", "成本", "目标值")),
        ("total_distance", ("distance", "路径长度", "距离")),
        ("throughput", ("throughput", "吞吐")),
    )
    for name, keywords in candidates:
        if any(keyword in lowered for keyword in keywords):
            return name
    return ""


def _priority(metadata: dict[str, Any], text: str) -> str:
    explicit = str(metadata.get("priority") or metadata.get("severity") or "").lower()
    if explicit in {"low", "medium", "high"}:
        return explicit
    lowered = text.lower()
    if any(word in lowered for word in ("critical", "major", "must", "严重", "必须")):
        return "high"
    return "medium"


def _slug(text: str, fallback: str) -> str:
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")
    return slug[:64] or fallback


def _first_paragraph(body: str) -> str:
    paragraphs = re.split(r"\n\s*\n", body.strip())
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if stripped and not stripped.startswith(("#", "-", "*")):
            return " ".join(stripped.split())
    return "Algorithm optimization task derived from a CSPaper review."


def _path_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
