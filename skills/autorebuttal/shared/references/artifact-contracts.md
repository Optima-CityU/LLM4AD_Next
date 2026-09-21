# Stage artifact contracts

Publish exactly one object with `publish_stage_result(artifact, idempotency_key)`.
Use a stable key such as `<stage>-v1`. Repair schema errors returned by the
tool; do not bypass them.

## Baseline phase

Publish `summary`, nested `intake`, nested `analysis`,
`ready_for_generation`, and `findings`. Complete both internal steps before
publishing. When generation is blocked, set `ready_for_generation` false and
record an author action in intake `open_questions` or phase `findings`.

### Intake

`summary`, `paper_summary`, `constraints`, `reviewer_sources`, `open_questions`.
Constraints contain `venue`, `venue_year`, `response_mode`, `output_format`,
`per_reviewer_limit`, `total_limit`, `author_notes`, and `forbidden_claims`.
Each reviewer source contains `review_id`, `reviewer_id`, and optional `title`.

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
`format_plan`, and `budget_plan`. `format_plan` contains `response_mode`,
`output_format`, `global_summary`, and `assumptions`. `budget_plan` contains
`unit` (`response_characters`), `per_reviewer_limit`, `total_limit`,
`safety_margin`, and one `reviewer_budgets` item per reviewer. Each allocation
contains `reviewer_id`, `target_characters`, and `limit`.

### Draft

`summary` and non-empty `entries`. Each entry contains `id`, `reviewer_id`,
`label`, `title`, `response`, non-empty `concern_ids`, `evidence_status`, and
`source_refs`. `character_count` may be zero because the backend recomputes it.

### Compliance

All draft fields plus `findings`, `ready_for_submission`, and
`open_placeholders`. An unready result must include a finding or placeholder.
