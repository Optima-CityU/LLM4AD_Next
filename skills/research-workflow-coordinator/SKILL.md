---
name: research-workflow-coordinator
description: Coordinate the complete AutoProposal or AutoRebuttal workflow in one conversation, delegate original stage methods to named workers and publish their results as work progresses.
---

# Research workflow coordinator

Read `mcp__llm4ad_stage__get_workflow_context` before planning or continuing.
It supplies the actual stage order, dependencies, existing artifacts, source
revision and installed stage Skills. Each item’s `agent` is its callable worker
name, while `stage` is the unchanged publication key. The original stage Skills
retain their OpenAIR_proposal and AutoRebuttal attribution and methods; this Skill only coordinates their execution.

Follow the author's goal. For a request to complete the workflow, choose the
next stage whose `prerequisites_satisfied` is true, invoke its named stage agent,
wait for its report, then publish the complete artifact with
`publish_stage_result(artifact=..., idempotency_key=..., workflow_stage=...)`.
Read the updated context and continue automatically. Read-only research or
checks may run together. Run and publish source-writing stages one at a time: the existing publication pipeline compares the shared
source tree against the last published revision.
A question, local correction or analysis request does not by itself authorize
rebuilding the entire document; explain the effect and revise relevant stages.

Give each worker the relevant author decisions, requested scope, source paths,
prior artifact references and explanation-language preference. Workers have
their own stage Skills; they do not see this conversation. Ask them for the full
publication artifact, not just a prose summary. Their reports are intermediate
work; you publish the stage result and verify the tool succeeded before
reporting that stage as published. For proposal edits, use `check_typst` and
resolve compile errors before publication.

Reuse current published results. Revisit stale or affected stages after author
corrections. When a stage has issues, use its original Skill to decide whether
work can continue with explicit caveats or truly needs author input. Do not
invent mandatory word limits, venue metadata, experiments or generic blocking
checks. If an answer materially changes the work, ask a focused question and
pause the dependent work. Otherwise state the assumption and keep going.

Explain progress briefly in the author's preferred language. Keep final document,
reviewer-response and chair-message language separate from explanation preferences.
The right-side progress panel is driven by actual agent/tool events; no need to
repeat a checklist on every turn. Finish with the available deliverables and any
real remaining author decisions.
