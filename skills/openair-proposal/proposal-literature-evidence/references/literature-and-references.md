# Literature, Evidence, and Reference Method

The literature stage is not a paper list. It builds the evidence chain that later proposal stages can safely reuse.

## 1. Build an evidence map

Start from the project foundation and identify the minimum research threads needed to support:

- the importance and specificity of the research problem;
- the state of the art for each relevant subproblem;
- the limitations that motivate the proposed work;
- the feasibility of the intended method and evaluation;
- competing or contradictory findings that affect scope.

For each thread, record its question, search terms, representative work, established evidence, limitation, and intended citation location. Search gaps, not arbitrary paper counts.

## 2. Search progressively

1. Use focused arXiv searches to discover candidate work.
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
- the BibTeX key is stable, unique, and used in the Typst section;
- no bibliography entry is orphaned and no citation key is missing;
- one paper is not being used to support unrelated claims.

Export metadata through the approved MCP tool for every cited paper. If metadata cannot be verified, omit the claim or clearly leave it unresolved; never invent bibliographic fields, evidence, or quantitative results.

The literature stage is complete only after the runtime has observed successful paper search, evidence inspection, and citation export operations. A fluent model-authored paragraph is not a substitute for those operations.

This is a recurring evidence stage, not a one-time gate. If rationale,
objectives, methods, or final review exposes a named evidence gap, rerun this
stage with that gap as the search target. Do not broaden the bibliography merely
because a later stage exists; add evidence only when it supports a concrete
claim, comparison boundary, feasibility question, or unresolved contradiction.

## 5. Citation coverage check

Before finishing, create a mental coverage matrix:

| Proposal need | Evidence present | Limitation stated | Citation key valid |
| --- | --- | --- | --- |
| problem importance | yes/no | if applicable | yes/no |
| each research thread | yes/no | yes/no | yes/no |
| proposed gap | yes/no | yes/no | yes/no |
| method precedent | yes/no | yes/no | yes/no |
| evaluation precedent | yes/no | yes/no | yes/no |

The output should be concise enough to support the proposal argument while retaining source traceability. Citation density follows the claims, not a fixed quota.
