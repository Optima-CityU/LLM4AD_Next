# Knapsack cloud task

This is a small, standard-library 0/1 knapsack example for the standalone
`llm4ad-cloud-package` Skill. Python 3.12 and the matching `llm4ad-next` platform
are required; the task has no additional Python dependencies.

`algorithm/solver.py` contains a greedy value-density baseline inside EVOLVE
markers. Its JSON input is `{capacity, items: [{weight, value}, ...]}` and output
is `{selected: [unique item indices]}`. Positive weights and nonnegative values
are required. The evaluator recomputes capacity use and total value; its score
is total value, maximized. `total_weight` is a diagnostic metric, not a second
objective in this example. The two baseline sample scores are 44 and 79; these
are sanity checks, not claims of optimality.

Run `python test_evaluator.py` with the verification environment's interpreter.
It checks both real samples, known better/worse and empty feasible selections,
invalid/duplicate/out-of-range indices, capacity violations, malformed output,
missing candidates, syntax/runtime failures and actual process timeout handling.
Then use the Skill's `scripts/validate_and_pack.py` on this directory to produce
the ZIP and report. That command validates the extracted package in a copied
candidate layout, requires no model key, and does not perform LLM evolution.

`config.yaml` starts with a small Island GA configuration (2 islands, 4
individuals each, 5 generations), `coder.type: custom`, and memory disabled.
The included data is for a smoke test; add representative data for a real study.
`providers: []` is intentional: select the model on the cloud platform.

In the web UI, open a project and choose **New Task → Import ZIP**, upload the generated
archive, and import it as a new task in that project. Select a platform
model in the task configuration before running. For older deployments, extract
and upload the entire task directory, then import its YAML at confirmation.

To optionally run paid local evolution, set `LLM_BASE_URL`, `LLM_API_KEY`, and
`LLM_MODEL`, then execute `python debug_run.py`. It binds the provider in memory;
do not put credentials in the package. Keep generated `runs/` outside the folder
used for packaging, or build from a clean source directory.

## Context and output assumptions

This small Python task explicitly captures 80 lines before/after each EVOLVE
block, allows 16,384 tokens of custom-coder project context, and reserves 8,192
output tokens for the coder and each ordinary planning sampler. Mutation parent
context allows 48,000 characters. The seed is short; the extra room accommodates
longer evolved implementations and their helper code.

Choose a model supporting at least a 64K context and 8K output, and check the
complete prompt size as candidates grow (especially with multiple parents).
These settings describe different parts of the prompt and are not summed as if
they were all token windows. Local baseline checks make no model calls and do not
verify a model's capacity. Re-size explicitly if adapting this task to larger code.
