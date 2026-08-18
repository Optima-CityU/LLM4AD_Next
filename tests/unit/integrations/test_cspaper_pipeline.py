from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from llm4ad.integrations.cspaper.compiler import SuggestionCompiler
from llm4ad.integrations.cspaper.pipeline import export_candidates
from llm4ad.planner.base import (
    Algorithm,
    EvaluationResult,
    InsightType,
    WorktreeInfo,
)

from .test_cspaper_compiler import REVIEW


def _candidate(tmp_path: Path, candidate_id: str, score: float, parent_ids: list[str]):
    worktree = tmp_path / candidate_id
    worktree.mkdir()
    (worktree / "algorithm.py").write_text(f"# {candidate_id}\n", encoding="utf-8")
    return Algorithm(
        id=candidate_id,
        insight_type=InsightType.INITIAL,
        description=f"candidate {candidate_id}",
        name=candidate_id,
        parent_ids=parent_ids,
        evaluation=EvaluationResult(score=score, metrics={"solution_cost": 100 - score}),
        worktree=WorktreeInfo(
            name=candidate_id,
            path=str(worktree),
            branch=f"candidate/{candidate_id}",
            commit_hash="abc",
            created_at=time.time(),
            last_used_at=time.time(),
        ),
    )


def test_exports_ranked_top_k_and_lineage(tmp_path: Path) -> None:
    """Top-K export is fitness-ranked and retains parent relationships."""
    spec = SuggestionCompiler().compile_text(REVIEW)
    first = _candidate(tmp_path, "first", 10.0, [])
    second = _candidate(tmp_path, "second", 30.0, ["first"])
    third = _candidate(tmp_path, "third", 20.0, ["first"])
    result = SimpleNamespace(
        best_individual=second,
        final_population=[first, second, third],
    )

    exported = export_candidates(result, tmp_path / "out", spec=spec, top_k=2)

    leaderboard = json.loads(Path(exported.leaderboard_path).read_text(encoding="utf-8"))
    assert [item["candidate_id"] for item in leaderboard["candidates"]] == [
        "second",
        "third",
    ]
    lineage = json.loads(Path(exported.lineage_path).read_text(encoding="utf-8"))
    assert {"source": "first", "target": "second"} in lineage["edges"]
    assert (Path(exported.candidates_directory) / "01-second/code/algorithm.py").is_file()
