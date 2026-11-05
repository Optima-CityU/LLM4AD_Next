# Produced run files and their meaning

## Roots and input boundaries

Paths below describe producers in this repository; use MCP listings to find the
actual paths for the selected task. `area="run"` is the backend's outer runtime
root. In cloud execution this is mounted at `/task/data`.
`src/backend/app/services/task_service/execution.py::run_task` overrides
`base_dir="/task/data/"`, `project_name="llm4ad"`, `run_id="run"`, so the library's
run directory is `/task/data/llm4ad/run/`. Relative to that outer root:

```text
.events.jsonl                         # Container event stream, when written
.final_state.json                     # Container's post-run visualization export
llm4ad/run/
  best/code/                         # End-of-run candidate snapshot
  best/metadata.json
  best/summary.txt
  best/pareto/<index>/code/           # Multi-member archive, when exported
  best/pareto/<index>/metadata.json
  state/evolution_state.json         # Final visualization export
  state/<prefix>_genNNNN.json         # Caches only where an orchestrator saves them
  logs/llm4ad.log                     # Default log destination
  generated/                         # Actual Algorithm JSON/Markdown records
  checkpoints/                       # Used by some, not all, checkpoint producers
  worktrees/                         # Candidate git working directories
```

Standalone/local library runs use `{base_dir}/{project_name}/{run_id}/` and can
configure workspace subdirectories. `StateTracker.set_workspace_dirs` in
`src/llm4ad/infra/state.py` creates directories, including `best`, `temp`, `coder`,
`memory` and `embedding`; directory existence alone proves no result was written.
Do not assume every method populates them.

`Task.input_args` is the stored configuration. Files under `Task.input_data_path`
in RustFS are canonical inputs (`area="input"`).
`src/backend/app/tasks/evolution.py::_prepare_run_environment` clears the prior runtime
directory and downloads those inputs with `strip_first_level=True`. The runtime
`README.txt` explains that edits there do not update task parameters. A draft
under `/workspace/source/draft/` also is not an input until publication. Child
creation must copy canonical files; `seed="best"` must copy the selected exported
best code into the child's `version_control.local_path`, keeping evaluator/data.
A seed flag or a runtime-only edit is not evidence of that copy.

## Best code, metadata and summary

`src/llm4ad/llm4ad.py::LLM4AD.run` first awaits `orchestrator.run()`, then, when
`workspace.auto_create` is enabled, writes `state/evolution_state.json` and calls
`src/llm4ad/infra/best_exporter.py::export_best`. Thus the snapshot is produced
only at run end. Failure, forced stop, no best individual, missing worktrees or a
best-effort export failure can leave it absent or incomplete. Read status and
logs before diagnosing missing best code. A graceful run return may export it;
do not promise that every stop does.

`best/code/` is a plain copy of the selected candidate worktree, filtering `.git`
and Python caches. If no worktree is attached, the exporter writes
`Algorithm.code_artifacts` instead. If an attached source worktree no longer
exists, the exporter may create an empty code directory and log a warning.

`best/metadata.json` is produced by `_metadata_for`, with `id`, `name`,
`description`, `key_innovations`, `design_notes`, `domain`, `insight_type`,
`generation`, `island_id`, `parent_ids`, `tags`, top-level `score`, `metrics`,
`evaluation`, `worktree`, `lines_added`, `lines_removed`, `lines_modified`,
`custom_metadata`, `created_at`, `updated_at`. When present, `evaluation` contains
`score`, `metrics`, `evaluation_time_ms`, `error`; `worktree` contains `name`,
`path`, `branch`, `commit_hash`. The path records provenance inside the task
runner; it is not an arbitrary host path the Agent should open.

`best/summary.txt` contains best score/generation/ID, optional island/worktree,
final generation, total evaluations and duration. It is a human summary, not
per-instance evaluation evidence. The exporter also writes
`best/pareto/<index>/code/` and `metadata.json` when the result's final population
or `metadata.elitist_archive` has more than one member, and lists their metrics
in the top-level summary. Canonical single-best output is still written when
`best_individual` exists. Do not assume Pareto member directories have their own
`summary.txt`, or that a single scalar ranks every multi-objective tradeoff.

## Raw metrics, fitness and minimization

Read the task's actual evaluator implementation and configured metric names.
`src/llm4ad/evaluator/base.py::Metric` declares `name`, `type` (`minimize` or
`maximize`), `weight`, `description`. Evaluator `EvaluationResult` has `score`,
`metrics`, `monitor_metrics`, `metadata`, `success`, `error_message`, `duration_ms`
and optional behavior/feedback. Raw `metrics` preserve evaluator units; do not
present a negated or normalized internal score as the original objective.

The default `BaseEvaluator.compute_score` negates each minimization value, keeps
maximization values, and divides the weighted sum by total included weight.
It does **not** rescale raw values into a universal zero-to-one quality scale.
Custom evaluators can override this computation. The algorithm record's
`evaluation.score` is internal fitness, where higher is better; `Algorithm.score`
and `Algorithm.metrics` are properties forwarding to its evaluation
(`src/llm4ad/planner/base.py`). Inspect `evaluation.error` before interpreting an
algorithm's score; an unevaluated algorithm's score property defaults to zero.
Do not call a negative fitness worse just because the raw objective is positive.

MEoH uses direction-adjusted objective vectors in
`src/llm4ad/orchestrator/meoh_population.py::MEoHPopulation.get_objective_vector`; its hypervolume
calculation normalizes the archive objective ranges. The representative best
selection in `src/llm4ad/orchestrator/meoh.py::MEoHOrchestrator._select_best_individual` actually
selects by the first direction-adjusted objective (`vec[0]`), despite its docstring
describing a normalized sum. Read executable producer logic when descriptions
disagree. Internal objective vectors and normalized hypervolume are not raw
metric values or comparable performance percentages across runs.

`src/llm4ad/evaluator/dispatcher.py::EvaluationDispatcher._aggregate_results` returns a single-instance
result unchanged. For multiple instances it averages scores/metrics over the
successful results and records `metadata.num_instances`, `successful_instances`,
`per_instance` (each entry has `score`, `success`, `error_message`). Some evaluators
aggregate their own dataset internally. Partial failure can coexist with an
aggregated `success=True`; inspect actual per-instance evidence where available.
Do not claim a full benchmark passed from a mean over only successful instances.

## Visualization state versus checkpoints

`StateTracker.export_for_visualization` in `src/llm4ad/infra/state.py` exports
`run_id`, `state`, `current_generation`, `total_generations`, `start_time`,
`end_time`, `global_best_score`, `total_evaluations`, `total_migrations`,
`metrics_history`, `module_timing`, `resource_history`, `config`, `metadata`,
`behavior_data_available`. These support progress plots and timing analysis;
they do not serialize the complete executable population for resuming search.
`LLM4AD.save_state` writes this format. `state/evolution_state.json` is the final
library export. `StateTracker.save_to_cache(prefix)` writes
`state/<prefix>_gen{current_generation:04d}.json`; Island GA calls it with
`init_population` and `generation`. Such caches need not be current for every
method. Treat their generation/time as recorded evidence, not live status.

Actual resume checkpoints come from each orchestrator's `save_checkpoint`:

- `src/llm4ad/orchestrator/island_ga.py` writes
  `checkpoints/iga_checkpoint_gen_<generation>_<random8>.json` by default;
  `dyca.py` similarly writes `dyca_checkpoint_gen_<generation>_<random8>.json`.
  They include `generation`, `population`, `best_individual`, `history` and
  method-specific `metadata`; Island GA metadata includes `islands`, migration
  data and `state_tracker`.
- `meoh.py` uses `config.checkpoint_dir/meoh_<generation>.json`; `eoh.py`,
  `reevo.py`, and `mcts_ahd.py` use `config.checkpoint_dir` or `.` with their own
  filenames. Their checkpoint payload/restore scope is method-specific; for
  example MCTS-AHD restores the active pool rather than the whole search tree.

Discover the actual files; do not promise all checkpoints are under
`llm4ad/run/checkpoints/`, or that every method saves at every generation.
A new child seeded with input/best starts from canonical source and parameters;
it does not resume one of these checkpoints.

## Logs, generated candidates and container exports

`src/llm4ad/llm4ad.py::LLM4AD._setup_logging` defaults to
`<library-run-dir>/logs/llm4ad.log`, with rotation/compression; `logging.file` can
override it. Logs establish build/evaluation errors, timeouts and export failures.

`src/llm4ad/planner/base.py::Algorithm.write` writes both `.json` and `.md` under
the supplied output directory, normally `generated/`. The prefix is
`[island_<id>_][gen_<generation>_]<algorithm-id>`; the `stage` argument describes
the content and does not add a stage suffix to the filename. A later stage may
overwrite the same candidate record. JSON is the Algorithm model, including
`id`, `generation`, `parent_ids`, `worktree`, `code_artifacts`, `evaluation`,
`timing`, `behavior_data` and `custom_metadata`. Inspect these actual fields;
do not assume every planner writes standalone candidate `.py` files there.
Candidate source lives in worktrees/code artifacts and the final best copy.
Optional behavior companions use `_viz_<bd>_<viz>.png` or
`_raw_<bd>_<viz>.json`, referenced through `_saved_image_file` or
`_saved_raw_data_file`. Their existence depends on actual behavior data.

`src/backend/app/tasks/container_runner.py::EventsSink` appends to
`/task/data/.events.jsonl`. Log events have `type="log"`, `timestamp`, `level`,
`message`, `module`, `function`, `line`. `_scan_generated` scans
`/task/data/llm4ad/run/generated/*.json` and emits `type="generated"`,
`timestamp`, `file_name`, `data`, sanitizing nonfinite numeric values for JSON.
After `llm4ad.run()` returns, `main` writes `/task/data/.final_state.json` from
`llm4ad.export_state()` and emits `{"type":"final_state","data":...}`. These
two dotfiles are at the **outer** root, not inside `llm4ad/run/`. They are a
backend execution feature, not an artifact guaranteed by a local library run;
an interrupted process may never write final state. They are neither canonical
inputs nor resume checkpoints.

State/config records may contain operational details. Use the server's sanitized
readers; do not reproduce credentials in analysis. Compare runs only after
checking evaluator/data/config differences, metric directions and candidate
validity. Cite task ID, listed file path, relevant JSON field and any explicit
line range; separate conclusions from missing or partial evidence.
