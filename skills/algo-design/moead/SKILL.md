---
name: moead
description: "MOEA/D (Multi-objective Evolutionary Algorithm based on Decomposition) method skill. USE WHEN the user explicitly requests MOEA/D / Decomposition-based multi-objective evolution, or wants weight-vector decomposition with neighborhood collaboration."
triggers:
  - moead
  - moea/d
  - decomposition-based
  - weight vector decomposition
---

# MOEA/D Skill

> **Paper**: Zhang & Li, "MOEA/D: A Multiobjective Evolutionary Algorithm Based on Decomposition", IEEE TEC 2007.

## 1. Method Essence

MOEA/D **decomposes** a multi-objective problem into N single-objective sub-problems: each sub-problem is defined by a weight vector + aggregation function (weighted sum / Tchebycheff), and the entire population = a set of uniformly distributed weight vectors. Core mechanisms:

- **Sub-problem division**: Each weight vector corresponds to a direction on the front; the population collectively covers the entire front
- **Neighborhood collaboration**: Each sub-problem exchanges information only with its T nearest weight vectors (neighbors) — crossover/mutation occurs mainly between neighboring sub-problems, replacing global pairing
- **Aggregation function**: Tchebycheff `max_i w_i |f_i - z_i|` (z is reference point) is effective for non-convex fronts and is commonly used
- **Update rule**: If a new individual improves the aggregation value for its sub-problem, it replaces that sub-problem and its neighbors' solutions

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Note**: The number of objectives is determined by the length of `objective_metrics`. Weight vectors are generated in this dimensional space.

### What Happens During Evolution

1. Generate uniform weight vectors in objective space
2. Initialize population (one solution per weight vector)
3. Each generation:
   - For each sub-problem (weight vector):
     - Select parents from neighborhood
     - Generate offspring via LLM operators
     - Evaluate offspring
     - Update sub-problem and neighbors if offspring improves aggregation
4. Weight vectors define search directions; population covers the front uniformly
5. Final front is the set of best solutions for each weight vector

### Common Pitfalls

- Front has gaps → increase `population_size` (more weight vectors)
- Convergence uneven → check weight vector distribution; some directions may be harder
- All solutions clustered → reduce neighborhood size T for more local search
- Non-convex front → Tchebycheff aggregation works better than weighted sum

## 4. Acceptance Criteria

- Weight vectors uniformly distributed in objective space
- Each sub-problem has a corresponding solution on the front
- Front covers all weight vector directions (no missing regions)
- Neighborhood collaboration visible (solutions from nearby weights share features)
- Final front represents the full trade-off surface
