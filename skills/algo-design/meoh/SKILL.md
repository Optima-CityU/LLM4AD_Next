---
name: meoh
description: "Multi-objective Evolution of Heuristics (MEoH) method skill. USE WHEN the user explicitly requests MEoH / Multi-objective EoH, or wants Pareto-based population evolution with archive for multi-objective problems."
triggers:
  - meoh
  - multi-objective eoh
  - multi-objective evolution
---

# Multi-objective Evolution of Heuristics (MEoH) Skill

> **Paper**: Yao et al., "Multi-objective evolution of heuristic using large language model", AAAI 2025.

## 1. Method Essence

MEoH extends EOH's evolutionary operators (E1/E2/M1/M2) to **multi-objective** scenarios: individuals are evaluated with multiple objective vectors, Pareto non-dominated sorting determines selection pressure, and a **non-dominated archive** maintains the historical best front. The LLM sees multiple representative individuals from the front during generation, accommodating different objective preferences.

Key differences from single-objective EOH:
- Selection is based on **domination + crowding**, not a single score
- **Front diversity** must be maintained: both ends and the middle of the front must have representatives
- Archive individuals can be "resurrected" for crossover (even if not in the current population)

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Note**: The number of objectives is determined by the length of `objective_metrics`, not a separate `num_objs` parameter.

### What Happens During Evolution

1. Population initialized; evaluate all individuals
2. Each generation:
   - Identify non-dominated front (Pareto front)
   - Archive front members
   - Generate offspring via LLM operators, using front members as parents
   - Evaluate offspring
   - Merge offspring into population
   - Non-dominated sorting + crowding distance truncation
3. Archive grows as better front members are found
4. Final archive contains the best Pareto front discovered

### Common Pitfalls

- Front not diverse → increase `population_size` or force exploration at front ends
- Archive too large → increase crowding pressure; archive pruning is automatic
- Convergence slow → check if objectives are truly conflicting; some problems may be easier with single-objective
- Front biased toward one objective → manually set extreme weight vectors as seeds

## 4. Acceptance Criteria

- Non-dominated front identified and archived
- Front covers both objective extremes and balanced trade-offs
- Archive members used as parents for crossover (not just current population)
- Crowding distance prevents front collapse
- Final archive represents the full Pareto front
