## ADDED Requirements

### Requirement: One task page offers workbench and Agent views
The system SHALL offer AutoDesign for manually created, imported and generated
tasks without requiring an earlier AI build or paper. Switching views SHALL retain
the selected version, conversation and execution state.
The global header SHALL NOT offer a task mode switch. Existing task parameter
viewing and adjustment actions SHALL open the parameter workflow, which offers
contextual AI assistance only when the selected version and its entire family
are confirmed idle. Unsaved editable parameters SHALL be saved successfully
before opening the Agent. The Agent SHALL offer a return to the same parameter
workflow, preserving its step and conversation state. There SHALL NOT be a
standalone switching row or floating assistant launcher. Switching task versions
SHALL return to the workbench rather than carry an Agent view across tasks.
Workbench-only actions and its auxiliary side panel SHALL be hidden in Agent
mode while retaining their state for the return to the workbench.

#### Scenario: Analyze an existing task
- **WHEN** a user opens AutoDesign for a task version
- **THEN** its stable session exposes the real configuration, status and files for
  that version and its related experiments.

### Requirement: Modifications produce independently runnable child inputs
The Agent SHALL be able to create a child, apply configuration changes, use actual
best code when requested, validate modified packages and start or stop an owned
family task on the user's request.

#### Scenario: Continue from the best algorithm
- **WHEN** the user requests a child seeded from a completed task's best code
- **THEN** the code is copied into that child's canonical inputs and draft and
  the parent run remains the comparison reference.

### Requirement: Direct project construction supports optional materials
An independent AutoDesign conversation SHALL accept a problem description without
a paper or dataset upload. The Cloud Skill SHALL guide generation and actual
verification before a project is prepared for task setup.

#### Scenario: Prepare an independently built project
- **WHEN** a verified draft is prepared for a new or existing project
- **THEN** the verified files become canonical inputs directly with normal
  model/memory settings and retrying preparation returns the existing task.

### Requirement: Analysis uses documented real artifacts
The Agent SHALL receive a Skill explaining configuration, metrics, raw versus
normalized scores, state, logs, candidate code and best snapshots. Its file tools
SHALL read the authorized task files rather than substitute a database summary.

#### Scenario: Inspect an in-progress run
- **WHEN** the best snapshot does not yet exist
- **THEN** the Agent uses available state and logs and distinguishes partial
  observations from final results.

### Requirement: AutoDesign is a task creation mode
The system SHALL expose only Algorithm Evolution Projects in navigation for this
workflow. New Task SHALL offer AutoDesign beside manual creation and ZIP import.
The task list SHALL NOT include a separate AutoDesign launch button.

#### Scenario: Build before creating an execution task
- **WHEN** a user chooses AutoDesign in New Task
- **THEN** the evolution page shows the Agent and optional reference uploads,
  without a task workbench or an execution task.

#### Scenario: Configure a verified design
- **WHEN** the user opens task setup after real project verification has passed
- **THEN** the exact verified project creates an unstarted task and opens the
  existing configuration workflow, with execution only at final confirmation.

#### Scenario: Upload supporting files during conversation
- **WHEN** a user uploads references in the right panel
- **THEN** the Agent can read them in references/ and existing drafts remain intact.

### Requirement: Text-only references can be removed
Reference upload SHALL accept only supported UTF-8 text documents and reject
unsupported extensions and binary content before replacing any uploaded files.
Users SHALL be able to preview and delete their own reference documents.

#### Scenario: Reject a disguised binary upload
- **WHEN** an uploaded document has a text extension but binary content
- **THEN** the server rejects the batch and existing references remain intact.

### Requirement: Artifacts and references use stable navigation
The right workspace SHALL provide only Artifacts and References with a shared
reader and two existing tabs that remain visible at narrow widths. Project files SHALL open
directly without a separate overview or design-record view. The header SHALL contain the project name and a compact status badge. Upload and refresh actions
SHALL be inside their relevant file views; task configuration stays in the footer.
New sessions without artifacts SHALL initially show References with optional
upload guidance. Existing artifacts SHALL open directly when revisiting a session.
New artifacts SHALL receive a visible tab notification without replacing the
user's active reference reader. Opening the artifacts SHALL acknowledge the update.

#### Scenario: Switch materials while editing
- **WHEN** a user changes material categories with unsaved project changes
- **THEN** selected files, edits, reading state and the native conversation remain
  available, while task setup stays disabled until the edits are saved and verified.

### Requirement: Project status is available on demand
The project header SHALL show a compact, keyboard-accessible badge derived from
current file and verification state. Opening it SHALL show existing real check
results and the execution task state without adding a permanent overview.
The badge SHALL distinguish missing projects, unverified drafts, verification in
progress, failed checks, changed verified files and unsaved editor content.
Loading or failed state requests SHALL NOT present stale verification as current.

#### Scenario: Inspect verification while files change
- **WHEN** verification finishes, a verified file changes, or the editor has unsaved changes
- **THEN** the badge and its open details reflect that state without replacing the
  conversation, losing editor content, creating documents or launching a task.

### Requirement: Project materials can be collapsed
The desktop AutoDesign split view SHALL offer a central collapse/expand control
using the existing panel style. Collapsing the materials SHALL preserve selected
files, unsaved buffers and the mounted conversation. Narrow layouts SHALL retain
their conversation/material tabs. The AutoDesign conversation header SHALL omit
the duplicate artifact shortcut.

#### Scenario: Collapse and restore project materials
- **WHEN** the user collapses and reopens the right pane
- **THEN** the conversation gains the available width while collapsed, and the
  project reader and edits return intact on expansion.

### Requirement: The Agent maintains the project name
The Agent SHALL set a concise meaningful project title as its conversational goal
becomes clear, and update the title when that goal changes. The saved name SHALL
be reflected by the interface and used by task setup.

#### Scenario: Name changes during construction
- **WHEN** the Agent updates the project name through its scoped tool
- **THEN** the workspace and any attached unstarted task use the updated display
  name without replacing the conversation or creating a new task.

### Requirement: Routine construction does not generate process documentation
The Agent SHALL create runnable project files and concise usage documentation,
perform real verification and explain outcomes in the conversation. It SHALL NOT
create separate design records or verification documents unless explicitly asked.
The platform SHALL clean temporary verifier output instead of publishing report
files to the source workspace or object storage.

#### Scenario: Verification completes or fails
- **WHEN** the real verifier completes, rejects a baseline or fails to execute
- **THEN** its actual status is returned and temporary output is cleaned, without
  creating a design-record directory or persisted verification report.

### Requirement: The Agent verifies current inputs before setup
The Agent SHALL perform configuration, dependency, source/data and real evaluator
checks through the existing verification tool. No manual verification action is
required in the material workspace. Task setup SHALL require matching verified
inputs; execution remains in the existing final task confirmation.

#### Scenario: Draft changes after verification
- **WHEN** configuration, code, dependencies or datasets change after verification
- **THEN** setup becomes unavailable until those inputs pass verification again.

### Requirement: Active execution locks Agent editing
Queued or running tasks SHALL disable the Agent view and its parameter entry.
The server SHALL reject runtime setup and mutations against an active task family.

#### Scenario: Task starts while an Agent tab is open
- **WHEN** execution changes to pending or running
- **THEN** the task page switches to the workbench and unmounts the Agent view,
  while stale mutation requests are rejected without altering task inputs.

### Requirement: Embedded conversations share the research presentation
Embedded research conversations SHALL follow the platform's AutoResearch visual
language: inherited platform typography, rounded message surfaces, compact
expandable tool records and an integrated composer. Processing and stop controls
SHALL stay inside the embedded composer. Thinking, tool details, message copying,
editing and model/context/preference menus SHALL remain available.
The presentation SHALL work in both themes and narrow layouts without changing
the conversation engine, history, streaming identity or scroll ownership.

#### Scenario: Read and control an embedded research conversation
- **WHEN** the user reads a reply, expands tools, changes composer height or stops a turn
- **THEN** the platform styling remains coherent, controls stay with the input,
  and the current conversation, file readers and message selection remain intact.

### Requirement: Shared composers expose workspace settings and processing state
AutoDesign, AutoProposal, AutoRebuttal and AutoDiscovery SHALL show the active
platform model at the lower left of the native composer. Users SHALL select
models from a searchable provider-grouped menu without a configuration dialog.
Adjacent menus SHALL edit token settings and conversation preferences through
the existing authenticated workspace APIs. Credentials SHALL stay outside the
iframe. Model changes SHALL retain token settings and conversation identity.

#### Scenario: Apply a workspace preference or model
- **WHEN** the user selects a model or applies token settings or preferences
- **THEN** the saved workspace configuration is used by a refreshed runtime
  profile, preserving the conversation and displaying failures without claiming
  the rejected selection was saved.

#### Scenario: A submitted message is being processed
- **WHEN** the composer enters submitted or streaming state
- **THEN** an animated border indicates processing until completion or stop,
  with a static indication for users who prefer reduced motion.

### Requirement: Research model transport has bounded, visible retries
Research model calls SHALL use separate configurable first-response and stream
idle deadlines, with a bounded retry budget before any response bytes are
forwarded. Defaults SHALL be 60 seconds, 120 seconds, and two retries. SDK retries
SHALL NOT multiply this budget. Ordinary evolution provider traffic SHALL retain
its existing timeout behavior. Authentication failures SHALL fail immediately.

#### Scenario: The upstream accepts a connection but returns no response
- **WHEN** a research request receives no response bytes before its deadline
- **THEN** the gateway closes that attempt, reports the next attempt to the UI,
  and retries within its budget, stopping after the final failure.

#### Scenario: The upstream stalls after partial output
- **WHEN** an upstream stream stops producing data past the idle deadline
- **THEN** the gateway terminates the stream and reports its failure without
  replaying partial replies or tool calls.

#### Scenario: Refresh during a retry
- **WHEN** a user reconnects to the research runtime health stream
- **THEN** the snapshot restores the actual failure reason and retry progress,
  and a healthy snapshot clears stale failure messages.

#### Scenario: A related version starts executing
- **WHEN** the selected task or another version in its family is pending or running
- **THEN** parameter viewing remains read-only, the Agent entry is unavailable,
  and an already open task Agent returns to the workbench.

#### Scenario: Parameter edits fail to save before requesting assistance
- **WHEN** saving the current parameter edits fails
- **THEN** the user remains on the same step with their edits intact and sees a
  localized error rather than opening an Agent with outdated parameters.

#### Scenario: Adjust an existing child through the assistant
- **WHEN** the user opens AI assistance from an existing child's parameter page
- **THEN** the assistant displays parameter-tuning guidance in the interface language,
  reads the selected child's actual configuration and relevant parent results,
  and reuses the editable child rather than repeating new-project intake.
- **AND** standalone design conversations retain their project-creation guidance.

### Requirement: Stopping chat cancels model transport
The authenticated native chat proxy SHALL associate starts and aborts with the
current conversation. Aborting SHALL interrupt pending model I/O and retry
backoff, clear that conversation's previous failure state, and discard late
proxy failure notices. A new user turn SHALL be able to issue new requests
without reviving pending requests from the stopped turn.
The assistant SHALL NOT add a gateway warning banner above the conversation;
model selection remains in the composer controls.

#### Scenario: Stop during an upstream stall
- **WHEN** the user stops while awaiting headers, response data or retry backoff
- **THEN** the gateway closes the request without scheduling another attempt
  or injecting a new timeout notice.

#### Scenario: A reasoning model returns no final code
- **WHEN** a code-generation request has empty final content
- **THEN** diagnostics distinguish the finish reason, actual input/output token
  usage and available reasoning token counts from an extraction-format error.
- **AND** task construction and tuning guidance account for reasoning output
  separately from source context without treating reasoning text as final code.


### Requirement: Shared memory in task conversations

AutoDesign SHALL expose the existing card manager and account memory model
binding from its conversation header. Standalone sessions SHALL support user
memory and, when associated, project memory. Task sessions SHALL also expose
the existing task-family memory scope.

#### Scenario: Agent consults existing experience

- **WHEN** the user asks the Agent to use earlier experience during construction, analysis or tuning
- **THEN** the runtime loads the task-memory Skill and exposes context and paginated card tools
- **AND** reads use the same authenticated memory service and scopes as the card manager
- **AND** foreign users and unrelated task families cannot be read

#### Scenario: User requests saving a verified finding

- **WHEN** the user asks to save a finding grounded in actual task artifacts
- **THEN** the Agent can create or update a card through the existing memory service
- **AND** source task and artifact metadata remain attached to the card
- **AND** reading or saving a card does not silently change evolution memory configuration

### Requirement: Executed versions retain experimental evidence
Stored parameters, canonical input files and prior results SHALL remain attached
to their executed version. A new run SHALL target an unstarted version. Manual
and Agent-created copies SHALL omit prior reports and logs by default. Renaming
a historical version SHALL remain available.

#### Scenario: Run or edit an executed version
- **WHEN** a client tries to start an executed version or replace its parameters or canonical inputs
- **THEN** the existing version is preserved and the response directs the caller to create an unstarted child
- **AND** result analysis alone does not create or start a task

### Requirement: Drafts preserve concurrent task edits
Task drafts SHALL track their canonical starting state outside published inputs.
Unchanged draft fields and files SHALL incorporate manual updates. Conflicting
edits SHALL remain available for explicit resolution and block validation/setup.
Verification SHALL expire when its task inputs change.

#### Scenario: Both the parameter form and Agent edit a field
- **WHEN** the stored task and draft change the same field differently
- **THEN** context identifies the conflict without overwriting either value
- **AND** the Agent can explicitly resolve it before verifying and preparing the target task

### Requirement: Child preparation retains its selected settings
Publication into an existing unstarted version SHALL preserve that version's
model bindings and memory settings. A source task's settings SHALL apply only
when creating a new child from that source. Task mutations SHALL carry an
explicit target ID, while reading family results SHALL remain available.

#### Scenario: Prepare a child from a parent conversation
- **WHEN** the child selected a different coder model or disabled memory
- **THEN** publishing validated files into that child retains those selections

#### Scenario: Agent starts a child while viewing the source conversation
- **WHEN** the Agent starts an explicitly selected unstarted child
- **THEN** the workbench selects that running child and preserves parent evidence
- **AND** the conversation stays associated with its original task instead of silently changing its default

### Requirement: Task deletion removes its Agent lifecycle
Deleting a task SHALL delete its bound AutoDesign conversation in the same
metadata transaction, then remove its runtime containers, credentials, references
and local artifacts through the existing workspace cleanup. Deleting a root
SHALL include its child versions and their Agents, while deletion of a child
SHALL preserve other versions and their conversations. Pending or running
versions SHALL be stopped before deletion is allowed.

#### Scenario: Refresh after deleting an AI-built task
- **WHEN** the user deletes a task and refreshes the task list
- **THEN** its conversation does not reappear as an unpublished AI construction draft

#### Scenario: Discard a draft before task setup
- **WHEN** the user confirms deletion of an unpublished construction draft
- **THEN** its owned conversation and runtime resources are removed
- **AND** the task list leaves the deleted draft without creating an execution task

### Requirement: Construction drafts share task-list operations
Unpublished construction drafts and execution tasks SHALL use the same task row,
rename/delete action controls and confirmation dialogs on desktop and mobile.
Draft actions SHALL dispatch to the existing workspace deletion and AutoDesign
rename operations. Renaming an execution task SHALL update its own Agent title
without changing inputs, verification or another version's conversation.

#### Scenario: Rename or delete a construction draft
- **WHEN** the user selects Rename or Delete from a construction task's actions
- **THEN** the existing task dialog handles the operation and refreshes its name or selection
- **AND** refreshing the page retains the renamed item or keeps the deleted item absent
