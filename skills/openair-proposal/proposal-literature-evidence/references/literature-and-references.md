# Literature, Evidence, and Reference Method

Literature synthesis is not a paper list. It builds an evidence chain that related proposal arguments can safely reuse.

## 1. Build an evidence map

Start from the project foundation and identify the minimum research threads needed to support:

- the importance and specificity of the research problem;
- the state of the art for each relevant subproblem;
- the limitations that motivate the proposed work;
- the feasibility of the intended method and evaluation;
- competing or contradictory findings that affect scope.

For each thread, record its question, search terms, representative work, established evidence, limitation, and intended citation location. Search gaps, not arbitrary paper counts.

## 2. Search progressively

1. Use focused searches through available approved research tools to discover candidate primary work; relevant author-supplied sources may already be sufficient.
2. Read titles and abstracts to remove irrelevant candidates.
3. Read bounded sections of promising papers to verify the exact claim, method, dataset, limitation, or result.
4. Follow citations only when they fill a named gap in the evidence map.
5. Stop expanding a thread when its central claim and limitation are supported by representative primary work.

Prefer primary and foundational papers, important recent developments, and work that directly defines the comparison boundary. arXiv status must not be described as peer-reviewed unless the metadata establishes a publication venue.

## 3. Organize the research landscape

Group writing by research thread, mechanism, problem class, or historical development—not by author and year. Within each thread:

1. define the problem and comparison dimension;
2. explain how representative methods progressed;
3. state what those methods established;
4. identify limitations under the proposal's actual conditions;
5. summarize the unresolved gap without claiming that no prior work exists;
6. infer a development trend only when the sequence of evidence supports it.

Use a progression such as `early formulation -> representative advance -> recent extension -> unresolved limitation`. Preserve contrary findings when the field is unsettled.

## 4. Manage references as evidence

For every cited item, verify:

- author, title, year, identifier, and publication status;
- the cited claim is actually supported by the inspected text;
- the in-text number matches the correct entry in `4-参考文献.md`, or an existing
  legacy BibTeX key remains stable, unique and resolvable;
- no cited reference is missing and uncited research candidates remain clearly
  distinguished from the final reference list;
- one paper is not being used to support unrelated claims.

Verify citation metadata using inspected primary sources or available approved citation tools. If metadata cannot be verified, omit the claim or clearly leave it unresolved; never invent bibliographic fields, evidence, or quantitative results.

A fluent model-authored paragraph is not a substitute for verified evidence. Research completeness depends on the question and available support, not a mandatory tool sequence or backend-observed operation count.

This is a recurring evidence method, not a one-time gate. If rationale,
objectives, methods, or final review exposes a named evidence gap, rerun this
method with that gap as the search target. Do not broaden the bibliography merely
because another method is available; add evidence only when it supports a concrete
claim, comparison boundary, feasibility question, or unresolved contradiction.

## 5. Numbered references and legacy bibliography

For new proposals, use the official numbered in-text form `[1]`, `[2]` or
continuous ranges such as `[1-3]`. S4, `4-参考文献.md`, holds the matching list
ordered by first occurrence across S1–S9, excluding the reference list itself.
Use verified publication metadata and consistent conference/journal formatting
where applicable: `[C]//Conference, Year.` or `[J]. Journal, Year.` Do not invent
a venue or imply peer review for a preprint.

Phase 0 numbering can be provisional. When additions or edits change first
occurrence, reconcile all affected markers and S4 entries together, preserving
the paper's identity, author annotations and manual edits. Each in-text marker
must resolve to the intended source without duplicate or skipped final numbers.

Existing citation styles and optional legacy `references.bib` remain supported.
Reuse keys, deduplicate by DOI/arXiv identifier or checked metadata, flag conflicts
and merge without overwriting entries. Do not force a new BibTeX file, change an
old project's format or maintain competing bibliographies. Search breadth follows
the evidence question; upstream examples are not literature-count, venue,
date-window, national-origin or preprint-percentage quotas.

## 6. Citation coverage check

Before finishing, create a mental coverage matrix:

| Proposal need | Evidence present | Limitation stated | Reference resolves |
| --- | --- | --- | --- |
| problem importance | yes/no | if applicable | yes/no |
| each research thread | yes/no | yes/no | yes/no |
| proposed gap | yes/no | yes/no | yes/no |
| method precedent | yes/no | yes/no | yes/no |
| evaluation precedent | yes/no | yes/no | yes/no |

The output should be concise enough to support the proposal argument while retaining source traceability. Citation density follows the claims, not a fixed quota.
