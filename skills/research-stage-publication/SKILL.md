---
name: research-stage-publication
description: Save and publish versioned research sources and optional typed rebuttal outputs using the current workspace contract, source-base protection and author review flow.
---

# Research revision publication

Read fresh `get_workflow_context` before preparing a research revision. It
provides the current `revision`, canonical file paths and `publication_schemas`
for `publish_research_revision`. Retain that revision with the draft. Removed or
replaced conversation turns do not represent the current requested result.
For manuscript inputs, first record any full unindexed chat-pasted reports
through the main-agent `record_reviewer_feedback` tool and use its returned
platform identities. Refresh workflow and region context after recording or
other input changes; capture that refreshed revision for output preparation.
Already indexed reviews need no new recording step.

The main agent integrates the requested changes into canonical authoring
sources. New proposals use the nine root S1–S9 Markdown chapters plus
`proposal-state.json` and `proposal-log.md` defined in `openair-proposal`.
Numbered in-text references match `4-参考文献.md`; optional legacy BibTeX stays
supported without forcing a new file. Do not hand-maintain a second
`proposal.md` aggregate. Existing single-Markdown, sectioned Markdown and Typst
projects retain their files, format and editing workflow without automatic
migration.

The platform detects changed files; the agent does not enumerate them.
Optional read-only subagents return text diffs and evidence with their base
revision, and never write files or publish.

Call `mcp__llm4ad_stage__publish_research_revision` with `summary`, a fresh
`idempotency_key`, the captured revision as `base_revision`, and an optional
`artifact` matching one exact discriminated schema from workflow context.
The artifact discriminator is `type`; match the exposed field types and
required fields. `summary` belongs to the publication envelope, not the strict
artifact body. Do not invent metadata. Proposal
file revisions need no fabricated analytical artifact. For rebuttals, choose
only the relevant independent `baseline`, `rebuttal` or `ac_summary` output.
A rebuttal carries the agent's canonical response; it does not require separate
strategy, draft and compliance publications.

Use a fresh key for changed content or a changed base. Reuse a key only for an
exact retry after an uncertain response. Preserve the draft's original base;
if it conflicts, compare base, draft and current author text before preparing
a reviewed new revision. Never relabel a stale draft with the latest revision
just to force publication. Keep earlier sources, artifacts and export outputs
in the platform's immutable history rather than replacing or deleting them.

Inspect the actual response. A candidate or `requires_author_approval` means
proposed changes await author acceptance. Do not describe them as saved
canonical changes, silently accept them or bypass review through another tool.
Publisher protection targets actual manual author changes since the last
agent publication. An earlier agent-generated draft alone does not require
approval of every subsequent automatic revision; follow the returned state
without inventing another approval gate. Scoped region requests keep their
own author-acceptance contract.

For proposals, ordinary chapter saves are distinct from author confirmation
after each meaningful phase's summary and next options. Tool success, a saved
revision, a completed research step or `draft_done` never means phase approval
or author finalization. Preserve confirmed facts and unresolved choices in
state/log. Only the author's explicit **确认并排版** UI action asks the platform
to convert the ordered chapters to Typst/PDF. The model does not automatically
generate/export Typst/PDF or require a compiler. This proposal exception does
not change AutoRebuttal's existing advancement, publication or approval policy.

A confirmed saved revision and a successful PDF export are separate outcomes.
Markdown drafts stay saved if deterministic Pandoc/Typst/PDF export fails;
report the export diagnostic and retain earlier successful outputs. Normal
authoring does not require shell commands, Typst probes or formatter runs.

A source/version or wire-schema error requires a precise correction. Research
limitations, novelty judgments, missing experiments and editorial concerns
remain Skill guidance and author actions; they do not become backend quality
gates, invented word limits or mandatory intake metadata.

For an explicit scoped region request, use `read_region_request` and
`propose_region_change` against its own base revision. Do not replace that
approval flow with whole-document publication.

This publication contract applies to the unified proposal/rebuttal assistants.
Other research products retain their own exposed tools and contracts.
