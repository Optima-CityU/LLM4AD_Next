# Evidence policy

Adapted from AutoRebuttal's evidence ledger and measured-experiment guidance.
See [the adaptation attribution](../../ATTRIBUTION.md).

When the current output schema includes an evidence-status field, preserve
these distinctions. If the field is absent, keep the distinctions clear in the
response without inventing new wire metadata:

- `source_grounded`: directly supported by the submitted paper.
- `verified`: supported by explicit author-provided evidence outside the paper.
- `placeholder`: a concrete value or experiment is still required.
- `needs_author`: only the author can decide or attest to the claim.

Never turn a planned experiment into a completed result. Never fabricate
numbers, citations, reviewer intent, implementation details, or venue rules.
Use visible placeholders such as `[AUTHOR: insert measured result for ...]`.
Do not hide uncertainty with vague claims like “results will be provided.”

Source references should be auditable paths, section names, review IDs, or
author-note identifiers. If a claim cannot be traced, weaken it or mark it for
the author.
