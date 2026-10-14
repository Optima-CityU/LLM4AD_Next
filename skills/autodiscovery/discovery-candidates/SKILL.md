---
name: discovery-candidates
description: Compare source-supported algorithm directions and select an evolvable boundary with the user.
---

# AutoDiscovery · Candidate directions

Read the uploaded source and call `mcp__llm4ad_stage__read_predecessor_artifact`
for `discovery_baseline`. Compare genuinely distinct algorithm directions,
including their evolvable function boundary, supporting evidence, feasibility
and evaluator risks. Do not present a paper claim as a measured improvement.
Ask one focused question when the choice depends on the author. If there is no
defensible candidate, publish the missing inputs instead of inventing one.

Publish with `mcp__llm4ad_stage__publish_stage_result`:

```json
{
  "schema_version": "discovery-candidates-v1",
  "summary": "What was compared and why",
  "candidates": [{"id": "candidate-a", "title": "Direction", "evolvable_boundary": "Exact function or code region", "rationale": "Source-grounded reason", "feasibility": "Implementation and data feasibility", "evaluation_risks": ["Risk"], "source_refs": ["paper.md — Method section"]}],
  "selected_candidate_id": "candidate-a",
  "open_questions": []
}
```

Set `selected_candidate_id` to null when the author has not chosen. That
publishes a useful comparison but leaves the next stage unavailable. Never
call the task builder in this stage.
