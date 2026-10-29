# Understand the task before generating a package

## Establish a concrete brief

First read the user's problem statement and supplied code, paper, data, and
existing evaluation instructions. Inspect representative data and the current
baseline when available. Summarize what is known, what is inferred, and what is
still missing. Do not start by copying the knapsack example and renaming it.

Before creating the candidate, evaluator or dataset, establish:

| Topic | Information that affects the runnable task |
| --- | --- |
| Actual problem | What decision/algorithm should improve, for whom, and under what constraints? |
| Candidate boundary | Function/CLI input and output, language, permitted changes, and code/data that must stay fixed. |
| Data | Real source, representative files and schema, scale, preprocessing, and any license/access requirement. |
| Evaluation | Feasibility checks, objective and units, maximize/minimize direction, aggregation, and baseline/reference result. |
| Runtime | Local and cloud OS/CPU, Python/compiler/solver/dependencies, available CPU/GPU, and realistic per-instance cost. |
| Search setup | Requested supported method, or a reasoned recommendation; model capacity, concurrency and run size. |
| Acceptance | What the local check must actually demonstrate before the user imports the task. |

Ask only about missing information that changes implementation or evaluation.
Group the most important questions into a short message. For example:

> I understand that the goal is to optimize vehicle routes while keeping the
> existing distance evaluator fixed. Before building the package, please clarify:
> (1) which input files represent the real workload and their capacity/time-window
> fields; (2) whether the objective is distance, profit, or a stated combination;
> (3) what the algorithm must return and the available evaluation time.

Present the proposed brief in plain language and let the user resolve remaining
material assumptions. If the user has already supplied a complete agreed brief
and asked for generation, proceed without requesting the same confirmation again.
Do not block on optional naming/style choices or an already authorized choice.
Record the resulting task/evaluation brief in the package README.

## Missing materials are not permission to invent a different task

- If actual data is unavailable, ask whether a synthetic pilot is acceptable.
  Label it as synthetic and state what it can and cannot test. Do not replace a
  real benchmark with tiny random data and claim the requested task is complete.
- If no baseline exists, propose a domain-appropriate executable starting point
  and explain why it is meaningful. A constant score, empty stub or unrelated
  sample algorithm is not a completed starting point.
- If the scoring definition is unclear, ask about the real objective and
  feasibility rules before implementing an evaluator. Do not choose an arbitrary
  metric merely because it is easy to compute.
- A small Island GA run can be a recommendation, not a substitute for
  understanding the problem. Explain unsupported requested methods and agree on
  an available method rather than silently changing it.

## Completion requires execution evidence

Run syntax/build checks, the task's `test_evaluator.py`, and
`scripts/validate_and_pack.py` on the final project. The helper evaluates every
configured dataset entry using a copied candidate directory; it also rejects an
empty candidate and verifies returned metrics. Read failures, fix their causes,
and rerun after edits. Merely importing the evaluator or creating a ZIP does not
meet acceptance.

The task-specific test must demonstrate known valid outputs and scoring,
representative invalid outputs, and candidate dependence. A finite but constant
score can pass generic shape checks, so use known better/worse solutions where
the domain permits. Enforce and test timeouts for processes that can hang.
Use the bundled test as an example, not as the test for an unrelated domain.

If data, dependencies, compiler/solver access or the target environment prevents
execution, report the specific missing evidence. Do not deliver an unverified ZIP
as ready to import. Record the host, inputs, baseline results and unresolved
target-platform differences; baseline success is not a promise of improvement
from later LLM evolution.
