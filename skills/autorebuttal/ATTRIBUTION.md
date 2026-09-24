# AutoRebuttal adaptation attribution

The skills in this directory are an architectural adaptation of
[YoujunZhao/AutoRebuttal](https://github.com/YoujunZhao/AutoRebuttal), pinned
for this adaptation at commit `39a62b82e73eec79050b00133b33acefd4fcd749`.

Upstream provides one end-to-end Markdown skill and deterministic helper
scripts. LLM4AD preserves its reviewer-model, evidence-first, response
strategy, drafting, and compliance ideas. Five internal responsibilities
are orchestrated behind baseline confirmation and automatic rebuttal generation.
LLM4AD adds a third, author-facing AC/Chair message stage that synthesizes the
published rebuttal; this stage is a local extension, not an upstream feature.
Shared rules live once under `shared/`; stage skills contain only orchestration
and publication contracts.

The LLM4AD adaptation intentionally differs in these ways:

- The paper source is a read-only Markdown or LaTeX project supplied by the author.
- Reviewer reports saved through the panel are stored separately and exposed as
  read-only runtime context; review text pasted into the chat is also accepted
  for baseline analysis.
- The OpenReview browser importer was removed. The author supplies review text
  directly rather than asking the agent to retrieve or verify a forum page.
- Stage artifacts are validated and persisted by the backend instead of helper shell scripts.
- The upstream experiment ledger and experiment runner are not enabled in the
  read-only rebuttal runtime. Missing measurements remain explicit author
  placeholders governed by the evidence-first policy.
- The final product is structured rebuttal entries plus one agent-produced
  canonical Markdown or text response that the platform persists unchanged,
  not a modified paper or PDF.
- Numeric response-budget allocation and enforcement are intentionally omitted
  from this integration.
- Upstream revise mode is not supported; the paper source remains read-only.
- Reviewer feedback accepts manually entered Markdown or plain text, not the
  upstream PDF/OCR review-ingestion path.
- The adaptation does not emit upstream's dual Markdown and LaTeX response
  outputs; it emits one canonical Markdown or plain-text submission artifact.

The upstream MIT license is preserved in `LICENSE.AutoRebuttal`.
