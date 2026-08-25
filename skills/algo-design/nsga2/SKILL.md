---
name: nsga2
description: "NSGA-II multi-objective evolutionary method skill. USE WHEN the user explicitly requests NSGA-II / Non-dominated Sorting Genetic Algorithm, or wants multi-objective optimization with non-dominated sorting and crowding distance selection."
triggers:
  - nsga2
  - nsga-ii
  - non-dominated sorting
  - pareto front optimization
---

# NSGA-II Skill

> **Paper**: Deb et al., "A fast and elitist multiobjective genetic algorithm: NSGA-II", IEEE TEC 2002.

## 1. Method Essence

NSGA-II is a classic multi-objective GA with a two-step selection mechanism:

1. **Non-dominated sorting**: Divide the population into layers — Pareto front layer (not dominated by anyone), second layer (dominated only by front layer), etc.; earlier layers have higher priority for survival
2. **Crowding distance**: Within the same layer, sort by "neighbor sparsity" in objective space; sparse individuals are preserved first (maintains uniform front coverage)

Overall cycle: Generate offspring → merge parent-offspring → non-dominated sorting → truncate to pop_size by layer + crowding → next generation. LLM version uses LLM operators (E1/E2/M1/M2) instead of traditional genetic operators for offspring generation.

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Note**: The number of objectives is determined by the length of `objective_metrics`.

### What Happens During Evolution

1. Population initialized; evaluate all individuals
2. Each generation:
   - Generate offspring via LLM operators
   - Merge parent + offspring populations
   - Non-dominated sorting: classify into layers
   - Crowding distance: within each layer, rank by sparsity
   - Truncate to `population_size` by layer priority + crowding
3. Pareto front gradually expands and becomes more uniform
4. Final front represents optimal trade-offs between objectives

### Common Pitfalls

- Front not diverse → increase `population_size` or enable more operators
- Convergence too slow → check if objectives are conflicting; reduce `num_objs` if possible
- Single-objective dominance → verify `num_objs` matches your problem; check evaluator metrics

## 4. Acceptance Criteria

- Non-dominated front visible in evolution log (Pareto front members identified)
- Front covers multiple trade-off points (not clustered in one region)
- Crowding distance prevents front collapse (uniform spread)
- Final best individuals span the front from extreme to balanced solutions
