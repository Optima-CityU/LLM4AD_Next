---
name: autorebuttal-workflow
description: Analyze reviewer concerns, draft or revise an evidence-grounded author rebuttal, and prepare a chair message in one conversation using attributed AutoRebuttal methods.
---

# Rebuttal assistant

Use one coordinator conversation for the author's actual request. Select
intake, concern analysis, response strategy, drafting, review or an optional
AC/chair message as useful research methods, not mandatory phases. A local
response revision does not require a new full baseline or separate strategy,
draft and compliance publications.

Read fresh `get_workflow_context` and `get_region_context` before analysis.
They expose current author text, saved outputs, `revision` and exact artifact
contracts under `publication_schemas`. The submitted paper is read-only.
Original reviewer reports remain
inspectable evidence, with stable reviewer UUIDs and concern IDs. Read linked
original versions and exact quotes when supplied; do not manufacture quotations
or mark uncited analysis verified. Missing legacy references remain explicitly
unverified without blocking a useful draft. A forum URL alone is not review text.

Accept author-supplied review text from the panel or conversation. Keep distinct
reviewers separate, preserve complete raw reports, and retain weak points,
questions and minor comments without dropping reviewer-specific detail.
Current regional author edits outrank older artifacts or file mirrors.

For a full chat-pasted report not already indexed, the manuscript main agent
calls `mcp__llm4ad_stage__record_reviewer_feedback(reviewer_label, content,
title=None)` before creating a baseline that refers to it. Pass the complete
original report unchanged. The platform deduplicates the full-report content
hash plus label and returns `review_id`, `reviewer_id`, `display_label` and a
revision. Use those platform UUIDs exactly; never invent reviewer identities.
Refresh workflow and region context after recording reports or other input
changes, then capture the refreshed revision before preparing the output.
Existing indexed reviews require no new recording call or intake gate.
Read-only subagents leave recording to the main agent.

Ground factual responses in the paper or explicit author evidence. Never
invent experiments, measurements, citations, venue policy or completed
commitments. Preserve contrary evidence and known limitations. Use visible
`[AUTHOR: ...]` placeholders for facts or decisions only the author can supply,
with a concrete next action. Submission rules come from the author or verified
current official requirements, not dated venue examples. Do not add word
limits, response budgets, metadata requirements or backend scientific gates.

The main agent integrates canonical response changes. Optional read-only
subagents may analyze independent questions in parallel and return text diffs,
evidence and uncertainties with their captured revision. They do not Write/Edit,
create scratch reports, publish outputs or modify the submitted paper.

## Choose useful method guidance

- [Paper/review inputs and concern analysis](references/rebuttal-baseline.md)
- [Response strategy, drafting and quality review](references/autorebuttal.md)
- [Author-to-chair message](references/ac-summary.md)

Preserve the upstream reviewer-model, strategy, evidence, drafting and review
methods. Use the parts needed for the request; neither the method grouping nor
a successful tool call implies a fixed workflow or completion of future work.
Report actual logical research questions, approaches and outcomes through
`report_workflow_step`, reusing logical IDs and keeping tool/child progress in
chat. Show only work that has started and its current/completed outcomes.

## Publish only the requested output

Use `publish_research_revision(summary, idempotency_key, base_revision,
artifact=...)` with the revision captured before preparation and an exact typed
artifact variant from workflow context. The optional `baseline`, `rebuttal` and
`ac_summary` outputs are independent; publish only the deliverable needed.
For a rebuttal, produce the canonical `rendered_text` yourself and keep it
consistent with the final reviewer entries and optional global response.
Pass `summary` as the publication argument. Use `type="rebuttal"` and the
flat artifact fields needed by the UI: `entries`, optional `global_response`,
`rendered_text`, `findings` and `open_placeholders`, subject to the exposed
schema. Do not duplicate `summary` inside that strict artifact. Do not wrap it
in a mandatory three-phase structure.

A chair message is from the author to the AC/chair and synthesizes what the
author actually answered. It does not adopt the chair's voice, invent evidence
or describe unresolved placeholders as completed work. The submission language
and conversational language are separate choices.

Use fresh idempotency keys for new revisions, with the same key only for an
exact retry. Preserve the original base and all prior source/output history.
A source conflict requires comparison and reconciliation, not blindly adopting
a newer revision. A candidate or `requires_author_approval` is a proposed diff,
not an applied result; preserve author acceptance. Findings and missing evidence
remain useful author guidance rather than backend publication/export gates.
Publisher protection addresses actual manual author edits since the last agent
publication. Do not require approval for every automatic draft merely because
an earlier agent result exists; follow the returned publication state. Explicit
scoped response requests still retain their own author-acceptance flow.

## Scoped author requests

For a right-panel response request, call `read_region_request` with its exact
ID. Preserve the selected reviewer/concern identity, author text, scope and base
revision. Read necessary evidence, then use
`propose_region_change(request_id=..., replacement_text=..., reason=...)`.
Present the localized diff for acceptance; do not silently apply it, publish a
whole response to bypass approval, edit the paper or rewrite unrelated replies.
Refresh and compare conflicts with newer author changes before reproposing.
Queued work is not progress until its research or revision actually starts.

The retained methods are pinned adaptations, not a claim of latest upstream
verification. See [ATTRIBUTION.md](ATTRIBUTION.md) and the preserved
[MIT license](LICENSE.AutoRebuttal).
