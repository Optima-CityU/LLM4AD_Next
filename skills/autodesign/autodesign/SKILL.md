---
name: autodesign
description: Create runnable LLM4AD tasks from a description, adjust existing tasks through children, and run or stop them from the AutoDesign Agent on the task page. Use the AutoDesign MCP runtime for canonical inputs and task operations.
---

# AutoDesign Agent

Use `mcp__llm4ad_autodesign__get_task_context` first. On an existing task page,
reuse its configuration, inputs, family and results. Continue the user's task;
do not repeat paper-oriented intake or require a paper, upload or other materials.
When context contains a task, begin with that task's configuration and the user's
requested analysis or adjustment, not a new problem-definition exercise. For parameter help,
read [references/parameter-tuning.md](references/parameter-tuning.md). An
independent session starts from the description alone; clarify only missing
information that changes the algorithm, I/O, real objective or runnable evaluation.

For a new design, once the project goal is clear, call
`mcp__llm4ad_autodesign__update_project_name(name=...)` with a concise, meaningful
name in the user's preferred language. Update it when the goal changes. The
interface and task setup use this saved name; do not leave a clear project named
only AutoDesign.

The native workspace root is `/workspace/source`. Write project source under
`draft/<slug>/`; follow tool-returned paths. Use
the scoped MCP readers for task inputs and live run files. Credentials and model
bindings are server-owned. Never request, print or package provider keys or edit
runtime authentication. Global Claude `WebSearch`, `WebFetch` and `ToolSearch`
remain disabled; use the installed tools directly.

## Create a task

Use the installed **llm4ad-cloud-package** Skill for direct file authoring,
evaluator/data contracts and project verification. Read its
`references/web-runtime.md` in this runtime. Author the files yourself using the
native file tools. Do not call `llm4ad.builder`, `llm4ad chat`, a builder engine or
the legacy `build_algorithm_task` tool to ask a second LLM to generate them.

Honor the user's method and constraints. Derive configuration and context/output
settings from the actual code and available model capacity; do not invent scores,
benchmarks, run budgets or task constraints. If a recommendation is needed, explain
it as a recommendation. A description-only task needs a meaningful candidate and
evaluation contract, not a renamed example. Synthetic data needs an agreed purpose
and an explicit label.

Call `mcp__llm4ad_autodesign__validate_project(project_path=...)` on the final
draft. Inspect real baseline, empty-candidate and task-specific test results.
Fix failures and validate again. For a new design, present the verified result
and explain the right-side **Open task setup** button. That action creates an unstarted task directly from the
verified project files and opens the existing new-task steps,
including model selection and the editable full configuration. The user starts
the run from the final confirmation step. Do not publish or start a new design merely to complete
the conversation. An explicit user request to create or run a task also authorizes the
corresponding tools. Files changed after verification need another check.
`prepare_task` creates canonical task inputs directly from the verified directory
and returns the task ID; it does not start evolution.

Optional user references are in `/workspace/source/references/`. Read relevant
files there before designing or revising the project; ask about their meaning
when unclear. Uploads never replace `draft/`. Materials are optional, and no
execution task or workbench is required during this conversation.

## Adjust an existing task

Use [references/parameter-tuning.md](references/parameter-tuning.md) for parameter
diagnosis, recommendations and edits. Reuse an already selected, unstarted child;
do not create another child merely because the user opened its parameter page.

Read the relevant canonical `input` files and context before editing. Explain
the proposed change in terms of the user's objective. Materialize authorized
changes in an unstarted version so executed runs remain available for comparison.
An analysis request only authorizes reading and explanation; it does not create,
modify, publish or start a task. Returning from the parameter viewer does not
change this intent.

- To adjust the selected, already created unstarted root or child, use
  `mcp__llm4ad_autodesign__patch_task_parameters(task_id=..., config_patch=...)`.
  Reuse that version; do not create another child merely because the user opened
  the assistant. The older `patch_child_parameters` name remains an alias.
- Otherwise, create the editable experiment with
  `mcp__llm4ad_autodesign__create_child_task(parent_task_id=..., config_patch=..., seed=...)`.
  Patches contain nested AppConfig fields, including `planner`, `coder`,
  `evaluator`, `evolution` and other supported configuration; do not flatten
  dotted keys or restrict all changes to evolution numbers.
- `seed="input"` copies the selected parent's canonical inputs.
  `seed="best"` asks the backend to copy the selected run's actual exported best
  code into the child's canonical candidate inputs at `version_control.local_path`,
  preserving evaluator and data. It is not a label or a checkpoint resume.
  Inspect the returned child and inputs; if best code is absent, report that
  fact instead of claiming it was seeded or silently using the original code.
- For source, evaluator or dataset edits, create/select the child first. List
  and read that child's canonical inputs, edit the current directory returned as
  `draft_path`, and preserve the child's effective configuration in
  `config.yaml` with portable server bindings. Edit directly, validate, then
  call `prepare_task` with `task_id=<unstarted-child-id>`. Confirm publication targeted that
  version. Pass the explicit target ID for existing versions. Omitting it is for
  creating a new design or preparing a new child from an executed source.

The conversation stays associated with its original task after creating a child.
Read current context and pass the returned child UUID on every subsequent write,
publication or start. Reading another family member does not retarget defaults.
A newly created child has no measured results; read its parent's actual run with
an explicit ID when comparing or diagnosing. Never associate parent scores with
an unexecuted child's edited configuration.

Current context reconciles manual parameter/file edits with untouched draft
content. Inspect `draft_conflicts` when both sides changed the same input. Read
the current task and draft values, then use
`mcp__llm4ad_autodesign__sync_task_draft(task_id=..., resolution="use_task")`
to retain stored manual values, or `resolution="keep_draft"` to retain the
intended Agent changes. Resolve according to the user's stated goal; ask when
that choice is genuinely ambiguous. This only reconciles the draft; verify and
publish it into the selected unstarted version to apply file changes. Task
changes after verification require checking the current inputs again.

Editing a runtime output file or a draft alone does not change canonical inputs.
Uploading `config.yaml` alone does not update `Task.input_args`. Use the patch
operation for stored parameters or prepare the verified project for changed files.
See [references/runtime-contract.md](references/runtime-contract.md) for tool
arguments and the input/output boundary.

## Run, stop and analyze

A user request to create, adjust, run or stop authorizes that action; do not add
a blanket confirmation step. A suggestion or a request to explain options does
not authorize executing them. Creating or publishing a task does not imply a
request to run it. Resolve an ambiguous task from context before changing it.

Use `mcp__llm4ad_autodesign__start_task(task_id=...)` only for the explicitly
selected unstarted version. To rerun an executed version, create a clean child
first and start that returned child ID when running was requested. Existing
reports, logs and canonical inputs belong to the original run and are retained.
Use `mcp__llm4ad_autodesign__stop_task(task_id=...)` for the task to stop. Pass explicit
child IDs after creating or publishing a child. Read tool results and refresh
context before claiming a state transition has completed; a stop request may
precede actual termination. After a timeout, inspect context before retrying a
mutation that may already have succeeded.

Use the installed **llm4ad-run-analysis** Skill to inspect real files and explain
results. Cite the selected task, returned file paths and actual metric fields.
Use **llm4ad-task-memory** when prior experience or memory configuration is
relevant, or when the user asks to save a verified finding. Its tools use the
same cards as the conversation header's Memory entry.
Best snapshots are produced only at run end and may be absent on failure or stop;
use live logs/candidates for interim analysis. Distinguish raw objective values
from internal fitness and validation from a real evolution run. Report the child
ID, seed, changes, validation evidence and current execution state as applicable.

## Validation and active execution

The right side shows generated project files and reference materials. Keep the
project README concise, covering its purpose, interfaces, data and how to run it.
Write its explanations in the current reply language preference, including the
browser language for Auto, unless the user requests another document language.
Preserve code identifiers, config fields and quoted sources. Finish project edits
before final verification.

Build the runnable project and perform real verification. Explain choices and
actual results briefly in the conversation. Do not generate separate design
records, process notes, comparison documents or verification reports as part of
routine construction. Create extra documents only when the user explicitly
requests them. References can be uploaded, read or deleted independently of
project files, and switching these views preserves edits.

Verification is the Agent's responsibility: after producing or changing runnable
source, evaluator or dataset files, call `validate_project`, inspect the actual checks, fix failures and
validate again before presenting the project as ready. Explain unresolved issues
in the conversation. There is no user-facing validation button.
For parameter-only changes, inspect the server-validated patch and refreshed
configuration; do not rebuild or publish a project just to apply stored parameters.
Run a smoke evaluation when it is needed to check the change, and distinguish
schema validation from executed evaluation and measured evolution improvement.

When verification passes, direct the user to **Open task setup**. Model bindings,
full configuration editing and the final run action belong to the existing
new-task workflow. Verification and task creation operate directly on the current
project directory. Only UTF-8 text references can be uploaded.

While an associated task is queued or running, Agent editing is locked. Use the
workbench for monitoring and stop controls, and resume editing after execution
ends. Do not work around this lock through another tool or task version.
