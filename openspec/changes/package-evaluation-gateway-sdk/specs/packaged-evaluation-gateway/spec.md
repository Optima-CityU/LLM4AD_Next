## ADDED Requirements

### Requirement: Direct installed gateway startup
The SDK SHALL provide direct gateway startup from installed adapters or custom file/module evaluator specifications without requiring initialization or source export.

#### Scenario: Run an installed adapter
- **WHEN** a user starts the built-in example using platform and explicit user settings
- **THEN** the SDK connects using its installed implementation and durable runtime state
- **AND** source inspection exposes its SDK, adapter, lifecycle and deployment locations without creating project files

### Requirement: Optional template generation
The SDK SHALL retain optional evaluator and Docker/Compose templates without making them prerequisites of direct execution.

#### Scenario: Choose an editable launcher
- **WHEN** a user requests starter files
- **THEN** the CLI copies its bundled example and deployment templates without overwriting existing files
- **AND** system, Docker and user-defined launchers all use the same gateway core

### Requirement: User-owned evaluation boundary
Adapters SHALL own problem-specific compilation, execution, dispatch, locks, data and scoring; the SDK SHALL own connection, accepted-task ownership, worker deadlines, progress, result/artifact transfer and recovery.

#### Scenario: Load a custom evaluator
- **WHEN** a user provides a file or installed module evaluator
- **THEN** effective flags/config/environment reach its constructor and workers
- **AND** the optional HGamey native dependencies are not required or started

### Requirement: Optional device case lifecycle
The HGamey case SHALL use the packaged adapter/executor and preserve serialized device ownership, readiness, recovery and evidence retention.

#### Scenario: Select the HGamey adapter
- **WHEN** the user selects HGamey with prepared native tools and configured devices
- **THEN** the SDK starts the packaged lifecycle and registers only after readiness checks
- **AND** normal gateways remain independent of that hardware case

### Requirement: Separated templates and complete examples
Reusable templates SHALL be independent of examples. Each complete example SHALL co-locate its source, configuration and deployment. HGamey SHALL expose compilation, queue/executor, device/ADB and measurement logic in named responsibility directories.

#### Scenario: Inspect the HGamey case
- **WHEN** a user inspects the installed HGamey example
- **THEN** its compiler, queue, wireless ADB, protocol and deployment files are visible together
- **AND** its configuration files are named settings.json, game-ab.json and game-check.json without a redundant example suffix

### Requirement: Rebuildable native example
The HGamey case SHALL include the native source and build inputs used by its device evaluator and SHALL provide a container build without requiring a private HGamey runtime image. README SHALL distinguish host tools, phone prerequisites and test component installation.

#### Scenario: Rebuild a native runtime
- **WHEN** the operator builds the case container
- **THEN** phonefarm, Android runner, Vulkan layer and refbench APK are built from the included source
- **AND** required host tools are prepared during image construction
- **AND** Root, ADB authorization and official game installation are documented operator prerequisites

### Requirement: Shared service infrastructure
All evaluator entrypoints SHALL use the same SDK lifecycle, local health contract and acknowledged file collector. Hardware examples SHALL contribute only optional backend readiness/draining and external-evidence policy hooks.

#### Scenario: Run an ordinary custom evaluator
- **WHEN** a user supplies only an evaluator class
- **THEN** the SDK supplies health, shutdown, cleanup and transport lifecycle without requiring case-specific service files
- **AND** platform disconnection is reported separately from local/backend liveness

#### Scenario: Run a persistent device backend
- **WHEN** the evaluator provides optional runtime and evidence hooks
- **THEN** the shared service owns the backend lifecycle and local health server
- **AND** cleanup waits for both upload acknowledgement and the backend's restoration/ownership checks

### Requirement: Complete evaluator reporting
The SDK SHALL persist attempt-scoped diagnostic messages and extra metadata independently of progress. Final successful and failed results SHALL retain auxiliary metrics, evolution feedback, elapsed time, declared evidence and file descriptions through platform storage and client retrieval. Generic worker exceptions SHALL retain their original cause and diagnostic traceback; explicitly unavailable work SHALL wait for recovery without automatic re-execution.

#### Scenario: Report extra information before completion
- **WHEN** an evaluator reports messages, progress and metadata and the gateway restarts
- **THEN** ordered message history and extra context survive and reach the final result
- **AND** independent messages do not start or reset execution timing, or modify a replacement attempt

#### Scenario: Fail with diagnostic evidence
- **WHEN** a worker fails during adapter initialization or evaluation
- **THEN** the SDK captures its exception cause and diagnostic files through the normal file outbox
- **AND** structured failure feedback, metadata and attachment descriptions survive platform storage and client retrieval
- **AND** old minimal error reports remain accepted

### Requirement: SDK-owned task lifecycle
An ordinary local evaluator SHALL require only its contract and evaluation function. The SDK SHALL own candidate scheduling, capacity, local execution recovery, cancellation and deadlines. Optional pre/post hooks SHALL extend evaluate, recover and cancel operations without replacing SDK state transitions. External backend confirmation overrides SHALL remain compatible.

#### Scenario: Recover ordinary local computation
- **WHEN** a gateway restarts with accepted work that never started or a confirmed-stopped SDK local worker without external saved handles
- **THEN** the SDK resumes queued work or restarts the interrupted local evaluation without a custom recovery implementation
- **AND** a still-live worker holds an execution lease and is not duplicated
- **AND** replay clears only unfinished, unuploaded SDK outputs and preserves message history
- **AND** explicitly suspended work and legacy unknown execution are not blindly replayed

#### Scenario: Cancel an ordinary evaluator
- **WHEN** the platform requests cancellation of an SDK-owned local evaluation
- **THEN** the SDK persists the stop request, terminates the local process tree and confirms cancellation without requiring a user cancel method
- **AND** a replacement gateway can cancel an owned worker that survived the original gateway
- **AND** cancellation survives restarts and prevents local replay

#### Scenario: Extend an operation
- **WHEN** an adapter overrides optional before_task or after_task hooks
- **THEN** the SDK invokes them around its selected operation and supplies its result or error
- **AND** the post hook also observes preparation errors without masking the original error
- **AND** external execution is confirmed through its optional domain integration before the SDK releases ownership
