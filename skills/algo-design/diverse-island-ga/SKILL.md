---
name: diverse-island-ga
description: "Diverse Island Genetic Algorithm skill. USE WHEN the user explicitly requests Diverse Island GA / diversity-oriented island GA, or wants specialized island roles with adaptive migration and novelty preservation instead of a uniform island GA."
triggers:
  - diverse island ga
  - diverse-island-ga
  - diversity island genetic algorithm
  - adaptive migration island ga
---

# Diverse Island GA Skill

> **Reference**: LLM4AD_Next specialized island model. Extends IslandGA with **specialized island roles** (a continuous exploitation↔exploration spectrum), **adaptive stagnation-aware migration**, and **novelty preservation**.

## 1. Method Essence

Diverse IslandGA builds on the classic island model but removes the assumption that all islands are identical. Instead, it assigns each island a **continuous strategy position** along the exploration↔exploitation spectrum, and coordinates them through **adaptive migration** that fires only when an island stagnates. This keeps the population diverse and avoids the premature spread of a local optimum.

Core mechanisms:

| Concept | Role |
|---|---|
| **Island strategy spectrum** | `island_strategy_strength` controls how much islands differ (experience-reuse → novelty exploration) |
| **Adaptive migration** | `adaptive_migration` receives migrants only after an island stagnates |
| **Stagnation threshold** | `migration_stagnation_threshold` consecutive non-improving generations before migration is allowed |
| **Novelty survivor** | `novelty_survivor_ratio` reserves non-elite survivor slots for code-dissimilar individuals |
| **Exploration restart** | `exploration_restart_ratio` share of candidates built from scratch on the most exploratory island |
| **Short-run guard** | `short_task_max_migrations` caps migration events on short runs |

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Note**: This variant ships with a smaller out-of-box workload (`max_generations`=10, `island_population_size`=5) than the plain IslandGA, so it is safe to run without heavy tuning.

### What Happens During Evolution

1. Initialize `num_islands` islands, each at a distinct point on the strategy spectrum
2. Each island evolves with its own exploration/exploitation bias and memory policy
3. Every generation, elites are reevaluated at higher fidelity (`elite_reevaluation_count`)
4. Migration is **adaptive**: an island may receive migrants only after `migration_stagnation_threshold` non-improving generations
5. Novel survivors (low code similarity) are retained to preserve diversity
6. On short runs, migration is capped by `short_task_max_migrations`

### Common Pitfalls

- Uniform islands (strength too low) → little diversity benefit; raise `island_strategy_strength`
- Migration too aggressive → premature convergence; rely on adaptive stagnation gating
- Exploration island flooding → tune `exploration_restart_ratio` and novelty ratio
- Short runs over-migrating → keep `short_task_max_migrations` low (default 1)

## 4. Acceptance Criteria

- Islands occupy distinct points on the exploration↔exploitation spectrum
- Migration triggers only after stagnation (adaptive, not periodic-only)
- Novelty survivors maintain inter-island diversity
- Short runs respect the migration cap
- Final best beats a uniform IslandGA baseline on the same problem