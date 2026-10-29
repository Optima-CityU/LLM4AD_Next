## Context

AutoDiscovery presently has one `discovery` stage. Its Agent calls `build_algorithm_task` before it can publish, and `DiscoveryArtifact` contains only validated runnable proposals. `PaperAlgorithmProposal` stores the resulting package in RustFS; `create_proposal_tasks` imports it into an ordinary project. The workbench hides the stage navigator in algorithm mode and shows only package/source tabs. Ordinary task services already own run, stop, copy, update, tree, log, and result operations.

## Goals / Non-Goals

**Goals:** Four inspectable research stages, source-grounded intermediate results, build only after a direction and evaluator are chosen, embedded evolution in the last stage, and task-family-scoped Agent operations without duplicating the runner.

**Non-Goals:** A second evolution engine, automatic background search across papers, scientifically certifying metrics from a smoke test, autonomous stopping without a user-delegated request, or exposing arbitrary project tasks to the research Agent.

## Decisions

### 1. Reuse the backend-owned stage system

Add `discovery_baseline`, `discovery_candidates`, and `discovery_evaluation` before the existing `discovery` key, which remains the final build/evolution stage. This preserves old published `discovery` results and their project links. Each new stage has its own run kind, dedicated Skill, prerequisite, and validated publication schema. The normal `WorkflowStepper` renders algorithm mode as it already renders proposal and rebuttal modes. A legacy workspace with a linked final-stage task remains accessible without fabricating earlier stage publications; a new revision follows all four stages.

An alternative is one Agent session with client-only tabs. That cannot durably distinguish intermediate evidence from a built package, prevent early builds, or apply revision-aware prerequisites.

### 2. Store intermediate artifacts separately from runnable proposals

Baseline, candidate, and evaluation JSON are validated at publication and stored through the existing `PaperAgentRun.artifact_object_key` in RustFS. Add an owned, read-only stage-artifact endpoint returning the latest artifact plus source-version/status metadata. Keep `PaperAlgorithmProposal` for final validated packages only. Do not delete earlier candidates on republish; superseded artifacts remain tied to their run/source version. Provide the current predecessor artifacts to later Agents through a workspace/run-scoped read tool on the stage bridge, not by copying untrusted JSON into system instructions.

The candidate artifact may publish alternatives before selection; it is marked `needs_revision` until a stable candidate ID is selected. The evaluation artifact may explain missing data without inventing measurements; it becomes `ready` only when a defensible contract is present. Final build requires ready predecessors. Candidate IDs and source references are strings in the artifact, not new relational entities.

An alternative is putting every result in `PaperWorkspace` JSON columns. That would duplicate large research text in PostgreSQL and conflict with the project's RustFS artifact pattern.

AutoDiscovery baseline intake also accepts a paper discovered by the Agent. A baseline session may start without a source version, but publication still requires one. The Agent uses the existing rate-limited arXiv MCP to search and download; a private stage-bridge tool imports only the downloaded paper from that workspace's fixed arXiv directory into the normal RustFS-backed `PaperSourceVersion`. The run is bound to that source on import. The upload path remains available if retrieval fails or the user already has a paper. This is user-directed discovery, not background crawling.

### 3. Build once, import once, then link ordinary tasks

The final stage uses the existing builder tool and its technical validation gate for the selected direction. Publication stores the exact returned package. The existing idempotent `create_proposal_tasks` service creates the project/root task in the same last-stage workbench. Task execution remains the ordinary `task_service.run_task` path. The step's published status means the package is ready; it is independent of the running/completed/failed state of evolution. A source or upstream-stage revision marks the build stage stale but never deletes an imported task or rewrites a running version.

### 4. Scope all Agent lifecycle tools to the workspace's task family

The stage bridge exposes read status/tree/results and write create-child, apply-parameter-patch, start, and stop tools only for the final stage. A backend adapter resolves the workspace owner and `PaperTaskLink`, verifies that the target is the linked root or a descendant in that root's `group_id` within the same project, and calls existing task services. Tool arguments cannot authorize a different project. Mutation tools use a fresh persisted status check and an optimistic version-time guard. The Agent may recommend changes freely; actual start/stop/child creation follows the user's explicit conversational request or control click.

For built-in provider URLs, the adapter obtains execution credentials server-side using the existing authentication/gateway mechanism; it never returns credentials to the Agent or stores them in the stage artifact. If that cannot be done safely for a provider, Agent start reports an actionable error while the authenticated UI start control remains available.

### 5. Treat tuning as a child experiment, not an in-place edit

Change `copy_task` additively so the new AutoDiscovery path can copy with `activate_child=False`; existing callers keep their current default. A parameter patch is validated against the task configuration schema, shown as a diff, and applied only to a non-running child. This avoids editing an active run's config and preserves the parent package. The user or Agent selects the active child explicitly. The current global `stop_task` records `FAILED`; the embedded view and scoped status response distinguish a user-requested stop using the persisted `TaskInterrupted` log until a platform-wide `STOPPED` state is designed separately.

### 6. Keep the workbench visually and operationally consistent

Use the existing research `WorkflowStepper` and stage-specific CloudCLI conversation. The right side shows a readable, source-linked result for the selected stage; the final stage adds a collapsible task area with status, bounded logs/result summary, start/stop, child list and a link to the full project page. Closing this area changes only local UI state. Fetch live task data via existing generated client/query infrastructure and regenerate the client with `openapi-ts` after backend OpenAPI changes; do not hand-edit generated files or add a dependency.

## Risks / Trade-offs

- **Legacy single-stage workspaces** → Preserve the `discovery` key and linked proposal/task; show the existing final artifact while new intermediate stages remain unpublished.
- **Agent action on unrelated tasks** → Resolve links server-side and reject IDs outside the root/descendant family, regardless of prompt content.
- **Races between status read and mutation** → Recheck persisted status and version on the backend immediately before each operation.
- **Costly/unexpected runs** → Require a user-initiated action or clearly delegated request, and keep start/stop separate from panel open/close.
- **Misleading validation badge** → Label builder checks as technical; show data/evaluator assumptions and real observed results separately.
- **Source edits after import** → Mark research stages stale without mutating already-imported task files or deleting their results.

## Migration Plan

1. Add stage schemas, Skills, publication/read support, and backend tests while preserving the legacy final stage.
2. Add scoped task adapter and tool bindings with owner, family, race, child-copy and credential tests.
3. Add stage navigation, result panels, embedded task controls, bilingual guidance, and regenerated OpenAPI client.
4. Run backend/frontend validation, test old linked proposals and fresh workspaces, then deploy without a data backfill. Rollback restores the old stage presentation and leaves already-created tasks intact.

## Open Questions

- Whether a later release should support scheduled autonomous stop policies; this change supports user-delegated immediate lifecycle actions only.
- Whether evaluator semantic checks should become a dedicated offline audit beyond the current technical validator; this change records and exposes assumptions rather than certifying science.
