# Cloud package configuration

## Package and runtime boundaries

One package describes one executable evolution task. The web platform groups tasks
under a project; select or create a project, then import the package through
**New Task → Import ZIP**. A package is not a database export and carries no account IDs.

```text
my-task.zip
└── my-task/
    ├── config.yaml
    ├── README.md
    ├── requirements.txt
    ├── validation.json             # Written by validate_and_pack.py
    ├── test_evaluator.py
    ├── debug_run.py                # Optional real-model local run
    ├── my_evaluator.py
    ├── algorithm/
    │   └── solver.py               # Actual implementation with EVOLVE markers
    └── data/sample/
        ├── instance_001.json
        └── instance_002.json
```

Use one top-level directory: the current web runtime removes that directory when
downloading stored inputs into `/task/data`. Keep paths inside `config.yaml`
relative to its directory. The helper requires `config.yaml` at this root.

`version_control.local_path: algorithm` means only the CONTENTS of `algorithm/`
are copied into each candidate worktree. Consequently the evaluator uses
`Path(cfg.project_root) / "solver.py"`, not `... / "algorithm" / "solver.py"`.
Put every helper required by the algorithm in the candidate directory too. Keep
trusted scoring logic and benchmark data outside the evolvable directory.

## Isolated verification environment

Use the native commands in [platforms.md](platforms.md) on Windows PowerShell,
macOS or Linux. Both the local verification host and cloud target matter.

For a checkout matching the target cloud deployment:

```bash
uv venv --python 3.12 /tmp/my-task-check-env
uv pip install --python /tmp/my-task-check-env/bin/python /path/to/LLM4AD
uv pip install --python /tmp/my-task-check-env/bin/python -r /path/to/my-task/requirements.txt
uv pip check --python /tmp/my-task-check-env/bin/python
/tmp/my-task-check-env/bin/python <skill-dir>/scripts/validate_and_pack.py \
  /path/to/my-task --output /path/to/deliverables/my-task.zip
```

Use a uniquely named environment rather than overwriting an existing one. On
Windows use the environment's `Scripts/python.exe`. A matching released
`llm4ad-next==<target-version>` may replace the checkout; version numbers alone do
not identify an unreleased deployment, so record its revision in the task README.
Install method extras, e.g. `<checkout>[dyca]` or `<checkout>[meoh]`, when selected.

`requirements.txt` lists task-specific third-party dependencies and compatible
versions, not the platform itself. An `import llm4ad` refers to `llm4ad-next`, not
the unrelated PyPI distribution `llm4ad`. Neither platform distribution belongs
in the task requirements that the cloud task runner auto-installs. Do not add
speculative heavyweight libraries solely because evolution might use them later.

The helper evaluates using the interpreter that launches it. It does not create a
virtual environment or run a package installer. Install dependencies first and
use `uv pip check` to detect incompatible declared requirements. Test native
libraries in a matching Linux environment if verification was performed on macOS
or Windows. The helper records its environment and does not claim a container test.

## Configuration sections

See `../assets/knapsack/config.yaml` for a complete valid cloud configuration.
Omit advanced fields whose defaults are suitable, except context/output sizing,
which must be chosen explicitly for the project. Do not copy parameters from a
different evolution method. The installed `AppConfig.model_json_schema()` is the
source of truth for accepted names and defaults.

| Section/field | Meaning and cloud behavior |
| --- | --- |
| `project_name` | Human-readable task slug; used locally for output organization. |
| `description_zh`, `description_en` | Optional descriptions displayed by the web template workflow; not execution parameters. |
| `background` | Actual problem, function I/O, objective, feasibility constraints and scoring; passed to the planning model. |
| `random_seed` | Random seed for the platform. Seed stochastic evaluation/data generation separately where necessary. |
| `base_dir`, `run_id` | Local output location and run identifier. Web execution overrides them with the task's managed directory and run ID. |
| `path_resolution` | Normally `config_dir`; use portable paths relative to the configuration file. |
| `providers` | Set `[]` in cloud packages. The web platform binds the user's selected model at runtime. |
| `planner` | Chooses the planning implementation, parent/sampler selection and optional candidate build script. |
| `coder` | Generates candidate changes. `type: custom`, `provider: default` works with the platform's selected model. Other deployed coder backends are `claude_code` and `opencode`. |
| `evaluator` | Scoring implementation, dataset discovery, timeout, evaluation concurrency and metric names. |
| `evolution` | Registered search method and its own parameters; see `algorithms.md`. |
| `version_control` | Use `type: git_worktree`, `enabled: true`, `auto_initialize: true`; `local_path` points to the candidate directory. |
| `repo_analyzer` | Use `evolve_detector`; `include` must match the candidate language (e.g. `['*.py']`). |
| `memory` | Enable memory and automatic extraction by default; see [memory.md](memory.md). Local baseline validation makes no memory/model calls, so it does not require disabling memory. Web import inherits the current account/project settings. Never package remote memory credentials or account identities. |
| `multimodal` | Optional rendering/behavior configuration. Ordinary text-only tasks can set `enabled: false`, `behavior_storage: none`. |
| `workspace` | Managed output subdirectories. Usually omit and use platform defaults. These are not input paths. |
| `logging` | Runtime log settings; generally `level: INFO`. Do not hardcode a local machine's log path. |
| `embedding` | Needed by methods/features that use embeddings. Keep it portable; bind required services on the target environment rather than embedding credentials. |

### Planner, coder, and build configuration

For `island_ga`, `diverse_island_ga`, and `dyca`, set `planner.type: llm_evolution`.
Other methods have dedicated planners listed in `algorithms.md`. The three
ordinary island samplers are `init_sampler`, `mutation_sampler`, and
`crossover_sampler`; each entry is `{name: <registered-name>, config: {}}`.

Set `planner.provider` and `coder.provider` to `default`. The web binding field
`provider_model` is filled by the web UI; do not populate it with an API model
name. `coder.default_extension` is `py` for Python, and any custom prompt must
preserve the real function interface and EVOLVE boundaries. A literal `{code}`
inside `coder.prompt_template` is the platform's code substitution variable.

### Context and generation sizing

Inspect the candidate source, function signatures, imported helpers, and expected
growth during evolution before choosing these settings. The defaults are not a
task-specific sizing decision: `evolve_detector` captures only 5 lines on each
side, and the custom coder uses a 4,096-token project context and output budget.
Always write explicit values in a generated Cloud package.

| Setting | Actual scope | Starting point for a small multi-file algorithm |
| --- | --- | --- |
| `repo_analyzer.context_lines_before` / `context_lines_after` | Surrounding source lines captured for each EVOLVE block, not a model token window | `80` / `80`; inspect that signatures, imports and nearby helpers are included. Increase when those dependencies lie farther away. |
| `coder.context_max_tokens` | Project source injected by `type: custom`; currently approximated at 4 characters per token | `16384`; consider `32768` for larger candidates when the selected model supports the complete prompt. |
| `coder.max_gen_tokens` | Custom coder's generated replacement/output, separate from its input context | `8192`; consider `16384` for large replacements supported by the model. |
| `planner.samplers[].config.max_tokens` | Output budget for that sampler's planning request | `8192` for the ordinary samplers; set per sampler rather than inventing `planner.max_tokens`. |
| Mutation sampler `config.parent_context_char_budget` | Parent code/feedback budget in **characters** for `mutation_sampler` | `48000`; size for the actual parent and feedback. This is not a universal setting shared by other samplers. |

These are starting values, not fixed limits for every generated project. The
bundled small example uses them explicitly to leave room for longer evolved
candidates. Measure the relevant source length, review the repository analyzer's
captured context, and reserve space for problem instructions, parents, evaluation
feedback, memory and generated output. Crossover includes multiple parents, so
account for all of them. The custom coder's character estimate is not an exact
token count, especially for non-English text.

Keep total prompt tokens plus requested output comfortably within the chosen
model's context window and its output cap. If no target model is known, document
the assumed capacity and require checking it when binding the platform model;
do not claim that setting `16384` changes the model's actual context window. For
large source trees, first remove irrelevant input files or clarify the evolvable
boundary, then increase the applicable budget if needed. Include required helpers
in the candidate directory; increasing the number alone cannot supply absent files.

`providers[].max_tokens` is an **output** setting, not an input-context setting.
Cloud packages still use `providers: []`; do not add credentials or a fictitious
model-context property to get a larger window. Explicit custom-coder and sampler
values override their inherited defaults. Dedicated planners (`meoh_evolution`,
`eoh_evolution`, `reevo_evolution`, `mcts_ahd_evolution`) configure their own
samplers: inspect that planner's accepted settings instead of copying ordinary
island samplers into it. The repository analyzer settings remain relevant.
For `claude_code` / `opencode`, custom-coder-only fields do not configure the CLI's
context window; use the deployed CLI and selected model's supported behavior.

Record the chosen settings and rationale in README, including a model assumption
such as “a model supporting at least a 64K context and 8K output” for a modest task
after checking its estimated full prompt. Baseline-only local verification makes
no model calls, so it cannot prove that the bound model accepts these settings.

### Candidate builds

Python tasks normally omit `planner.build`. Compiled tasks can use:

```yaml
planner:
  build:
    script:
      linux: "g++ -O2 -std=c++17 solver.cpp -o solver"
    timeout: 60
```

Build commands run INSIDE the candidate directory. A successful macOS build does
not prove that the required Linux command works. The packaging helper executes
the build script for the local OS, if supplied, and then evaluates its artifacts.

### Evaluator and dataset

Read [evaluator-contract.md](evaluator-contract.md) for the exact API and failure
semantics and [dataset-contract.md](dataset-contract.md) for instance contents,
discovery, provenance and aggregation. Choose these against the agreed real task.

For a Python evaluator, use `type: custom`, `module: my_evaluator.py:MyEvaluator`,
and `metrics` matching its `Metric.name` definitions. Use a relative `.py` path
inside the package for this mode. `timeout` is seconds per instance, `parallel`
enables concurrent evaluations and `batch_size` caps instance concurrency. The
packaging helper deliberately verifies instances serially for clear results.

Three dataset modes are supported, and the helper verifies every discovered file:

```yaml
# Explicit set
dataset: {mode: files, files: [data/sample/a.json, data/sample/b.json]}
# Directory; set recursive deliberately
dataset: {mode: directory, path: data/sample, recursive: false}
# Glob rooted at the package directory
dataset: {mode: glob, pattern: "data/sample/*.json"}
# Equivalent glob relative to an explicit directory
dataset: {mode: glob, path: data/sample, pattern: "*.json"}
```

The default packaging workflow requires at least one included instance. For a
simulation/policy task, each instance can describe an environment seed/scenario
rather than contain the entire simulation. The helper also accepts registered
`executable` or `solver` evaluator configurations through the same dispatcher;
their toolchain/backend must be installed and their input files must be included.
Keep evaluator-specific path fields portable and test them in the copied layout.

### Local model testing is separate

`providers: []` is intentional for cloud binding and is sufficient for baseline
verification. A real local `LLM4AD.run()` also needs a provider named `default`.
Use the example's `debug_run.py` with `LLM_BASE_URL`, `LLM_API_KEY`, and
`LLM_MODEL` set in the environment. It adds the provider in memory without
rewriting the cloud config and performs paid model calls. The archive must not
include the actual key or a local `.env`.

## Verification report

`validation.json` and the sibling report record the tested platform version,
interpreter/OS, algorithm, config path, actual sample scores/metrics, empty-candidate
check, the executed `test_evaluator.py` outcome, and SHA-256 of each packaged input file.
The helper requires that task-specific test script to pass in a separate process
before writing the deliverable. The overall `--timeout` covers baseline checks
and the test script together. `full_evolution_run` remains false:
passing baseline evaluation does not certify generated code or algorithm quality.
The hash table excludes `validation.json` itself. The helper validates the extracted
snapshot, so an edited source directory requires a new archive and new report.

The report is evidence from the local machine, not an authorization token or a
server attestation. Web import should verify the actual package and parse its
config rather than trust a user-supplied `status: passed` on its own.
