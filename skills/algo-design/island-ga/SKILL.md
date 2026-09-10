---
name: island-ga
description: "Island Genetic Algorithm (IslandGA) method skill. USE WHEN the user explicitly requests IslandGA / island genetic algorithm, or wants multiple sub-populations evolving in parallel with periodic inter-island migration to preserve diversity and enable parallel search."
triggers:
  - island ga
  - island-ga
  - island genetic algorithm
  - distributed evolutionary algorithm
---

# Island Genetic Algorithm (IslandGA) Skill

> **Reference**: Classic distributed evolutionary computation model, widely used for parallel evolutionary search. Independent sub-populations (islands) evolve in parallel with periodic migration.

## 1. Method Essence

IslandGA runs a **distributed evolutionary algorithm** in which the population is divided into several independent **islands** (sub-populations). Each island evolves on its own — with independent selection, crossover, and mutation — and only exchanges individuals through **periodic migration events**. This decoupled structure preserves genetic diversity (each island drifts toward different optima) and enables **parallel execution** of evolution workloads.

Core mechanisms:

| Concept | Role |
|---|---|
| **Island** | An independent sub-population evolving on its own GA loop |
| **Migration** | Periodic exchange of individuals between islands to spread good solutions |
| **Migration interval** | Number of generations between inter-island migration events |
| **Migration rate** | Fraction of individuals migrated out of the source island each event |
| **Migration strategy** | Which individuals migrate (best / random / elite / worst) |
| **Migration topology** | How islands are connected (ring / fully-connected / hierarchical / mesh) |

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Total population = `num_islands` × `island_population_size`.**

### What Happens During Evolution

1. Split the population into `num_islands` islands, each initialized independently
2. Each island runs its own GA loop (selection → crossover → mutation → evaluation)
3. Every `migration_interval` generations, a migration event occurs:
   - Select emigrants per the `migration_strategy` and `migration_rate`
   - Send them along the `migration_topology` to neighboring islands
   - Incoming migrants are inserted into the target island population
4. Islands evolve in parallel (`parallel_islands`) or sequentially
5. Final best individual across all islands is the solution

### Common Pitfalls

- Too few islands or uniform islands → no diversity benefit; add more islands
- Migration too frequent or too high rate → premature homogenization/collapse
- Migration too rare → islands cannot share good solutions; slow convergence
- No topology diversity → ring is a safe default; fully-connected spreads fastest
- Failure to enable `parallel_islands` on large runs → unnecessarily slow

## 4. Acceptance Criteria

- Multiple islands evolve independently with observable divergence
- Periodic migration events occur at the configured interval
- Migrants cross islands and improve the receiving population
- Diversity is maintained across islands (not a single converged population)
- Final best solution improves over a single-population baseline