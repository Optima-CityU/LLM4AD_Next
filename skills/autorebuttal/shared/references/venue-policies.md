# Venue presentation guidance

Adapted from AutoRebuttal's dated venue-policy reference and default formatting
rules. See `/app/skills/autorebuttal-shared/ATTRIBUTION.md`.

This integration does not allocate or enforce numeric response limits. Use this
reference only to choose presentation and tone. Never invent a venue rule; an
explicit user instruction or current official rule overrides this snapshot.

## Formatting defaults

- ICLR: use a brief global summary followed by reviewer blocks when the
  response form permits it; keep discussion civil and communicate claimed
  manuscript changes explicitly.
- ICML, NeurIPS, and AAAI: default to reviewer-specific, concise point-to-point
  blocks.
- CVPR, ICCV, and ECCV: use a brief shared summary followed by reviewer blocks
  when that structure is accepted.
- ARR, ACL, and EMNLP: keep the response text-only and focus on factual
  correction, clarification, and directly requested evidence.
- Unknown venue: use generic reviewer-specific blocks unless the author has
  selected a shared response, and do not infer additional policy. Missing venue
  or year is not a reason to stop generation.

Within reviewer blocks, preserve `W`, `Q`, and `M` labels from the analysis.
Each label starts its own response item. A global response is for genuinely
shared concerns, not a duplicate summary of reviewer-specific items.
