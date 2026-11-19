<!-- Adapted from Maxine-1520/OpenAIR_proposal at 9649af2ff75aa00c161b9fddc98c1282ad31aa25; see ../ATTRIBUTION.md. -->

# Author-paced proposal workflow

This adapts the official pinned
[`write-proposal`](https://raw.githubusercontent.com/Maxine-1520/OpenAIR_proposal/9649af2ff75aa00c161b9fddc98c1282ad31aa25/skills/write-proposal/SKILL.md)
guidance to one main agent. Grouped methods support the conversation; optional
helpers analyze read-only material and return text, evidence and uncertainties.
Only the main agent integrates chapter, bibliography, state and log changes.
Helpers never write shared files, scratch reports or publication records.

## Sources and chapter order

Use the established project workspace root for new proposals. Do not add an
`output/` or `sections/` layer or require another path confirmation when the
workspace is already established. The canonical text consists of these files:

| Section | Root Markdown file | Grouped method |
| --- | --- | --- |
| S1 | `1-研究目的与意义.md` | Rationale and value |
| S2 | `2-国内外研究现状.md` | Literature and evidence |
| S3 | `3-应用前景与学术价值.md` | Rationale and value |
| S4 | `4-参考文献.md` | Literature and references |
| S5 | `5-研究基础与条件.md` | Foundation and feasibility |
| S6 | `6-研究目标与内容.md` | Objectives and key problems |
| S7 | `7-研究方法.md` | Methods and evaluation |
| S8 | `8-可行性分析.md` | Foundation and feasibility |
| S9 | `9-创新点与计划.md` | Innovation and work plan |

Save chapters as their work is undertaken; missing future chapters are not
completed work. Keep `proposal-state.json` and `proposal-log.md` beside them.
They record workflow context, not a second copy of the proposal. Do not
hand-maintain an aggregate `proposal.md` or a parallel Typst source.

Existing single-Markdown, sectioned Markdown or Typst projects retain their
source paths, format, bibliography and editing support. Read the actual sources
from workflow context before choosing a format. An absent state file is not
permission to create a competing nine-chapter document or migrate an old one.
Apply phase guidance to the corresponding existing passages without renaming,
splitting, deleting or converting files automatically. An author-requested
conversion is a separate scoped change.

## Official Phase 0–6 guidance

| Phase | Research work and useful result | Sections |
| --- | --- | --- |
| 0 — 前期准备 / 文献调研 | Reuse author materials, locate the research question, establish an evidence map and initial reference numbering. | S4 |
| 1 — 立项依据核心 | Develop the research landscape and purpose together; iterate S2 ↔ S1 and fill named evidence gaps. | S2, S1, S4 |
| 2 — 价值论证 | Connect application prospects and academic value to the established need, capabilities and beneficiaries. | S3, S4 |
| 3 — 研究方案纲领 | Define objectives, content and key problems; revisit S1/S2/S3 where the bounded design changes their argument. | S6; related S1–S3 |
| 4 — 研究方法 | Develop the technical route, formulation, evaluation and failure paths; reconcile references. | S7, S4 |
| 5 — 收尾 | Derive innovations, schedule and expected outcomes; substantiate feasibility and real research foundation. Independent read-only analysis may run in parallel. | S9, S8, S5 |
| 6 — 全文审查 | Review the whole argument, references, terminology, notation and cross-section consistency; present findings and targeted revisions for author review. | S1–S9 |

These phases guide research, not frontend stage locks or backend save/export
gates. S4 remains a continuing evidence task; S5 can use confirmed facts whenever
available. Recommend useful dependencies without blocking an author's explicit
request for a particular chapter, iteration or different order. A local edit
does not restart all phases. Do not invent mandatory iteration counts, length,
venue, publication-count or percentage constraints from upstream examples.

Within the agreed phase, carry out authorized research and ordinary saves.
Before advancing each meaningful phase, summarize what was actually saved or
remains a candidate, the evidence and open choices, and offer concrete options:
continue with a recommended next phase, revise the current work, supply missing
facts, or change/defer the route. Wait for the author's choice before advancing;
do not treat silence, tool success, a helper response, a completed progress step
or `draft_done` as that choice. A confirmation to proceed does not settle an
unanswered scientific choice or authorize final typesetting. Ask within a phase
when consequential alternatives require the author's decision, rather than
requesting approval for each tool call or ordinary chapter save.

## Durable state, author choices and recovery

Retain the upstream state fields when applicable: `output_path`, `created_at`,
`last_updated`, `project_info`, `current_phase`, `sections` and `ref_counter`.
`project_info` records known topic, field, program/level and duration without
making unknown values mandatory intake gates. Each S1–S9 record identifies its
actual `file`, `status`, `iteration`, `last_updated` and `notes`. Existing-format
projects record their real file/heading locations instead of new chapter files.
`ref_counter` tracks assigned reference numbers, never a literature target or
completion percentage. Preserve existing state fields and recorded evidence.

| Section status | Meaning |
| --- | --- |
| `not_started` | No work on this section has started. |
| `in_progress` | Work is underway; record an interrupted or unresolved save accurately. |
| `draft_done` | An initial draft is saved in its canonical source. |
| `revised` | A revision is saved; the author has not necessarily finalized it. |
| `finalized` | The author explicitly accepted this section as final at the recorded revision. |

Increment `iteration` for a saved draft/revision, update timestamps and append
the actual transition to `proposal-log.md`. Record pending candidates separately;
an unapplied diff is not a saved chapter. Keep `current_phase` at the current
phase while awaiting its author decision; record confirmation before changing
it. Draft readiness can justify recommending a transition, never automatic
advancement. Phase 6 review does not automatically mark sections `finalized`.

Use existing context fields when available; otherwise the main agent can add
`author_context` and `phase_reviews` to this local state. For example, these are
extension fields, not a backend request schema:

```json
{
  "author_context": {
    "confirmed_facts": [],
    "decisions": [],
    "open_choices": []
  },
  "phase_reviews": {
    "0": {
      "status": "awaiting_author",
      "summary": "Initial evidence map saved; research scope remains open.",
      "options": ["Refine scope", "Continue to Phase 1 after choosing scope"],
      "author_choice": null,
      "source_revision": null
    }
  }
}
```

Record each confirmed fact with its author/source locator. Record selected and
discarded routes, reasons, unresolved alternatives and the exact author message
or decision reference and source revision when available. Preserve confirmed
prior facts and choices across turns; do not repeatedly ask for supplied facts
or silently turn a hypothesis or open choice into a settled design. A later
author correction supersedes the earlier fact explicitly, with its provenance
retained. A phase confirmation records permission to advance, separately from
section finalization and the final UI action.

Append log entries for saved work, status changes, author decisions and recovery
events, including time, phase, method, affected chapters, summary and remaining
questions. Preserve prior entries and evidence locators. Do not copy proposal
text into the log as another editable source.

On resume, read fresh workflow/region context, state, log and the actual existing
chapters or legacy sources. Compare their revisions and manual edits; current
author text and explicit author decisions take precedence over stale ledger
claims. If a save was interrupted, reconcile the files and publication response
before repairing state. Do not infer approval from file existence or a former
`draft_done`. Restore the pending author choice and show current progress with
next options. Keep previously confirmed facts without repeating intake. If an
edit affects a formerly finalized passage, expose that change for renewed
author review while retaining the earlier acceptance record. Reconcile manual
edit conflicts and pending candidates through the existing publication flow.

## References and final author action

For new chapters, use numbered in-text references such as `[1]`, `[2]` or a
continuous range `[1-3]`, matching entries in `4-参考文献.md`. Follow first
occurrence across chapter order S1–S9, excluding S4's own list. Phase 0 may start
with provisional numbers; reconcile all affected chapter markers together as
the argument develops, preserving source identity and manual annotations.
Verify authors, title, year, identifier and publication status. Use the official
conference/journal form where applicable (`[C]//` or `[J].`) without inventing a
venue or mislabelling a preprint. Every in-text number resolves to the correct
entry. Keep uncited research candidates identifiable as notes, not falsely
cited final references. Legacy BibTeX and existing keys remain supported; do not
force a new `references.bib` or copy its contents into a competing bibliography.
Evidence coverage follows research claims, without numeric literature quotas.

After Phase 6, present the saved chapters, remaining findings and author options
for revision or finalization. Only the author's explicit **确认并排版** UI action
requests platform conversion of the ordered canonical chapters to Typst/PDF.
The model must not click it for the author, automatically generate/export
Typst/PDF, require a compiler or treat `draft_done`/successful publication as
final approval. Phase confirmation in chat alone does not trigger conversion.
The platform owns generated presentation files; they are not editable duplicate
proposal sources. Preserve saved chapters and prior successful outputs if that
explicit export fails. Legacy documents keep their existing sources and editing
workflow without automatic migration.
