# LLM4AD Task Package Templates

This directory contains two complete, verified task package templates. Copy one as a starting point for your own task.

## Which template to use?

### TSP Template (one-shot solver pattern)
**Use for:** Optimization problems, planning problems, single-shot algorithms
- Algorithm reads one problem instance, outputs one solution
- Evaluator runs algorithm as subprocess, parses JSON output
- Examples: TSP, knapsack, scheduling, bin packing, graph coloring

**Structure:**
- Algorithm: standalone script with EVOLVE markers, reads JSON input, prints JSON result
- Evaluator: spawns algorithm subprocess per instance, validates output, computes metrics
- Data: one JSON file per problem instance

### LunarLander Template (RL rollout pattern)
**Use for:** Reinforcement learning, control problems, interactive environments
- Algorithm defines a policy function (state → action), called many times per episode
- Evaluator spawns itself as subprocess, runs gymnasium episodes internally
- Examples: Game AI, robot control, resource management, adaptive systems

**Structure:**
- Algorithm: policy function with EVOLVE markers, called per timestep
- Evaluator: `__main__` block is the subprocess entry, runs gym rollouts internally
- Data: configuration files for environment parameters (optional)

## Files in each template

Both templates are complete, self-contained packages:
- `config.yaml` — evolution config (providers, evaluator, dataset, version control)
- `<name>_evaluator.py` — evaluator that scores candidates
- `<algo_dir>/<algo>.py` — algorithm with EVOLVE markers
- `debug_run.py` — smoke test (runs full pipeline once)
- `test_evaluator.py` — unit test (loads evaluator via dispatcher)
- `requirements.txt` — dependencies
- `data/sample/*.json` — 2-3 test instances

All contracts match `../api-contract.md` exactly.
