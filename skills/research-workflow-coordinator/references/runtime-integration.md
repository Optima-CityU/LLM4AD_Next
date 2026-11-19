# Unified assistant integration contract

## Installed resources and method guidance

Copy the complete `skills/openair-proposal/` root to `/app/skills/openair-proposal`
and `skills/autorebuttal/` to `/app/skills/autorebuttal-workflow`. Install one
root Skill per domain, plus `research-workflow-coordinator` and
`research-stage-publication`. Keep grouped method references, detailed guides,
attribution and `LICENSE.AutoRebuttal`; do not create nested stage Skills.
Reference filenames retained for compatibility describe research topics, not
executable stage ownership. Proposals use official Phase 0–6 as author-paced
conversation guidance, with a summary, options and author choice before each
meaningful phase advances. This is not a mandatory frontend stage sequence or
backend scientific gate. AutoRebuttal retains its existing policy without
proposal phase confirmations.

New proposals use the nine root Markdown chapters S1–S9 plus
`proposal-state.json` and `proposal-log.md`, as defined in `openair-proposal`.
Numbered in-text citations match `4-参考文献.md`; legacy BibTeX is optional and
does not require creating a new file. Do not hand-maintain a `proposal.md`
aggregate. Existing single-Markdown, sectioned Markdown and Typst projects keep
their source files, format and editing workflow without automatic migration.
Only the author's explicit **确认并排版** UI action requests deterministic
platform conversion of ordered chapters using Pandoc and the selected Typst/PDF
presentation assets. The model does not automatically generate/export Typst/PDF
or require a compiler, shell probe or formatter for authoring. Technical export
failures preserve saved drafts and earlier exports linked to their revisions.

The main agent alone integrates canonical authoring changes and publishes.
Optional subagents get read-only tools and explicit author goals, relevant
source paths, decisions, evidence and base revision. They return text diffs,
analysis and citations in their response; no scratch files, report files,
Write/Edit permissions, direct publication or shared bibliography mutations
are part of the worker contract. Independent research may run in parallel.

For manuscript inputs only, bind
`record_reviewer_feedback(reviewer_label, content, title=None)` to the main
agent, not read-only subagents. An unindexed chat-pasted report is registered
as its complete original text before baseline use. The tool deduplicates full
content hashes plus reviewer labels and returns `review_id`, `reviewer_id`,
`display_label` and `revision`. The agent uses those platform identities rather
than inventing UUIDs. The parent runtime attaches the public review service's
storage/history hooks so the full original remains clickable. After recording
or other input changes, refresh workflow and region context before preparing
an output and capturing its base. Existing indexed reviews are usable without
an additional recording or confirmation gate.

## Revision publication contract

`get_workflow_context` exposes `revision`, canonical source paths and the exact
publication/artifact contract under `publication_schemas`. Treat the returned revision as an opaque base:
capture it before preparing changes and return it unchanged as `base_revision`
to `publish_research_revision(summary, idempotency_key, base_revision,
artifact=None)`. The optional artifact must match an exposed discriminated
variant using discriminator `type` and the exposed field types rather than
copying fields from a historical example. `summary` is a publication-envelope
field, not a field to duplicate inside the strict artifact. For manuscript outputs, the
independent variants are `baseline`, `rebuttal` and `ac_summary`; the optional
`proposal` variant carries only proposal metadata when needed. A source-only
proposal revision can omit its artifact.

The platform detects changed files, validates ownership and source/version
integrity, and retains immutable revision snapshots using its existing
persistence. No manual changed-file list or new scientific validator is
required. The agent supplies the canonical rebuttal text and reviewable
analytical content; the backend does not invent or rewrite a response.

Reuse an idempotency key only for an exact retry with the same base and content.
Changed content or a reconciled base uses a new key. A conflict requires a
comparison of base, proposed changes and current author text, preserving author
edits; blindly substituting the newest revision is not reconciliation.
`requires_author_approval` or a candidate response means proposed changes are
not yet canonical. Preserve the existing author review flow and distinguish
candidate, saved revision and export outcomes in the UI and conversation.
Publisher author protection is based on actual manual edits since the last
agent publication, not the existence of a prior agent draft. Do not add an
approval requirement to ordinary automatically saved revisions; obey the
returned candidate/approval state. This does not remove the explicit acceptance
required for scoped user-request diffs.

For proposals, ordinary save approval is separate from author-paced phase
confirmation and final UI typesetting. Tool success, `draft_done`, a saved
revision or a completed progress step must not imply phase approval or author
finalization. Persist pending choices and prior confirmed facts in proposal
state/log and reconcile them against actual sources on recovery. Wait for the
author before phase advancement, and never turn a phase decision into an export
request. Rebuttal publication and approval policy remain unchanged.

Scoped region requests continue through `read_region_request` and
`propose_region_change`; whole-revision publication must not bypass their
approval or overwrite unrelated regions.

Retain complete source and output history: earlier Markdown and bibliography
revisions, analytical artifacts, candidates, and generated Typst/PDF outputs
remain linked to the revision that produced them. Export attempts retain their
status/diagnostics without overwriting prior successful outputs. Historical
imports remain readable; changing authoring format does not delete old sources.
Scientific/novelty/compliance findings are guidance, not backend save/export
constraints. Author-supplied or verified official requirements take precedence
over dated examples without becoming invented metadata or word-limit gates.

## Logical research progress

The coordinator reports meaningful research work through
`mcp__llm4ad_stage__report_workflow_step(step_id, title, status, goal="",
approach="", summary="", stage="", output_paths=None)`.

Before work starts, provide a project-specific title, goal/question and
approach or method/design with running status. A logical research question can
span many tools or workers. Update that same logical ID with a brief actual
outcome and completed status when done; keep its goal and approach. Waiting
identifies a real author answer or decision needed to continue, not an active
tool, queued request or ordinary editorial finding. Independent goals can run
in parallel. Revisit a goal with the same ID; a different meaningful goal gets
a new ID even if it uses the same optional artifact adapter in `stage`.

Proposal phase confirmation is a real author decision: waiting status should
name that pending choice. A completed step is a research outcome, not acceptance
of a phase or final document. Restore proposal state/log as well as progress,
preserving confirmed facts and unresolved choices. Do not apply these proposal
waits to AutoRebuttal.

The hidden progress endpoint remains
`POST /api/v1/llm4ad/papers/runtime/workflow-step`, with query `workspace_id`
and `run_id`, the existing runtime-token header and JSON body `id`, `title`,
`status`, optional `goal`, `approach`, `summary`, `stage` and `outputPaths`.
The MCP maps `step_id` to `id` and `output_paths` to `outputPaths`. This internal
progress report is not a revision publication or a frontend stage gate.

Persist explicit `workflowSteps` and `workflowSequence` in the existing progress
snapshot. Restore them through the runtime GET or
`get_workflow_context.progress.workflowSteps` / `progress.workflowSequence`.
Merge by logical ID and workflow cursor, retaining history through empty iframe
snapshots and ignoring stale responses. Only explicitly started/current work
and actual completed outcomes appear in the right panel. Do not derive steps
from Read/Edit/Task calls, worker attempts or publication attempts, and do not
prefill future plans, totals or percentages. Child-tool details remain in chat.

## Historical compatibility only

Old public adapter keys and stored shapes remain available for historical
readers, not normal authoring instructions. These include `publish_stage_result`,
`workflow_stage`, `source_path`, `source_paths`, `entry_path`, `context_patch`,
`base_source_version_id`, `base_content_hash`, the `rebuttal_baseline` and
`autorebuttal` adapter names, and nested `strategy`/`draft`/`compliance` payloads.
They do not impose stage locks, scratch-write workers or a fixed platform plan.
New proposals use Markdown chapters; legacy Typst projects retain Typst editing.
Historical provenance and template licenses remain intact.
