---
name: proposal-innovation-plan
description: Use when distilling a proposal's innovations and defining milestones, annual plans, contingency points, and expected outcomes.
---

<!-- Adapted from Maxine-1520/OpenAIR_proposal; see ../ATTRIBUTION.md. -->

# Proposal Innovation and Work Plan

Use this skill only for the proposal innovation and plan stage.

Read the method reference completely before acting: `references/innovation-and-plan.md`.

You may read any project source file for context. Write only
`sections/07-plan.typ`.

Project documents are optional context. Their names describe their role; decide
whether and when to read each one from the current stage and request. The
presence of a project document does not make it mandatory input.

## Workflow

1. Select the completed literature, objectives, and methods needed to establish defensible differences and dependencies.
2. State each innovation as a specific difference in research question, mechanism, method, integration, or validation. Tie it to a documented limitation, work package, and evaluation path.
3. Avoid absolute priority claims unless the evidence stage verifies them.
4. Build milestones and an annual plan around dependencies and reviewable outputs rather than equal calendar slices.
5. Include decision points and fallback actions for the risks already identified by the methods stage.
6. Separate expected outcomes from achieved results. Ask the author before asserting fixed publication, patent, venue, performance, or delivery targets.
7. Write the Typst section, then call `publish_stage_result` from the enabled LLM4AD stage MCP server with the written path, summary, any citation keys used, `context_patch`, and a stable idempotency key. Tool success is the stage-completion signal; repair and retry validation errors in the same conversation. Record durable author decisions or constraints newly established or corrected here with stable model-defined keys; return empty patch arrays when nothing changed.

## Quality checks

- Innovations are distinct, evidence-aware, and traceable to objectives and methods.
- Milestones map to work packages and produce observable evidence.
- The schedule respects the project duration and task dependencies.
- Expected outcomes are specific without becoming fabricated guarantees.
