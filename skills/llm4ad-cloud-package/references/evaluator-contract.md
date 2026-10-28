# Evaluator contract for portable cloud tasks

The import name is `llm4ad`; install the distribution `llm4ad-next`. These APIs
come from the platform source, rather than an unrelated similarly named package.

```python
from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)
```

For a plain custom evaluator, subclass `BaseEvaluator`, define a no-argument
constructor and the `name` / `metrics` properties, and implement
`async def evaluate(self, cfg: EvalContext) -> EvaluationResult`.

The inherited no-argument constructor is sufficient; do not add an unused one.
If the task needs custom YAML options, the dispatcher also supports
`__init__(self, config, provider_config=None)`: `config` is a
`CustomEvaluatorConfig` and extra YAML fields are available in `config.model_extra`.
It is not the per-call `EvalContext`. Do not require unprovided positional
constructor arguments. Use `module: scoring/my_evaluator.py:MyEvaluator` to load
the class from the package; see the complete
[knapsack evaluator](../assets/knapsack/knapsack_evaluator.py).

### Results and score direction

```python
Metric(name="cost", type=MetricType.MINIMIZE, weight=1.0)
EvaluationResult(metrics={"cost": 12.0}, score=-12.0, success=True)
EvaluationResult(metrics={}, success=False, error_message="invalid output")
```

`metrics` is required. The orchestration score is always maximized; return
negative cost for minimization, or implement and document an equivalent mapping.
Declare separate objective metric directions correctly for MEoH. Invalid output
should return an explicit failure rather than a plausible successful result.

**A custom evaluator must set `score` itself.** Supplying only `metrics` leaves
`score` at its default `0.0`; the dispatcher does not automatically call
`compute_score`. For the default direction-aware weighted mean, use:

```python
values = {"cost": 12.0}
return EvaluationResult(metrics=values, score=self.compute_score(values), success=True)
```

`compute_score` negates MINIMIZE values, then divides the weighted sum by the
total weight of the present declared metrics. It does not normalize metric units
or scales. Choose weights and normalization based on the agreed objective.
Store raw metric values (cost 12, not -12) in `metrics`; the separate scalar score
can be -12. Return finite numbers, a consistent metric set on every successful
instance, and `error_message=None` on success.

| Result field | Meaning |
| --- | --- |
| `metrics` | Required dictionary of metric name → numeric measurement; names match the evaluator definitions and `evaluator.metrics`. |
| `score` | Finite scalar fitness, higher is better. Explicitly computed by a custom evaluator. |
| `success`, `error_message` | Explicit failure and useful reason for missing candidates, invalid outputs, infeasibility, crashes and timeouts. |
| `duration_ms` | Measured elapsed time in milliseconds when needed; it is not the timeout in seconds. |
| `metadata` | JSON-serializable case IDs, seeds, counts and diagnostics; keep output manageable. |
| `evolution_feedback` | Optional compact, factual findings for subsequent evolution; not a replacement for score/metrics. |
| `behavior` | Optional `BehaviorData` for rendering; omit for ordinary numerical evaluation. |

For MEoH, set `evolution.objective_metrics` to the intended objective names and
declare each direction. A diagnostic metric with `weight: 0` is excluded from
`compute_score`, but it is still a Pareto objective if named in `objective_metrics`.

### Input and candidate execution

`cfg.project_root` is the CANDIDATE directory. `cfg.data_path` identifies one
instance, `cfg.timeout` is the per-instance timeout in seconds, and
`cfg.behavior_storage` selects optional behavior recording. Load the trusted
benchmark from `cfg.data_path`; load the candidate from `cfg.project_root`.

`cfg.evaluation_profile` may be `standard` or `elite`. A task may ignore it; if it
changes evaluation fidelity, document the comparison policy. The dispatcher can
create a fresh evaluator for every candidate/instance pair. Do not depend on
in-memory state from a previous call or a previous instance's file traversal order.
For data formats and multi-file instances, read [dataset-contract.md](dataset-contract.md).

For the ordinary separate-script pattern:

1. Candidate `solver.py` reads one JSON string from `sys.argv[1]` and prints one
   JSON result to stdout. Diagnostic logs go to stderr.
2. The evaluator executes `sys.executable, <cfg.project_root>/solver.py, <json>`
   using `asyncio.create_subprocess_exec` and enforces `cfg.timeout`.
3. Check exit status, parse JSON, validate feasibility and recompute objective
   values independently. Do not trust a candidate's self-reported score.
4. Return explicit failures for a missing candidate, invalid data/output,
   constraint violations, and execution errors. Catch expected runtime errors
   and return `success=False` with useful `error_message`.

JSON-in-argv is a convention of this small example, not a platform requirement.
For larger instances, use a file argument or stdin to avoid OS command-length
limits, especially on Windows. The chosen CLI protocol must be documented and
implemented identically by the baseline, evaluator and generated candidates.

Do not reference a fixed original `solver.py`, import a previously cached
candidate module, or find a fallback solution outside `cfg.project_root`. A
candidate may have the same name on every iteration but different contents.
An empty candidate directory must fail the package helper's dependency check.

### Timeouts, concurrency and aggregation

The dispatcher passes `cfg.timeout` but does **not** wrap an arbitrary custom
`evaluate` coroutine in a timer. The evaluator must enforce timeouts itself and
terminate/reap timed-out child processes. `async def` alone does not make blocking
Python or `subprocess.run` cancellable. Prefer async subprocess execution for
untrusted/hanging candidates; do not block the event loop with their computation.
If candidates start child processes, arrange process-tree cleanup appropriate to
the task and OS. An enclosing packaging-worker timeout is only a backstop, not
proof that candidate-level timeout handling works.

`parallel: true` schedules evaluations concurrently; `batch_size` limits the
in-flight evaluations within a dispatcher. Several candidates can also be
scheduled by the orchestrator. Avoid writing shared fixed filenames, mutating
trusted input files or relying on global random state. Use isolated per-call
working files and explicit seeds where appropriate.

The dispatcher averages successful per-file scores/metrics and can report overall
success despite failed files. The packaging helper rejects any failed baseline
entry; it does not alter the later runtime aggregation. For an all-or-nothing or
weighted suite, evaluate a suite manifest inside one custom call as described in
the dataset contract. Do not claim all instances succeeded solely because an
aggregated `success` field is true.

For policy/simulation tasks, spawn an isolated evaluation process which loads
the candidate policy from `cfg.project_root` and drives the rollout. A dataset
file may represent a seed or scenario. Still validate at least one actual rollout
with the real dependencies installed. Non-Python/solver tasks use the relevant
registered evaluator and compiled artifact/backend with the same candidate-root
principle; document any runtime/toolchain dependency in the task README.

The included knapsack example demonstrates independent feasibility/value checks,
finite scoring, async subprocess execution, timeout cleanup, and both successful
and failing evaluations. Adapt its logic to the user's domain, including known
better/worse outputs, missing candidates, malformed/duplicate/out-of-range or
infeasible outputs, runtime errors, and a candidate that exceeds the timeout.
Run the generated `test_evaluator.py` against these cases. Verify that changing
the actual candidate changes its result; a constant or cached score is not a
useful evaluation even if it is finite.

The packaging helper uses `EvaluationDispatcher.dispatch_batch` with a copied
candidate directory. It verifies every configured sample independently, so a
partially successful batch cannot silently pass by averaging only its successes.
It also checks that configured metrics are declared and actually returned.
Before creating the deliverable, it runs the required `test_evaluator.py` in a
separate process from the extracted snapshot. A missing or failing test prevents
delivery. The agent must still design meaningful task-specific assertions; an
empty script exiting zero cannot prove scoring correctness.
