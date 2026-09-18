# OpenAIR_proposal attribution and adaptation map

The proposal-writing skills in this directory are adapted from
[`Maxine-1520/OpenAIR_proposal`](https://github.com/Maxine-1520/OpenAIR_proposal),
reviewed at commit `9649af2ff75aa00c161b9fddc98c1282ad31aa25`.

LLM4AD does not present these skills as an independent upstream design. It
reworks the original Claude Code proposal workflow into backend-governed stages:
each stage runs in a separate agent session, owns explicit Typst files, publishes
through the LLM4AD stage bridge, and shares durable project context with later
stages.

## Adaptation map

| LLM4AD stage skill | OpenAIR_proposal source skill(s) | Adaptation |
| --- | --- | --- |
| `proposal-foundation-layout` | `write-proposal` | Adds LLM4AD intake, durable context, file ownership, and Typst layout coordination. |
| `proposal-literature-evidence` | `write-literature-review`, `write-references` | Combines evidence discovery, research-landscape writing, and BibTeX consistency. |
| `proposal-rationale` | `write-research-purpose`, `write-application-prospects` | Combines purpose, necessity, significance, and value within the official template's first part. |
| `proposal-objectives` | `write-research-objectives`, `write-key-problems` | Keeps objectives, research content, and key problems in one tightly coupled stage. |
| `proposal-methods` | `write-research-methods` | Adds explicit assumptions, evaluation evidence, failure conditions, and Typst notation rules. |
| `proposal-innovation-plan` | `write-innovation-plan` | Owns innovation claims, milestones, annual plan, and expected outcomes. |
| `proposal-foundation-feasibility` | `write-research-foundation`, `write-feasibility` | Keeps achieved foundation facts and feasibility claims together under the official template's third part. |
| `proposal-final-review` | `write-proposal` Phase 6 | Adds a read-mostly cross-stage assembly and export gate. |

The backend orchestration in `src/backend/app/services/paper_workflow.py` replaces
the upstream `write-proposal` orchestrator. The arXiv MCP integration replaces
the upstream search helper for this runtime. `typst-author` is a separate
vendored dependency with its own attribution and is not derived from
OpenAIR_proposal.

## Typst layout source

OpenAIR_proposal provides a Markdown writing workflow, not a Typst template.
The default LLM4AD NSFC presentation layer is separately adapted from
[`Readon/NSFC-application-template-typst`](https://github.com/Readon/NSFC-application-template-typst)
at commit `39e6baf84a3db6691c2e3c95134a7dbb97f1de6b` under the MIT License. Only its
layout parameters and presentation approach are reused. Its 2023 outline and
remote `cuti` package dependency are not used; LLM4AD supplies the 2026 outline
and a self-contained browser-compatible style. See
`proposal-foundation-layout/references/typst-template.md` for the adaptation
boundary.

## Structural differences

- OpenAIR_proposal writes nine Markdown sections under one orchestration skill.
- LLM4AD groups tightly coupled source skills according to the official fund
  template, but gives every resulting stage a separate agent session.
- Content stages cannot edit one another's files. Re-running an earlier stage
  invalidates dependent later results so the author can iterate safely.
- Innovation and work planning are separate from research foundation and
  feasibility. This prevents one agent from mixing future claims with achieved
  facts and keeps both sections under the correct official-template heading.
- Final review can finish with a durable `needs_revision` outcome. Blocking
  findings name the owning stage and file, preserving the upstream
  review-and-return loop without granting the reviewer cross-stage write access.

## License note

No `LICENSE` file was present in the referenced upstream revision when this
attribution was recorded. This notice documents provenance; it does not create
or replace an upstream license grant. Confirm reuse permission before publishing
or redistributing adapted material outside the applicable project context.
