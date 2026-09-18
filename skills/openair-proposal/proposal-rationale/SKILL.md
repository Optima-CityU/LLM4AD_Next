---
name: proposal-rationale
description: Use when developing a proposal's research purpose, necessity, significance, application value, and practical problem framing from verified evidence.
---

<!-- Adapted from Maxine-1520/OpenAIR_proposal; see ../ATTRIBUTION.md. -->

# Proposal Rationale and Value

Use this skill only for the proposal rationale stage.

Read the method reference completely before acting: `references/purpose-and-value.md`.

## Outcome

Turn the project foundation and verified literature into a coherent argument for why the research should be undertaken. Write only:

- `sections/01-rationale.typ`
- `sections/03-value.typ`

You may read any project source file for context. The proposal foundation describes the established scope, the literature section describes the evidence landscape, and the bibliography defines available citation keys. Never edit files owned by another stage.
Project documents are optional context. Their names describe their role; decide whether and when to read each one from the current stage and request. The presence of a project document does not make it mandatory input.

## Workflow

1. Apply the purpose, specificity, necessity, significance, application-value, and practical-problem methods from the reference.
2. Separate research purpose from academic, practical, and potential application value.
3. Ground factual claims in the literature stage and reuse only citation keys present in `references.bib`.
4. Ask the author when the intended impact, audience, or application claim is consequential and not resolved by the foundation.
5. Preserve the scope and success criteria in the foundation. Do not enlarge the proposal merely to sound ambitious.
6. Write complete Typst sections, then call `publish_stage_result` from the enabled LLM4AD stage MCP server with both paths, the citation keys used, `context_patch`, and a stable idempotency key. Tool success is the stage-completion signal; repair and retry validation errors in the same conversation. Upsert any model-defined durable context established or corrected through this stage, preserving existing stable keys; remove a key only when explicitly invalidated. Return empty patch arrays when shared context did not change.

## Quality checks

- The rationale identifies a precise gap rather than asserting generic importance.
- Claimed value follows from the proposed work and is not presented as an achieved result.
- Terminology, scope, and audience match the persisted foundation.
- No evidence, team capability, or expected result is fabricated.
