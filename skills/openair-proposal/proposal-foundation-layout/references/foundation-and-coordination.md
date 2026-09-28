# Proposal Foundation and Coordination Method

This method adapts a full-proposal writing workflow to LLM4AD Next's
backend-managed stages. The platform owns stage status, source persistence,
session continuity, and write isolation; do not create a second state file or
workflow log in the source tree. The latest published result is authoritative;
do not assume that the source tree is a version-control history.

## 1. Establish the project foundation

Derive or confirm the project-specific information needed before section drafting begins. The following are prompts for analysis, not a required schema:

- proposal or funding-program type, target readers, language, and submission format;
- research theme, object of study, central problem, and intended decision or outcome;
- required sections, evidence, deliverables, page or word limits, and deadline constraints;
- available source material, preliminary results, team facts, equipment, and institutional facts;
- explicit exclusions, confidentiality constraints, and facts that remain unknown;
- presentation principles that later sections must preserve.

Do not turn guesses into foundation facts. Use `AskUserQuestion` only for choices that materially change scope, compliance, or the document architecture. Represent confirmed facts and unresolved matters as clearly titled model-defined context blocks. Their number and internal Markdown hierarchy should follow the project, not this checklist.

## 2. Inspect the source before changing layout

1. Identify the Typst entry file, imported section files, bibliography, assets, and style modules.
2. Detect an institutional or venue template. Its required page geometry, headings, numbering, and metadata take precedence over generic preferences.
3. Identify missing imports or paths without editing stage-owned content files.
4. Preserve mathematical meaning, labels, citation keys, and source ordering.

## 3. Create a durable Typst system

Centralize reusable presentation rules in the entry document or `styles/`:

- page size, margins, header, footer, and page numbering;
- body font with multilingual fallbacks, line spacing, and paragraph rhythm;
- heading hierarchy, numbering, and keep-with-next behavior;
- figure, table, equation, caption, cross-reference, and bibliography styles;
- restrained emphasis and color that remain legible in print.

Prefer semantic Typst structure over manual coordinates and repeated spacing. Reuse an existing template instead of replacing it. Do not add remote Typst packages unless the author explicitly approves them.

## 4. Preserve the proposal dependency chain

The eight platform stages correspond to these writing responsibilities:

1. foundation and presentation;
2. evidence, research landscape, and references;
3. research purpose, necessity, significance, and application value;
4. objectives, research content, and key problems;
5. methods, technical route, and evaluation;
6. innovation, schedule, milestones, and expected outputs;
7. research foundation, available conditions, feasibility, and risk controls;
8. assembly and cross-section review.

Later stages may read earlier and later draft files for consistency, but the platform controls which stage owns each output. Do not create, rewrite, or repair another stage's file.

The sequence is iterative rather than write-once. Literature and rationale may
need more than one pass, and later stages can expose missing evidence or an
over-broad objective. Return to the owning stage when that happens. The backend
marks only its transitive dependents stale, so repair the earliest affected
stage and then refresh the downstream chain instead of silently patching files
from the wrong agent. Each successful publication increments the owning stage's
iteration counter; it records how many accepted passes occurred, not a complete
source snapshot history.

## 5. Foundation quality gate

Before returning the result, confirm:

- the theme names a bounded research object rather than only a broad field;
- the intended outcome is reviewable and does not promise an unsupported result;
- the chosen context blocks capture the actual submission rules, expected evidence, scope exclusions, and non-negotiable author requirements without forcing irrelevant headings;
- presentation decisions are actionable throughout the document when they matter;
- every durable context claim is traceable to a source file or author answer.

Return an explicit context patch. Reuse stable keys for changed blocks, add keys for materially new topics, and remove keys only when they are explicitly invalidated. Unchanged blocks must be omitted from the patch so the backend can preserve them exactly.
