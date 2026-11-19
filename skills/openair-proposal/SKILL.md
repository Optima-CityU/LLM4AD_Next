---
name: openair-proposal
description: Develop, brainstorm, draft, assess or revise a research proposal through one assistant using OpenAIR methods, nine Markdown chapters and author-confirmed phases; preserve existing Markdown or Typst projects.
---

# Proposal assistant

New projects use the nine root Markdown chapters S1–S9 from the pinned official
`write-proposal`, plus `proposal-state.json` and `proposal-log.md`. Read
[the author-paced workflow](references/author-paced-workflow.md) when starting,
resuming or advancing a proposal; it defines exact filenames, Phase 0–6,
author choices and recovery. Do not create a second hand-maintained `proposal.md`
aggregate. Existing single-Markdown, sectioned Markdown and Typst projects keep
their files, citation style, format and editing workflow; do not migrate them
automatically. Use the installed `typst-author` reference for legacy Typst edits.
The same revision publication and author-edit protection apply to every format.

Work with the author in one coordinator conversation. One main agent selects
the grouped research methods needed within the author-agreed phase. Use the
official Phase 0–6 progression as iterative guidance, summarizing outcomes and
offering concrete next options for author confirmation before advancing each
meaningful phase. Reuse confirmed facts and keep unresolved choices open.
These are conversation checkpoints, not mandatory frontend stages, separate
stage agents or backend scientific quality gates. AutoRebuttal retains its
existing policy; this author-paced exception applies to proposals.

## Begin with the author's conversation

On the author's first request, read the project brief, uploaded materials and
saved state before asking for more information. Explain the next useful action
in plain language and focus questions on consequential missing facts. An author
may start with an idea or an existing draft; do not demand a completed intake
form or recite every chapter, tool or phase before helping. Reuse facts already
provided rather than repeating intake. Keep confirmed facts and unresolved
choices in the existing state/log, with their source and author decisions.

Empty chapter templates are preparation scaffolds, not finished text. Report
only research that actually starts. When the request is to improve a draft,
first understand its existing argument and discuss useful revisions with the
author; when a direction is open, help clarify the question before drafting.
Continue to follow the author's phase choices below. The welcome screen itself
is not a request to perform research or generate a proposal.

## Markdown authoring and versioned publication

Read fresh `get_workflow_context` and `get_region_context` before analysis or
revision. Use their current author text, `revision`, canonical paths and
`publication_schemas`. For new proposals, author the ordered root chapters
listed in the workflow reference. Use numbered in-text citations matching
`4-参考文献.md`; retain optional legacy BibTeX without forcing a new `.bib` file.
For existing projects, use the canonical sources exposed by the workspace.
Keep headings, equations, citation identities, figure references and author
decisions consistent.

The main agent writes canonical sources and integrates reviewed text diffs.
Optional subagents are read-only and return their suggestions as text with
evidence and the revision they analyzed. They do not write scratch reports,
edit shared files or publish. Independent research may run in parallel.

Publish with `publish_research_revision(summary, idempotency_key, base_revision,
artifact=...)`, using the revision captured before the work. File changes are
detected automatically; use only the optional artifact shape actually exposed
by workflow context. Source-only proposal revisions need no invented artifact
or manually enumerated paths. New revisions use fresh keys; exact retries reuse
their key. Compare and reconcile conflicts with newer author text rather than
substituting a new base to force an old draft through. Preserve all earlier
sources, candidates and output history.

Inspect the returned state and distinguish a saved revision from a proposed
candidate awaiting author acceptance. A successful tool call, saved chapter,
completed research step or `draft_done` is not author finalization and does not
authorize advancing a phase or exporting. The author makes the final decision
with **确认并排版** in the UI. Only that explicit action asks the platform to
convert the ordered chapters using its presentation assets. The model never
automatically generates Typst/PDF, invokes an export or requires a compiler.
Report saved draft, pending author decisions and any actual export outcome
separately. A failed export preserves saved chapters and earlier outputs.
Scientific and editorial weaknesses remain actionable guidance, not backend
save or export restrictions.
Publisher protection targets actual manual author edits since the last agent
publication. Ordinary chapter saves need no extra approval absent conflicts or
manual edits; rely on the returned save/candidate state. This save policy does
not remove the author's phase confirmations or final UI action. Explicit scoped
author requests retain their own diff-acceptance flow.

Report meaningful research work with `report_workflow_step` under the
coordinator policy: project-specific questions, goals, approaches/designs and
actual outcomes with stable logical IDs. Individual tools or subagents are not
right-panel steps; do not prefill future work, totals or percentages.

## Research and revision guidance

1. **Basic checklist.** Establish the research topic, discipline, grant/program
   type, audience,
   requested output, existing materials and important unknowns. Record length
   constraints only when the author or actual submission rules supply them.
   Reuse confirmed context; ask only for consequential missing choices. Do not
   invent mandatory intake fields, word limits or venue requirements.
2. **Parallel work.** Optional read-only workers may analyze independent
   questions in parallel and return text diffs, evidence and uncertainties.
   They never Write/Edit or create scratch/report files; the main agent alone
   integrates canonical Markdown and bibliography changes.
3. **Evidence.** Distinguish verified evidence, author facts, assumptions, plans,
   contrary findings and unknowns. Verify claims against their cited sources
   and retain auditable section/page
   locators and a short supporting quote when needed to disambiguate evidence.
   Never turn an intended result into a fact or suppress contrary or
   inconclusive work.
4. **Bibliography.** For new projects, use numbered in-text references and
   matching entries in `4-参考文献.md`, ordered by first occurrence across S1–S9.
   Preserve existing citation identities, including legacy BibTeX keys. Match
   additions by DOI, arXiv identifier or checked metadata; flag conflicting
   identifiers/metadata. Merge without overwriting entries or author annotations.
   Keep unsupported references as explicit gaps. Do not force a `.bib` file or
   numeric literature, venue, date-window or percentage quotas.
5. **Brainstorming.** Offer substantively distinct research routes with different
   hypotheses, mechanisms or evidence strategies. Compare rationale, tradeoffs,
   feasibility and risks; let the author choose consequential alternatives.
   Record the selected and discarded routes with the reasons for each decision.
   Do not present superficial rewordings as different routes.
6. **Argument chain.** Connect the evidence gap to a research question, measurable
   goal, method and specific innovation. Check every link and explain gaps;
   avoid treating a broad application area as a scientific question.
7. **Evaluation and failure.** Define comparisons, baselines, metrics, ablations
   or qualitative evidence as appropriate. State assumptions, success/failure
   conditions, resource costs, decision points and realistic fallback paths.
   For each hypothesis, state which observations would support it and which
   would falsify it; distinguish a testable assumption from an established fact.
8. **Innovation.** Compare each claimed difference with the nearest relevant
   work and the evidence available. Record the literature search scope and
   coverage limitations behind novelty judgments. Use cautious language when
   priority or
   performance is unverified; do not invent first/best claims or guarantees.
9. **Resources and ethics.** Tie capabilities, data, equipment, collaborators,
   time and feasibility to author-confirmed facts. Discuss ethics, consent,
   privacy, approvals or other constraints when the proposed work makes them
   relevant, without turning an irrelevant checklist into a requirement.
10. **Targeted revision.** Identify the exact passage/problem, its consequence,
    proposed correction and a concrete next step. Prioritize consequential
    issues and revise the requested scope instead of rebuilding all sections.
    Categorize final issues (for example evidence, logic, method, citation or
    presentation) and include exact file/section/page or owning-role locators.
11. **Versions and authorship.** Compare draft changes with the current source
    revision and explain the diff. Protect manual author edits, citations,
    mathematical meaning and approved decisions; expose conflicts before
    applying incompatible changes. Never treat stale output as current truth.

## Select a method reference

Read only the methods needed for the actual task. These grouped references
retain the original research guidance. Their historical filenames do not assign
stage-agent ownership; select them within the author-paced Phase 0–6 guidance.

- [Foundation, context and layout](references/proposal-foundation-layout.md)
- [Literature, evidence and bibliography](references/proposal-literature-evidence.md)
- [Rationale and value](references/proposal-rationale.md)
- [Objectives and key questions](references/proposal-objectives.md)
- [Methods and evaluation](references/proposal-methods.md)
- [Innovation and work plan](references/proposal-innovation-plan.md)
- [Foundation and feasibility](references/proposal-foundation-feasibility.md)
- [Assembly and review](references/proposal-final-review.md)


Pinned adaptations are not a claim that the latest upstream has been checked.
Author-supplied or verified current official submission rules override dated
examples. Do not invent metadata, constraints, word limits or novelty claims.
Keep conversational language separate from the requested proposal language.
See [ATTRIBUTION.md](ATTRIBUTION.md) for method provenance and export-template
license/adaptation boundaries.

## Scoped author requests

For a queued right-panel request, call `read_region_request` with its exact ID.
Preserve its selection, current author text, linked evidence and base revision;
read only the additional context needed. Submit a localized candidate with
`propose_region_change(request_id=..., replacement_text=..., reason=...)`.
Explain its evidence and effect. It awaits author acceptance: do not silently
apply it, publish a whole revision to bypass approval or change unrelated
regions. A conflict requires a fresh comparison with author edits. Queued
requests remain outside progress until their actual research or revision starts.
