---
name: proposal-final-review
description: Use when assembling a completed staged Typst proposal and checking its evidence, logic, citations, structure, and export readiness.
---

<!-- Adapted from Maxine-1520/OpenAIR_proposal; see ../ATTRIBUTION.md. -->

# Proposal Assembly and Final Review

Use this skill only for the final proposal stage. Edit only the proposal entry document specified in the task prompt.

Read the method reference completely before acting: `references/full-proposal-review.md`.

You may read any project source file for context. You may modify only the entry document.
Project documents are optional context. Their names describe their role; decide whether and when to read each one from the current stage and request. The presence of a project document does not make it mandatory input.

## Workflow

1. The proposal foundation, entry document, section files, and bibliography describe the assembled proposal. Select the material needed for the checks performed in this review.
2. Ensure the entry document includes the completed section files in this logical order:
   - `sections/01-rationale.typ`
   - `sections/02-literature.typ`
   - `sections/03-value.typ`
   - `sections/04-objectives.typ`
   - `sections/05-methods.typ`
   - `sections/07-plan.typ`
   - `sections/06-feasibility.typ`
3. Preserve the layout system established in the first stage. Modify only assembly, document metadata, bibliography wiring, and other entry-level Typst configuration.
4. Apply the complete cross-section review and dependency checks from the reference.
5. Check citation keys, terminology, symbols, section order, and unresolved foundation questions. Do not silently rewrite another stage's section.
6. If a substantive or blocking section problem remains, describe it in `findings` with its severity, owning stage, exact file, and required correction; keep its source untouched so the author can return to that stage.
7. Call `publish_stage_result` from the enabled LLM4AD stage MCP server with the entry path, summary, findings, `ready_for_export`, `context_patch`, and a stable idempotency key. Use the patch only for durable context explicitly corrected or established during final interaction; use stable model-defined keys and empty arrays when nothing changed.
8. Set `ready_for_export` to `false` when any blocking finding remains. A successful publication then records a `needs_revision` review outcome instead of pretending the proposal is complete. Set it to `true` only when the entry document is structurally compilable and no blocking finding remains. Repair and retry only schema, path, or source-validation errors returned by the tool; do not retry merely because the valid review outcome requires revision.

## Constraints

- Do not weaken or override the project foundation.
- Do not fabricate missing references, results, resources, or author facts.
- Do not copy section content into the entry file; include the owned section files.
- A warning may remain in `findings` while the document is exportable, provided the Typst assembly is structurally complete.
