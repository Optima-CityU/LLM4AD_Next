# Refine mode

Improve existing generated blocks incrementally while preserving user edits.

1. Read the host-provided refinement request, current manifest, and every current block referenced by that manifest.
2. Treat current blocks, including user edits, as the baseline. Prefer local edits over regenerating everything.
3. Add, delete, merge, split, or reorder blocks only when the request requires it or the semantic boundary is demonstrably wrong.
4. Unless explicitly asked to remove or compress material, preserve every existing fact, formula, parameter, code sample, example, counterexample, constraint, prerequisite, conflict, source boundary, and causal chain.
5. Reread only the relevant original sources when a fact is uncertain after context compaction; do not restart analysis of unrelated sources.
6. Keep the organized document contract from `contracts.md`. Do not emit a new planning proposal instead of editing the files.
