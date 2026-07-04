# Python Design Task Template

This template provides a reference for creating new Python design tasks in LLM4AD. It summarizes patterns from the existing tasks (TSP, Sorting, LunarLander) into reusable templates.

## Directory Structure

Every Python design task follows this layout:

```
my_task_python/
  config.yaml      # Task configuration (entry point)
  my_evaluator.py             # Custom evaluator (also subprocess entry point)
  my_algorithm/               # Algorithm code directory (version-controlled)
    my_function.py            # Algorithm file with EVOLVE markers
  data/                       # Evaluation data
    sample/                   # Data instances
      instance_001.json
      instance_002.json
  test_evaluator.py           # Evaluator test script
  debug_run.py                # Debug entry point for full pipeline
  runs/                       # (auto-created) Run outputs
```

### Naming Conventions

| Component | Convention | Examples |
|-----------|-----------|----------|
| Task directory | `<task>_python/` | `tsp_benchmark_python/`, `lunarlander_python/` |
| YAML config | `config.yaml` | `config.yaml` |
| Evaluator | `<task>_evaluator.py` | `tsp_evaluator.py`, `sorting_evaluator.py` |
| Algorithm dir | descriptive name | `tsp_algorithm/`, `policy/`, `sorting_algorithm/` |
| Algorithm file | function-based name | `solve.py`, `sort.py`, `choose_action.py` |

## Subprocess Isolation (Standard)

**All evaluations run in a subprocess.** LLM-generated code is untrusted — it may segfault, deadlock, call `sys.exit()`, or leak memory. Subprocess isolation ensures none of these can crash the main orchestrator process.

### Why subprocess?

| Fault | Without subprocess | With subprocess |
|-------|-------------------|-----------------|
| Segfault | Main process crashes | `returncode != 0` -> score=0 |
| `sys.exit()` | Main process exits | `returncode != 0` -> score=0 |
| Infinite loop | Main process hangs | `asyncio.wait_for` timeout -> `proc.kill()` |
| Memory leak | Accumulates to OOM | OS reclaims on process exit |
| Global state pollution | Later evaluations get wrong results | Complete isolation |

### Two Subprocess Variants

Both variants use `asyncio.create_subprocess_exec()` for true async parallelism.

#### Variant A: Separate Script (TSP, Sorting)

```
Evaluator  --subprocess-->  python solve.py '<input_json>'
                                    |
                              JSON output to stdout
                                    |
Evaluator  <--json.loads()--- parses stdout
```

The algorithm file already has a `main()` that reads JSON from argv and writes JSON to stdout. The evaluator simply spawns it and parses the output.

**When to use:** Algorithm is a self-contained script (one function call per evaluation).

#### Variant B: Self-Spawning Evaluator (LunarLander)

```
Evaluator  --subprocess-->  python my_evaluator.py '<config_json>'
                                    |
                              __main__ block:
                                loads algorithm module
                                runs simulation/loop
                                prints JSON result
                                    |
Evaluator  <--json.loads()--- parses stdout
```

The evaluator spawns **itself** as a subprocess. The `__main__` block at the bottom of the evaluator file loads the algorithm module, runs the evaluation (e.g., a simulation loop), and prints JSON to stdout.

**When to use:** Evaluator needs to orchestrate the algorithm (simulation loop, environment setup, multiple function calls per evaluation).

### Variant Comparison

| Aspect | Separate Script (A) | Self-Spawning (B) |
|--------|--------------------|--------------------|
| Subprocess target | `python solve.py` | `python my_evaluator.py` |
| Algorithm loading | Script loads itself | Evaluator loads via importlib |
| Calls per eval | 1 | Many (e.g., 200 for RL) |
| Extra files | Algorithm has `main()` + `__main__` | Evaluator has `__main__` |
| Fault isolation | Full | Full |
| Parallelism | True async | True async |
| Use case | TSP, Sorting | LunarLander, RL tasks |

### Data Format (Unified)

Both variants use the **same data convention**: one JSON file per instance.

```
data/
  train/                        # or small/, test/, etc.
    instance_001.json           # {"seed": 6, "max_steps": 200}
    instance_002.json           # {"nodes": [[x,y], ...]}
    instance_003.json           # task-specific fields
```

The **dispatcher** discovers all files in the configured directory and calls `evaluate()` once per file. After all calls complete, it aggregates results (mean by default). The evaluator never needs to iterate over instances itself.

## EVOLVE Markers

The `EVOLVE_START` and `EVOLVE_END` markers define the region of code that the LLM will modify during evolution. Everything outside these markers is fixed scaffolding.

```python
# Fixed imports and setup (NOT evolved)
import json
import sys

# EVOLVE_START
def my_algorithm(data):
    """This function will be evolved by the LLM."""
    # Baseline implementation
    return data
# EVOLVE_END

# Fixed scaffolding (NOT evolved)
def main():
    input_data = json.loads(sys.argv[1])
    result = my_algorithm(input_data)
    print(json.dumps({"result": result}))

if __name__ == "__main__":
    main()
```

**Rules:**
1. Markers must be Python comments: `# EVOLVE_START` and `# EVOLVE_END`
2. The evolvable function must be self-contained between the markers
3. Imports needed by the evolvable function should be inside the markers
4. Keep fixed scaffolding (IO, validation, entry point) outside the markers
5. The function signature should remain stable (same args and return type)

## YAML Config Anatomy

See `config.yaml` for a fully annotated reference. Key sections:

| Section | Purpose |
|---------|---------|
| `project_name` | Unique task identifier |
| `background` | Problem description for LLM context |
| `providers` | LLM API configurations |
| `evaluator` | Evaluator module, dataset, metrics, timeout |
| `evaluator.dataset` | Data discovery mode: `directory`, `files`, or `glob` |
| `evolution` | Island GA parameters (population, generations, rates) |
| `coder` | Code generation prompt with EVOLVE markers |
| `version_control` | Git worktree settings for algorithm isolation |
| `repo_analyzer` | EVOLVE marker detection settings |

## Score Computation

The `score` field in `EvaluationResult` is the primary value used for evolution (higher is better).

- **MAXIMIZE tasks** (reward, accuracy): return score directly
  ```python
  score = mean_reward  # e.g., 200.0 for successful landing
  ```
- **MINIMIZE tasks** (tour length, time): negate the score
  ```python
  score = -tour_length  # e.g., -1234.56 (shorter tours rank higher)
  ```
- **Composite scoring**: weighted combination of metrics
  ```python
  score = (reward / 200.0) * 0.5 + success_rate * 0.3 + (1 - fuel / 100) * 0.2
  ```

## Worktree Compatibility

During evolution, each individual's algorithm lives in a git worktree. The file structure differs from local development:

| Mode | Algorithm location |
|------|-------------------|
| Local development | `project_root/my_algorithm/my_function.py` |
| Worktree (production) | `project_root/my_function.py` (flat) |

Your evaluator **must** handle both cases:

```python
# Worktree-compatible path resolution
algo_dir = project_root / "my_algorithm"
if not algo_dir.exists():
    if (project_root / "my_function.py").exists():
        algo_dir = project_root
    else:
        return EvaluationResult(score=0.0, metrics={}, success=False,
                                error_message="Algorithm not found")
```

## Checklist for Adding a New Task

1. **Create task directory** under `examples/applications/`
2. **Write algorithm file** with EVOLVE markers and baseline implementation
3. **Prepare data files** — one JSON per instance in a `data/` subdirectory
4. **Implement evaluator** with subprocess isolation (use this template)
5. **Write YAML config** with all sections filled in
6. **Write test_evaluator.py** to verify evaluator works in isolation
7. **Write debug_run.py** for full pipeline testing
8. **Test locally:**
   ```bash
   cd examples/applications/my_task_python/
   uv run python test_evaluator.py      # Test evaluator alone
   uv run python debug_run.py           # Test full pipeline
   ```
9. **Lint check:**
   ```bash
   uv run --python 3.12 ruff check examples/applications/my_task_python/
   ```

## Existing Task Reference

| Task | Variant | Algorithm File | Evaluator | Data Format |
|------|---------|---------------|-----------|-------------|
| TSP | Separate script | `tsp_algorithm/solve.py` | `tsp_evaluator.py` | `{"nodes": [[x,y], ...]}` |
| Sorting | Separate script | `sorting_algorithm/sort.py` | `sorting_evaluator.py` | `{"input": [5,3,8,...]}` |
| LunarLander | Self-spawning | `policy/choose_action.py` | `lunarlander_evaluator.py` | `{"seed": 6, "max_steps": 200}` |
