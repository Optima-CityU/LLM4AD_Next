## ADDED Requirements

### Requirement: Selected-direction task construction
The final stage SHALL build and validate a runnable task package for the selected candidate and evaluation contract, then create an idempotently linked project/task through the existing project-management services. It SHALL not run a second requirements-gathering agent.

#### Scenario: Successful build
- **WHEN** the Agent builds a package that passes the official technical validator
- **THEN** the published result references that exact package and the user can create or access its linked task inside AutoDiscovery

#### Scenario: Technical validation versus scientific evidence
- **WHEN** a package passes technical validation but no real comparison has run
- **THEN** the UI identifies it as runnable and does not label its algorithm superior or its evaluation scientifically verified

#### Scenario: Repeated import
- **WHEN** the same published proposal is imported again
- **THEN** the original linked project/task is returned rather than creating a duplicate

### Requirement: Embedded evolution controls
The final stage SHALL show status, a bounded progress/log/results view, start/stop controls, and task-version navigation for its linked project. Closing this view SHALL only hide the panel; it SHALL NOT stop the task. Ordinary project management SHALL remain an optional advanced route.

#### Scenario: Start and close panel
- **WHEN** a user starts evolution and closes the embedded panel
- **THEN** the existing task runner continues, and reopening the panel shows the current task state

#### Scenario: Explicit stop
- **WHEN** a user stops a pending or running task
- **THEN** the existing stop service is used and the workbench identifies a user-requested stop separately from an unexplained execution failure

### Requirement: Scoped Agent task operations
The final-stage Agent SHALL be able to inspect the linked task and its child versions, create a child version, propose and apply a parameter change, and start or stop an eligible task. Every operation MUST be scoped to the current workspace's linked task family, MUST use actual persisted task status, and MUST reject an unrelated task ID.

#### Scenario: Status-aware start or stop
- **WHEN** the user asks the Agent to start or stop a linked task
- **THEN** it checks current status and invokes the existing task service only if the requested transition is valid

#### Scenario: Tune a running task
- **WHEN** the Agent is asked to optimize parameters while a task is running
- **THEN** it creates an independent child version and applies the proposed parameter diff there, without mutating the active run or starting an unrequested run

#### Scenario: Unrelated task
- **WHEN** a tool call names a task outside the workspace's linked root and child family
- **THEN** the backend rejects the operation even if the model supplied a syntactically valid task ID

### Requirement: Version and result fidelity
Creating a child for tuning SHALL preserve the parent package and results, SHALL not make that child the active version until explicitly selected, and SHALL expose the parameter difference and observed task result without inventing benchmark claims.

#### Scenario: Child created
- **WHEN** a child task is created for a parameter experiment
- **THEN** it inherits a runnable package, starts uninitialized, and leaves the root's active selection unchanged

#### Scenario: New run has no result
- **WHEN** the child has not completed an evaluation
- **THEN** the UI and Agent report its result as pending rather than comparing it with a parent score
