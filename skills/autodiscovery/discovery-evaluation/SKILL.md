---
name: discovery-evaluation
description: Design an executable evaluation contract for a selected AutoDiscovery candidate.
---

# AutoDiscovery · Evaluation design

Read `/workspace/source` and the `discovery_baseline` and
`discovery_candidates` artifacts through
`mcp__llm4ad_stage__read_predecessor_artifact`. Use the exact selected
candidate ID. Define the metric and minimize/maximize direction, a baseline
comparison, data split or instance protocol, invalid-result handling,
reproducibility and evaluator checks. Explain any missing data. A technical
smoke test is not scientific validation; never invent observed results.

Publish with `mcp__llm4ad_stage__publish_stage_result`:

```json
{
  "schema_version": "discovery-evaluation-v1",
  "summary": "Evaluation design and remaining uncertainty",
  "candidate_id": "candidate-a",
  "metrics": ["Primary objective"],
  "optimization_direction": "minimize",
  "baseline": "Unmodified original algorithm using the same evaluator",
  "data_protocol": "Instances, split, seeds and repetitions",
  "validity_checks": ["Reject malformed or constraint-violating output"],
  "reproducibility": "Fixed seeds and recorded versions",
  "source_refs": ["paper.md — Experiments"],
  "unknowns": [],
  "ready_for_build": true
}
```

If a material evaluator decision is missing, use `ready_for_build: false`
and state what the user must provide. Do not call the task builder here.
