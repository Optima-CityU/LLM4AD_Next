# AutoDesign runtime contract

The MCP server is installed as `llm4ad_autodesign`. Invoke tools using the full
`mcp__llm4ad_autodesign__` prefix. Results are objects returned by the tools;
inspect their actual contents and paths rather than guessing a publication path
or writing a fake result file.

| Tool suffix | Arguments and effect |
| --- | --- |
| `get_task_context` | `task_id=None`; live task, sanitized effective config, family, stats, draft path and validation state. A standalone session may have no task. |
| `get_memory_context` | `task_id=None`; memory availability, configured scopes, redacted defaults and task memory settings. |
| `list_memory_cards` | `scope="user"`, `task_id=None`, `page=1`, `page_size=20`; existing full cards and pagination from user, associated project or task-family memory. |
| `save_memory_card` | `content`, `title=""`, `memory_type="general_insight"`, `scope="user"`, `task_id=None`, `memory_id=None`, `tags=None`, `metadata=None`; create or update an experience in the same memory store as the UI. |
| `update_project_name` | `name`; update the current design title from the conversation. Task setup uses this display name. |
| `list_task_files` | `area='run'`, `task_id=None`, `directory=''`; list real scoped files. Areas are `input`, `run`, `draft`, `references`, `analysis`. Each entry includes `path`, `size`, and optional `modified_time` (UTC ISO timestamp; `null` for storage inputs when unavailable). |
| `read_task_file` | `path`, `area='run'`, `task_id=None`, `start_line=None`, `end_line=None`; full file by default. Explicit ranges are one-based and inclusive; inspect `total_lines`, `start_line`, `end_line`. |
| `create_child_task` | `parent_task_id`, `config_patch=None`, `seed='input'` or `'best'`, `name=None`; create a child and copy canonical inputs. `best` must actually replace the candidate files with exported best code on the server. |
| `patch_task_parameters` / `patch_child_parameters` | `task_id`, `config_patch`; merge nested AppConfig fields into an explicitly selected unstarted root or child. |
| `sync_task_draft` | `task_id`, `resolution=None`, `"keep_draft"` or `"use_task"`; merge manual changes and resolve reported conflicts without publishing canonical inputs. |
| `validate_project` | `project_path`, `timeout_seconds=120`; run genuine validation in an isolated clone of the task runner, using the shared Cloud evaluator checks. Set timeout from the real workload when needed. |
| `prepare_task` | `project_id=None`, `task_id=None`, `name=None`, `enable_memory=None`; create a task from the current verified project directory. A target task must be an unstarted version. Without a target, create an independent root or a new child for an existing task. |
| `start_task` | Required `task_id`; start an unstarted version. An executed source requires a new child. |
| `stop_task` | Required `task_id`; stop the explicitly requested active version. |

The backend scopes access to the workspace and task family. `/workspace/source`
is the workspace root; edit the returned `draft_path`. References are separate.
Verification returns `validation_report`, `project_ready` and `project_revision`.
Inspect the actual check results; preparation uses the files matching that
verified revision directly. The working project stays the same after task creation.
Verification returns its execution status and measurements directly; temporary
execution files are cleaned up and no report file is published to the source
workspace or object store. Explain the result in the conversation instead of
creating design records or other process documents.

The bridge's HTTP wait is configurable with
`LLM4AD_AUTODESIGN_TIMEOUT_SECONDS`. Validation waits for at least the requested
worker timeout plus `TASK_DEP_INSTALL_TIMEOUT` (the execution service defaults
to 600 seconds) and 30 seconds for completion. These are transport/install
allowances, not evolution run budgets. A timeout is not proof of a failed action;
refresh context before retrying a mutation.

Canonical task configuration is `Task.input_args`, and canonical input files are
the objects referenced by `Task.input_data_path`. A new run downloads those files
and strips their single input-root directory. Runtime output is a separate,
replaceable copy. In `src/backend/app/tasks/evolution.py::_prepare_run_environment`, the
runtime directory is cleared and inputs are downloaded again on execution;
`src/backend/app/services/task_service/execution.py::run_task` supplies the
stored configuration and server model bindings. A runtime IDE edit cannot be
used as evidence that the next child's input was changed.

For configuration-only changes a child patch changes the stored parameters.
For source changes, carry the child's effective configuration into the draft
before validation and publish to that child; do not accidentally restore an old
parent `config.yaml` over a newer child patch. Keep `providers: []` in project
configurations and use the server's model selection. Missing model/service bindings
are actionable configuration errors, not a reason to invent credentials.
