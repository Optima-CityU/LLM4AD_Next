## ADDED Requirements

### Requirement: Durable explicit evaluation evidence
The system SHALL retain explicitly declared files and structured per-instance results for local and remote evaluations, bound to task version, execution, candidate, source fingerprint, evaluation profile and instance. Existing evaluators SHALL remain compatible and score selection SHALL remain unchanged.

#### Scenario: Candidate workspace is removed
- WHEN a candidate completes evaluation and its workspace is removed
- THEN declared evidence SHALL remain available independently of the workspace

### Requirement: Transfer recovery and cleanup
The system SHALL acknowledge evidence only after files and manifests are saved. Failed transfers SHALL be retried without rerunning evaluation, and unacknowledged data SHALL be retained. Task deletion SHALL remove retained task evidence.

#### Scenario: Worker restarts during transfer
- WHEN a transfer is interrupted before acknowledgement
- THEN a worker SHALL resume the transfer from the persistent outbox without rerunning the evaluator

### Requirement: Unified authenticated workbench
The workbench SHALL provide evaluation state, metrics, per-instance metadata and files for local and remote tasks. JSON, text, Markdown and images SHALL support safe preview, while other files SHALL remain downloadable. Existing remote evidence SHALL remain readable.

#### Scenario: User switches task version
- WHEN a different task version is selected
- THEN evaluation queries and node filters SHALL refer only to that version
