"""Compose native research coordinators from the existing stage definitions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import HTTPException
from sqlmodel import Session

from app.models import PaperAgentRun, PaperWorkspace
from app.services import paper_service, paper_workflow

COORDINATOR_SKILL = "research-workflow-coordinator"


def stage_paths(workflow: paper_workflow.ResearchWorkflowDefinition, stage: str, entry: str) -> list[str]:
    """Resolve the existing source ownership paths for one stage."""
    return [
        entry if p == "@entry" else p for p in workflow.writable_paths_by_run_kind[workflow.run_kind_by_stage[stage]]
    ]


def coordinator_skills(workflow: paper_workflow.ResearchWorkflowDefinition) -> list[str]:
    """Collect installed stage methods without replacing their upstream content."""
    return list(
        dict.fromkeys([COORDINATOR_SKILL, *(s for skills in workflow.skills_by_run_kind.values() for s in skills)])
    )


def coordinator_agents(
    workspace: PaperWorkspace, preferences: str, reference_tools: Sequence[str] = ()
) -> dict[str, dict]:
    """Give stage workers explicit methods and context instead of assuming inheritance."""
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    agents = {}
    for stage in workflow.stages:
        skills = list(workflow.skills_by_run_kind[workflow.run_kind_by_stage[stage]])
        paths = stage_paths(workflow, stage, workspace.proposal_entry_path or "proposal.typ")
        tools = ["Read", "Glob", "Grep", "mcp__llm4ad_stage__get_workflow_context"]
        if paths:
            tools.extend(["Write", "Edit", "mcp__llm4ad_stage__check_typst"])
        if stage == "literature":
            tools.extend(reference_tools)
        agents[stage.replace("_", "-")] = {
            "description": f"Perform the {stage} research stage using its original stage Skills.",
            "model": "inherit",
            "skills": skills,
            "tools": tools,
            "prompt": (
                f"You are the {stage} stage worker in a research workflow. "
                "Read get_workflow_context for the current materials and persisted results. "
                f"Source root: /workspace/source. Readable method files: /app/skills. "
                f"Use these installed Skills: {', '.join(skills)}. "
                f"Edit only these stage-owned paths: {paths}. "
                "The coordinator's task prompt supplies the author's goal and relevant decisions; "
                "ask it for missing context rather than assuming the parent's conversation is visible. "
                "Complete the stage method and return its full structured publication artifact to the coordinator. "
                "The coordinator publishes that artifact through publish_stage_result; your task ends with the report. "
                "Explain genuine evidence gaps using the stage method; do not invent venue rules, limits or requirements."
                + preferences
            ),
        }
    return agents


def workflow_context(db: Session, workspace: PaperWorkspace) -> dict:
    """Return current authoritative stage state and full published artifacts."""
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    stages = []
    for stage in workflow.stages:
        kind = workflow.run_kind_by_stage[stage]
        state = (workspace.proposal_stage_states or {}).get(stage) or {}
        item = {
            "stage": stage,
            "agent": stage.replace("_", "-"),
            "status": state.get("status", "pending"),
            "skills": list(workflow.skills_by_run_kind[kind]),
            "prerequisites": list(workflow.prerequisites_by_run_kind.get(kind, ())),
            "prerequisites_satisfied": all(
                workflow.prerequisite_satisfied(
                    stage, predecessor, ((workspace.proposal_stage_states or {}).get(predecessor) or {}).get("status")
                )
                for predecessor in workflow.prerequisites_by_run_kind.get(kind, ())
            ),
            "writable_paths": stage_paths(workflow, stage, workspace.proposal_entry_path or "proposal.typ"),
        }
        if state.get("run_id"):
            try:
                item["artifact"] = paper_service.get_stage_artifact(
                    db, workspace.user_id, workspace.id, stage
                ).model_dump(mode="json")
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                item["artifact"] = None
        stages.append(item)
    return {
        "mode": workspace.mode,
        "title": workspace.title,
        "description": workspace.description,
        "proposal_entry_path": workspace.proposal_entry_path,
        "source_root": "/workspace/source",
        "research_root": "/workspace/.research",
        "source_version_id": str(workspace.active_source_version_id),
        "stages": stages,
    }


def publication_run(
    db: Session, workspace: PaperWorkspace, coordinator: PaperAgentRun, stage: str | None
) -> PaperAgentRun:
    """Resolve a coordinator publication to the existing stage artifact pipeline."""
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    if stage not in workflow.stages:
        raise HTTPException(status_code=422, detail="Specify a stage belonging to this research workflow")
    run_id = uuid.uuid5(workspace.id, f"cloudcli:{stage}")
    run = db.get(PaperAgentRun, run_id)
    if run is None:
        run = PaperAgentRun(id=run_id, workspace_id=workspace.id, run_kind=workflow.run_kind_by_stage[stage])
    run.source_version_id = workspace.active_source_version_id
    run.provider_id = coordinator.provider_id
    run.model_name = coordinator.model_name
    run.session_id = coordinator.session_id
    run.request_payload = {
        "native_runtime": "cloudcli",
        "workflow_stage": stage,
        "workspace_mode": workspace.mode,
        "coordinator_run_id": str(coordinator.id),
        "writable_paths": stage_paths(workflow, stage, workspace.proposal_entry_path or "proposal.typ"),
        "source_path": (workspace.proposal_entry_path or "proposal.typ") if stage == "formatting" else None,
    }
    db.add(run)
    db.flush()
    return run
