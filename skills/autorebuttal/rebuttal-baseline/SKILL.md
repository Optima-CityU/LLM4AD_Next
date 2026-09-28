---
name: rebuttal-baseline
description: Build and confirm the complete paper, review, constraint, concern, and reviewer-card baseline before rebuttal generation.
---

<!-- Adapted from YoujunZhao/AutoRebuttal; see /app/skills/autorebuttal-shared/ATTRIBUTION.md. -->

# Rebuttal Baseline

Read these shared references completely before acting:

- `/app/skills/autorebuttal-shared/references/input-contract.md`
- `/app/skills/autorebuttal-shared/references/reviewer-analysis.md`
- `/app/skills/autorebuttal-shared/references/reviewer-model.md`
- `/app/skills/autorebuttal-shared/references/rebuttal-playbook.md`
- `/app/skills/autorebuttal-shared/references/evidence-policy.md`
- `/app/skills/autorebuttal-shared/references/artifact-contracts.md`

This author-facing phase contains two internal steps.

## Step 1: Intake

Read the paper entry point and follow relevant includes. Then collect reviews
from the available sources:

1. Read every report in the review index when it is non-empty.
2. Read any review text the author pasted into the conversation, including
   separately labeled reports in one message.
   Preserve each distinct conversation-only report in the corresponding
   `reviewer_sources` item as `review_markdown`, organized for display with
   Markdown headings and lists. Retain the supplied details and do not invent
   absent fields. The right-hand reviewer panel displays this Markdown after
   baseline publication.
3. Merge matching reports from these sources without dropping reviewer-specific
   details. A forum URL alone is not review text; ask the author to paste the
   report or enter it in the right-hand review panel.
4. If neither source contains a review, ask the author to paste one into the
   conversation or enter it in the review panel.

Assign one stable reviewer UUID per distinct report. Use the indexed UUID for
saved reviews; for a conversation-only report, create a UUID and retain it in
the published baseline. Preserve a human-readable reviewer label separately.
Do not claim to have read a linked forum unless its review text was supplied.

Normalize the paper summary, stable reviewer UUIDs and display labels, venue,
response mode, output format, author notes, forbidden claims, and unresolved
questions.

## Step 2: Review analysis

Split every substantive report into atomic `W` weaknesses, `Q` questions, and
`M` minor points. Record stable concern IDs, severity, likely answer source,
response move, and auditable source references. Build exactly one reviewer card
per indexed report and assign every concern to exactly one matching card. Use
the index `reviewer_id` UUID everywhere; never substitute or derive it from the
human-readable `display_label`.

Present the compact baseline to the author. Do not draft rebuttal prose, choose
a final strategy, edit paper files, or invent venue policy. Missing venue/year,
the default response mode or output format, absent numeric limits, and evidence
that will require an author placeholder are not generation blockers. Record
such items as assumptions, warnings, or open questions and continue.

Only set `ready_for_generation` false when the source material is unusable for
an evidence-grounded response: the paper has no substantive readable content,
neither the review index nor the conversation supplies a review,
or none of the collected reviews contains a substantive concern. In that case,
identify the exact missing source and required author action. Otherwise publish
the baseline with `ready_for_generation` true.

Publish the complete Baseline phase contract exactly once with
`publish_stage_result` and idempotency key `rebuttal-baseline-v1`.
