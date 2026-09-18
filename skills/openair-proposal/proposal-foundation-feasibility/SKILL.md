---
name: proposal-foundation-feasibility
description: Use when documenting a proposal's research foundation, available conditions, team support, feasibility, and risk controls from author-supplied facts.
---

<!-- Adapted from Maxine-1520/OpenAIR_proposal; see ../ATTRIBUTION.md. -->

# Proposal Research Foundation and Feasibility

Use this skill only for the proposal foundation and feasibility stage.

Read the method reference completely before acting: `references/foundation-and-feasibility.md`.

You may read any project source file for context. Write only
`sections/06-feasibility.typ`.

Project documents are optional context. Their names describe their role; decide
whether and when to read each one from the current stage and request. The
presence of a project document does not make it mandatory input.

## Workflow

1. Select the foundation, completed methods, author materials, and source-grounded facts needed to assess capability and feasibility.
2. Document only real preliminary work, team capabilities, facilities, data access, collaborations, and institutional support supplied by the author or trusted source material.
3. Explain which planned task or project risk each foundation item supports; do not present a biography or equipment inventory as proof by itself.
4. Assess goal, technical, resource, schedule, and risk feasibility against the completed objectives and methods.
5. Ask the author for missing consequential facts. Never invent publications, results, team members, facilities, data access, or available resources.
6. Keep achieved facts distinct from planned work and conditional risk controls.
7. Write the Typst section, then call `publish_stage_result` from the enabled LLM4AD stage MCP server with the written path, summary, any citation keys used, `context_patch`, and a stable idempotency key. Tool success is the stage-completion signal; repair and retry validation errors in the same conversation. Upsert durable author-confirmed facts or decisions newly established or corrected in this stage under model-defined stable keys. Do not repeat untouched blocks or force a fixed field structure.

## Quality checks

- Every feasibility claim has author-provided or source-grounded support.
- Every required method has credible expertise, resources, time, and a fallback.
- Achieved work and proposed work are unmistakably separated.
- Names, results, facilities, and team facts are not inferred or embellished.
