---
name: proposal-methods
description: Use when translating established proposal objectives into research methods, a technical route, formal definitions, and an evaluation plan.
---

<!-- Adapted from Maxine-1520/OpenAIR_proposal; see ../ATTRIBUTION.md. -->

# Proposal Research Methods

Use this skill only for the proposal methods stage. Write only `sections/05-methods.typ`.

Read the method reference completely before acting: `references/research-methods.md`.

You may read any project source file for context. You may modify only `sections/05-methods.typ`.
Project documents are optional context. Their names describe their role; decide whether and when to read each one from the current stage and request. The presence of a project document does not make it mandatory input.

## Workflow

1. The foundation, evidence, rationale, and objectives provide optional context for method design. Select the material needed to keep the proposed methods aligned and source-grounded.
2. Apply the technical-route, problem-modeling, mechanism-design, validation, notation, and risk methods from the reference.
3. Specify assumptions, inputs, procedure, evaluation evidence, failure conditions, and fallback paths at an appropriate level of technical depth.
4. Preserve mathematical notation and define every symbol consistently in Typst.
5. Use `AskUserQuestion` when the choice of method, data, experimental condition, or evaluation standard materially changes the project.
6. Do not claim unavailable data, equipment, results, or implementation maturity.
7. Write the Typst section, then call `publish_stage_result` from the enabled LLM4AD stage MCP server with the written path, summary, citation keys actually used, `context_patch`, and a stable idempotency key. Tool success is the stage-completion signal; repair and retry validation errors in the same conversation. Record any durable method boundary, author decision, resource fact, or other project-specific context newly established or corrected in this stage using model-defined blocks and stable keys. Return empty patch arrays when nothing changed.

## Quality checks

- The technical route is executable and directly addresses the objectives.
- Evaluation criteria can distinguish success from failure.
- Risks and assumptions are visible rather than hidden behind general language.
- Citations exist in the project bibliography.
