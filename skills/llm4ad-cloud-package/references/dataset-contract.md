# Dataset contract

## What the platform loads

The dispatcher discovers **paths**, not a universal dataset schema. It calls the
evaluator once for each discovered entry, with that path in `cfg.data_path`.
The evaluator owns file parsing and domain validation. JSON, CSV, text, NumPy
arrays or scenario manifests are possible; choose what matches the actual task.
Do not force an existing benchmark into the knapsack JSON format.

Before generating data, agree on its source and purpose using
[task-definition.md](task-definition.md). The generated README must specify the
actual filenames/layout, encoding, fields/columns and types, shapes and units,
allowed ranges, missing-value handling, and at least one readable input/output
example. Document index conventions (e.g. zero-based item IDs) and sidecar files.
Reject malformed instances with a useful message instead of inventing values.

For the bundled example only:

```json
{"capacity": 10, "items": [{"weight": 6, "value": 30}, {"weight": 3, "value": 14}, {"weight": 4, "value": 16}, {"weight": 2, "value": 9}]}
```

| Field | Meaning |
| --- | --- |
| `capacity` | Finite nonnegative number in the same units as item weight; booleans are not numbers in this contract. |
| `items` | Nonempty array of objects, with their array positions used as zero-based indices. |
| `items[].weight` | Finite positive number. |
| `items[].value` | Finite nonnegative number. |
| Candidate `selected` | Array of unique integer indices in range; an empty selection is feasible. |

For this instance, `{"selected": [0, 1]}` has weight 9 and value/score 44.
`[0, 2]` has weight 10 and score 46; `[0, 1, 2]` is infeasible. These hand-checkable
outcomes validate feasibility and score direction, not a general optimality claim.

## Discovery configuration

All input paths in the cloud config are package-root-relative with `/`, on every
host OS. The runtime strips the ZIP's one enclosing folder before running.
The following are alternative values for **`evaluator.dataset`**:

```yaml
# Each listed file is evaluated; preserve intentional repetitions/order.
mode: files
files: [data/evolution/a.json, data/evolution/b.json]
```

```yaml
# Every file in this directory; keep README/sidecars outside it.
mode: directory
path: data/evolution
recursive: false
```

```yaml
# Pattern relative to the package root.
mode: glob
pattern: data/evolution/*.json
```

```yaml
# Equivalent glob with an explicit search directory.
mode: glob
path: data/evolution
pattern: "*.json"
```

`directory.recursive` defaults to **true** in the platform; choose it explicitly.
`glob` uses `pattern` (including `**` for recursion), not the `recursive` flag.
If `glob.path` is omitted or `.`, the base is the package root. A broad `*` can
also match directories in the current dispatcher: choose a pattern matching only
the intended files. The packaging helper rejects a non-file match rather than
silently validating a different collection. Avoid accidental duplicate entries.

Keep trusted data outside `version_control.local_path`. For a multi-file case,
use a small per-case manifest and resolve its referenced files relative to that
manifest (or another documented package location), not the process's working
directory or a local user's home. Include all required referenced files.

## Scope, fairness and aggregation

Document whether the included data is a smoke test, an evolution benchmark, or a
separate final evaluation set. Only include splits when the problem needs them;
do not silently mix a claimed held-out test set into the evolution glob. The
platform does not automatically create train/validation/test splits. A few tiny
smoke instances demonstrate execution, not research-level effectiveness.

Record original source/version or how synthetic data was generated, including
parameters and fixed seeds. Use the same instances and random seeds to compare
baseline and candidates. The platform's `random_seed` does not automatically seed
custom subprocesses or simulation environments; pass the task's seeds explicitly.
Avoid per-evaluation downloads and regenerating a different benchmark each time.

For multiple files, the dispatcher currently averages the **successful** results
with one equal contribution per file entry. It does not automatically weight by
row count, normalize different scales, or mark an entire candidate failed when
only some files fail. A JSON containing 1,000 cases is still one dispatcher entry.
Define internal aggregation in the evaluator and document it. Use comparable
scales or a justified normalization when averaging heterogeneous instances.

The packaging helper requires every selected baseline entry to pass separately.
If the task requires candidate-level all-or-nothing success or a particular
weighted/worst-case aggregation, use one suite manifest and evaluate its cases
inside a custom evaluator, returning one result with the agreed policy. Report
invalid outputs honestly; never mark a failed execution successful just to get
an aggregate score. Set a timeout sufficient for the entire manifest evaluation.

## Evidence required before delivery

Include at least one representative, usable instance in a Cloud package and
actually run all entries selected by its configuration. A simulation scenario
containing seeds/settings can serve as the instance for a data-free problem.
The general runtime supports no-file evaluations, but this packaging workflow
requires concrete inputs so its baseline verification is reproducible.

For external/large data, arrange the actual local inputs and target upload/runtime
availability with the user. A filename or download URL in README is not proof
that a runnable package has the dataset. If data is missing, do not generate a
passing report using unrelated placeholder instances.
