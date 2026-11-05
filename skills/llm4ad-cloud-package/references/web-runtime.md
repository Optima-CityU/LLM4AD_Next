# AutoDesign web runtime

Use this method inside the web platform. Build, verify and prepare a task directly
from the current working directory. The local portable delivery workflow does not
apply here.

## Read the current context

Call `mcp__llm4ad_autodesign__get_task_context` first. An independent conversation
may have no task yet. For an existing task use the returned `draft_path`, effective
configuration, input files and task family. Continue that working directory rather
than creating a second copy after task setup.

The workspace root is `/workspace/source`. New project files belong in
`draft/<slug>/`; optional reference documents are in `references/`. Read relevant
references before deciding the algorithm, dataset or evaluator. Reference files
and generated project files have independent views. Use the native file tools to
author code, data, configuration and a useful README. Do not invoke another builder
model. Credentials and actual model bindings are supplied by the platform. Global
Claude search tools remain disabled; use installed tools directly.

The project README can be opened with the generated project files. Keep its
explanations consistent with the user's reply language preference, including
the browser language when Auto is selected, unless a different document language
was requested. Preserve code identifiers, configuration fields and cited sources.
Keep the README concise: explain the actual goal, candidate I/O, data and how
to run the project. Explain design choices in the conversation; do not generate
separate design notes, comparison documents or reports unless explicitly asked.

## Verify the project

Call the installed tool on the current project directory:

```text
mcp__llm4ad_autodesign__validate_project(
  project_path="/workspace/source/draft/my-task",
  timeout_seconds=120
)
```

Choose timeouts from the actual workload. The platform uses an isolated ordinary
task runner, installs the project dependencies and runs configuration, source/data,
baseline, empty-candidate and task-specific evaluator checks. Do not replace these
with an import-only check in the Agent's file workspace.

Inspect the returned check results. Success returns `project_ready=true`, a
`project_revision`, and `validation_report.status="passed"`. The platform returns
the actual checks and cleans up its temporary execution files; no report document
is required. Fix actual failures and verify again; after a transport timeout
inspect the current state before retrying. Later input changes require another
check. Finish documentation edits before final verification. Explain the actual check outcome in the conversation without creating another
report or duplicating measurements in the README.
These checks establish that the initial project runs; they do not establish an
improvement from evolution.

## Continue to task setup

For a new design, explain the verified result and the right-side **Open task setup**
button. The user chooses when to continue. That action creates an unstarted task
directly from the current verified files and opens the existing configuration
steps. Users choose run models, review the complete configuration and submit from
the final step. There is no manual verification button.

An explicit request to create a task authorizes
`mcp__llm4ad_autodesign__prepare_task`. It returns the task/session and does not
start execution. An explicit request to run authorizes `start_task`; verify the
returned state before claiming the task is running.

For source, evaluator or dataset changes to an existing task, create/select its
unstarted child, read the canonical input files and effective configuration, edit
the returned working directory, then verify and call `prepare_task` with the child
`task_id`. Omission creates a new root for an independent session or a child for an
existing task. Preserve the child's configuration when editing files. For
configuration-only changes use `create_child_task` or `patch_child_parameters`.
`seed="best"` must copy the actual exported best code, not just label the child.

Canonical parameters are `Task.input_args`; canonical files are the task's stored
inputs. Editing a runtime output file alone does not change the next run. Follow
returned task IDs when creating, starting, stopping or analyzing a child. Use the
run-analysis Skill for results. Agent editing is locked while a task is queued or
running; monitor or stop it in the workbench and continue afterward.
