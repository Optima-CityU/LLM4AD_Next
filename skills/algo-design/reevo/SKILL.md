---
name: reevo
description: "Reflective Evolution (ReEvo) method skill. USE WHEN the user explicitly requests ReEvo / Reflective Evolution, or wants evolution with reflection mechanisms that summarize failure lessons and inject them into the next generation."
triggers:
  - reevo
  - reflective evolution
  - reflection-based evolution
---

# Reflective Evolution (ReEvo) Skill

> **Paper**: Ye et al., "ReEvo: Large Language Models as Hyper-Heuristics with Reflective Evolution", NeurIPS 2024.

## 1. Method Essence

ReEvo adds a **reflection layer** to the evolution loop (sample → evaluate → select): before each generation, it summarizes success/failure patterns from historical samples and injects the reflection into the prompt, so the next mutation "stands on experience" rather than trying blindly. Reflection has two levels:

- **Long-term reflection**: Overall summary of all historical individuals — "what structures work/fail, what patterns repeatedly fail"
- **Short-term reflection**: Local review of the most recent N individuals (N=5) — "what changed last round, how did it perform"

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Parameter Guidance**:
- `population_size`: 8-12 gives more material for reflection; start with 8.
- `mutation_rate`: 0.5 is balanced; increase for more diversity, decrease for stability.
- `max_sample_nums`: ReEvo is more sample-efficient than EoH; 100 is often sufficient.

### What Happens During Evolution

1. Population initialized; reflection buffer empty
2. Each generation:
   - Compute short-term reflection (last 5 individuals) and long-term reflection (all history)
   - Generate offspring via LLM with reflection-augmented prompts
   - Evaluate, select, update population
3. Reflection evolves: as more individuals are evaluated, reflections become more insightful
4. Common pattern: early generations explore broadly; later generations refine based on accumulated wisdom

### Common Pitfalls

- Reflection becomes too vague → reduce window size or add more specific prompts
- No improvement after reflection → check if the problem is too constrained for the operator
- Overfitting to reflection → occasionally force exploration with fresh random mutations

## 4. Acceptance Criteria

- Reflection prompts appear in the evolution log before each generation
- Population shows improvement correlated with reflection insights
- Short-term and long-term reflections are distinct (not redundant)
- Best individual incorporates ideas from reflection (traceable in code changes)
