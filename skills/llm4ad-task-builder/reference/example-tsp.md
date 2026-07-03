# Reference: a complete, known-good task package

For a full, runnable example of an LLM4AD_Next task package, see the TSP benchmark
in this repository:

    examples/applications/tsp_benchmark_python/

It contains every piece a package needs, and is the best concrete reference to
adapt:

- `config.yaml` — the 10-section pipeline config (see `../config-template.yaml`
  here for an annotated skeleton).
- `tsp_evaluator.py` — a `BaseEvaluator` subclass (`PythonTSPEvaluator`): runs the
  algorithm as a subprocess, validates the tour, returns score + metrics.
- `tsp_algorithm/solve.py` — the algorithm with `# EVOLVE_START` / `# EVOLVE_END`
  around the function to evolve; reads a JSON CLI arg, prints a JSON result.
- `debug_run.py` — runs the full pipeline once (smoke test).
- `test_evaluator.py` — loads the evaluator via the dispatcher.
- `data/` — sample instances the evaluator scores against.

When building a new package, mirror this structure and the file contracts described
in `../../SKILL.md`, adapting names, the algorithm, the metrics, and the dataset to
the user's problem.

Note: on the platform (Web UI / backend), the package is generated programmatically
by the build engine and the same structure is produced automatically — this
reference is the shape that engine targets and that any coding agent should
reproduce.
