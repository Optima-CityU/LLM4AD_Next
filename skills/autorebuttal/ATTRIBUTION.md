# AutoRebuttal adaptation attribution

The skills in this directory are an architectural adaptation of
[YoujunZhao/AutoRebuttal](https://github.com/YoujunZhao/AutoRebuttal), pinned
for this adaptation at commit `39a62b82e73eec79050b00133b33acefd4fcd749`.

Upstream provides one end-to-end Markdown skill and deterministic helper
scripts. LLM4AD preserves its reviewer-model, evidence-first, response
strategy, budget, drafting, and compliance ideas. Five internal responsibilities
are orchestrated behind two author-facing stages: baseline confirmation and
automatic rebuttal generation. Shared rules live once under `shared/`; stage
skills contain only orchestration and publication contracts.

The LLM4AD adaptation intentionally differs in these ways:

- The paper source is a read-only Markdown or LaTeX project supplied by the author.
- Reviewer reports are stored separately and exposed as read-only runtime context.
- Stage artifacts are validated and persisted by the backend instead of helper shell scripts.
- The upstream experiment ledger and experiment runner are not enabled in the
  read-only rebuttal runtime. Missing measurements remain explicit author
  placeholders governed by the evidence-first policy.
- The final product is a structured list of rebuttal entries, not a modified paper or PDF.

The upstream MIT license is preserved in `LICENSE.AutoRebuttal`.
