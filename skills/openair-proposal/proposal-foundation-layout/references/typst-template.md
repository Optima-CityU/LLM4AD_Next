# Typst Template Provenance and Adaptation

The default `styles/nsfc-proposal.typ` presentation layer is adapted from
[`Readon/NSFC-application-template-typst`](https://github.com/Readon/NSFC-application-template-typst)
at commit `39e6baf84a3db6691c2e3c95134a7dbb97f1de6b` under the MIT License.
The generated style file carries the upstream copyright and full license text.

Reuse is limited to presentation ideas and measured layout parameters:

- A4 page geometry and margins;
- body type size, leading, indentation, and paragraph spacing;
- the centered `报告正文` title;
- blue fixed-outline typography and heading spacing.

Do not restore the upstream document outline: it targets the 2023 application
form. LLM4AD owns the proposal structure in `proposal.typ` and currently follows
the 2026 three-part outline. Do not restore the upstream `cuti` package import;
the browser compiler must work without Typst package-network access.

Keep responsibilities separated:

- `proposal.typ` owns official outline text, section ordering, and includes;
- `styles/nsfc-proposal.typ` owns reusable presentation rules;
- `sections/*.typ` own author content and must not redefine global layout.

The template is unofficial. Preserve the current-year outline downloaded from
the NSFC information system when an author supplies it; official submission
requirements take precedence over both upstream and LLM4AD defaults.
