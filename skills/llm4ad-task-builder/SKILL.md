---
name: llm4ad-task-builder
description: "Use when a user wants to build an LLM4AD_Next task package — a runnable directory that lets the LLM4AD platform evolve an algorithm for their problem. Covers what files a task package contains, each file's contract, and how the package is run on the LLM4AD platform."
---

# Building an LLM4AD_Next Task Package

## What LLM4AD_Next is

LLM4AD_Next is an automated algorithm-design platform: it combines an LLM with
evolutionary optimization to discover and improve algorithms automatically. The
user describes a problem (e.g. "evolve a solver for the Traveling Salesman
Problem"), marks the code region to evolve with `# EVOLVE_START` / `# EVOLVE_END`,
and the platform repeatedly asks an LLM to propose better code inside that region,
scores each candidate with a custom evaluator, and keeps the best across
generations. Better algorithms emerge through guided search rather than manual
trial and error.

Your job with this skill: turn a user's problem into a **complete, runnable task
package**, then verify it actually runs before handing it over.

## What a task package is

A task package is a self-contained directory with everything the platform needs to
evolve algorithms for one problem:

| File | Purpose |
|---|---|
| `config.yaml` | Master config for the whole pipeline (evolution params, providers, evaluator, dataset). This is what gets run. |
| `<name>_evaluator.py` | A `BaseEvaluator` subclass that runs a candidate algorithm on test data and returns a score + metrics. Defines what "better" means. |
| `<algo_dir>/<algo>.py` | The algorithm, with the function to evolve wrapped in `# EVOLVE_START` / `# EVOLVE_END`. Reads input as a JSON CLI arg, prints a JSON result. |
| `debug_run.py` | Runs the full pipeline once (`LLM4AD("config.yaml").run()`) — a smoke test. |
| `test_evaluator.py` | Loads the evaluator via the dispatcher to confirm it imports and is wired correctly. |
| `data/sample/*.json` | 2-3 small test instances the evaluator scores algorithms against. |

## The contracts each file must satisfy

**Algorithm file** (`<algo_dir>/<algo>.py`):
- The algorithm directory is the one named in `version_control.local_path`; the
  platform copies it into a git worktree for each candidate and edits only the code
  between the markers.
- The function to evolve is wrapped exactly in `# EVOLVE_START` and `# EVOLVE_END`
  comment markers (the platform only edits code between them).
- It reads its input as a single JSON string from `sys.argv[1]`, and prints a JSON
  result to stdout — so the evaluator can run it as a subprocess.
- The file name must match the name the evaluator looks for (see below). In the TSP
  example both sides use `solve.py`.

**Evaluator** (`<name>_evaluator.py`):
- Subclasses `BaseEvaluator`, decorated `@BaseEvaluator.register("<name>")`, with a
  no-argument `__init__(self)` (the base `__init__` takes no config).
- Implements `metrics` (list of `Metric(name, type=MINIMIZE|MAXIMIZE, weight, description)`),
  `name`, and `async def evaluate(self, cfg: EvalContext) -> EvaluationResult`.
- `cfg: EvalContext` carries `cfg.project_root` (the candidate's worktree dir),
  `cfg.data_path` (the current data file), and `cfg.timeout` (seconds per instance).
- `evaluate` locates the algorithm file **by hardcoded name** under
  `cfg.project_root` (e.g. `Path(cfg.project_root) / "solve.py"`), runs it as a
  subprocess on `cfg.data_path`, parses its JSON output, validates it, and returns
  `EvaluationResult(score, metrics, success, ...)` (negative cost for a minimization
  objective). That hardcoded name **must** equal the algorithm file's actual name —
  this is the most common wiring mistake.

**config.yaml** (10 sections — generated programmatically, not hand-written):
- `providers` use `${LLM_BASE_URL}` / `${LLM_API_KEY}` / `${LLM_MODEL}` env
  placeholders (the platform fills them in).
- The evaluator `module` reference (`<file>.py:<ClassName>`), the `metrics` list,
  the `dataset` path (`data/sample`), and the coder `prompt_template`'s EVOLVE block
  must all be consistent with the other files.

**debug_run.py** / **test_evaluator.py**: standard boilerplate that runs
`config.yaml` end-to-end and loads the evaluator, respectively.

See `reference/config-template.yaml` for the config shape and `reference/example-tsp.md`
for a pointer to a complete, known-good package in this repo to adapt.

## The information to gather before building

A correct package needs these decided (ask the user; let the agent fill sensible
defaults where the user has no preference):

1. **Problem description** — what is being optimized. (Required — ask.)
2. **Function/algorithm to evolve + input/output format** — ask if the user has any
   constraints; if not, the agent designs it.
3. **Evaluation metric(s)** — what to minimize/maximize. (Required — ask clearly.)
4. **Reuse existing code/evaluator?** — user provides existing code, or generate from
   scratch. (Ask.)
5. **Data source** — user provides data, or generate sample instances. (Ask.)
6. **Programming language** — default Python. (Offer as a choice.)
7. **Project name** — the agent proposes one from the description; confirm with user.

## Build workflow

1. Understand the problem and the 7 items above (inspect any user-provided code/data).
2. Write the algorithm file first; verify it runs standalone and prints valid JSON.
3. Write 2-3 sample data files under `data/sample/`.
4. Write the evaluator; keep its metrics consistent with `config.yaml`.
5. Write `config.yaml`, `debug_run.py`, `test_evaluator.py`.
6. **Self-test (required):** run `test_evaluator.py` (evaluator loads — no LLM, no
   network, cheap and always runnable). Then run `debug_run.py`, which executes the
   full pipeline and **does call the LLM**: it needs the `LLM_BASE_URL` /
   `LLM_API_KEY` / `LLM_MODEL` env vars set (the config uses these placeholders) and
   consumes tokens over the network. Read every error, fix the relevant file,
   re-run. Not done until both run cleanly. If no provider credentials are available,
   say so — a missing-credential failure of `debug_run.py` is not a package defect.

<!-- PLATFORM-USAGE-DRAFT: 以下"如何在平台上使用"为草稿，待产品同学修订 -->
## How the package is used on the LLM4AD platform

Once the package exists, the user runs it to evolve their algorithm. Two paths:

**CLI:**
```bash
llm4ad run path/to/config.yaml
# resume an interrupted run: pass a real checkpoint file written under
# ./runs/<project>/<run_id>/checkpoints/ (Island GA names them
# iga_checkpoint_gen_<N>_<hash>.json; MEoH names them meoh_<N>.json).
llm4ad run config.yaml --resume ./runs/<project>/<run_id>/checkpoints/iga_checkpoint_gen_5_a1b2c3d4.json
```
It launches the evolution pipeline and prints per-generation progress (best score
each generation).

**Web platform (this project's Web UI):**
1. Create or open a task (the package's `config.yaml` + files back it).
2. Configure an LLM provider (the user's own key/model — OpenAI-compatible or
   Anthropic). The package's `config.yaml` references providers by the platform's
   env placeholders; the platform injects the real credentials at run time.
3. Start the evolution run; monitor progress in the browser — current generation,
   best individual's score, metrics, and logs — in real time.
4. When it finishes, inspect the best evolved algorithm and its score.

**Tuning after generation (optional):** users often adjust `config.yaml` evolution
parameters to trade cost vs. quality — e.g. `max_generations`, `num_islands`,
`island_population_size`, `mutation_rate` / `crossover_rate`, `early_stop_patience`,
the provider `model`, and `evaluator.timeout`.

**Where results go:** each run writes to `./runs/{project}/{run_id}/`:
- `best/code/` — the evolved algorithm source (the main deliverable)
- `best/metadata.json`, `best/summary.txt` — score, generation, metrics
- `state/evolution_state.json` — full generation history (drives the Web UI dashboards)
- `logs/llm4ad.log` — full execution log for troubleshooting
- `checkpoints/` — snapshots to resume from
<!-- END PLATFORM-USAGE-DRAFT -->

## Completion criteria

The package is done only when `test_evaluator.py` loads the evaluator AND
`debug_run.py` runs without raising. Then summarize what was built (files + the
7 decisions) and the verification result.
