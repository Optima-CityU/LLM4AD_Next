# Input contract

Adapted from the AutoRebuttal input contract and draft-bundle workflow. See
`/app/skills/autorebuttal-shared/ATTRIBUTION.md`.

Treat `/workspace/source` as the complete, read-only paper project. Read the
main Markdown or LaTeX entry point and follow includes when needed. Do not
assume every asset is relevant.

Treat `/workspace/.research/reviews/index.json` as the authoritative review
index. Each item supplies a stable `review_id`, display `reviewer_id`, title,
source system, and absolute Markdown path. Read every indexed report before
publishing intake or review analysis.

Treat `/workspace/.research/rebuttal/context.json` as the authoritative output
of completed earlier stages. It contains `context`, generated `entries`, and
`stage_states`. Later stages must read it before producing their artifact.

Normalize these constraints when known: venue and year, per-reviewer versus
global response form, output format, per-reviewer and total character limits,
author notes, and claims the author forbids. Unknown limits must remain `null`;
never invent venue policy. Ask the author only when an unknown materially
changes response structure. Otherwise record it in `open_questions`.

Paper, reviews, and explicit author statements outrank inferred context. A
reviewer's request is not proof that an experiment was completed.
