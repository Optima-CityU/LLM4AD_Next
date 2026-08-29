# Evaluator API

`llm4ad.evaluator` runs algorithms against a dataset and returns scored metrics that drive evolution.

## Public surface

| Symbol | Purpose | Source |
|---|---|---|
| `BaseEvaluator` | Root class for custom Python evaluators; subclass and implement `evaluate(...)` | `src/llm4ad/evaluator/base.py` |
| `BaseBatchEvaluator` | Base for evaluators that compare a same-generation cohort via `evaluate_batch(...)` | `src/llm4ad/evaluator/base.py` |
| `PythonEvaluator` | Convenience subclass that calls a Python function directly | `src/llm4ad/evaluator/base.py` |
| `BenchmarkEvaluator` | Multi-instance aggregation (one evaluation per dataset file) | `src/llm4ad/evaluator/base.py` |
| `LLMJudgeEvaluator` | LLM-as-a-judge evaluator for outputs you cannot measure directly | `src/llm4ad/evaluator/llm_judge.py` |
| `PaperRevisionEvaluator` | Static protection plus multi-LLM panel or debate evaluation for selected paper sections | `src/llm4ad/evaluator/paper_revision/` |
| `ExecutableEvaluator` | Runs an external command and extracts metrics from stdout via regex | `src/llm4ad/evaluator/base.py` |
| `EvaluationDispatcher` | Dispatches to the concrete evaluator based on `evaluator.type` + `module:` | `src/llm4ad/evaluator/dispatcher.py` |
| `EvaluationResult` | Standard return envelope: `score`, `metrics`, `metadata`, `success`, … | `src/llm4ad/evaluator/base.py` |
| `Metric`, `MetricType` | Single-metric definition (name, direction, weight) | `src/llm4ad/evaluator/base.py` |
| `BehaviorData`, `BehaviorVisualization` | Behavior-data payload returned by multimodal evaluators | `src/llm4ad/evaluator/behavior.py` |
| `BaseRenderer` | Renders raw behavior data into images when `behavior_storage="raw"` | `src/llm4ad/evaluator/renderer.py` |

## Writing a custom Python evaluator

```python
# my_eval.py
from llm4ad.evaluator import PythonEvaluator
from llm4ad.evaluator.base import EvaluationResult, Metric, MetricType

class SortEvaluator(PythonEvaluator):
    @property
    def metrics(self):
        return [Metric(name="comparisons", type=MetricType.MINIMIZE)]

    async def evaluate(self, cfg) -> EvaluationResult:
        # Run the algorithm at cfg.project_root and collect stats
        n_cmp = run_algorithm_and_count(cfg.project_root, cfg.data_path)
        return EvaluationResult(
            score=-n_cmp,            # evolution always maximizes score
            metrics={"comparisons": n_cmp},
            success=True,
            duration_ms=42.0,
        )
```

In YAML:

```yaml
evaluator:
  type: custom
  module: my_eval:SortEvaluator
  metrics: ["comparisons"]
  dataset:
    mode: directory
    path: ./data
    recursive: true
```

`module` accepts two forms: `pkg.module:ClassName` or `path/to/file.py:ClassName`. Extra YAML keys beyond the schema (e.g. `api_config:`) flow through `model_extra` into the evaluator constructor.

## EvalContext

Each `evaluate(cfg)` call receives an `EvalContext`:

| Field | Meaning |
|---|---|
| `project_root` | Worktree root for this individual (the coder created it) |
| `data_path` | One instance path resolved from `DatasetConfig` (mode-dependent) |
| `timeout` | Soft timeout in seconds |
| `behavior_storage` | `"rendered"` / `"raw"` / `"none"` — hints whether evaluators should capture behavior data |
| `candidate_id` | Current candidate ID; legacy evaluators may ignore it |
| `generation` | Evolution generation used for reproducible assignment and provenance |
| `parent_ids` | Parent candidate IDs used to trace revision lineage |

## Paper revision evaluator

`PaperRevisionEvaluator` evaluates normalized selected-section revisions. It does not parse PDF/TeX, generate text, replace source, or persist memory. The upstream parser writes a task JSON containing `task_id`, `section_id`, `original_text`, neighboring context, relevant `cspaper_findings`, constraints, and an optional rubric. Each candidate worktree contains `candidate.json` with `candidate_id`, `section_id`, and `revised_text`.

Configure it as a custom evaluator:

```yaml
evaluator:
  type: custom
  provider: judge_openai
  module: llm4ad.evaluator.paper_revision:PaperRevisionEvaluator
  mode: panel                  # panel or debate
  judges: [judge_openai, judge_claude]
  panel_size: 2
  min_judges: 2
  candidate_file: candidate.json
  random_seed: 42
  dataset:
    mode: files
    files: [paper-task.json]
```

`panel` anonymizes and deterministically swaps original/revision order before independent scoring. `debate` uses `BaseBatchEvaluator` to review a same-generation cohort, cross-examine anonymous reviews, and cast final ballots.

Stable metrics are `baseline_score`, `revised_score`, `score_delta`, `judge_agreement`, and `static_valid`. Dimension details, judge reports, ballots, static checks, and side-effect-free `memory_candidates` are stored in `EvaluationResult.metadata`. Persist recommended memory only after the winning candidate has been selected and accepted.

## Multi-instance / benchmark evaluation

`BenchmarkEvaluator` calls `evaluate_instance` for each dataset file (per `dataset.mode = files | directory | glob`) in parallel, then `aggregate(...)` combines the scores and metrics.

```python
class TSPBenchmark(BenchmarkEvaluator):
    metrics = [Metric(name="tour_length", type=MetricType.MINIMIZE)]

    async def evaluate_instance(self, algorithm, ctx, instance_path) -> EvaluationResult:
        ...

    def aggregate(self, results) -> EvaluationResult:
        avg = sum(r.metrics["tour_length"] for r in results) / len(results)
        return EvaluationResult(score=-avg, metrics={"tour_length": avg})
```

## Behavior data / multimodal

`EvaluationResult.behavior` lets evaluators return images, trajectories, or observations to the planner. Enable `multimodal.enabled` to feed them into prompts via the multimodal samplers — see [Multimodal](../guides/multimodal.md).
When `behavior_storage="raw"`, register a `BaseRenderer` so images can be reconstructed later from the raw data; see `src/llm4ad/evaluator/renderer.py` for a worked example.

## See also

- [Evaluators Guide](../guides/evaluators.md) — task-oriented walkthrough
- [Configuration Guide](../guides/configuration.md#evaluator) — `evaluator:` schema
- Source of truth: `src/llm4ad/evaluator/`
