## Design

AutoDesign sessions reuse the existing native research runtime under a new
`autodesign` workflow mode/stage. A small session table associates that runtime
workspace with an optional project and task. Each selected task version resolves
to its own stable session to avoid cross-tab changes to another version's context;
family-scoped tools can inspect or create related experiments explicitly. A
conversation created from New Task stays taskless during authoring and becomes
the same task conversation when the user opens task setup.

The native profile loads `autodesign`, `llm4ad-cloud-package` and
`llm4ad-run-analysis`. AutoDesign may write its draft tree; upstream task files and
run artifacts are read through authenticated, task-scoped tools. Global Claude
search stays disabled. Task context reports configuration, actual status and file
locations without returning provider secrets. Presentation preferences apply to
AutoDesign explanations too.

The Agent writes a portable project under its workspace draft directory. A
dedicated ephemeral task-runner container installs requirements using the same
installer as execution, invokes the Cloud Skill verifier and returns its actual check results directly. Temporary execution files are
cleaned on completion, and no verification document is stored in the project or
object store. Task setup captures the verified source files directly and
checks them against the verifier manifest without archive creation or extraction. Existing-task code edits publish to an unstarted child; numeric
or other configuration patches validate against AppConfig. A best-code seed must
materialize the actual selected run's best code into child inputs, not merely edit
an old run directory. Repeated publication of one verified revision is idempotent.

Task-view changes are UI state only and retain the native conversation. The
workbench uses existing Tabs, dialogs, model controls and task panels. New task
creation offers manual, ZIP import and AutoDesign. The sole navigation entry is
Algorithm Evolution Projects. AutoDesign opens in the evolution page with a
persistent design-session search parameter and no workbench, task actions or run
panels. Draft sessions are resumable task-list rows, not a separate launch button.
Optional references are stored under source/references without replacing drafts.
The Open task setup action prepares the verified snapshot as an uninitialized
task and opens the existing task workbench configuration. Publication does not
start evolution. The unchanged UninitializedView and ConfirmStep provide all
existing steps, model bindings, editable full YAML and the final submit action.
Existing legacy AI histories remain accessible.

The right workspace provides only Artifacts and References. Both views reuse
the existing SourceNavigator, Markdown reader and Monaco editor, with a
collapsible/resizable file navigator. Two existing Tabs remain visible at narrow
widths. Artifacts open directly, without an overview, design-record index or
report reader. The header contains the project name and a compact status badge. The name is maintained by the Agent through
`update_project_name`. Reference upload belongs inside the reference browser;
existing task configuration stays in the footer.

References support text upload, reading and deletion; project drafts support
conflict-aware editing. Switching the two categories preserves selection,
reading state, unsaved edits and the native conversation. File modification
metadata indicates updates without moving the current selection.

Routine Agent construction produces the runnable project and concise usage
README. Decisions and check outcomes are explained in chat. It does not generate
separate design notes, comparison documents or verification reports unless
explicitly requested. The verifier uses private temporary files for execution
results and progress, then removes them on success or failure. Existing
revision-aware state still supports task setup without publishing report files.

The Agent verifies the real project and repairs failures; no manual validation
button is added. Saving a draft expires its verified revision, and unsaved buffers
prevent setup handoff. Current setup retains UninitializedView/ConfirmStep, full
YAML and model selection. Material navigation adds no new database fields,
parameter forms, or UI dependencies. Every material points to its original file.

## First version boundaries

Users can delegate immediate start/stop and ask for current or finished-result
analysis. Task state and verified project state refresh in the open workbench.
Unattended multi-run search and a closed-browser completion-resume scheduler are
not part of this first version. Existing execution, quotas and authentication
remain authoritative.

## Validation

Exercise session ownership, task-family scope, path traversal, result file reads,
secret filtering, child configuration and best-code inputs, actual Cloud directory
verification/task preparation and idempotent retries. Check the task/Agent view switch in
a browser, run relevant backend/frontend tests and production build, regenerate
the API client with openapi-ts and deploy only to the test server.

Auto language resolves the browser's weighted Accept-Language header and is
part of the runtime profile cache key. Explicit Chinese/English selections
remain authoritative. AutoDesign README explanations follow that language;
paper and rebuttal submission content retain their separate language rules.
Published task sessions reuse the original verified working directory, and the
file browser displays that current project without a second UUID-named copy.
Local portable delivery instructions live in a separate Cloud Skill reference;
the platform entrypoint loads direct authoring, verification and task setup.
