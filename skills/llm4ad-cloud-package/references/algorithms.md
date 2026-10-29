# Supported cloud evolution methods

The current platform's `AppConfig.evolution` union and orchestrator registry both
contain these seven methods. This list describes server-supported methods, not
every algorithm available as a standalone methodology Skill.

| Display name | `evolution.type` | `planner.type` | Suitable use |
| --- | --- | --- | --- |
| Island GA | `island_ga` | `llm_evolution` | General search with parallel populations and periodic migration. |
| Diverse Island GA | `diverse_island_ga` | `llm_evolution` | Distinct success/correction/exploration roles, novelty retention and adaptive migration. |
| DyCA | `dyca` | `llm_evolution` | Different instance distributions benefiting from specialist algorithms. |
| MEoH | `meoh` | `meoh_evolution` | Multiple objective metrics and a Pareto archive. |
| EoH | `eoh` | `eoh_evolution` | Single-objective heuristic evolution with E1/E2/M1/M2 operators. |
| ReEvo | `reevo` | `reevo_evolution` | Genetic search guided by short/long-term reflection. |
| MCTS-AHD | `mcts_ahd` | `mcts_ahd_evolution` | Tree search over algorithm candidates. |

FunSearch, NSGA-II and MOEA/D have methodology Skills in this repository but are
not registered cloud orchestrators. Do not write those values into an import
package and claim the platform can execute them.

Use the user's chosen method. The configs below are SMALL EXAMPLES, not global
defaults or claims that one method is always better. Replace the whole
`evolution` section when switching and set the matching planner type. The runtime
only needs the planner type changed in these snippets: retain the ordinary
`planner.samplers` from the complete example for the `llm_evolution` methods.
The runtime
also reads dedicated `evolution.planner_type` on methods that define it; do not
leave it pointing at an old planner. Changing methods starts a new search rather
than resuming an incompatible checkpoint.

## Island GA

```yaml
evolution:
  type: island_ga
  max_generations: 5
  num_islands: 2
  island_population_size: 4
  elite_ratio: 0.25
  migration_interval: 3
  migration_rate: 0.25
  mutation_rate: 0.3
  crossover_rate: 0.5
  max_llm_concurrency: 2
planner:
  type: llm_evolution
```

`num_islands × island_population_size` is the initial population scale. More
islands/individuals/generations increase model and evaluation work. Migration
interval and rate determine when and how much to exchange.

## Diverse Island GA

```yaml
evolution:
  type: diverse_island_ga
  max_generations: 5
  num_islands: 3
  island_population_size: 4
  max_llm_concurrency: 2
  adaptive_migration: true
  migration_stagnation_threshold: 2
  island_strategy_strength: 1.0
  novelty_survivor_ratio: 0.2
  exploration_restart_ratio: 0.3
planner:
  type: llm_evolution
```

`island_strategy_strength` controls strategy separation; `novelty_survivor_ratio`
reserves survival opportunities for novel candidates. `exploration_restart_ratio`
controls parentless restarts on the exploration side. Success/error memory roles
take effect when memory is enabled; disabling memory still permits the other
diversity mechanisms. `elite_reevaluation_count` optionally adds second-stage
evaluation and defaults to zero.

## DyCA

```yaml
evolution:
  type: dyca
  max_generations: 5
  n_clusters: 2
  n_anchors: 2
  generalist_pool_size: 3
  specialist_pool_size: 3
  complementary_pool_size: 3
  offspring_per_generation: 2
  recluster_interval: 3
planner:
  type: llm_evolution
```

Provide enough diverse instances for the requested clusters and enough evaluated
anchors to characterize them. Pool sizes and offspring count control workload;
`recluster_interval` controls reassignment frequency. Install matching clustering
dependencies (`dyca` extra). Baseline packaging verification does not run the
clustering/evolution loop.

## MEoH

```yaml
evolution:
  type: meoh
  max_generations: 5
  population_size: 4
  selection_num: 2
  max_sample_nums: 20
  num_samplers: 1
  objective_metrics: [quality, execution_time_ms]
planner:
  type: meoh_evolution
```

Replace `objective_metrics` with metric names actually returned and declared by
the evaluator. Each metric's minimize/maximize direction must be correct. A
single aggregate `score` does not substitute for the separate objective metrics.
Use the platform's `meoh` extra when its diversity backends require it.

## EoH

```yaml
evolution:
  type: eoh
  max_generations: 5
  population_size: 4
  selection_num: 2
  max_sample_nums: 20
  num_samplers: 1
  use_e2_operator: true
  use_m1_operator: true
  use_m2_operator: true
planner:
  type: eoh_evolution
```

`population_size` is the surviving pool; `selection_num` controls crossover
parents. The operator flags enable additional design/structure/parameter changes.
`max_sample_nums` is the method's sampling budget, not an upload limit.

## ReEvo

```yaml
evolution:
  type: reevo
  max_generations: 5
  population_size: 4
  mutation_rate: 0.5
  max_sample_nums: 20
  num_samplers: 1
planner:
  type: reevo_evolution
```

Reflection adds model calls beyond producing a candidate. `mutation_rate`
controls elite-based mutation work; `max_sample_nums` bounds sampling.

## MCTS-AHD

```yaml
evolution:
  type: mcts_ahd
  max_generations: 10
  init_size: 2
  population_size: 4
  selection_num: 2
  max_sample_nums: 20
  alpha: 0.5
  lambda_0: 0.1
  max_depth: 5
planner:
  type: mcts_ahd_evolution
```

Here `max_generations` is a tree-search iteration count. `alpha` controls
progressive widening, `lambda_0` controls exploration, and `max_depth` limits the
search depth. These fields do not apply to island methods.

## Verify against the installed platform

The helper checks the selected type against the installed schema and registry.
To inspect both lists after an upgrade:

```python
from llm4ad.config import AppConfig
from llm4ad.orchestrator import BaseOrchestrator

schema = AppConfig.model_json_schema()
print(schema["properties"]["evolution"]["discriminator"]["mapping"])
print(BaseOrchestrator.list())
```
