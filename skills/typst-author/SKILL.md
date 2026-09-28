---
name: typst-author
description: Generate idiomatic Typst (.typ) code, edit and troubleshoot Typst documents and projects, and answer Typst syntax/reference questions. Use when working with .typ files or when the user explicitly asks for Typst document creation, editing, debugging, compilation, formatting, template work, or package usage.
---

# typst-author skill

## Overview

This skill helps agents generate, edit, and reason about Typst documents. It provides quick‑start examples, detailed workflows, and links to the Typst documentation (overview, guides, tutorials, reference).

## Minimal document example

```typst
#set document(title: "My Document", author: "Author Name")
#set page(numbering: "1")
#set text(lang: "en")

// Enable paragraph justification and character-level justification
#set par(
  justify: true,
  justification-limits: (
    tracking: (min: -0.012em, max: 0.012em),
    spacing: (min: 75%, max: 120%),
  )
)

#title[My Document]

= Heading 1

This is a paragraph in Typst.

== Heading 2

#lorem(50)
```

## Workflows

- **Creating a new Typst project**:
  - Use the "Minimal document example" above as a starting point.
  - Skim the tutorial for the basics ([docs/tutorial/1-writing.md](docs/tutorial/1-writing.md)), then create the `.typ` file(s).
  - After each `.typ` edit, follow the post-edit formatting checks below when `typstyle` is available.
- **Editing existing content**:
  - Locate the target text and apply changes; confirm syntax against the reference when needed ([docs/reference/index.md](docs/reference/index.md)).
  - After each modified `.typ` file, follow the post-edit formatting checks below.
- **Formatting & styling**:
  - Consult the styling guide ([docs/reference/language/styling.md](docs/reference/language/styling.md)) for `set rule`, `show rule`, selectors, and transformations.
  - Preserve visual consistency: new layouts, figures, tables, and drawings must reuse the document's existing fonts, colors, and typographic hierarchy (unless instructed otherwise). Internal consistency should be maintained (same function → same size and color).

## Documentation

- **Orientation**: `docs/overview.md` for a high-level introduction, `docs/reference/index.md` for the reference structure, and `docs/tutorial/index.md` for the tutorial sequence.
- **Tutorial**: `docs/tutorial/1-writing.md` for basic markup, functions, labels, citations, and math; `docs/tutorial/2-formatting.md` for set rules, page setup, headings, and text formatting; `docs/tutorial/3-advanced.md` for templates, show rules, and more complex styling; `docs/tutorial/4-template.md` for reusable templates and packages.
- **Language syntax**: `docs/reference/language/syntax.md` for markup/math/code modes, comments, escapes, and identifiers.
- **Language scripting**: `docs/reference/language/scripting.md` for expressions, code/content blocks, bindings, conditionals, loops, fields, methods, modules, packages, and operators.
- **Language styling**: `docs/reference/language/styling.md` for set rules, show rules, selectors, and transformations.
- **Language context**: `docs/reference/language/context.md` for style context, location context, nested contexts, and compiler iterations.
- **Foundations**: `docs/reference/library/foundations.md` for basic types/functions, `std`, `sys.version`, and `sys.inputs`.
- **Document model**: `docs/reference/library/model.md` for headings, figures, bibliography, citations, references, numbering, outlines, lists, tables, and document structure.
- **Layout**: `docs/reference/library/layout.md` for arranging elements on the page: page, block, box, grid, columns, alignment, spacing, transforms, and measurement.
- **Text**: `docs/reference/library/text.md` for text styling and the `text` function.
- **Math**: `docs/reference/library/math.md` for math syntax, symbols, equations, alignment, fonts, accessibility, and math functions.
- **Drawing and media**: `docs/reference/library/visualize.md` for shapes, image/graphics primitives, color, gradients, strokes, and visual accessibility.
- **Introspection**: `docs/reference/library/introspection.md` for counters, state, locations, `query`, and document-aware generated content.
- **Data loading**: `docs/reference/library/data-loading.md` for loading and encoding external data such as CSV, JSON, TOML, YAML, XML, CBOR, and raw files.
- **Symbols and emoji**: `docs/reference/library/sym.md` for named symbols and shorthands; `docs/reference/library/emoji.md` for emoji.
- **Export formats**: `docs/reference/export/pdf.md` for PDF standards and PDF-specific features; `docs/reference/export/html.md` for HTML export and typed HTML; `docs/reference/export/bundle.md` for multi-output bundles and assets; `docs/reference/export/svg.md` and `docs/reference/export/png.md` for image exports.
- **Guides**: `docs/guides/page-setup.md` for pages, margins, headers, footers, numbering, and columns; `docs/guides/tables.md` for semantic tables and table styling; `docs/guides/accessibility.md` for semantic structure, alt text, language, PDF accessibility, and export considerations; `docs/guides/for-latex-users.md` for LaTeX-to-Typst translation.

## Detailed instructions

1. **PRIORITY: Trust local documentation**. Your internal training data regarding Typst may be outdated or hallucinated. Always verify function names, parameters, and syntax against the local Typst 0.15.0 `docs/` folder before generating code.
2. **Read the relevant documentation** using local file search and open tools on the paths above.
3. **Use local docs for syntax and reference questions**. Verify syntax, function names, parameters, and reference behavior from the bundled docs. Run a minimal Typst probe only when runtime or evaluation behavior remains unclear after checking the docs.
4. **Generate or modify the `.typ` source** according to the user's request.
5. **Run the post-edit formatting checks below** for every `.typ` file you created or edited in that pass.
6. **Validate** with `typst compile` after the formatting decision is complete when you created or edited `.typ` files, or when the user explicitly asks for verification (if tool access is allowed).
7. **Summarize touched files and outcomes**. Provide full `.typ` content only when the user requests it or when direct editing is not possible, and optionally include a rendered preview (PDF/HTML).

### Probing uncertain behavior

- Use a probe when the bundled docs do not settle runtime or evaluation behavior.
- Model the case with Typst scripting as described in [docs/reference/language/scripting.md](docs/reference/language/scripting.md).
- When a probe is necessary, prefer a fileless expression probe with `typst eval` instead of creating scratch `.typ` files. See [docs/reference/library/foundations.md](docs/reference/library/foundations.md) for system values and [docs/reference/library/introspection.md](docs/reference/library/introspection.md) for contextual document queries.
- Example: `typst eval '1 + 2'`

### Post-edit formatting checks

1. **Check whether `typstyle` is available** with `command -v typstyle`. If it is unavailable, skip the remaining formatting checks.
2. **After each `.typ` file modification, run `typstyle --check <file>`** for the file you just created or edited.
3. **If `typstyle --check` fails, inspect the formatter changes with `typstyle --diff <file>`** before deciding what to do.
4. **Apply formatting with `typstyle -i <file>`** only when the formatter changes are limited to a newly created file or to code you created or edited in the current task.
5. **Stop and ask the user when formatting would change untouched pre-existing code**. If the diff reaches outside your own edits, or if you cannot confidently prove that every formatter change is limited to your edits, ask instead of formatting.

## Quick syntax reference

### Critical distinctions

- **Arrays**: `(item1, item2)` (parentheses). See [docs/reference/library/foundations.md](docs/reference/library/foundations.md).
- **Dictionaries**: `(key: value, key2: value2)` (parentheses with colons). See [docs/reference/library/foundations.md](docs/reference/library/foundations.md).
- **Content blocks**: `[markup content]` (square brackets). See [docs/reference/language/scripting.md](docs/reference/language/scripting.md).
- **NO tuples**: Typst only has arrays.

### Hash usage (markup vs code)

- Use `#` to start a code expression inside markup or content blocks; it disambiguates code from text. This is required for content-producing function calls and field access in markup: `#figure[...]`, `#image("file.png")`, `text(...)[#numbering(...)]`.
- Do not use `#` inside code contexts (argument lists, code blocks, show-rule bodies). Example: `#figure(image("file.png"))` (no `#` before `image`).
- Reference: [docs/reference/language/scripting.md](docs/reference/language/scripting.md), [docs/tutorial/1-writing.md](docs/tutorial/1-writing.md)

```typst
// Incorrect (missing # inside content block)
text(...)[(numbering(...))]

// Correct
text(...)[(#numbering(...))]
```

### Styling rules: set vs show

- `set`: Set rule to configure optional parameters on element functions (style defaults scoped to the current block or file).
- `show`: Show rule to target selected elements and apply a set rule or transform/replace the element output.
- Use `set` for common styling; use `show` for selective or structural changes (e.g., `heading.where(level: 1)`, labels, text, regex).

```typst
// Set rule: configure optional parameters for an element type
#set heading(numbering: "I.")
#set text(font: "New Computer Modern")

// Show-set rule: apply a set rule only to selected elements
#show heading: set text(navy)

// Show transform rule: replace/reshape element output
#show heading: it => block[#emph(it.body)]
```

## Common mistakes to avoid

- Calling things "tuples" (Typst only has arrays).
- Using `[]` for arrays (use `()` instead).
- Accessing array elements with `arr[0]` (use `arr.at(0)`).
- Omitting `#` in markup/content blocks (e.g., `text(...)[numbering(...)]` should be `text(...)[#numbering(...)]`).
- Using `#` inside code contexts (e.g., `figure(#image("x.png"))` in an argument list).
- Mixing up content blocks `[]` with code blocks `{}`.
- Forgetting to include the namespace when accessing imported variables/functions (e.g., use `color.hsl` instead of just `hsl`).
- Using LaTeX syntax (do **NOT** use `\begin{...}`, `\section`, or other LaTeX commands).
- Hallucinating environments (e.g., `tabular` does not exist; use `table`).

## Advanced features

- **Reusable styling**: See [docs/reference/language/styling.md](docs/reference/language/styling.md) for set rules, show rules, and styling scoped by code or content blocks.
- **Scripting**: Use Typst's scripting capabilities ([docs/reference/language/scripting.md](docs/reference/language/scripting.md)) for automatic generation.
- **Math and visualisation**: Reference [docs/reference/library/math.md](docs/reference/library/math.md) and [docs/reference/library/visualize.md](docs/reference/library/visualize.md) for formulas and diagrams.

### For large projects

When working on large projects, consider organizing the project across multiple files.

- Use `#include "file.typ"` to split into multiple files
- Relevant documentation: [docs/reference/language/scripting.md](docs/reference/language/scripting.md)

## Troubleshooting

### Missing font warnings

If you see "unknown font family" warnings, remove the font specification to use system defaults. Note: Font warnings don't prevent compilation; the document will use fallback fonts.

### Template/Package not found

If import fails with "package not found":

- Verify exact package name and version on Typst Universe.
- Check for typos in `@preview/package:version` syntax. See [docs/reference/language/scripting.md](docs/reference/language/scripting.md) for package import syntax and [docs/guides/for-latex-users.md](docs/guides/for-latex-users.md) for Typst Universe examples.

### Compilation errors

Common fixes:

- **"expected content, found ..."**: You're using code where markup is expected - wrap in `#{ }` or use proper syntax.
- **"expected expression, found ..."**: Missing `#` (or `#(...)`) in markup/content blocks.
- **"unknown variable"**: Check spelling, ensure imports are correct.
- **Array/dictionary errors**: Review syntax - use `()` for both, dictionaries need `key: value`, singleton arrays are `(elem,)`.
