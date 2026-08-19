"""Application-owned plan artifacts exposed to the parser through SDK MCP tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_LOSS_LEVELS = {"lossless", "light", "lossy"}
MAX_CANDIDATES = 8


def _text(value: object, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{field} must contain 1-{maximum} characters")
    return result


def _text_list(value: object, field: str, maximum_items: int, maximum_length: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{field} must be a list with at most {maximum_items} items")
    return [_text(item, field, maximum_length) for item in value]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class PlanStore:
    """Validate and persist model-produced plans without arbitrary filesystem access."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.planning_dir = output_dir / "planning"
        self.candidates_dir = self.planning_dir / "candidates"
        self.analysis_path = self.planning_dir / "source-analysis.json"
        self.manifest_path = self.planning_dir / "manifest.json"
        self.legacy_plan_path = output_dir / "plan.json"

    def save_source_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_overview = payload.get("source_overview")
        if not isinstance(raw_overview, list) or len(raw_overview) > 20:
            raise ValueError("source_overview must contain at most 20 entries")
        overview = []
        for item in raw_overview:
            if not isinstance(item, dict):
                raise ValueError("source_overview entries must be objects")
            overview.append(
                {
                    "filename": _text(item.get("filename"), "filename", 255),
                    "summary": _text(item.get("summary"), "summary", 120),
                    "key_sections": _text_list(item.get("key_sections", []), "key_sections", 5, 120),
                }
            )
        normalized = {
            "topic_summary": _text(payload.get("topic_summary"), "topic_summary", 300),
            "source_overview": overview,
        }
        _atomic_json(self.analysis_path, normalized)
        return normalized

    def upsert_plan_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = _text(payload.get("id"), "id", 128)
        if not _ID_PATTERN.fullmatch(candidate_id):
            raise ValueError("candidate id must use lowercase letters, numbers, and hyphens")
        existing_ids = {path.stem for path in self.candidates_dir.glob("*.json")}
        if candidate_id not in existing_ids and len(existing_ids) >= MAX_CANDIDATES:
            raise ValueError(f"at most {MAX_CANDIDATES} candidates are allowed")
        loss_level = str(payload.get("loss_level") or "")
        if loss_level not in _LOSS_LEVELS:
            raise ValueError("invalid loss_level")
        raw_documents = payload.get("documents")
        if not isinstance(raw_documents, list) or not 1 <= len(raw_documents) <= 20:
            raise ValueError("documents must contain 1-20 entries")
        documents = []
        for item in raw_documents:
            if not isinstance(item, dict):
                raise ValueError("document entries must be objects")
            source_coverage = _text_list(item.get("source_coverage"), "source_coverage", 5, 180)
            if not source_coverage:
                raise ValueError("source_coverage must not be empty")
            documents.append(
                {
                    "title": _text(item.get("title"), "title", 255),
                    "purpose": _text(item.get("purpose"), "purpose", 100),
                    "source_coverage": source_coverage,
                    "must_preserve": _text_list(item.get("must_preserve", []), "must_preserve", 3, 180),
                }
            )
        if payload.get("document_count") != len(documents):
            raise ValueError("document_count must match documents length")
        normalized = {
            "id": candidate_id,
            "name": _text(payload.get("name"), "name", 255),
            "description": _text(payload.get("description"), "description", 200),
            "loss_level": loss_level,
            "document_count": len(documents),
            "documents": documents,
            "deduplication_policy": _text(
                payload.get("deduplication_policy"),
                "deduplication_policy",
                300,
            ),
        }
        _atomic_json(self.candidates_dir / f"{candidate_id}.json", normalized)
        return normalized

    def get_plan_candidate(self, candidate_id: str) -> dict[str, Any]:
        if not _ID_PATTERN.fullmatch(candidate_id):
            raise ValueError("invalid candidate id")
        path = self.candidates_dir / f"{candidate_id}.json"
        if not path.is_file():
            raise ValueError("candidate does not exist")
        return json.loads(path.read_text(encoding="utf-8"))

    def finalize_plan_set(self, recommended_candidate_id: str) -> dict[str, Any]:
        if not self.analysis_path.is_file():
            raise ValueError("source analysis has not been saved")
        candidates = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.candidates_dir.glob("*.json"))
        ]
        if not candidates:
            raise ValueError("at least one candidate is required")
        candidate_ids = [str(item["id"]) for item in candidates]
        if recommended_candidate_id not in candidate_ids:
            raise ValueError("recommended candidate does not exist")
        analysis = json.loads(self.analysis_path.read_text(encoding="utf-8"))
        payload = {
            **analysis,
            "recommended_strategy_id": recommended_candidate_id,
            "strategies": candidates,
        }
        _atomic_json(
            self.manifest_path,
            {
                "candidate_ids": candidate_ids,
                "recommended_candidate_id": recommended_candidate_id,
                "completed": True,
            },
        )
        # Keep the public/backend contract compatible while candidates migrate to artifacts.
        _atomic_json(self.legacy_plan_path, payload)
        return payload
