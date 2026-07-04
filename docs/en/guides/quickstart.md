# Quick Start Guide

This guide walks you through your first algorithm-design experiment with LLM4AD. There are two paths:

- **Path A — Automatic build (recommended for first-time users):** Describe the task in natural language and let `llm4ad chat` generate the evaluator, algorithm template, and config for you. ~5 minutes.
- **Path B — Manual build:** Hand-write the evaluator and config yourself. More work, but gives you full control. Use this once you understand what the auto build produces.

Both paths end with the same `llm4ad run` command.

## Prerequisites

- ✅ Python 3.12 or higher
- ✅ LLM4AD installed (see [Installation](installation.md))
- ✅ An API key for OpenAI, Anthropic, or any OpenAI-compatible endpoint

---

## Path A — Automatic Build (`llm4ad chat`)

`llm4ad chat` is a guided wizard. You describe the problem; it produces a complete, runnable application directory. No Python or YAML needed up-front.

### A.1 — Configure a default provider

Create `~/.llm4ad/settings.yaml` once. Future `llm4ad chat` and `llm4ad run` calls reuse this:

```yaml
providers:
  - name: "default"
    type: "openai_compatible"
    base_url: "https://api.openai.com/v1"
    api_key: "${OPENAI_API_KEY}"
    model: "gpt-4o"
```

Then export the key:

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

For Anthropic, set `type: "anthropic"` and `${ANTHROPIC_API_KEY}` instead. A full example lives in [`examples/config/settings.yaml`](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/examples/config/settings.yaml).

### A.2 — Run the wizard

```bash
llm4ad chat
```

The wizard will:

1. Ask what algorithm you want to evolve (e.g. *"sorting that minimizes comparisons"*).
2. Ask about input/output format and evaluation criteria.
3. Generate the full task package: evaluator, algorithm template with `EVOLVE` markers, sample data, debug runner, and `config.yaml`.
4. Self-validate by running the generated `debug_run.py` and `test_evaluator.py`.
5. Offer to launch the pipeline immediately.

You can also skip the conversation if you already know what to build:

```bash
llm4ad chat --prompt "evolve sorting algorithms that minimize comparisons" \
  --output ./my_task/
```

The result is a directory like:

```
my_task/sorting/
├── config.yaml                    # ready to run with `llm4ad run`
├── sorting_evaluator.py           # auto-generated evaluator
├── sorting_algorithm/sort.py      # algorithm template with EVOLVE_START/END markers
├── debug_run.py                   # quick smoke test
├── test_evaluator.py              # end-to-end evaluator test
└── data/sample/                   # sample inputs
```

### A.3 — Run it

```bash
llm4ad run my_task/sorting/config.yaml
```

Output and result inspection are the same as Path B — skip ahead to [Step 5: View Results](#step-5-view-results).

For the wizard's full flag list, validation pipeline, and `--code-path` mode (adapting existing code), see [Auto Builder](auto-builder.md) and [CLI § chat](cli.md#chat).

---

## Path B — Manual Build

If you want to wire everything by hand, follow these five steps. The result is functionally the same as what the wizard generates.

### Step 1: Set Up Your API Key

Set your LLM provider API key as an environment variable:

```bash
# For OpenAI
export OPENAI_API_KEY="your-openai-api-key"

# Or for Anthropic
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

### Step 2: Create a Simple Evaluator

Create a file `my_evaluator.py` with a simple sorting algorithm evaluator:

```python
"""Simple sorting algorithm evaluator."""

from llm4ad.evaluator.base import PythonEvaluator, EvaluationResult, Metric, MetricType


class SortingEvaluator(PythonEvaluator):
    """Evaluates sorting algorithms."""

    def __init__(self, config):
        super().__init__(config)

    @property
    def name(self) -> str:
        return "sorting_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        return [
            Metric(name="correctness", type=MetricType.MAXIMIZE, description="Fraction of correctly sorted arrays"),
            Metric(name="avg_time", type=MetricType.MINIMIZE, description="Average sorting time in seconds"),
        ]

    async def evaluate(self, cfg) -> EvaluationResult:
        """Evaluate a sorting algorithm."""
        import time
        import random

        # Test data
        test_cases = [
            [3, 1, 4, 1, 5, 9, 2, 6],
            [1, 2, 3, 4, 5],  # Already sorted
            [5, 4, 3, 2, 1],  # Reverse sorted
            [42] * 10,  # All same
            random.sample(range(100), 20),  # Random
        ]

        total_time = 0.0
        correct = 0

        for arr in test_cases:
            # Make a copy to sort
            arr_copy = arr.copy()
            expected = sorted(arr)

            # Time the sorting
            start = time.time()

            try:
                # Execute the sorting function
                # Assuming the algorithm defines a 'sort' function
                exec_globals = {"array": arr_copy}
                exec(cfg.project_root + "\nresult = sort(array)", exec_globals)
                sorted_arr = exec_globals["result"]

                elapsed = time.time() - start
                total_time += elapsed

                correct += 1 if sorted_arr == expected else 0
            except Exception as e:
                # If execution fails, count as incorrect
                total_time += 1.0  # Penalty

        correctness = correct / len(test_cases)
        avg_time = total_time / len(test_cases)

        return EvaluationResult(
            score=correctness * 100 - avg_time * 10,  # Combined score
            metrics={
                "correctness": correctness,
                "avg_time": avg_time,
            },
            success=True,
        )
```

### Step 3: Create a Configuration File

Create `quickstart_config.yaml`:

```yaml
# Quick Start Configuration

# Project settings
project_name: "quickstart-demo"
base_dir: "./runs"
random_seed: 42

# LLM Provider configuration
providers:
  - name: "default"
    type: "openai"
    api_key: "${OPENAI_API_KEY}"  # Uses environment variable
    model: "gpt-4o"
    temperature: 0.7
    max_tokens: 4096
    timeout: 60.0
    max_retries: 3

# Evaluator configuration
evaluator:
  module: "my_evaluator:SortingEvaluator"  # Import path to our evaluator
  timeout: 30.0
  max_retries: 2
  parallel: true
  batch_size: 5

# Evolution settings
evolution:
  type: "island_ga"
  planner_type: "llm_evolution"
  population_size: 10  # Small population for quick demo
  max_generations: 5   # Few generations for quick demo
  elite_ratio: 0.2
  mutation_rate: 0.3
  crossover_rate: 0.5
  selection_strategy: "tournament"
  tournament_size: 3
  early_stop_patience: 10
  early_stop_threshold: 0.01
  checkpoint_interval: 2
  max_checkpoints: 3

# Planner settings
planner:
  provider: "default"

# Coder settings
coder:
  type: "custom"
  provider: "default"
  timeout: 120.0
  max_retries: 2

# Memory settings
memory:
  max_entries: 1000
  similarity_threshold: 0.8

# Workspace settings
workspace:
  auto_create: true

# Logging settings
logging:
  level: "INFO"
  console: true
```

### Step 4: Run Your Experiment

Execute the experiment using the CLI:

```bash
llm4ad run quickstart_config.yaml
```

You should see output similar to:

```
╭─────────────────────────────────────────────────────────────╮
│  LLM4AD - LLM for Algorithm Design                        │
╰─────────────────────────────────────────────────────────────╯

Project: quickstart-demo
Run ID: a1b2c3d4
Workspace: ./runs/quickstart-demo/a1b2c3d4

Configuration loaded successfully
Starting evolution...

Generation 1/5
  Population: 10 individuals
  Best score: 85.2
  Avg score: 72.4

Generation 2/5
  Population: 10 individuals
  Best score: 88.7
  Avg score: 76.1

...

Evolution completed!
Best snapshot: ./runs/quickstart-demo/a1b2c3d4/best
```

### Step 5: View Results

After the experiment completes, check the results:

```bash
# View the best algorithm's source
cat ./runs/quickstart-demo/a1b2c3d4/best/code/<your-evolved-file>.py

# View structured metadata (score, generation, lineage, evaluation metrics)
cat ./runs/quickstart-demo/a1b2c3d4/best/metadata.json

# Human-readable one-page summary
cat ./runs/quickstart-demo/a1b2c3d4/best/summary.txt

# View logs
cat ./runs/quickstart-demo/a1b2c3d4/logs/run.log
```

---

## Understanding the Output

Both paths produce the same workspace layout.

### Directory Structure

LLM4AD automatically creates a well-organized workspace:

```
./runs/quickstart-demo/a1b2c3d4/
├── best/                # Stable snapshot of the best individual (and Pareto front for MEoH)
│   ├── code/                   # Plain copy of the best worktree
│   ├── metadata.json           # Score, generation, parents, evaluation metrics
│   ├── summary.txt             # One-page human-readable summary
│   └── pareto/                 # Only for MEoH: one subdir per archive member
├── state/               # Cached state (e.g. evolution_state.json) for resume
├── logs/                # Log files
│   └── run.log
├── checkpoints/         # Evolution checkpoints (per-generation, JSON)
├── generated/           # Every generated algorithm (per-individual JSON + Markdown)
├── worktrees/           # Live git worktrees built by the coder during evolution
└── temp/                # Temporary files
```

### Evolution Summary

The `evolution_summary.json` contains:

```json
{
  "best_score": 92.5,
  "best_generation": 4,
  "total_generations": 5,
  "population_size": 10,
  "total_evaluations": 50,
  "convergence_curve": [72.4, 76.1, 81.3, 88.7, 92.5],
  "best_metrics": {
    "correctness": 1.0,
    "avg_time": 0.0032
  }
}
```

## Next Steps

Now that you've run your first experiment, explore more advanced features:

### Try Different Problems

- [Sorting Algorithm Example](../examples/sorting.md) - Design better sorting algorithms
- [TSP Example](../examples/tsp.md) - Explore TSP algorithms
- [Symbolic Regression Example](../examples/symbolic-regression.md) - Discover mathematical expressions

### Advanced Configuration

- [Configuration Guide](configuration.md) - Learn all configuration options
- [Writing Evaluators](evaluators.md) - Create custom evaluators
- [Advanced Configuration](advanced.md) - Advanced usage patterns

### Customize Your Workflow

- Use different LLM providers (OpenAI, Anthropic, or custom)
- Adjust evolution parameters for your problem
- Implement custom selection strategies
- Add multi-objective optimization

## Common Issues

### "API key not found"

Make sure you set the environment variable before running:

```bash
export OPENAI_API_KEY="your-key"
llm4ad run quickstart_config.yaml
```

### "Module not found: my_evaluator"

Ensure `my_evaluator.py` is in the current directory or Python path:

```bash
# Add current directory to Python path
export PYTHONPATH="${PYTHONPATH}:."
llm4ad run quickstart_config.yaml
```

### "Out of memory"

Reduce population size or disable parallel evaluation:

```yaml
evolution:
  population_size: 5  # Smaller population

evaluator:
  parallel: false  # Disable parallel evaluation
```

## Tips for Success

1. **Start Small**: Begin with small populations and few generations
2. **Monitor Progress**: Check logs regularly to understand what's happening
3. **Adjust Temperature**: Lower temperature (0.3-0.5) for more deterministic code
4. **Set Timeouts**: Reasonable timeouts prevent hanging on bad code
5. **Use Checkpoints**: Enable checkpointing to resume interrupted runs

## Getting Help

- 📖 [Documentation Home](../index.md)
- 💬 [Discussions](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- 🐛 [Issue Tracker](https://github.com/Optima-CityU/LLM4AD_Next/issues)
