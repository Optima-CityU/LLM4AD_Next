# Rebuttal Playbook

Adapted without workflow compression from AutoRebuttal's rebuttal playbook.
See `/app/skills/autorebuttal-shared/ATTRIBUTION.md`.

## Concern Extraction

Convert each review into atomic concerns with this schema:

- reviewer
- concern
- concern type
- severity
- answer source
- draft move

Recommended concern types:

- novelty
- technical correctness
- missing baseline
- missing ablation
- unclear writing
- scope mismatch
- limitation / threat to validity
- reproducibility
- citation / positioning

## Reviewer Persona Layer

Use persona labels only as internal reasoning tools.

| Persona | Typical signal | Response emphasis |
| --- | --- | --- |
| Empirical skeptic | asks for more experiments, ablations, baselines | clarify existing evidence, bound promises, use placeholders instead of invented numbers |
| Theory skeptic | questions proof, assumptions, guarantees | restate assumptions, point to derivation, acknowledge limits explicitly |
| Clarity reviewer | says writing is unclear or hard to follow | simplify, point to lines/sections, promise wording or structure revisions |
| Novelty skeptic | says difference from prior work is unclear | sharpen distinction, contrast with nearest baselines, cite existing text or planned wording edits |
| Reproducibility reviewer | asks for implementation detail or setup | point to settings, release plans, appendix detail, and what will be clarified |
| Scope reviewer | asks for work beyond rebuttal scope | acknowledge value, explain scope boundary, promise future work only when realistic |

## Shared-Issue Strategy

When multiple reviewers raise the same issue:

1. Write one shared internal answer.
2. Reuse its logic consistently across reviewers.
3. Keep reviewer-specific wording light.

This avoids contradiction and unnecessary repetition.

## Draft Moves

Use one primary move per concern:

| Concern type | Primary move | Backup move |
| --- | --- | --- |
| Existing misunderstanding | clarify with a line / table / section reference | promise a wording revision |
| Genuine missing evidence | acknowledge gap and add placeholder | promise follow-up only if realistic |
| Excessive or out-of-scope demand | respectfully bound the scope | frame as future work |
| Weak positioning | sharpen contrast to prior work | promise clearer framing in revision |
| Writing complaint | simplify wording and structure | promise a clearer roadmap paragraph |

## Tone Rules

- Thank the reviewer briefly.
- Answer the concern directly.
- Avoid emotional language.
- Avoid "obviously", "clearly", and similar dismissive words.
- Prefer short paragraphs over long rhetorical blocks.

## Placeholder Policy

Use placeholders only for missing evidence, never for ideas the authors do not
actually intend to provide.

Allowed examples:

- `XX`
- `[RESULT-TO-FILL]`
- `[TABLE-PLACEHOLDER]`
- `[IF-RUN-LATER]`

Do not surround placeholders with fake certainty.

Good:

- "If the run is completed before camera-ready, we will report the value as `XX`."

Bad:

- "The final result will likely improve by `XX`."

## Final Internal Checklist

- Did we answer every substantive concern exactly once?
- Are shared issues consistent across reviewers?
- Did we avoid inventing evidence?
- Are promises realistic for the venue timeline?
- Does the tone stay polite and concrete?
