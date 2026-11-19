# Export Template Provenance and Adaptation

The historical `styles/nsfc-proposal.typ` presentation layer is adapted from
[`Readon/NSFC-application-template-typst`](https://github.com/Readon/NSFC-application-template-typst)
at commit `39e6baf84a3db6691c2e3c95134a7dbb97f1de6b` under the MIT License.
Its generated style carries the upstream copyright and full license text.
This is export-template provenance, not an instruction to author proposal text
in Typst or to activate a shell-based writing workflow.

The adaptation reuses presentation ideas and measured layout parameters:

- A4 page geometry and margins;
- body type size, leading, indentation and paragraph spacing;
- the centered `报告正文` title;
- fixed-outline typography and heading spacing.

The upstream outline targets the 2023 application form. The earlier LLM4AD
adaptation used a 2026 three-part outline and omitted the remote `cuti` package.
These are historical adaptation facts, not a claim that a pinned template is
the current official submission form. Author-supplied or verified current
requirements take precedence; do not silently impose either dated outline.

New proposals use the nine root Markdown chapters and numbered references in
`4-参考文献.md`; do not maintain a second `proposal.md` aggregate. Only the
author's explicit **确认并排版** UI action asks the platform to convert those
ordered chapters. The model does not automatically generate/export Typst/PDF
or require a compiler. The platform owns the generated representation and its
template dependencies. Preserve license notices and link derived files to their
source revision; export failures retain drafts and earlier successful outputs.
Do not add remote packages during normal authoring. Existing Markdown and Typst
projects retain their format, files and editing support; an existing canonical
Typst source remains editable and is not a generated duplicate to discard.
