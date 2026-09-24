---
name: autorebuttal
description: Automatically plan, draft, and compliance-check a complete evidence-grounded rebuttal from a confirmed baseline.
---

<!-- Adapted from YoujunZhao/AutoRebuttal; see /app/skills/autorebuttal-shared/ATTRIBUTION.md. -->

# AutoRebuttal

Read these shared references completely before acting:

- `/app/skills/autorebuttal-shared/references/input-contract.md`
- `/app/skills/autorebuttal-shared/references/strategy-and-format.md`
- `/app/skills/autorebuttal-shared/references/drafting-and-compliance.md`
- `/app/skills/autorebuttal-shared/references/rebuttal-playbook.md`
- `/app/skills/autorebuttal-shared/references/reviewer-model.md`
- `/app/skills/autorebuttal-shared/references/initial-rebuttal-style.md`
- `/app/skills/autorebuttal-shared/references/human-rebuttal-style.md`
- `/app/skills/autorebuttal-shared/references/venue-policies.md`
- `/app/skills/autorebuttal-shared/references/evidence-policy.md`
- `/app/skills/autorebuttal-shared/references/artifact-contracts.md`

If the baseline lacks details needed for a reply, read the saved review files
or ask the author to paste the relevant original passage into the conversation.
Do not treat an OpenReview link alone as evidence of the review's contents.

Read the confirmed intake and analysis from the rebuttal context before doing
any generation. This author-facing phase contains three internal steps that
must run in order without asking the author to type “continue.”

## Step 1: Response strategy

Resolve shared issues, priority reviewers, response moves, output structure,
and a reviewer-complete strategy. Decide whether shared concerns belong in one
global response or in reviewer-specific blocks. Keep the stable reviewer UUIDs
from the confirmed baseline; display labels are presentation only.

## Step 2: Drafting

Write concise responses covering every analyzed concern exactly once across
the global response and reviewer-specific entries. Ground factual claims in
the paper or explicit author evidence and attach auditable source references.
Use visible `[AUTHOR: ...]` placeholders when only the author can provide a
result or decision.

## Step 3: Compliance review

Audit and refine the full draft for concern coverage, reviewer identity,
global/shared consistency, evidence integrity, and submission readiness. Fix
non-blocking writing problems directly. Assemble the exact final submission in
`rendered_text`; it must agree with the final global response and entries. Keep
unresolved evidence visible and set `ready_for_submission` false when author
action remains. Publish concise author-facing guidance for those unresolved
items: identify what is missing, why it matters, and a practical next step.
Do not expose the internal compliance checklist as user guidance.

Publish the complete AutoRebuttal phase contract exactly once with
`publish_stage_result` and idempotency key `autorebuttal-v1`.
