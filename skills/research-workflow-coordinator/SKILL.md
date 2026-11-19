---
name: research-workflow-coordinator
description: Coordinate research proposal authoring or author rebuttals in one conversation, preserving logical research progress, evidence, output history and author revisions.
---

# Research workflow coordinator

Read `mcp__llm4ad_stage__get_workflow_context` when starting, reconnecting or
preparing a revision. It exposes the current `revision`, exact publication
schemas in `publication_schemas`, canonical source paths, saved outputs and
available tools. Read
`mcp__llm4ad_stage__get_region_context` alongside it before analysis. Current
regional text and author edits take precedence over older artifacts or file
mirrors. Select `openair-proposal` or `autorebuttal-workflow` for the domain
methods. Proposal authoring is the author-paced exception: follow
`openair-proposal`'s official Phase 0–6 guidance and obtain the author's choice
after each meaningful phase's summary and next options before advancing. These
are conversation checkpoints, not mandatory frontend stages or backend quality
gates. AutoRebuttal's existing policy remains intact; its methods do not acquire
proposal phase confirmations or an ordered workflow.

For manuscript review inputs, the main agent records each full chat-pasted
report not already indexed through `record_reviewer_feedback(reviewer_label,
content, title=None)`, retaining the complete original and the returned
platform UUIDs. Subagents do not call this input-writing tool. Refresh workflow
and region context after input changes and capture the new revision before
preparing a baseline or response. Existing indexed reports need no new gate.

Follow the author's actual request. A local correction does not authorize a
complete rewrite. Reuse confirmed facts and ask about a missing choice only
when it materially changes the work; continue independent research otherwise.
Do not invent submission metadata, word limits or scientific quality gates.

## Authoring and optional research assistance

The main agent writes canonical authoring sources. For new proposals, use the
nine root S1–S9 Markdown chapters, `proposal-state.json` and `proposal-log.md`
defined in `openair-proposal`. Numbered in-text references match
`4-参考文献.md`; optional legacy BibTeX remains supported without forcing a new
file. Preserve confirmed facts, author choices and unresolved alternatives in
state/log and recover them with the actual chapters on resume. Do not maintain
a second `proposal.md` aggregate. Existing single-Markdown, sectioned Markdown
and Typst projects keep their format, files and editing workflow without
automatic migration. The author presses **确认并排版** in the UI to request
platform conversion of ordered chapters; the model does not automatically
generate/export Typst/PDF or require a compiler. Generated export files remain
outputs linked to their source revision.

Optional subagents are read-only research assistants. Give each the author's
goal, relevant decisions, source paths, evidence and captured revision; they do
not inherit the conversation. They may analyze independent questions in
parallel and return text, proposed text diffs, citations and uncertainties.
They do not use Write/Edit, create scratch/report files, publish revisions or
modify shared bibliography. The parent checks their evidence and integrates
useful suggestions against the current author text. A worker's proposed diff
retains the revision it actually analyzed.

## Save and publish a revision

Use `mcp__llm4ad_stage__publish_research_revision(summary, idempotency_key,
base_revision, artifact=...)` after the requested work is ready to save. Capture
`base_revision` from workflow context before preparing the changes and pass it
unchanged. The tool detects changed canonical files; do not assemble manual
changed-file lists or guess publication fields. Use the exact exposed schema
for an optional typed artifact; omit the artifact when it is not needed.

Serialize integrations and publication. Use a fresh idempotency key for a new
revision and reuse it only for an exact retry with the same base and content.
On a conflict, refresh context, compare the base, proposed changes and current
text, preserve author edits and prepare a reconciled revision. Never substitute
a newer base merely to force an old draft through. Preserve citations,
bibliography keys, approved decisions and the existing output history.

Inspect the returned state. If `requires_author_approval` is true or a candidate
is returned, explain the proposed diff and wait for author acceptance; do not
claim it was applied or silently accept it. Otherwise report the saved revision
as the response confirms. Markdown drafts remain saved when PDF export fails.
Publisher author protection concerns actual manual changes since the last
agent publication; do not demand approval for every automatic draft or merely
because a previous agent output exists. Scoped author requests still use
their explicit candidate-acceptance flow.

For proposals, ordinary chapter saves and author phase confirmations are
distinct: tool success, `draft_done` or a saved revision is neither permission
to advance nor author finalization. Wait for the phase choice and leave final
typesetting to the author's explicit **确认并排版** UI action. These proposal
checkpoints do not change rebuttal publication or approval policy.

Describe save and export outcomes separately, retaining prior successful
exports. Scientific and editorial findings are actionable guidance, not save
or export gates. Publication details are in `research-stage-publication`.

For a scoped right-panel request, read its exact ID with `read_region_request`,
inspect only the necessary evidence, then call
`propose_region_change(request_id=..., replacement_text=..., reason=...)`.
Preserve its selected text, reviewer/concern identity and base revision. The
author approves this diff; publishing a whole revision must not bypass that
approval or change unrelated regions.

## Logical research progress

Before meaningful research, analysis or revision starts, call
`mcp__llm4ad_stage__report_workflow_step` with a stable project-specific
`step_id`, informative `title`, `status="running"`, the `goal` or research
question and the `approach` or method/design. One logical step may span many
reads, edits, searches or delegations. Do not create a step per tool or worker.

Update the same ID with a brief outcome in `summary` and completed status when
that work finishes. Use waiting status only when the step genuinely requires
an answer or decision from the author; name the input needed. Queues, running
tools and editorial findings alone are not author-input waits. Independent
research can continue in parallel. Revisit the same goal with the same logical
ID; a materially different goal gets a new ID. An optional artifact adapter in
`stage` does not define identity, order or gates; different goals may share it.

For proposals, waiting for the author's meaningful phase choice is a real
author-input wait. Completing a logical research step does not confirm the
phase or authorize starting the next one. Keep its pending choice in proposal
state/log; the UI progress record is not a finalization signal. This exception
does not add phase waits to AutoRebuttal.

Only explicitly started/current work and actual completed outcomes belong in
the right panel. Do not prefill future plans, totals, percentages or pretend
completion. Child-tool details stay in chat. Restore `progress.workflowSteps`
and `progress.workflowSequence` from workflow context on reconnect and update
existing logical records without deriving steps from the transcript.

Keep conversation language separate from the requested document or submission
language. Finish with available deliverables, their saved/candidate/export
state, meaningful outcomes and concrete remaining author actions.

Runtime maintainers: read [the integration contract](references/runtime-integration.md)
when packaging these Skills or connecting revision and progress persistence.
