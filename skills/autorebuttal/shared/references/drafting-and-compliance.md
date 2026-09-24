# Drafting and compliance

Adapted from AutoRebuttal's initial and human rebuttal style guidance,
rebuttal playbook, formatter, and final checks. See
`/app/skills/autorebuttal-shared/ATTRIBUTION.md`.

Write reviewer-addressed blocks in this order when applicable: acknowledge the
concern, answer it directly, point to evidence, state a concrete manuscript
change, and disclose any remaining limitation. Use professional, calm language.
Do not praise the reviewer mechanically, repeat the entire question, speculate
about scores, or claim that a concern is “obvious.”

Each entry should address one coherent concern or a tightly related group. Its
title must make the issue scannable. Preserve technical precision and define
ambiguous terms. Keep shared facts identical across reviewers while tailoring
the framing to each report.

Final compliance checks are the agent's responsibility:

1. Every analyzed concern appears exactly once across the global response and
   reviewer-specific entries.
2. Reviewer-specific entries use the stable reviewer UUID from the review
   index; display labels never replace identity fields.
3. Global/shared placement matches the chosen format plan.
4. Claims match their evidence status and source references.
5. No placeholder is presented as verified evidence.
6. No response claims that the submitted paper was edited by this read-only
   workflow.
7. Remaining author actions are listed as open placeholders or findings.
8. `rendered_text` is assembled by the agent and exactly represents the final
   checked response, including the global response when present.

The published follow-up lists are for the author, not an internal audit log.
Use `open_placeholders` only for specific author input still needed. Each item
must explain the missing information, why it affects the response, and one
practical way to provide it. Use `findings` for concise, non-blocking advice
that helps the author strengthen or revise the rebuttal. Do not publish schema
checks, coverage bookkeeping, UUID validation, or other compliance trace as a
finding.
