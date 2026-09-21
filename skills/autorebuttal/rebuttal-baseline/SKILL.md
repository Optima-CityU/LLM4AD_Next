---
name: rebuttal-baseline
description: Build and confirm the complete paper, review, constraint, concern, and reviewer-card baseline before rebuttal generation.
---

<!-- Adapted from YoujunZhao/AutoRebuttal; see /app/skills/autorebuttal-shared/ATTRIBUTION.md. -->

# Rebuttal Baseline

Read these shared references completely before acting:

- `/app/skills/autorebuttal-shared/references/input-contract.md`
- `/app/skills/autorebuttal-shared/references/reviewer-analysis.md`
- `/app/skills/autorebuttal-shared/references/evidence-policy.md`
- `/app/skills/autorebuttal-shared/references/artifact-contracts.md`

This author-facing phase contains two internal steps.

## Step 1: Intake

Read the paper entry point, follow relevant includes, and read every report in
the authoritative review index. Normalize the paper summary, reviewer IDs,
venue, response mode, output format, character limits, author notes, forbidden
claims, and unresolved questions. Unknown limits stay null.

## Step 2: Review analysis

Split every substantive report into atomic `W` weaknesses, `Q` questions, and
`M` minor points. Record stable concern IDs, severity, likely answer source,
response move, and auditable source references. Build exactly one reviewer card
per indexed report and assign every concern to exactly one matching card.

Present the compact baseline to the author when a material ambiguity or author
decision remains. Do not draft rebuttal prose, choose a final strategy, edit
paper files, or invent venue policy. Set `ready_for_generation` false until
blocking questions are resolved.

Publish the complete Baseline phase contract exactly once with
`publish_stage_result` and idempotency key `rebuttal-baseline-v1`.
