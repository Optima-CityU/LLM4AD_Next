# Reviewer model

Adapted from AutoRebuttal's reviewer-card model. See
`/app/skills/autorebuttal-shared/ATTRIBUTION.md`.

Build one card per stable reviewer ID. Each card captures:

- `reviewer_id`
- `sentiment`: positive / mixed / negative
- `movability`: supportive / swing / fixed
- `primary_concerns`
- `attitude`

These fields must directly influence what gets answered first, how narrow or
broad claims should be, and whether the tone should reassure, clarify, or
de-escalate. Prioritize swing reviewers and central concerns.

Persona and movability are internal hypotheses, not facts to mention in the
response. The user's display label is presentation metadata and must never be
used as the stable reviewer identity.
