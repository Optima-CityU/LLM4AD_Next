"""End-to-end CSPaper review to LLM4AD candidate evolution pipeline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from llm4ad.integrations.cspaper.bridge import (
    TaskAuditReport,
    build_task_from_spec,
    prepare_task_from_spec,
)
from llm4ad.integrations.cspaper.client import (
    CSPaperClient,
    CSPaperReviewJob,
    save_review_artifacts,
)
from llm4ad.integrations.cspaper.compiler import SuggestionCompiler
from llm4ad.integrations.cspaper.schemas import AlgorithmDesignSpec


class EvolutionExport(BaseModel):
    """Stable artifacts exported after an evolution run."""

    run_directory: str
    candidates_directory: str
    leaderboard_path: str
    lineage_path: str
    candidate_count: int
    best_candidate_id: str = ""
    warnings: list[str] = Field(default_factory=list)


class PreparedEvolution(BaseModel):
    """Prepared task config plus its preflight audit."""

    task_directory: str
    config_path: str
    audit: TaskAuditReport


class CSPaperEvolutionPipeline:
    """Coordinate review, compilation, task preparation, evolution, and export."""

    def __init__(self, compiler: SuggestionCompiler | None = None) -> None:
        """Initialize the pipeline with an optional custom compiler."""
        self.compiler = compiler or SuggestionCompiler()

    async def review_paper(
        self,
        paper_path: str | Path,
        output_dir: str | Path,
        *,
        api_key: str,
        agent_id: str,
        base_url: str = "https://cspaper-frontend-prod.azurewebsites.net",
        desk_rejection_enabled: bool = False,
        poll_interval: float = 30.0,
        timeout: float = 1800.0,
    ) -> tuple[CSPaperReviewJob, Path]:
        """Submit a paper, wait for its review, and save reproducible artifacts."""
        client = CSPaperClient(api_key, base_url=base_url)
        output = Path(output_dir).expanduser().resolve()
        job = await client.submit_and_wait_cached(
            paper_path,
            agent_id=agent_id,
            cache_path=output / "submission.json",
            desk_rejection_enabled=desk_rejection_enabled,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        _raw, review = save_review_artifacts(
            job,
            output,
            paper_name=Path(paper_path).name,
            agent_id=agent_id,
        )
        return job, review

    def compile_review(
        self,
        review_path: str | Path,
        output_path: str | Path,
        **inputs: Any,
    ) -> AlgorithmDesignSpec:
        """Compile and save one CSPaper review."""
        spec = self.compiler.compile_file(review_path, **inputs)
        spec.save(output_path)
        return spec

    async def build_task(
        self,
        spec: AlgorithmDesignSpec,
        output_dir: str | Path,
        **builder_options: Any,
    ) -> Path:
        """Generate a runnable task with the existing LLM4AD Builder."""
        return await build_task_from_spec(spec, output_dir, **builder_options)

    def prepare(
        self,
        spec: AlgorithmDesignSpec,
        task_dir: str | Path,
        *,
        require_confirmation: bool = True,
    ) -> PreparedEvolution:
        """Inject the spec and fail if the task cannot measure it."""
        audit = prepare_task_from_spec(
            spec,
            task_dir,
            require_confirmation=require_confirmation,
        )
        return PreparedEvolution(
            task_directory=str(Path(task_dir).resolve()),
            config_path=audit.config_path,
            audit=audit,
        )

    async def evolve(
        self,
        spec: AlgorithmDesignSpec,
        task_dir: str | Path,
        *,
        top_k: int = 10,
        require_confirmation: bool = True,
        resume_from_checkpoint: str | None = None,
    ) -> EvolutionExport:
        """Prepare, run LLM4AD, and export a ranked candidate cohort."""
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        prepared = self.prepare(
            spec,
            task_dir,
            require_confirmation=require_confirmation,
        )
        if not prepared.audit.ready:
            raise RuntimeError(
                "CSPaper task preflight failed:\n- " + "\n- ".join(prepared.audit.errors)
            )

        from llm4ad import LLM4AD

        engine = LLM4AD(prepared.config_path)
        result = await engine.run(resume_from_checkpoint=resume_from_checkpoint)
        run_dir = engine.get_run_directory()
        export_dir = run_dir / "candidates"
        return export_candidates(
            result,
            export_dir,
            spec=spec,
            top_k=top_k,
            run_directory=run_dir,
        )


def export_candidates(
    result: Any,
    output_dir: str | Path,
    *,
    spec: AlgorithmDesignSpec,
    top_k: int = 10,
    run_directory: str | Path = "",
) -> EvolutionExport:
    """Copy Top-K worktrees and write a leaderboard plus lineage graph data."""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    population = list(getattr(result, "final_population", None) or [])
    best = getattr(result, "best_individual", None)
    if best is not None:
        population.append(best)

    unique: dict[str, Any] = {}
    for candidate in population:
        candidate_id = str(getattr(candidate, "id", ""))
        if candidate_id:
            unique[candidate_id] = candidate
    ranked = sorted(
        unique.values(),
        key=lambda candidate: float(getattr(candidate, "score", 0.0)),
        reverse=True,
    )[:top_k]

    warnings: list[str] = []
    entries: list[dict[str, Any]] = []
    lineage_nodes: list[dict[str, Any]] = []
    lineage_edges: list[dict[str, str]] = []
    for rank, candidate in enumerate(ranked, start=1):
        candidate_id = str(candidate.id)
        candidate_dir = output / f"{rank:02d}-{candidate_id}"
        worktree = getattr(candidate, "worktree", None)
        source_path = Path(str(getattr(worktree, "path", ""))) if worktree else None
        code_path = candidate_dir / "code"
        if source_path and source_path.is_dir():
            shutil.copytree(
                source_path,
                code_path,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".llm4ad",
                    ".env",
                    ".env.*",
                    "__pycache__",
                    "*.pyc",
                    "*.pem",
                    "*.key",
                    "build",
                    "runs",
                ),
            )
        else:
            warnings.append(f"candidate {candidate_id} has no readable worktree")
        metrics = dict(getattr(candidate, "metrics", {}) or {})
        parent_ids = list(getattr(candidate, "parent_ids", []) or [])
        entry = {
            "rank": rank,
            "candidate_id": candidate_id,
            "name": str(getattr(candidate, "name", "")),
            "score": float(getattr(candidate, "score", 0.0)),
            "metrics": metrics,
            "generation": int(getattr(candidate, "generation", 0)),
            "parent_ids": parent_ids,
            "description": str(getattr(candidate, "description", "")),
            "key_innovations": list(getattr(candidate, "key_innovations", []) or []),
            "code_path": str(code_path) if code_path.exists() else "",
        }
        entries.append(entry)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "candidate.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        lineage_nodes.append(
            {
                "id": candidate_id,
                "rank": rank,
                "score": entry["score"],
                "generation": entry["generation"],
            }
        )
        lineage_edges.extend(
            {"source": str(parent_id), "target": candidate_id} for parent_id in parent_ids
        )

    leaderboard = {
        "schema_version": "1.0",
        "spec_review_sha256": spec.paper.review_sha256,
        "objective_metrics": [item.name for item in spec.objectives],
        "candidates": entries,
    }
    leaderboard_path = output / "leaderboard.json"
    leaderboard_path.write_text(
        json.dumps(leaderboard, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lineage_path = output / "evolution-lineage.json"
    lineage_path.write_text(
        json.dumps(
            {"nodes": lineage_nodes, "edges": lineage_edges},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    spec.save(output / "algorithm-design-spec.json")

    return EvolutionExport(
        run_directory=str(Path(run_directory).resolve()) if run_directory else "",
        candidates_directory=str(output),
        leaderboard_path=str(leaderboard_path),
        lineage_path=str(lineage_path),
        candidate_count=len(entries),
        best_candidate_id=str(entries[0]["candidate_id"]) if entries else "",
        warnings=warnings,
    )
