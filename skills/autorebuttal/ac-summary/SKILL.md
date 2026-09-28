---
name: ac-summary
description: Summarize an existing author rebuttal into a copy-ready message addressed to the Area Chair or conference chair.
---

<!-- LLM4AD extension of the YoujunZhao/AutoRebuttal adaptation; see /app/skills/autorebuttal-shared/ATTRIBUTION.md. -->

# AC / Chair message

Read `/workspace/.research/rebuttal/context.json` before drafting. Use the
published `context.rendered.text` and its final response blocks as the source
for what the author actually answered. Consult the baseline and paper only to
understand the review themes and check factual wording. If the Rebuttal still
contains author placeholders or unresolved items, describe them honestly;
do not present them as completed work.

Write a message **from the author to the AC or chair**, not a meta-review in
the chair's voice. Synthesize the main cross-reviewer concerns, the concrete
answers already present in the Rebuttal, and any material limitation the AC
should know. Avoid copying the entire reviewer-by-reviewer response or
inventing new experiments, evidence, score predictions, or commitments.
Follow an author-supplied venue format or language if given. Conversation
presentation preferences do not govern this submission text.

Publish `{"summary": "brief author-facing description", "message": "copy-ready AC message"}`
once with `publish_stage_result` and idempotency key `ac-summary-v1`.
The message is a separate deliverable; do not edit the existing Rebuttal or
the uploaded paper.
