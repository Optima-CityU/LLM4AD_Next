"""Configure the main research assistant and its optional read-only helpers."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.models import PaperAgentRun, PaperSourceVersion, PaperWorkspace
from app.services import paper_service, paper_workflow

COORDINATOR_SKILL = "research-workflow-coordinator"
UNIFIED_SKILLS = {"proposal": "openair-proposal", "manuscript": "autorebuttal-workflow"}
METHOD_BY_STAGE = {
    "formatting": "proposal-foundation-layout",
    "literature": "proposal-literature-evidence",
    "rationale": "proposal-rationale",
    "objectives": "proposal-objectives",
    "methods": "proposal-methods",
    "innovation_plan": "proposal-innovation-plan",
    "foundation_feasibility": "proposal-foundation-feasibility",
    "final_review": "proposal-final-review",
    "rebuttal_baseline": "rebuttal-baseline",
    "autorebuttal": "autorebuttal",
    "ac_summary": "ac-summary",
}


def workflow_paths(workflow: paper_workflow.ResearchWorkflowDefinition, entry: str) -> list[str]:
    """Resolve Markdown source roles while retaining legacy adapter paths."""
    if workflow.mode == "proposal" and not entry.lower().endswith(".typ"):
        from app.services.paper_proposal import DEFAULT_PROPOSAL_ENTRY, PROPOSAL_CHAPTERS

        if entry == DEFAULT_PROPOSAL_ENTRY:
            return [
                *(path for _identifier, _title, path in PROPOSAL_CHAPTERS),
                "proposal-state.json",
                "proposal-log.md",
                "styles/",
                "assets/",
            ]
        return list(dict.fromkeys([entry, "sections/", "references.bib", "styles/", "assets/"]))
    return list(dict.fromkeys(path for stage in workflow.stages for path in stage_paths(workflow, stage, entry)))


def stage_paths(workflow: paper_workflow.ResearchWorkflowDefinition, stage: str, entry: str) -> list[str]:
    """Resolve source roles without binding Markdown edits to an adapter."""
    if workflow.mode == "proposal" and not entry.lower().endswith(".typ"):
        return workflow_paths(workflow, entry)
    return [
        entry if p == "@entry" else p for p in workflow.writable_paths_by_run_kind[workflow.run_kind_by_stage[stage]]
    ]


def coordinator_skills(
    workflow: paper_workflow.ResearchWorkflowDefinition, proposal_entry_path: str | None = None
) -> list[str]:
    """Load the domain Skill, adding Typst authoring only for a legacy entry."""
    if workflow.mode in UNIFIED_SKILLS:
        skills = [COORDINATOR_SKILL, UNIFIED_SKILLS[workflow.mode], "research-stage-publication"]
        if workflow.mode == "proposal" and (proposal_entry_path or "").lower().endswith(".typ"):
            skills.append("typst-author")
        return skills
    return list(
        dict.fromkeys([COORDINATOR_SKILL, *(s for skills in workflow.skills_by_run_kind.values() for s in skills)])
    )


def _stage_agents(
    workflow: paper_workflow.ResearchWorkflowDefinition,
    workspace: PaperWorkspace,
    preferences: str,
    reference_tools: Sequence[str],
) -> dict[str, dict]:
    """Preserve stage workers for workflows outside the two unified assistants."""
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


def coordinator_agents(
    workspace: PaperWorkspace, preferences: str, reference_tools: Sequence[str] = ()
) -> dict[str, dict]:
    """Use read-only helpers for proposal/rebuttal and retain other stage workers."""
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    if workflow.mode not in UNIFIED_SKILLS:
        return _stage_agents(workflow, workspace, preferences, reference_tools)
    skills = [UNIFIED_SKILLS[workflow.mode]]
    tools = list(
        dict.fromkeys(
            [
                "Read",
                "Glob",
                "Grep",
                "mcp__llm4ad_stage__get_workflow_context",
                "mcp__llm4ad_stage__get_region_context",
                *(tool for tool in reference_tools if tool.startswith(("mcp__arxiv__", "mcp__research_search__"))),
            ]
        )
    )
    prompt = (
        "You are an optional read-only research assistant to the main agent, not a required stage worker. "
        "Before analysis, read get_workflow_context and the read-only get_region_context together. "
        "Use get_region_context as the authoritative current text, including manual author edits; "
        "published workflow artifacts and file mirrors may contain older text. "
        "Capture the workflow context's revision before analysis and return it unchanged with proposed text diffs. "
        "Retain source_version_id and source_content_hash as legacy source-base metadata when available. "
        "Shared documents under /workspace/source are read-only to helpers. "
        "Read relevant installed method references under /app/skills as guidance, never prerequisite gates. "
        "The main agent's task supplies the author's goal, relevant decisions and evidence paths; "
        "ask it for missing context instead of assuming its conversation is visible. "
        "Return findings, proposed text diffs, citations with locators and uncertainties in your final text response. "
        "Do not use Write, Edit or Bash, create scratch/report files, modify bibliography, publish revisions, "
        "propose regional changes through tools or delegate further. "
        "The main agent checks evidence, integrates useful suggestions and alone calls publish_research_revision "
        "with the captured base_revision and an optional typed artifact matching publication_schemas. "
        "If source or regional text changed, refresh context and reconcile without overwriting author edits. "
        "For scoped regional requests, the author approves the targeted diff; never bypass that approval. "
        "Do not invent evidence, venue rules, word limits or editorial export gates. "
        + (
            f"The existing proposal entry is {workspace.proposal_entry_path}; preserve its authoring format. "
            if workspace.mode == "proposal" and (workspace.proposal_entry_path or "").lower().endswith(".typ")
            else (
                "Proposals use Markdown authoring; the platform handles deterministic Typst/PDF export. "
                if workspace.mode == "proposal"
                else "Treat submitted paper source and original reviews as read-only evidence. "
            )
        )
        + preferences
    )
    return {
        name: {
            "description": description,
            "model": "inherit",
            "skills": list(skills),
            "tools": list(tools),
            "prompt": f"{description} {prompt}",
        }
        for name, description in {
            "analysis": "Optional research analysis of the requested question, logic or design; return findings as text.",
            "reference-check": "Optional evidence and citation checking using available arXiv/SearXNG tools; return findings as text.",
        }.items()
    }


def workflow_context(db: Session, workspace: PaperWorkspace) -> dict:
    """Expose revision APIs only for unified assistants, retaining other contracts."""
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    unified = workspace.mode in UNIFIED_SKILLS
    entry = workspace.proposal_entry_path or ("proposal.md" if workspace.mode == "proposal" else None)
    source = (
        db.get(PaperSourceVersion, workspace.active_source_version_id) if workspace.active_source_version_id else None
    )
    stages = []
    for stage in workflow.stages:
        kind = workflow.run_kind_by_stage[stage]
        state = (workspace.proposal_stage_states or {}).get(stage) or {}
        item = {
            "stage": stage,
            "status": state.get("status", "pending"),
            "skills": (
                [UNIFIED_SKILLS[workspace.mode]]
                if workspace.mode in UNIFIED_SKILLS
                else list(workflow.skills_by_run_kind[kind])
            ),
            "prerequisites": [] if unified else list(workflow.prerequisites_by_run_kind.get(kind, ())),
            "prerequisites_satisfied": unified
            or all(
                workflow.prerequisite_satisfied(
                    stage, predecessor, ((workspace.proposal_stage_states or {}).get(predecessor) or {}).get("status")
                )
                for predecessor in workflow.prerequisites_by_run_kind.get(kind, ())
            ),
            "dependencies_advisory": unified,
        }
        if unified:
            item["method_reference"] = (
                f"/app/skills/{UNIFIED_SKILLS[workspace.mode]}/references/{METHOD_BY_STAGE[stage]}.md"
            )
        else:
            item["agent"] = stage.replace("_", "-")
            item["writable_paths"] = stage_paths(workflow, stage, entry or "proposal.typ")
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
    context = {
        "mode": workspace.mode,
        "title": workspace.title,
        "description": workspace.description,
        "proposal_entry_path": entry,
        "publication_tool": "publish_stage_result",
        "source_root": "/workspace/source",
        "research_root": "/workspace/.research",
        "source_version_id": str(workspace.active_source_version_id) if workspace.active_source_version_id else None,
        "source_content_hash": source.content_hash if source is not None else None,
        "stages": stages,
        "progress": load_progress_snapshot(db, workspace),
        "stage_keys_are_artifact_adapters": unified,
    }
    if unified:
        from app.schemas.paper_publication import publication_schemas
        from app.services.paper_revision_history import current_state_hash

        context.update(
            {
                "revision": current_state_hash(db, workspace),
                "publication_schemas": publication_schemas(),
                "publication_tool": "publish_research_revision",
                "publication_instructions": (
                    "Capture revision before preparing changes and pass it unchanged as base_revision. "
                    "Use publication_schemas for the optional typed artifact; do not guess legacy stage payloads. "
                    "On conflicts, refresh context and reconcile with author edits instead of forcing a newer base."
                ),
                "authoring_format": "typst" if entry and entry.lower().endswith(".typ") else "markdown",
                "source_writes": "main_agent_only" if workspace.mode == "proposal" else "read_only_evidence",
                "regional_edits_authoritative": True,
                "optional_helpers": ["analysis", "reference-check"],
            }
        )
        if workspace.mode == "proposal":
            from app.services.paper_proposal import source_chapter_index

            chapters = source_chapter_index(source, entry) if source else []
            context.update(
                proposal_chapters=[chapter.model_dump() for chapter in chapters],
                progression_policy="author_confirmed_phases",
                final_layout_trigger="author_confirmation_button",
            )
            if any(chapter.id != "document" for chapter in chapters):
                context.update(chapter_state_path="proposal-state.json", writing_log_path="proposal-log.md")
    return context


def publication_run(
    db: Session,
    workspace: PaperWorkspace,
    coordinator: PaperAgentRun,
    stage: str | None,
    attempt_key: str | None = None,
    *,
    base_source_version_id: uuid.UUID | None = None,
    base_content_hash: str | None = None,
) -> PaperAgentRun:
    """Resolve a distinct publication attempt while retaining its draft base.

    Args:
        db: Caller-owned publication transaction.
        workspace: Authorized research workspace.
        coordinator: Parent run whose source version is the default draft base.
        stage: Historical artifact adapter key.
        attempt_key: Idempotency key reused only for retries of this revision.
        base_source_version_id: Source revision read before preparing the diff.
        base_content_hash: Source content hash captured with that revision.

    Returns:
        A publication run pinned to the draft base rather than newer author edits.
    """
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    if stage not in workflow.stages:
        raise HTTPException(status_code=422, detail="Specify a stage belonging to this research workflow")
    run_id = uuid.uuid5(coordinator.id, f"publication:{stage}:{attempt_key}") if attempt_key else uuid.uuid4()
    run = db.get(PaperAgentRun, run_id)
    if run is None:
        run = PaperAgentRun(id=run_id, workspace_id=workspace.id, run_kind=workflow.run_kind_by_stage[stage])
        run.source_version_id = base_source_version_id or coordinator.source_version_id
    elif base_source_version_id is not None and run.source_version_id != base_source_version_id:
        raise HTTPException(
            status_code=409, detail="Publication retry changed its source base; compare revisions first"
        )
    prior_hash = (run.request_payload or {}).get("base_content_hash")
    if prior_hash is not None and base_content_hash is not None and prior_hash != base_content_hash:
        raise HTTPException(
            status_code=409, detail="Publication retry changed its content base; compare revisions first"
        )
    source = db.get(PaperSourceVersion, run.source_version_id) if run.source_version_id is not None else None
    run.provider_id = coordinator.provider_id
    run.model_name = coordinator.model_name
    run.session_id = coordinator.session_id
    run.request_payload = {
        **(run.request_payload or {}),
        "native_runtime": "cloudcli",
        "auto_workflow": True,
        "workflow_stage": stage,
        "workspace_mode": workspace.mode,
        "coordinator_run_id": str(coordinator.id),
        "publication_attempt_key": attempt_key,
        "base_source_version_id": str(run.source_version_id) if run.source_version_id is not None else None,
        "base_content_hash": base_content_hash or prior_hash or (source.content_hash if source else None),
        "writable_paths": workflow_paths(workflow, workspace.proposal_entry_path or "proposal.md"),
        "source_path": (workspace.proposal_entry_path or "proposal.md") if stage == "formatting" else None,
    }
    db.add(run)
    db.flush()
    return run


class _ProgressTool(BaseModel):
    """Validate a tool call observed in the transcript."""

    id: str | None = None
    name: str
    done: bool
    failed: bool


class _ProgressStep(BaseModel):
    """Validate an actual worker or parent tool attempt."""

    id: str
    run_id: str | None = Field(default=None, alias="runId")
    parent_id: str = Field(alias="parentId")
    stage: str
    description: str
    status: Literal["running", "waiting", "completed", "failed", "interrupted"]
    tool_count: int = Field(ge=0, alias="toolCount")
    current_tool: str | None = Field(alias="currentTool")
    tools: list[_ProgressTool]
    started_at: str | None = Field(default=None, alias="startedAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    artifact_run_id: str | None = Field(default=None, alias="artifactRunId")


class _ProgressSnapshot(BaseModel):
    """Validate the host progress contract before durable storage."""

    session_id: str = Field(alias="sessionId")
    run_id: str | None = Field(default=None, alias="runId")
    sequence: int | None = Field(default=None, ge=0)
    status: Literal["ready", "running", "waiting", "idle", "interrupted", "failed", "completed"]
    current_tool: str | None = Field(alias="currentTool")
    agents: list[_ProgressStep]


class _WorkflowStepReport(BaseModel):
    """Describe research work independently of the tools used to carry it out."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: Literal["running", "waiting", "completed", "failed", "interrupted"]
    goal: str = ""
    approach: str = ""
    summary: str = ""
    stage: str = ""
    output_paths: list[str] = Field(default_factory=list, alias="outputPaths")


def record_workflow_step(db: Session, workspace: PaperWorkspace, run: PaperAgentRun, report: dict) -> dict:
    """Upsert a logical research step without creating a row for each tool call.

    Args:
        db: Caller-owned transaction, with the coordinator row locked by the API.
        workspace: Authorized research workspace.
        run: Coordinator whose conversation supplied the report.
        report: Step identity, purpose, approach, status and current conclusion.

    Returns:
        The current step, retaining its position and unmodified design details.
    """
    if run.workspace_id != workspace.id:
        raise HTTPException(status_code=409, detail="Step does not belong to this research workspace")
    if (
        workspace.mode not in UNIFIED_SKILLS
        or not (run.request_payload or {}).get("auto_workflow")
        or run.run_kind != "research_workflow"
        or not run.session_id
        or (run.request_payload or {}).get("coordinator_run_id")
    ):
        raise HTTPException(status_code=403, detail="This session is not a research coordinator")
    parsed = _WorkflowStepReport.model_validate(report)
    payload = deepcopy(run.request_payload or {})
    steps = {item["id"]: item for item in payload.get("research_workflow_steps", [])}
    old = steps.get(parsed.id)
    details = parsed.model_dump(by_alias=True)
    if old:
        details = {
            **{key: old.get(key, value) for key, value in details.items()},
            **parsed.model_dump(by_alias=True, exclude_unset=True),
        }
        if all(old.get(key) == value for key, value in details.items()):
            return deepcopy(old)
    now = datetime.now(UTC).isoformat()
    reopened = old and old["status"] in {"completed", "failed", "interrupted"} and parsed.status == "running"
    step = {
        **details,
        "runId": str(run.id),
        "revision": (old or {}).get("revision", 0) + 1,
        "startedAt": now if reopened else (old or {}).get("startedAt", now),
        "updatedAt": now,
    }
    steps[parsed.id] = step
    run.request_payload = {
        **payload,
        "research_workflow_steps": list(steps.values()),
        "research_workflow_sequence": payload.get("research_workflow_sequence", 0) + 1,
    }
    db.add(run)
    db.flush()
    return deepcopy(step)


def record_proposal_turn_end(db: Session, run: PaperAgentRun, status: str, ended_at: datetime) -> bool:
    """Persist an unfinished logical step after a confirmed native turn end.

    Args:
        db: Caller-owned transaction holding the coordinator row lock.
        run: Proposal coordinator whose native session ended.
        status: Interrupted or failed according to the runtime event.
        ended_at: Timestamp from the workspace runtime's event.

    Returns:
        Whether the current logical step changed.
    """
    payload = deepcopy(run.request_payload or {})
    current = max(payload.get("research_workflow_steps", []), key=lambda step: step.get("updatedAt", ""), default=None)
    if current is None or current["status"] != "running" or datetime.fromisoformat(current["updatedAt"]) > ended_at:
        return False
    current.update(
        status=status,
        revision=current.get("revision", 0) + 1,
        updatedAt=datetime.now(UTC).isoformat(),
    )
    payload["research_workflow_sequence"] = payload.get("research_workflow_sequence", 0) + 1
    run.request_payload = payload
    db.add(run)
    db.flush()
    return True


def record_progress_snapshot(db: Session, workspace: PaperWorkspace, run: PaperAgentRun, snapshot: dict) -> dict:
    """Persist observed progress and append a deduplicated event delta.

    Args:
        db: Caller-owned transaction; the caller commits after recording.
        workspace: Workspace already authorized by the runtime route.
        run: Coordinator run belonging to that workspace and session.
        snapshot: Actual transcript events, never a generated future checklist.

    Returns:
        The merged progress snapshot with its durable sequence number.
    """
    parsed = _ProgressSnapshot.model_validate(snapshot)
    if run.workspace_id != workspace.id or parsed.session_id != run.session_id:
        raise HTTPException(status_code=409, detail="Progress does not belong to this research session")
    payload = deepcopy(run.request_payload or {})
    previous = payload.get("research_progress")
    if previous and parsed.sequence is not None and parsed.sequence <= previous["sequence"]:
        return previous
    incoming = parsed.model_dump(by_alias=True, exclude_none=True)
    incoming["currentTool"] = parsed.current_tool
    incoming["runId"] = str(run.id)
    agents = {item["id"]: item for item in (previous or {}).get("agents", [])}
    changed = []
    for step in incoming["agents"]:
        step["runId"] = str(run.id)
        step["currentTool"] = step.get("currentTool")
        old = agents.get(step["id"])
        if old and old["status"] in {"completed", "failed", "interrupted"} and step["status"] in {"running", "waiting"}:
            continue
        if old:
            tools = {tool.get("id", str(index)): tool for index, tool in enumerate(old["tools"])}
            for index, tool in enumerate(step["tools"]):
                key = tool.get("id", str(index))
                prior = tools.get(key, {})
                tools[key] = {
                    **tool,
                    "done": tool["done"] or prior.get("done", False),
                    "failed": tool["failed"] or prior.get("failed", False),
                }
            step["tools"] = list(tools.values())
            step["toolCount"] = max(step["toolCount"], old["toolCount"], len(step["tools"]))
        if step["status"] not in {"running", "waiting"}:
            step["currentTool"] = None
        if old != step:
            changed.append(step)
        agents[step["id"]] = step
    incoming["agents"] = list(agents.values())
    if previous and incoming["status"] == "ready":
        incoming["status"] = previous["status"]
        incoming["currentTool"] = previous["currentTool"]
    if previous and not changed and all(incoming[key] == previous[key] for key in ("status", "currentTool")):
        return previous
    events = payload.get("research_progress_events", [])
    sequence = (previous or {}).get("sequence", 0) + 1
    incoming["sequence"] = sequence
    events.append(
        {
            "id": str(uuid.uuid5(run.id, f"progress:{sequence}")),
            "runId": str(run.id),
            "sequence": sequence,
            "at": datetime.now(UTC).isoformat(),
            "status": incoming["status"],
            "currentTool": incoming["currentTool"],
            "agents": changed,
        }
    )
    run.request_payload = {**payload, "research_progress": incoming, "research_progress_events": events}
    db.add(run)
    db.flush()
    return incoming


def load_progress_snapshot(db: Session, workspace: PaperWorkspace) -> dict | None:
    """Restore logical steps alongside diagnostic execution events."""
    runs = db.exec(
        select(PaperAgentRun).where(PaperAgentRun.workspace_id == workspace.id).order_by(PaperAgentRun.created_time)
    ).all()
    result = None
    agents = {}
    workflow_steps = {}
    workflow_sequence = 0
    for run in runs:
        payload = run.request_payload or {}
        snapshot = payload.get("research_progress")
        reported = payload.get("research_workflow_steps", [])
        if snapshot:
            for step in snapshot["agents"]:
                agents[(step.get("runId", str(run.id)), step["id"])] = deepcopy(step)
            result = deepcopy(snapshot)
        elif reported:
            active = next((step for step in reported if step["status"] == "running"), None)
            waiting = next((step for step in reported if step["status"] == "waiting"), None)
            result = {
                "sessionId": run.session_id or str(run.id),
                "runId": str(run.id),
                "status": "running" if active else "waiting" if waiting else reported[-1]["status"],
                "currentTool": None,
                "agents": [],
            }
        if snapshot or reported:
            workflow_sequence = payload.get("research_workflow_sequence", 0)
        for step in reported:
            old = workflow_steps.get(step["id"])
            if old is None or step.get("updatedAt", "") >= old.get("updatedAt", ""):
                workflow_steps[step["id"]] = deepcopy(step)
    if result is not None:
        result["agents"] = list(agents.values())
        if workflow_steps:
            result["workflowSteps"] = list(workflow_steps.values())
            result["workflowSequence"] = workflow_sequence
    return result
