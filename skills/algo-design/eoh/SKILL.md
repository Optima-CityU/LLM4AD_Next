---
name: eoh
description: "Evolution of Heuristics (EOH) method skill. USE WHEN the user explicitly requests EoH / Evolution of Heuristics, or wants population-level heuristic evolution with explore-exploit-merge-modify operators."
triggers:
  - eoh
  - evolution of heuristics
  - EoH
---

# Evolution of Heuristics (EOH) Skill

> **Paper**: Liu et al., "Evolution of Heuristics: Towards Efficient Automatic Algorithm Design Using Large Language Model", ICML 2024.

## 1. Method Essence

EOH treats the LLM as an **evolutionary operator** rather than a black-box sampler: it maintains a heuristic population and generates new individuals each generation using one of four LLM operators, evaluates them, and applies survival-of-the-fittest selection. The core is **operator division of labor**:

| Operator | Type | Role |
|---|---|---|
| E1 (explore) | Mutation | Generate a variant from 1 selected individual (structural/parametric changes) |
| E2 (exploit) | Crossover | Combine 2 selected individuals into a new one |
| M1 (merge) | Merge | Fuse strengths of multiple individuals into one implementation |
| M2 (modify) | Refinement | Targeted fine-tuning of the current best |

## 2. Recommended Parameters

The following are **starting-point recommendations** from the paper and practical experience. Adjust based on your problem complexity and computational budget.

```yaml
evolution:
  type: "eoh"
  max_generations: 50          # increase for harder problems
  population_size: 5           # top-k individuals kept after truncation
  selection_num: 2             # parents selected per E1/E2 crossover
  max_sample_nums: 100         # LLM call budget cap for the run
  num_samplers: 1              # parallel candidates per operator per gen
  use_e2_operator: true        # enable E2 (backbone-motivated crossover)
  use_m1_operator: true        # enable M1 (structural mutation)
  use_m2_operator: true        # enable M2 (parameter mutation)
  seed_path: null              # optional path to a seed heuristic file
```

**Parameter Guidance**:
- `population_size`: Start with 5; increase for harder problems or decrease for faster iteration.
- `selection_num`: 2 works well for most cases; increase for more diverse crossover.
- `max_sample_nums`: Increase for harder problems (up to 200+); decrease for quick experiments.
- `Operator toggles`: Enable/disable based on your search strategy.

### What Happens During Evolution

1. Population is initialized from seed or random variants
2. Each generation: select operators (E1/E2/M1/M2), generate offspring via LLM, evaluate, truncate to top-k
3. Operators rotate: E1 explores, E2 exploits, M1 merges, M2 refines
4. Early generations favor E1 (exploration); later generations favor M2 (exploitation)

### Common Pitfalls

- Too small population → premature convergence; increase `population_size`
- Too few samples → algorithm hasn't converged; increase `max_sample_nums`
- Disabling all operators except E1 → no exploitation; keep at least M2 enabled

## 4. Acceptance Criteria

- Population diversity maintained across generations
- Operator rotation observed (not just E1 repeated)
- Best individual shows clear improvement over initial population
- Convergence pattern: rapid early improvement, gradual later refinement
