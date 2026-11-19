# Versioned research output contract

Read fresh `get_workflow_context` for `revision` and `publication_schemas`
before preparing an output. The artifact discriminator is `type`; its exact
allowed fields, defaults and nested types are exposed in those schemas. Publish through
`publish_research_revision(summary, idempotency_key, base_revision, artifact=...)`.
Keep the captured revision unchanged as the base; use a fresh key for changed
content, and reuse a key only for an exact retry. The platform detects file
changes. Do not guess discriminator names, field types or required metadata.

The publication envelope carries `summary`, `idempotency_key`, `base_revision`
and optional `artifact`. Put the author-facing summary in that envelope, not
inside a strict artifact variant.

## Independent optional outputs

Choose only the output the author's request needs:

- `type="baseline"`: a useful saved analysis of the supplied paper/reviews, such as
  paper understanding, stable reviewer identities, concerns and reviewer-level
  reasoning. Use the current schema's supported fields; a baseline is not a
  required prerequisite for another output.
- `type="rebuttal"`: the current response and the few structured fields needed for its
  display and author actions, using the flat contract described below.
- `type="ac_summary"`: an author-to-chair message grounded in what was actually answered,
  using its required `message` and other fields permitted by its exposed schema. It is
  independent of a fixed publication order.

An artifact is optional when saving canonical files alone. Method reasoning is
not a requirement to publish separate intermediate analysis objects.

## Flat rebuttal output

Use `type="rebuttal"` with the required agent-produced `rendered_text`. Its
flat optional response fields include `entries`, `global_response`,
`findings`, `open_placeholders` and the schema's `output_format`. The response
summary remains the publication call's `summary` argument. Include fields according to that schema and the actual
UI deliverable, without manufacturing unused metadata or nested method phases.
Entry/global-response subfields, evidence references, allowed enum values and
required fields come from the exact schema, not from a historical example.

The agent produces the canonical `rendered_text` itself. It must faithfully
represent the final reviewer entries and optional global response, including
intentional wording, language and unresolved placeholders. The backend persists
this response; it does not generate or paraphrase the submission.

Use platform-returned reviewer UUIDs, stable concern IDs and original evidence
links. Before a baseline uses an unindexed chat-pasted report, record its full
original text with the main-agent `record_reviewer_feedback` tool; never invent
a review/reviewer UUID inside an artifact. Refresh context and capture the
revision after input changes. Already indexed reports need no additional gate. Do not
turn a current regional source reference into a different artifact type without
checking the exposed schema. A supplied exact quotation can support verification;
a missing quote or legacy association must not become fabricated support.

## Author guidance and semantic review

`open_placeholders` identify specific author facts, measurements or decisions
still needed. Each should explain what is missing, why it matters and a practical
next step. `findings` contain useful advice, not internal schema/UUID validation
logs. Never present unresolved measurements or planned experiments as complete.

For a complete response, check concern coverage without duplicate answers,
reviewer identity, shared-response consistency and evidence integrity. For a
scoped edit, review only its requested scope while preserving unrelated author
text. These are Skill-level research checks, not backend scientific gates.

Inspect publication results: a candidate or `requires_author_approval` awaits
author acceptance and is not an applied canonical revision. On source conflict,
compare base, proposed response and current author edits before preparing a new
revision. Preserve prior artifacts, raw review originals and output history.
Do not add approval to every automatic draft. Publisher author protection
targets actual manual changes since the last agent publication; the returned
state determines whether a candidate or a saved revision exists.
Scoped author requests keep their own `read_region_request` /
`propose_region_change` approval flow rather than whole-output publication.
