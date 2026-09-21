---
name: autorebuttal
description: Automatically plan, draft, and compliance-check a complete evidence-grounded rebuttal from a confirmed baseline.
---

<!-- Adapted from YoujunZhao/AutoRebuttal; see /app/skills/autorebuttal-shared/ATTRIBUTION.md. -->

# AutoRebuttal

Read these shared references completely before acting:

- `/app/skills/autorebuttal-shared/references/input-contract.md`
- `/app/skills/autorebuttal-shared/references/strategy-and-budget.md`
- `/app/skills/autorebuttal-shared/references/drafting-and-compliance.md`
- `/app/skills/autorebuttal-shared/references/evidence-policy.md`
- `/app/skills/autorebuttal-shared/references/artifact-contracts.md`

Read the confirmed intake and analysis from the rebuttal context before doing
any generation. This author-facing phase contains three internal steps that
must run in order without asking the author to type “continue.”

## Step 1: Response strategy

Resolve shared issues, priority reviewers, response moves, output structure,
and a reviewer-complete character budget. The format and limits must match the
confirmed baseline. Keep a safety margin and never invent venue policy.

## Step 2: Drafting

Write concise reviewer-addressed entries covering every analyzed concern
exactly once across the entry set. Ground factual claims in the paper or
explicit author evidence. Use visible `[AUTHOR: ...]` placeholders when only
the author can provide a result or decision.

## Step 3: Compliance review

Audit and refine the full draft for concern coverage, reviewer consistency,
evidence integrity, response-character limits, and submission readiness. Fix
non-blocking writing problems directly. Keep unresolved evidence visible and
set `ready_for_submission` false when author action remains.

Publish the complete AutoRebuttal phase contract exactly once with
`publish_stage_result` and idempotency key `autorebuttal-v1`.
