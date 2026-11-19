# AutoRebuttal adaptation attribution

The methods in this directory are an architectural adaptation of
[YoujunZhao/AutoRebuttal](https://github.com/YoujunZhao/AutoRebuttal), pinned
for this adaptation at commit `39a62b82e73eec79050b00133b33acefd4fcd749`.

Upstream provides one end-to-end Markdown skill and deterministic helper
scripts. LLM4AD preserves its reviewer-model, evidence-first, response
strategy, drafting, and compliance ideas. The unified `autorebuttal-workflow` Skill selects relevant internal methods in
one coordinator conversation. Original methods remain under `references/` and
shared guidance remains under `shared/`. Optional read-only workers return text
diffs and evidence, without scratch files, Write/Edit or publication permissions.
LLM4AD adds an optional AC/Chair message artifact that synthesizes the
current author response; this independent output is a local extension, not an
upstream feature.
Historical public adapter keys remain compatible with persisted work; they do
not define normal publication instructions, fixed phases or stage locks. The
pinned revision records provenance, not verification of the latest upstream or
current venue requirements.

The LLM4AD adaptation intentionally differs in these ways:

- The paper source is a read-only Markdown or LaTeX project supplied by the author.
- Reviewer reports saved through the panel are stored separately and exposed as
  read-only runtime context; review text pasted into the chat is also accepted
  for baseline analysis.
- The manuscript main agent records unindexed pasted reports through
  `record_reviewer_feedback`, preserving full clickable originals and the public
  review service's history hooks. Identical content-hash/label retries reuse
  platform UUIDs; the model does not invent reviewer identities. Input changes
  require refreshed workflow/region context before output preparation.
- The OpenReview browser importer was removed. The author supplies review text
  directly rather than asking the agent to retrieve or verify a forum page.
- The main agent publishes versioned revisions through the exposed current
  schemas. Optional independent output variants are `baseline`, `rebuttal` and
  `ac_summary`; a flat rebuttal replaces a mandatory three-phase output bundle.
- Backend checks cover wire contracts, ownership and source/version integrity,
  not scientific adequacy or completion of a prescribed research method.
- The coordinator reports started logical research work, goals, approaches and
  outcomes; child-tool activity is not a separate right-panel step.
- The upstream experiment ledger and experiment runner are not enabled in the
  read-only rebuttal runtime. Missing measurements remain explicit author
  placeholders governed by the evidence-first policy.
- The final product is structured rebuttal entries plus one agent-produced
  canonical Markdown or text response that the platform persists unchanged,
  not a modified paper or PDF.
- Numeric response-budget allocation and enforcement are intentionally omitted
  from this integration.
- Revision of the response is supported while the submitted paper remains
  read-only; the upstream paper-rewrite mode is not part of this adaptation.
- Reviewer feedback accepts manually entered Markdown or plain text, not the
  upstream PDF/OCR review-ingestion path.
- The adaptation does not emit upstream's dual Markdown and LaTeX response
  outputs; it emits one canonical Markdown or plain-text submission artifact.

Current author text and scoped request approvals are protected by captured
revision checks; a candidate is not described as applied. Original reviews stay
inspectable with stable identities and versioned evidence. Earlier response
artifacts, source revisions and output history remain accessible.
Publisher protection is based on actual manual author edits since the last
agent publication, rather than approval of every automatic draft. Explicit
scoped response requests keep their independent author-acceptance flow.

The upstream MIT license is preserved unchanged in `LICENSE.AutoRebuttal`.
