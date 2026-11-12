## ADDED Requirements

### Requirement: Genshin-focused evolvable Shader example
The platform SHALL expose one Genshin super-resolution Shader example through existing example discovery and remote gateway selection. Shader sources MUST be discoverable, evolvable and transferred consistently.

#### Scenario: User creates the example task
- **WHEN** a user selects the Genshin Shader example and an authorized compatible gateway
- **THEN** the task evolves shader.comp using the existing planner and code generator without requiring other game evaluators.

### Requirement: Local fixed benchmark and real compilation
The adapter SHALL compile and validate candidate GLSL and supply an immutable local baseline and local reference images. Candidate code MUST NOT change measurement conditions, device identity, baseline or reference data.

#### Scenario: Candidate compilation fails
- **WHEN** GLSL compilation or SPIR-V validation fails
- **THEN** no phone job is submitted and compiler diagnostics are returned as candidate feedback.

#### Scenario: Baseline and frame data change
- **WHEN** the fixed baseline, device profile, frame content or measurement request changes
- **THEN** the gateway benchmark contract changes so incompatible results are not pooled.

### Requirement: Durable external job reconciliation
The adapter SHALL save submission intent before dispatch, submit idempotently and recover existing work through persisted handles or idempotency lookup. Cancellation MUST be confirmed by executor state and cleanup evidence.

#### Scenario: Submission response is lost
- **WHEN** the executor accepted a request but the gateway lost its response
- **THEN** reconnection finds or idempotently reuses the same external job rather than scheduling another experiment.

#### Scenario: Running cancellation awaits restoration
- **WHEN** a running phone job is cancelled
- **THEN** the system retains pending cancellation until the current backend finishes cleanup and never claims the phone stopped merely because polling stopped.

### Requirement: Faithful operator measurement results
The adapter SHALL preserve report provenance, measurement validity, quality verdicts, statistical significance and restoration facts. It MUST NOT report missing metrics as zero or claim game-level improvement from an isolated Shader measurement.

#### Scenario: Experiment was aborted
- **WHEN** a report has status ABORT or unresolved device restoration
- **THEN** the job remains an evaluation/recovery issue rather than a scored negative algorithm sample.

#### Scenario: Valid candidate is not a significant improvement
- **WHEN** a quality-compliant measurement is valid but is_pareto_improvement is false
- **THEN** actual operator metrics remain available while the result explicitly retains the lack of demonstrated improvement.

### Requirement: Environment-configured unified container
The deployment SHALL package the canonical SDK and executor in one container and connect to the configured platform using environment variables. Platform jobs SHALL enter the durable executor queue and acquire device leases only when executing.

#### Scenario: Idle container
- **WHEN** a ready container has no accepted jobs
- **THEN** it maintains its platform connection without launching recurring phone experiments or holding a device evaluation lease.

#### Scenario: Container stops with pending work
- **WHEN** the container receives a graceful stop
- **THEN** new claims/dispatch stop, queued work remains durable, current backend cleanup can finish, and uncertain work is never replayed automatically after restart.

### Requirement: Startup readiness check
The container SHALL perform one explicitly identified readiness baseline check through the same executor queue by default, before registering a usable gateway. Readiness measurements MUST NOT be uploaded as evolutionary results.

#### Scenario: Readiness check fails
- **WHEN** compilation, device measurement, quality or restoration fails during startup
- **THEN** health reports unready with retained evidence and the gateway does not accept platform work.

#### Scenario: Readiness check was interrupted
- **WHEN** startup finds an incomplete durable readiness check
- **THEN** it reconciles that exact external job and preserves uncertainty instead of blindly submitting a replacement.

### Requirement: Multiple wireless device configurations
The combined container SHALL accept multiple wireless ADB devices with distinct addresses, connection ports and physical serials. Each selected device SHALL have its own platform gateway identity, benchmark contract, readiness check and recovery journal while sharing the existing serialized executor queue and device lease.

#### Scenario: Multiple phones are configured
- **WHEN** the operator supplies two enabled device entries and does not restrict the device ID
- **THEN** both devices are checked sequentially before registration and exposed as separate gateways with their own contracts.
- **AND** spawned SDK workers retain their exact device binding without changing a shared environment variable.

#### Scenario: One selected phone fails readiness
- **WHEN** a selected device cannot produce a valid readiness result
- **THEN** health identifies the failed device, no selected gateway starts claiming jobs, and no readiness job is repeatedly created.

#### Scenario: User pairs a phone without USB
- **WHEN** the user runs the container's interactive ADB pairing command
- **THEN** official Platform Tools accepts the pairing endpoint and user-entered code, persists Android identity, and does not start gateway evaluation.
- **AND** subsequent evaluation connects to the separately configured connection endpoint without USB fallback.

#### Scenario: Existing single-device installation gains another device
- **WHEN** a second device is added to a deployment with a durable legacy journal
- **THEN** the original device keeps its identity and recovery journal, and the new device receives a separate state directory.

### Requirement: Optional upstream game validation
The operator MAY enable the pinned HGamey real-game A/B procedure after the existing operator benchmark. The gateway SHALL build both the immutable baseline and the submitted candidate, execute identical configured SR conditions through the existing executor queue, and verify the loaded source identities. Game entry and camera motion SHALL use fixed upstream controls without requiring an Agent.

#### Scenario: Both real-game arms complete
- **WHEN** both arms enter the world, execute their identified shaders, provide required display/motion evidence and confirm restoration
- **THEN** the adapter includes the game observations alongside the existing operator result without treating game counters as FPS or claiming improvement from a short validation run.

#### Scenario: Wireless screenshot exceeds its budget
- **WHEN** capture exceeds the explicitly configured screenshot deadline or yields an incomplete image
- **THEN** the trial is invalid, original transport and restoration uncertainty remains recorded, and uncertain device writes are not blindly repeated.

#### Scenario: Device telemetry is unsupported
- **WHEN** the device lacks a trace source or power reading required by an optional metric
- **THEN** that metric remains unavailable rather than being invented or substituted with an unrelated counter.

### Requirement: Explicit Mali telemetry sources
The system SHALL detect supported device sources, retain an owned private trace
instance on a confirmed monotonic-raw clock, filter GPU work by exact application
UID/GPU, and retain loss counters, tail-window evidence and cleanup receipts.
Unknown cleanup SHALL stop outer device mutation and further arms.

#### Scenario: Supported MT6991/Mali game window
- **WHEN** application work-period and GED sources are available
- **THEN** the report distinguishes application GPU work, device utilization and
  frequency, and quantized application presentation histogram bounds
- **AND** work-period counts are not reported as frames or GPU time per frame

#### Scenario: Explicit battery units and discharge state
- **WHEN** a configured battery profile has consistent discharging, USB-offline
  reads within the marker window
- **THEN** the report may include the sampled battery-supply voltage-current
  product with units, polarity, raw readings and cache limitations
- **AND** it does not infer whole-device power, GPU power or a cell multiplier

#### Scenario: Charging or unsupported power source
- **WHEN** the profile is disabled or reads do not meet its explicit conditions
- **THEN** the report records the reason without inventing a numeric measurement

### Requirement: Capability-qualified Perfetto window evidence
The evaluator SHALL inspect real device source content and capture the existing
baseline/candidate A/B intervals using unique start/end markers. It SHALL use the
official Trace Processor to return target-scoped JSON alongside original trace
bytes. This integration SHALL NOT introduce power scoring.

#### Scenario: PowerStats exists but has no channels
- **WHEN** the advertised service returns empty consumer or meter arrays
- **THEN** capabilities report unsupported energy measurement without fabricated zero power
- **AND** ordinary frame/CPU/GPU capture may continue

#### Scenario: Optional trace records are missing
- **WHEN** target FrameTimeline records, valid battery units or an analysis dependency are unavailable
- **THEN** the JSON retains the source-specific reason and native observations remain separate
- **AND** unrelated SurfaceFlinger records cannot substitute for target game frames

#### Scenario: Recorder is interrupted
- **WHEN** startup, shutdown or transport loses its authoritative response
- **THEN** owned capture intent and recovery uncertainty remain durable
- **AND** recovery reconciles the exact original recorder instead of repeating the experiment

### Requirement: Durable binary evidence transfer
The SDK SHALL upload immutable checksum-bound evidence before its final result,
persist upload acknowledgements and retry through network outages. The platform
SHALL store original binary traces outside SQL/JSON result bodies and authorize
downloads against task ownership and the completed result manifest.

#### Scenario: Evidence upload response is lost
- **WHEN** the original trace was uploaded but acknowledgement did not reach the gateway
- **THEN** retry uploads identical bytes under the same attempt-scoped digest without another phone run

#### Scenario: Another user requests a trace
- **WHEN** a user does not own the task or the requested object is absent from its completed manifest
- **THEN** the platform refuses the download
