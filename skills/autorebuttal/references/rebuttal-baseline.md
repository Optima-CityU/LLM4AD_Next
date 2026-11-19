<!-- Adapted from YoujunZhao/AutoRebuttal; see ../ATTRIBUTION.md. -->

# Paper/review inputs and concern analysis

Read the shared guides needed for the request:

- [Input and author-source handling](../shared/references/input-contract.md)
- [Atomic concern analysis](../shared/references/reviewer-analysis.md)
- [Reviewer model](../shared/references/reviewer-model.md)
- [Response moves](../shared/references/rebuttal-playbook.md)
- [Evidence policy](../shared/references/evidence-policy.md)
- [Current output contract](../shared/references/artifact-contracts.md)

Read the relevant paper passages and actual review text supplied through the
panel or conversation. For a complete baseline, consider every supplied report;
for a targeted question, inspect only the necessary material. A forum link alone
is not a review. Ask for missing essential text instead of inferring its content.

Reuse the platform's indexed review/reviewer UUIDs, keeping display labels
separate. For a full chat-pasted report not already indexed, the main agent
first calls `record_reviewer_feedback(reviewer_label, content, title=None)` with
the complete original text. Use the returned `review_id`, `reviewer_id` and
`display_label`; never invent a UUID for a baseline. The tool deduplicates exact
full-report content hashes plus labels and stores a clickable original.
Refresh workflow and region context after input changes, and capture the new
revision before preparing or publishing analysis. Existing indexed reviews
need no new recording call or confirmation gate. Preserve all report detail. Retain
clickable original review versions and supplied exact quotations as evidence;
unsupported or missing references stay unverified.

Summarize the paper and explicitly known submission constraints when useful.
Keep missing venue/year, unknown numeric limits and unsupported measurements
as uncertainty or author questions, not automatic generation blockers.

Split substantive reviews into stable atomic concerns: weaknesses, questions
and minor points. Capture concern type, severity, likely answer source, response
move and auditable evidence. Reviewer attitude and movability are planning
hypotheses, not acceptance predictions or facts to assert to reviewers.

Use this analysis directly to answer the author's request. An independent typed
`baseline` artifact is optional when a saved baseline deliverable is useful;
use the exact current schema and `publish_research_revision` contract. It is
not a prerequisite for a rebuttal or chair message, and it does not require a
fixed intake/analysis publication sequence.
