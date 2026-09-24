---
name: research-stage-publication
description: Publish the current revision of an LLM4AD research workflow stage, including after a user edits and replaces an earlier conversation turn.
---

# Research Stage Publication

Use the current visible conversation and current workspace files as the complete
source of truth for this revision. Work that disappeared because the user edited
an earlier message does not count as the current stage result.

Complete the enabled domain Skill, then call `publish_stage_result` once for the
current revision even if this stage may have published an earlier revision. Do
not skip publication based on an earlier result that is absent from the current
conversation. Reusing the domain Skill's stable idempotency key is valid: exact
retries are deduplicated, while a changed artifact replaces the stage result.

If validation fails, correct the current artifact and retry as directed by the
domain Skill. A successful tool response is the only publication-completion
signal.
