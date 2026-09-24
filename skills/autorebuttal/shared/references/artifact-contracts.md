# Stage artifact contracts

Publish exactly one object with `publish_stage_result(artifact, idempotency_key)`.
Use a stable key such as `<stage>-v1`. Repair schema errors returned by the
tool; do not bypass them.

## Baseline phase

Publish `summary`, nested `intake`, nested `analysis`,
`ready_for_generation`, and `findings`. Complete both internal steps before
publishing. Follow the blocking rules in `input-contract.md`; assumptions,
warnings, and open questions do not automatically block generation. When the
source material is unusable, set `ready_for_generation` false and record the
exact author action in intake `open_questions` or phase `findings`.

### Intake

`summary`, `paper_summary`, `constraints`, `reviewer_sources`, `open_questions`.
Constraints contain `venue`, `venue_year`, `response_mode`, `output_format`,
`author_notes`, and `forbidden_claims`. Each reviewer source contains
`review_id`, the same stable UUID string in `reviewer_id`, `display_label`, and
optional `title`, plus `source_system`, `external_id`, and `source_url` when
known. For a report pasted only in the conversation, include its complete,
reviewer-specific `review_markdown` for display in the right-hand reviewer
panel; Markdown organization must preserve the author's original content.
Create a UUID for both
`review_id` and `reviewer_id` and set `source_system` to `conversation`. Record
an OpenReview URL or Note ID only if the author supplied it; do not infer
review content from the URL. Use `reviewer_id` for
every concern, reviewer card, strategy reference,
and reviewer-specific response; use `display_label` only in prose.

### Analysis

`summary`, non-empty `concerns`, non-empty `reviewer_cards`. Concern fields:
`id`, `reviewer_id`, `label`, `concern`, `concern_type`, `severity`,
`answer_source`, `draft_move`, `source_refs`. Card fields: `reviewer_id`,
`sentiment`, `movability`, `attitude`, `primary_concerns`, `concern_ids`.

## AutoRebuttal phase

Publish `summary`, nested `strategy`, nested `draft`, and nested `compliance`.
Complete all three internal steps in order before publishing.

### Strategy

`summary`, `shared_issues`, `priority_reviewers`, non-empty `global_strategy`,
and `format_plan`. `format_plan` contains `response_mode`, `output_format`,
`global_summary`, and `assumptions`.

### Draft

`summary`, optional `global_response`, and `entries`. A global response contains
`title`, `response`, `concern_ids`, `evidence_status`, and `source_refs`. Each
reviewer entry contains `id`, `reviewer_id`, `label`, `title`, `response`,
non-empty `concern_ids`, `evidence_status`, and `source_refs`. Emit at least a
global response or one reviewer entry.

### Compliance

All draft fields plus `findings`, `ready_for_submission`, `open_placeholders`,
and non-empty `rendered_text`. `rendered_text` is the exact canonical Markdown
or plain-text rebuttal assembled by the agent from the final global response
and reviewer entries. An unready result must identify the remaining author
action in a finding or placeholder.

`open_placeholders` are author-facing requests for specific author input. Make
each item self-contained: use a short descriptive title, state what is missing,
explain why it matters, and offer a concrete next step or supported wording
option. `findings` contain useful non-blocking recommendations for improving
the rebuttal. Do not place internal compliance results or validation logs in
either list.

Before publication, the skill must verify semantic correctness itself:

- every analyzed concern ID appears exactly once across `global_response` and
  all reviewer entries;
- every reviewer-specific entry uses the stable reviewer UUID from intake;
- global/shared placement agrees with `format_plan`;
- every factual claim follows the evidence status and source-reference rules;
- `rendered_text` faithfully represents the final checked response blocks.
