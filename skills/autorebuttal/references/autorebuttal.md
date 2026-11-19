<!-- Adapted from YoujunZhao/AutoRebuttal; see ../ATTRIBUTION.md. -->

# Response strategy, drafting and quality review

Read the relevant shared guides for the task:

- [Inputs](../shared/references/input-contract.md)
- [Strategy and response placement](../shared/references/strategy-and-format.md)
- [Drafting and review](../shared/references/drafting-and-compliance.md)
- [Response moves](../shared/references/rebuttal-playbook.md)
- [Reviewer model](../shared/references/reviewer-model.md)
- [Initial response style](../shared/references/initial-rebuttal-style.md)
- [Human response style](../shared/references/human-rebuttal-style.md)
- [Venue guidance](../shared/references/venue-policies.md)
- [Evidence policy](../shared/references/evidence-policy.md)
- [Current output contract](../shared/references/artifact-contracts.md)

Use current regional author text and the paper/reviews as evidence. Consult an
existing baseline when helpful; do not demand a confirmed earlier phase before
answering or revising a specific response. Missing evidence should produce an
honest limitation or precise author question, not invented results.

Choose a response strategy appropriate to the actual concerns. Resolve shared
issues consistently, adapt tone to reviewer concerns and decide whether a
shared/global response is useful. Keep stable reviewer and concern identities;
labels are presentation, not identity.

Write concise, direct answers supported by paper passages or explicit author
evidence. Use visible `[AUTHOR: ...]` placeholders for author-only facts or
measurements. For a full response, ensure concerns are covered without duplicate
answers; for a scoped revision, change only the requested block.

Review evidence integrity, reviewer identity, response placement, terminology
and canonical-text consistency. Produce the exact final `rendered_text` yourself
from the response you intend the author to submit. Keep author actions in
`open_placeholders` and useful advice in `findings`, without internal validator
logs or pretend completed experiments.

An optional typed `rebuttal` artifact uses the exposed flat response schema.
Strategy, drafting and review remain useful methods, not mandatory nested
outputs or three successive publications. Save only the deliverable requested
through `publish_research_revision`; a scoped request still uses
`propose_region_change` for author acceptance.
