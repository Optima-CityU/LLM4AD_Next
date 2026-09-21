# Evidence policy

Adapted from AutoRebuttal's evidence ledger and measured-experiment guidance.
See `/app/skills/autorebuttal-shared/ATTRIBUTION.md`.

Every response entry has one evidence status:

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
