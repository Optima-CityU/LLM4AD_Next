# OpenAIR_proposal attribution and adaptation map

The proposal-writing methods in this directory are adapted from
[`Maxine-1520/OpenAIR_proposal`](https://github.com/Maxine-1520/OpenAIR_proposal),
reviewed at commit `9649af2ff75aa00c161b9fddc98c1282ad31aa25`. The pinned
[`write-proposal` source](https://raw.githubusercontent.com/Maxine-1520/OpenAIR_proposal/9649af2ff75aa00c161b9fddc98c1282ad31aa25/skills/write-proposal/SKILL.md)
defines S1–S9, state/log, Phase 0–6, recovery and author-paced confirmation. The
[`write-references` source](https://raw.githubusercontent.com/Maxine-1520/OpenAIR_proposal/9649af2ff75aa00c161b9fddc98c1282ad31aa25/skills/write-references/SKILL.md)
and [`write-literature-review` source](https://raw.githubusercontent.com/Maxine-1520/OpenAIR_proposal/9649af2ff75aa00c161b9fddc98c1282ad31aa25/skills/write-literature-review/SKILL.md)
define numbered citations and the matching S4 reference list.

LLM4AD does not present these skills as an independent upstream design. It
uses one `openair-proposal` assistant Skill in a coordinator conversation.
The grouped methods are retained under `references/` with their detailed guides
and original provenance. The main agent writes nine canonical root Markdown
chapters for new projects, maintains state/log and publishes versioned revisions.
Existing Markdown and Typst projects retain their format, files and editing
workflow without automatic migration. Optional read-only workers return text
diffs and evidence; they do not create scratch files, edit source or publish.
The pinned revision is not a claim that the latest upstream or official
submission rules were checked.

## Adaptation map

| Preserved method reference | OpenAIR_proposal source skill(s) | Adaptation |
| --- | --- | --- |
| `author-paced-workflow`, `proposal-foundation-layout` | `write-proposal` | Retains nine root chapter names, Phase 0–6, state/log, recovery and author confirmations; adds durable choices and separates saving from final UI typesetting. |
| `proposal-literature-evidence` | `write-literature-review`, `write-references` | Combines evidence discovery, research-landscape writing and numbered in-text/S4 consistency; preserves optional legacy BibTeX. |
| `proposal-rationale` | `write-research-purpose`, `write-application-prospects` | Combines purpose, necessity, significance and value without imposing a dated official outline. |
| `proposal-objectives` | `write-research-objectives`, `write-key-problems` | Keeps objectives, research content and key problems conceptually coupled, without stage gates. |
| `proposal-methods` | `write-research-methods` | Preserves assumptions, evaluation evidence, falsification/failure conditions and consistent Markdown mathematics. |
| `proposal-innovation-plan` | `write-innovation-plan` | Groups innovation claims, milestones, annual planning and expected outcomes as research guidance. |
| `proposal-foundation-feasibility` | `write-research-foundation`, `write-feasibility` | Keeps achieved foundation facts and feasibility claims distinguishable from future plans, without fixed file ownership. |
| `proposal-final-review` | `write-proposal` Phase 6 | Retains argument, terminology and citation cross-checks, followed by author review; only the explicit final UI action requests platform conversion. |

The coordinator and unified assistant implement the upstream `write-proposal`
guidance in one conversation, selecting grouped methods instead of dispatching
writing subskills. Each meaningful phase ends with a summary and options, then
waits for the author's choice. The upstream README's optional autonomous mode
is not the proposal policy here. Available approved research tools supply
evidence and verified citation metadata. Upstream examples of length, venue,
literature counts, date windows or percentages are not imposed as constraints.
The eleven enhancement areas in the unified entry cover intake, parallel
read-only research, evidence, bibliography, brainstorming, argument structure,
evaluation/failure, innovation, resources/ethics, targeted revision and authorship.
They remain assistant guidance rather than backend scientific validators.
The separately vendored `typst-author` dependency is not derived from OpenAIR;
it is not required for normal Markdown authoring.

## Typst layout source

OpenAIR_proposal provides a Markdown writing workflow, not a Typst template.
The historical LLM4AD NSFC presentation layer is separately adapted from
[`Readon/NSFC-application-template-typst`](https://github.com/Readon/NSFC-application-template-typst)
at commit `39e6baf84a3db6691c2e3c95134a7dbb97f1de6b` under the MIT License. Only its
layout parameters and presentation approach are reused. Its 2023 outline and
remote `cuti` package dependency were not used; that adaptation supplied a 2026
outline and a self-contained style. Those historical choices do not override
author-supplied or verified current official requirements. The platform now
converts ordered canonical chapters through deterministic Pandoc/Typst/PDF only
after the author's explicit **确认并排版** UI action. The model never generates
Typst/PDF automatically or requires a compiler. See
`proposal-foundation-layout/references/typst-template.md` for the adaptation
boundary.

## Structural differences

- OpenAIR_proposal writes nine Markdown sections under one orchestration skill;
  new LLM4AD proposals retain their exact filenames at the workspace root plus
  `proposal-state.json` and `proposal-log.md`.
- LLM4AD groups closely related methods under one assistant entry, using official
  Phase 0–6 as author-paced guidance rather than mandatory frontend stages.
- Historical stage keys and artifact contracts remain compatible with saved work;
  methods do not impose backend stage locks or manual agent switching. The author
  can choose a chapter, revisit earlier work or change the recommended order.
- Optional workers are read-only and return text diffs; they do not write scratch
  reports or source. The main agent integrates against the captured revision,
  preserving author edits, bibliography keys and approval flows.
- New-project authoring uses nine chapters with numbered references matching
  `4-参考文献.md`, without a second hand-maintained `proposal.md` aggregate.
  Optional legacy BibTeX and old single-Markdown/Typst sources remain supported.
  Platform-generated Typst/PDF are version-linked outputs after the explicit UI
  action, not a second source.
- Saving a Markdown draft and exporting a PDF are separate outcomes; export
  failure preserves the saved draft and complete earlier output history.
- Innovation/planning and achieved foundation/feasibility remain distinct methods.
- The review-and-return loop produces targeted guidance without an editorial
  export gate. Ordinary saves do not require approval absent manual edits or
  conflicts; they do not imply phase confirmation or author finalization.

## License note

No `LICENSE` file was present in the referenced upstream revision when this
attribution was recorded. This notice documents provenance; it does not create
or replace an upstream license grant. Confirm reuse permission before publishing
or redistributing adapted material outside the applicable project context.
