# MEoH Method Guide

This guide covers how to use the `meoh` orchestrator in LLM4AD:

- Method positioning and current implementation scope
- Core execution flow
- Key configuration fields
- The `reuse_coder` and `direct_code` code-generation modes
- Multi-objective configuration and seed usage
- A minimal example configuration

## 1. Method positioning

The `meoh` shipped in this repo is a port of the external MEoH idea onto LLM4AD's existing architecture.

It's not a line-by-line clone of any external repo. Instead it builds on the project's existing parts:

- `planner -> coder -> evaluator -> orchestrator`
- repository EVOLVE block
- version control worktree
- state tracker

…to provide a minimal runnable variant.

This iteration aims to:

- be a new `evolution.type`,
- run inside the current framework,
- support multi-objective selection,
- support `seed`,
- support two code-generation paths.

It does **not** cover everything the external MEoH does, in particular:

- prompt details are not 1:1
- the external profiler stack is not fully ported
- checkpoint/resume support is intentionally light
- `direct_code` is a simplified EVOLVE-block-replacement implementation

## 2. Core layout

The functionality lives in:

- `src/llm4ad/orchestrator/meoh.py`
- `src/llm4ad/orchestrator/meoh_population.py`
- `src/llm4ad/planner/meoh_evolution.py`
- `src/llm4ad/planner/sampler/meoh_init_sampler.py`
- `src/llm4ad/planner/sampler/meoh_e1_sampler.py`
- `src/llm4ad/planner/sampler/meoh_e2_sampler.py`
- `src/llm4ad/planner/sampler/meoh_m1_sampler.py`
- `src/llm4ad/planner/sampler/meoh_m2_sampler.py`
- `src/llm4ad/planner/sampler/meoh_prompt_templates.py`

## 3. Execution flow

### 3.1 Entry point

When the config has:

```yaml
evolution:
  type: "meoh"
  planner_type: "meoh_evolution"
```

The pipeline becomes:

```text
CLI
-> LLM4AD
-> BasePlanner.create("meoh_evolution")
-> BaseOrchestrator.create("meoh")
-> MEoHOrchestrator.run()
```

### 3.2 What counts as a generation

One of the biggest differences between `meoh` and `island_ga` is the meaning of "generation".

In `meoh`:

- a generation is **not** "every candidate produced"
- and **not** "every operator invocation"
- it is "every `survival()` event"

That is:

- new individuals enter `next_gen_population` first
- once `next_gen_population` accumulates `population_size` items
- a `survival()` is triggered
- then `generation += 1`

### 3.3 Operator scheduling

`meoh` currently supports five operators:

- `i1`
- `e1`
- `e2`
- `m1`
- `m2`

They correspond to:

- `i1`: initialize an algorithm idea
- `e1`: from multiple parents, generate a clearly different new algorithm
- `e2`: from the common skeleton of multiple parents, generate a new algorithm
- `m1`: structural mutation on a single parent
- `m2`: parameter / local-strategy mutation on a single parent

During initialization, only `i1` is used.

During evolution:

- the default order is `e1 -> e2 -> m1 -> m2`
- whether `e2` / `m1` / `m2` run is controlled by configuration

## 4. Population logic

`MEoHPopulation` keeps three sets:

- `population`
- `next_gen_population`
- `elitist_archive`

Their meanings:

- `population`: the active population, used for parent selection
- `next_gen_population`: new candidates accumulated since the last survival event
- `elitist_archive`: the global non-dominated front

### 4.1 survival

`survival()` does:

1. Merge `population + next_gen_population`.
2. Update the Pareto non-dominated front using `objective_metrics`.
3. Trim the active population using a CodeBLEU similarity penalty plus dominance.
4. Keep `max(1, int(population_size * active_population_ratio))` active individuals.
5. Clear `next_gen_population`.
6. `generation += 1`.

### 4.2 selection

`selection()` only picks parents from valid individuals — those that have been evaluated successfully and whose objective metrics are readable. Invalid individuals stay in the bookkeeping but never become parents.

### 4.3 duplicate / dominated clones

A candidate is treated as duplicate-or-discardable when:

- the code string is identical to an existing one, **or**
- the objective vector is identical and the existing scalar `score` is no worse than the candidate's

## 5. Multi-objective mechanics

`meoh` does not run Pareto selection on `Algorithm.score`. It uses an explicitly configured list, `objective_metrics`. For example:

```yaml
evolution:
  objective_metrics:
    - "tour_length"
    - "candidate_runtime_ms"
```

These metrics come from `EvaluationResult.metrics` returned by the evaluator. The direction is taken from the evaluator's `Metric` definition (`MetricType.MINIMIZE` / `MetricType.MAXIMIZE`); internally everything is normalized into a maximize-space before comparison.

### 5.1 Relationship to the framework's existing `score`

Even though the Pareto logic is independent of the scalar `score`, the framework still keeps `evaluation.score` for:

- logging
- the state tracker
- `best_individual`
- history output

So:

- multi-objective selection looks at `objective_metrics`,
- the compatibility layer still surfaces a single `score`.

## 6. Two code-generation modes

`meoh` supports two paths:

```yaml
evolution:
  code_generation_mode: "reuse_coder"
```

or:

```yaml
evolution:
  code_generation_mode: "direct_code"
```

### 6.1 `reuse_coder`

The path closer to the rest of LLM4AD.

```text
planner.init()
-> planner.plan(operator=...)
-> planner.implement()
-> coder.generate(...)
-> evaluator
```

Pros:

- reuses the existing coder
- consistent with the rest of the system
- easier to extend later

### 6.2 `direct_code`

A lighter, more direct path.

```text
planner.init()
-> planner.plan(operator=...)
-> planner.generate_direct_code()
-> directly replace the EVOLVE block
-> evaluator
```

Here no external coder is invoked; the planner uses the provider to generate the EVOLVE-block replacement code itself.

Its current role is:

- quick validation
- closer to the external "direct code" flavor

…but it still rides on the project's EVOLVE-block workflow rather than a full external function/program system.

## 7. Seed support

`meoh` supports seed initialization, but only in the project's own `Algorithm` JSON format.

```yaml
evolution:
  seed_path: "./seeds/meoh_seed.json"
```

The seed file should be:

- a list produced by `Algorithm.model_dump()`, or
- a JSON object with an `algorithms` field

External-format seeds are not currently supported.

## 8. CodeBLEU dependency

`meoh` uses CodeBLEU syntactic similarity for the diversity penalty by default. The corresponding optional install extra is `meoh`:

```bash
uv sync --extra meoh
```

If you also want the TSP example, install both extras:

```bash
uv sync --extra meoh --extra tsp
```

Without `codebleu` installed, `meoh` fails during the similarity step.

## 9. Minimal example config

The repo ships a minimal example at [`examples/config/config.meoh.yaml`](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/examples/config/config.meoh.yaml). The key block:

```yaml
evolution:
  type: "meoh"
  planner_type: "meoh_evolution"
  max_generations: 5
  population_size: 8
  selection_num: 2
  max_sample_nums: 32
  objective_metrics:
    - "tour_length"
    - "candidate_runtime_ms"
  code_generation_mode: "reuse_coder"
  active_population_ratio: 0.25
  use_e2_operator: true
  use_m1_operator: true
  use_m2_operator: true

planner:
  provider: "default"

coder:
  provider: "default"
```

## 10. Configuration reference

`MEoHConfig` is defined in `src/llm4ad/config/evolution.py`. The most relevant fields:

### `population_size`

- Threshold of accumulated new candidates before a survival event.
- `survival()` triggers when `next_gen_population` reaches this size.

### `selection_num`

- How many parents `e1` and `e2` pick by default.

### `max_sample_nums`

- Maximum number of candidates a single run may produce.

### `num_samplers`

- How many candidates each operator round produces.

### `objective_metrics`

- The metric names used for multi-objective selection.
- Must match what the evaluator returns in `metrics`.

### `use_e2_operator` / `use_m1_operator` / `use_m2_operator`

- Toggle the corresponding operators.

### `seed_path`

- Path to a seed file.

### `active_population_ratio`

- Fraction of the active population kept after `survival()`. Default `0.25`.

### `generation_mode`

- Currently fixed to `"survival"`.

### `code_generation_mode`

- Either `"reuse_coder"` or `"direct_code"`.

## 11. Running it

From the repo root:

```bash
uv sync --extra meoh --extra tsp
uv run llm4ad run examples/config/config.meoh.yaml
```

If you use a custom provider/coder, make sure the corresponding env vars are set, e.g.:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## 12. Recommendations

If you want to research or extend on top of this iteration, a useful order is:

1. First, run a real benchmark with `reuse_coder` to confirm the link is good.
2. Then refine the operator prompts to match the original MEoH more closely.
3. Then consider strengthening `direct_code` to align with external function/program generation.

If your goal is "wire the method into the platform and validate the link", this iteration is enough.
If your goal is "fully reproduce the external paper or repo", this is just the first step.

## 13. Related files

- `src/llm4ad/orchestrator/meoh.py`
- `src/llm4ad/orchestrator/meoh_population.py`
- `src/llm4ad/planner/meoh_evolution.py`
- `src/llm4ad/planner/sampler/meoh_prompt_templates.py`
- [`examples/config/config.meoh.yaml`](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/examples/config/config.meoh.yaml)
