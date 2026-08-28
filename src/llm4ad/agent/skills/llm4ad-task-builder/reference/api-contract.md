# LLM4AD_Next API contract (authoritative)

Read this **before writing any evaluator or test code**. Every signature and
field below is taken from the platform source — copy them exactly. The most
common build failures come from guessing these and getting them subtly wrong
(e.g. writing `type=MINIMIZE` instead of `type=MetricType.MINIMIZE`).

## Imports

The evaluator symbols live in `llm4ad.evaluator.base`:

```python
from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)
```

`EvalContext` is also importable from `llm4ad.config.schema` (both work — the
base module re-exports it). Use `llm4ad.evaluator.base` for consistency.

## MetricType (enum — never a bare name)

```python
class MetricType(Enum):
    MINIMIZE = "minimize"   # lower is better
    MAXIMIZE = "maximize"   # higher is better
```

Always qualify it: `MetricType.MINIMIZE` / `MetricType.MAXIMIZE`. Writing
`MINIMIZE` unqualified raises `NameError`.

## Metric (pydantic model, frozen)

```python
Metric(
    name: str,                              # required
    type: MetricType = MetricType.MAXIMIZE, # default MAXIMIZE
    weight: float = 1.0,                    # for multi-objective scoring
    description: str = "",
)
```

## EvaluationResult (pydantic model)

```python
EvaluationResult(
    metrics: dict[str, float],   # REQUIRED — the only field with no default
    score: float = 0.0,          # primary score used to rank candidates
    monitor_metrics: dict[str, float] = {},
    metadata: dict = {},         # extra info NOT used for evolution (logs/analysis)
    success: bool = True,
    error_message: str | None = None,
    duration_ms: float = 0.0,
    behavior: BehaviorData | None = None,   # multimodal only
)
```

Notes:
- `metrics` is the only required argument. On a failure path, pass
  `metrics={}` (or a partial dict) plus `success=False` and `error_message`.
- `score` is what the evolution ranks on and is **always maximized** by the
  orchestrator. For a minimization objective, return the **negative** cost as
  the score (see the TSP template: `score = -tour_length`).
- Put anything not used to rank candidates (seeds, dataset path, step counts)
  in `metadata`, not `metrics`.

## EvalContext (runtime context passed to `evaluate`)

Exactly four fields — do not expect any others:

```python
EvalContext(
    project_root: str,             # REQUIRED — the candidate's worktree dir
    data_path: str = "",           # current data file (absolute path)
    timeout: float = 60.0,         # seconds per instance
    behavior_storage: Literal["rendered", "raw", "none"] = "none",
)
```

Inside `evaluate`, read `cfg.project_root`, `cfg.data_path`, `cfg.timeout`.

## BaseEvaluator subclass contract

```python
@BaseEvaluator.register("<unique_name>")
class MyEvaluator(BaseEvaluator):
    def __init__(self):                       # MUST be no-arg (only self)
        self._metrics = [Metric(...), ...]

    @property
    def name(self) -> str: ...                # abstract — required

    @property
    def metrics(self) -> list[Metric]: ...    # abstract — required

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult: ...  # abstract, async
```

Critical wiring rules (each is a real failure mode):

1. **`__init__(self)` must take no arguments beyond `self`.** The dispatcher
   inspects the signature: only a bare `(self)` gets called with no args
   (`llm4ad/evaluator/dispatcher.py`). An `__init__(self, config)` on a
   `custom` evaluator changes the call path and typically breaks loading.
   (`PythonEvaluator` / `BenchmarkEvaluator` base classes take a config — do
   NOT subclass those for a custom evaluator; subclass `BaseEvaluator`.)

2. **`evaluate` is `async`.** Run the candidate with
   `asyncio.create_subprocess_exec(...)` + `await asyncio.wait_for(proc.communicate(), timeout=cfg.timeout)`
   so multiple instances run truly in parallel and timeouts are enforced.

3. **The algorithm file is located by a hardcoded name under
   `cfg.project_root`.** e.g. `Path(cfg.project_root) / "solve.py"`. That
   hardcoded name MUST equal the algorithm file's actual name, and the file
   MUST sit inside the directory named by `version_control.local_path` in
   `config.yaml` — the platform copies only that directory into each
   candidate's git worktree and edits only the code between the EVOLVE markers.

4. **Register with a unique string**: `@BaseEvaluator.register("my_name")`, and
   return that same string from the `name` property.

5. **Never let `evaluate` raise.** Wrap the body in `try/except` and return an
   `EvaluationResult(metrics={}, success=False, error_message=...)` on any
   error, so one bad candidate cannot abort the run.

## How config.yaml references the evaluator

```yaml
evaluator:
  type: "custom"
  module: "my_evaluator.py:MyEvaluator"   # "<file>.py:<ClassName>" relative to config.yaml
  metrics: ["metric_a", "metric_b"]        # names must match Metric(name=...) above
  dataset: {mode: "directory", path: "data/sample", recursive: false}
```

The `module` path is resolved relative to the config file's directory. The
`metrics` list here must use the same names as the `Metric` objects the
evaluator defines.

## Two valid `test_evaluator.py` styles

Both are correct; the templates show one each.

- **Via the dispatcher** (TSP template) — checks the class loads and wires
  through the registry exactly as the pipeline loads it:

  ```python
  from llm4ad.evaluator.dispatcher import EvaluationDispatcher
  from llm4ad.config import CustomEvaluatorConfig
  cfg = CustomEvaluatorConfig(module="my_evaluator.py:MyEvaluator", ...)
  dispatcher = EvaluationDispatcher(config=cfg)  # raises if the class can't load
  ```

- **Direct instantiation + one `evaluate` call** (LunarLander template) —
  also exercises the scoring path once:

  ```python
  from my_evaluator import MyEvaluator
  from llm4ad.evaluator.base import EvalContext
  result = await MyEvaluator().evaluate(EvalContext(project_root=".", data_path="data/sample/x.json"))
  ```

## debug_run.py must chdir + run async

The pipeline entry is async and `config.yaml` uses paths relative to its own
directory, so the smoke test must chdir into the package first:

```python
import asyncio, os
from pathlib import Path
from llm4ad import LLM4AD

os.chdir(Path(__file__).resolve().parent)   # so relative paths in YAML resolve

async def main():
    result = await LLM4AD("config.yaml").run()
    ...

asyncio.run(main())
```

`debug_run.py` runs the **full pipeline and calls the LLM** — it needs
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` set. A missing-credential failure
is an environment issue, not a package defect.
