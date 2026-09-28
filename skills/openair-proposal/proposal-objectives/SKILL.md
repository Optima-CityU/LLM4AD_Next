---
name: proposal-objectives
description: Use when defining a proposal's objective hierarchy, research content, work packages, and key scientific or technical problems.
---

<!-- Adapted from Maxine-1520/OpenAIR_proposal; see ../ATTRIBUTION.md. -->

# Proposal Objectives and Key Problems

Use this skill only for the proposal objectives stage. Write only `sections/04-objectives.typ`.

Read the method reference completely before acting: `references/objectives-and-key-problems.md`.

You may read any project source file for context. You may modify only `sections/04-objectives.typ`.
Project documents are optional context. Their names describe their role; decide whether and when to read each one from the current stage and request. The presence of a project document does not make it mandatory input.

## Workflow

1. The foundation and completed earlier sections provide optional context for scope, evidence, and dependencies. Select the material needed for the objectives being defined.
2. Apply the objective-content-problem correspondence and key-problem argument methods from the reference.
3. Define research work packages and the key scientific or technical questions that make each package necessary.
4. Make dependencies and boundaries explicit: what is in scope, what is excluded, and what evidence would demonstrate progress.
5. Ask the author only when a consequential objective, deliverable, or boundary remains ambiguous.
6. Write the Typst section, then call `publish_stage_result` from the enabled LLM4AD stage MCP server with the written path, summary, reused citation keys, `context_patch`, and a stable idempotency key. Tool success is the stage-completion signal; repair and retry validation errors in the same conversation. Upsert project-specific durable context learned or corrected here under descriptive stable keys. Do not map it into predefined headings, repeat unchanged blocks, or remove a block without explicit invalidation.

## Quality checks

- Objectives describe outcomes, not a list of activities.
- Every work package maps to a stated problem and expected evidence.
- The scope is achievable within the duration and constraints in the foundation.
- No method detail is invented merely to fill the next stage.
