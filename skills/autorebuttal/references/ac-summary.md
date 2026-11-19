<!-- LLM4AD extension of the YoujunZhao/AutoRebuttal adaptation; see ../ATTRIBUTION.md. -->

# Author-to-AC/chair message

Use fresh workflow and region context to identify the current author-approved
response and open author actions. Current regional edits take precedence over
older saved artifacts or file mirrors. Consult original reviews and paper
passages only as needed to check themes and factual wording.

Write from the author to the AC/chair, not in the chair's voice. Synthesize
cross-reviewer concerns, concrete answers already supplied and material
limitations. Do not copy the whole reviewer-by-reviewer response or invent
experiments, evidence, score predictions, manuscript changes or commitments.
Unresolved placeholders remain unresolved and must not become completed claims.

Follow an actual author-supplied submission language or venue format. Do not
require missing venue metadata merely to write a useful message. Conversation
presentation preferences are separate from the requested submission language.

The optional typed `ac_summary` output is an independent deliverable under the
exact schema from workflow context. Save it through `publish_research_revision`
with the captured revision and a fresh logical-revision key. An earlier baseline
or rebuttal publication is not a gate; the evidence available for what was
actually answered still bounds the message. Do not edit the paper or silently
replace existing reviewer responses.
