# Attribution

This skill is vendored from an upstream project, not written for LLM4AD Next.

- **Source**: https://github.com/apcamargo/typst-skills (`typst-author`)
- **Commit**: `433372acabe4764020632317fe1ca914ce27a945` (2026-09-08)
- **License**: the upstream repository declares none. Its `docs/` folder is a
  mirror of the official Typst documentation, which is Apache-2.0
  (https://github.com/typst/typst). Confirm the upstream license before
  redistributing this directory outside this repository.

## Why it is vendored

The upstream README states the problem directly: most models struggle with Typst
syntax because the language is newer than their training data, so the skill
ships a local copy of the Typst 0.15 documentation and instructs the agent to
read it instead of guessing.

That is exactly the failure this project hit. The previous hand-written Typst
skill named no list functions, and a stage produced `#numbered-list(...)` — a
function that does not exist. Typst parsed the hyphen as subtraction and
reported `unknown variable: numbered-list`. The real function is `enum`, which
this skill's mirror documents in `docs/reference/language/syntax.md` and
`docs/guides/for-latex-users.md`.

## Local modifications

None. The directory is a verbatim copy of the upstream `typst-author` skill as
of the commit above, so it can be replaced wholesale on the next sync.

## Maintenance

To update, copy the upstream `typst-author` directory over this one and refresh
the commit hash above. Do not edit files in `docs/` — they are a mirror, and
local edits are lost on the next sync. Any LLM4AD-specific behavior belongs in
the proposal stage skills that reference this one, not here.
