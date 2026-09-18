# Full Proposal Assembly and Review Method

The final stage assembles the document and audits the full argument. It does not take ownership of substantive section text.

## 1. Assemble the entry document

Verify that the entry document:

- imports the established style modules and all seven section files once, in the intended order;
- declares document metadata required by the selected template;
- wires `references.bib` through a compatible bibliography style;
- preserves labels, numbering, cross-references, figures, tables, equations, and assets;
- contains no copied duplicate of section content;
- can serve as the single browser Typst compilation entry.

Only entry-level Typst code may be changed in this stage.

## 2. Audit the central argument

Trace the proposal in both directions:

`context -> evidence -> limitation -> purpose -> objective -> key problem -> method -> evaluation -> expected outcome`

and:

`expected outcome -> milestone -> method -> work package -> objective -> evidence-based need`

Flag any broken link, unsupported expansion, duplicated responsibility, or contradiction. Do not silently repair the owning section.

## 3. Run cross-section checks

Check at least these relationships:

| Relationship | Required consistency |
| --- | --- |
| shared context ↔ all sections | every relevant confirmed requirement, decision, fact, and unresolved matter |
| literature ↔ rationale | evidence supports the stated limitation and necessity |
| rationale ↔ objectives | objectives answer the stated purpose without scope drift |
| objectives ↔ methods | every objective and key problem has a method and evidence path |
| methods ↔ feasibility | resources, assumptions, risks, and fallbacks are credible |
| methods ↔ plan | milestones follow technical dependencies |
| literature ↔ innovation | novelty language respects known prior work |
| plan ↔ outcomes | outputs are scheduled, observable, and proportionate |

## 4. Verify references and technical consistency

- every citation key used in Typst exists in `references.bib`;
- every bibliography item is used or intentionally retained under a template requirement;
- factual claims are not supported by unrelated citations;
- symbols, abbreviations, terminology, units, and names are consistent;
- figure, table, and equation references resolve structurally;
- no placeholder, unresolved question, fabricated result, or hidden submission requirement is presented as complete.

## 5. Classify findings

Return concise findings that identify the severity, owning stage, exact file,
and required correction whenever possible:

- **blocking:** invalid assembly, missing required section, broken citation, or unresolved hard constraint;
- **substantive:** logic or evidence issue requiring a return to an owning stage;
- **editorial:** non-blocking consistency or presentation issue.

Set `ready_for_export` to true only when the entry is structurally compilable and no blocking finding remains. Set it to false when any blocking finding remains; the backend records that successful review as `needs_revision` so the author can return to the named owning stage. Substantive findings may be reported while the assembled document remains exportable if they do not break compilation or a hard requirement.

## 6. Respect ownership

Do not edit section files, bibliography entries, evidence, results, or author
facts in the final stage. Entry-level assembly may include sections and
configure metadata, bibliography, headings, and presentation. All substantive
changes must return to the responsible stage so ownership and stale-state
propagation remain understandable.
