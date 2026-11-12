## Context

LLM4AD already owns candidate evolution, outbound gateway transport, SQLite recovery state and user-scoped gateway selection. The extracted executor at hgamey-evaluator runs a serialized wireless-ADB experiment and exposes v2 phonefarm reports. Its upstream snapshot is 95f55e5c72ae28806181d299107fc57471818be5 with local Mali changes.

## Goals / Non-Goals

Goals: one Genshin-focused super-resolution Shader case, SDK startup, real compilation, fixed A/B baseline, frozen local references and conditions, result provenance, reconnection and safe cancellation reconciliation.

Non-goals: other games, running a second GOL search loop, game-level FPS claims, uploading private reference frames, new frontend components or automatic device setup.

## Decisions

1. Place a discoverable config.yaml under examples/applications/hgamey_shader and an independent SDK-loadable adapter under examples/remote_evaluation/hgamey. Use existing remote evaluator configuration and the dynamic-island planner. Keep baseline, frame data and conditions local to the gateway, outside candidate-controlled paths.
2. Accept only shader.comp as the candidate. Compile source and the fixed bilinear baseline using one pinned/local compiler invocation, run spirv-val, stage per-attempt files under a host directory mounted read-only at /inputs in the executor. Keep GPU bindings, dispatch coverage and output format compatible with the runner. Source hashes and compiler identity are part of evidence.
3. Load a local JSON settings file through HGAMEY_GATEWAY_CONFIG. Derive the evaluation contract data version from the local reference content, baseline, measurement request and device profile. A changed benchmark cannot silently share an old contract.
4. Implement evaluate/recover/cancel against a minimal executor compatibility API: idempotency_key on submit, lookup by key and cancellation request. Persist submission intent before HTTP POST and retain external handles. Reconnect to existing work after network loss. Interrupted or unconfirmed work remains recovery-required. Running cancellation lets the current backend finish and restore; it never claims immediate phone cancellation by killing only an HTTP client.
5. Preserve upstream status, source, qualification, quality, significance and restoration facts. Rank only valid, quality-compliant operator measurements; POOR_QUALITY/SLOW/CRASH remain candidate failures, while ABORT and uncertain device state cannot be learned as algorithm failures. Missing metrics remain absent rather than fabricated zeroes. A PASS does not imply statistically significant improvement or game-level qualification.
6. Restrict the default adapter to concurrency 1 per extracted serialized executor. Separate device/data/protocol profiles create separate contracts. The gateway timeout must include compilation and actual local executor waiting; document this and avoid oversubscribing a single phone.
7. Add source support consistently to repository discovery, coder context and remote bundles. No SPIR-V binary from the model is trusted; binaries are generated locally.

## Risks / Trade-offs

- Real Genshin screenshots and an authorized, compatible phone are required for representative results; synthetic fixtures only establish integration correctness.
- Cancellation of a running experiment may take until its cleanup finishes. If restoration cannot be confirmed, retain an uncertain state and do not permit duplicate execution.
- The external repository contains unrelated local changes. Preserve them; distribute only the scoped compatibility diff and record its upstream/extraction provenance.
- The existing Shader report qualifies operator benchmarks only; retain that limitation in documentation and result metadata.

## Migration Plan

Apply the compatibility patch to the extracted executor, rebuild its executor image when ready, configure local screenshots and mounts, run the adapter preflight, then register one authorized gateway through the existing CLI. Select that gateway in the example task wizard. Verify baseline-versus-baseline and a known-equivalent candidate before a short evolution run. Device experiments are a separate explicit acceptance step; automated tests use isolated temporary directories and local HTTP fixtures.

## Open Questions

Real-device acceptance depends on the user's available Genshin frame set and device readiness. No device runtime service is started implicitly during implementation. The local executor compatibility API additionally exposes an operator-attributed resolve operation for inactive quarantined work, so a verified manual restoration can release the queue without fabricating measurement evidence.

## Single-container deployment

The extracted executor image also contains an exported, minimal LLM4AD SDK runtime and the adapter. A Python service owns the SDK lease, executor queue/server and shutdown sequence. The SDK receives platform jobs; adapter submissions enter the existing durable executor queue and acquire the phonefarm lease only during execution. No device probe or evolution experiment runs merely because an execution thread exists. A separately identified, one-time startup baseline smoke check is explicitly requested by the user and traverses the same queue and locks. It checks compilation, measurement validity, image quality and restoration; its shortened protocol and results are readiness-only and never become evolutionary fitness. Incomplete startup checks reconcile durably before another is created. Failed/uncertain startup checks do not register a usable gateway.

Environment variables supply server URL, gateway name, allowed-user UUIDs, device selection/identity, reference paths, measurement conditions and startup-check settings. Configuration is generated into the persistent runtime directory. Reference inputs remain read-only; compiled/staged artifacts use a separate writable runtime location shared by SDK and executor within the container. Gateway identity, startup journal, executor jobs and device leases survive container recreation. Shutdown stops claims and queue dispatch, retains queued work, waits for the current backend cleanup, and preserves uncertainty if forcibly interrupted. Remote server outages do not restart active device work.

## Multiple wireless devices

Accept `HGAMEY_DEVICES_JSON` containing IP addresses, connection ports, physical serials, labels, enabled flags and optional per-device GPU/driver profiles. Existing executor JSON and single-device environment settings remain supported; an optional device ID narrows the selection. Reuse executor validation for duplicate IDs/endpoints/serials and IPv4/IPv6 parsing. Each enabled device advertises a separate SDK identity and benchmark contract, with independent persistent journals. Generated local evaluator subclasses bind immutable settings paths so spawned workers cannot read another device's configuration through a shared environment variable. Legacy single-device identity/journals stay in place and are associated with their original device before adding new ones.

All devices share the existing serialized Engine and phonefarm lease; this is a multi-device selector, not parallel phone execution. Readiness checks run once per device sequentially before any SDK begins claiming work. A failed selected device keeps the container unready and is identified in health diagnostics. Stop and recovery retain the shared queue and each device's local SDK state.

The image includes pinned official Android Platform Tools 36.0.2 with checksum verification and a build cache, replacing the distribution ADB that lacks `pair`. Build-time URL/checksum overrides support operator mirrors. An explicit `adb` entrypoint runs interactive wireless pairing without starting evaluation. Android credentials persist in the existing writable Android directory; an existing authorized key is optional, never required as a prerequisite to create the container. No USB devices, privileged mode, unattended pairing-code capture or automatic Root setup are introduced.

## Reused upstream game validation

An optional `game_check` runs the pinned `HGamey/experiments` selfsr A/B script after the existing headless operator benchmark. This reuses the upstream asynchronous resize path and existing candidate compiler; it does not introduce another super-resolution algorithm. The baseline and candidate get separate frozen libraries, identical SR settings, and live source-hash checks. The executor handles this as `game-ab` through its existing durable queue and native shared device lease. Recovery follows both accepted job identities; cancellation is not complete until both jobs have confirmed cleanup.

Game entry uses fixed waits/taps, then a native world-readiness capture; camera-motion validation uses upstream swipes and video analysis. Model navigation is disabled by default. The adapter keeps operator latency as fitness and PSNR as quality evidence, with separate real-game display/motion observations. Missing GPU/game-frame telemetry remains unavailable. A short run can establish functional integration, not improved game performance or replacement of the internal game upscaler.

The wireless flow explicitly selects a 45-second PNG screenshot deadline while retaining the existing bounded capture watchdog and recovery records. The native default stays at 15 seconds for unrelated callers. No lossy image conversion, blanket retry, lock removal or replay of uncertain writes is used to mask capture failures. A root-owned temporary recording is retrieved through the existing transport and removed by its owning trial, avoiding this device's shell write restrictions.

## Mali telemetry source adaptation

Game A/B detects the actual GPU. The validated MT6991/Mali path uses an owned
private ftrace instance, exact application UID `gpu_work_period` filtering, GED
device utilization/frequency, battery temperature and the application's
SurfaceFlinger presentation histogram. A two-second trace tail captures delayed
events; per-CPU loss counters and cleanup receipts remain in the evidence. No
global tracing or charging policy is changed. Unconfirmed cleanup stops further
device writes. This is not a claim of support for every Mali driver.

`gpu_uid_active_pct` is application GPU active time divided by the window;
`gpu_device_utilization_pct` describes the entire GPU. Frequency and temperature
are separate metrics. `display_frame_p95_lower_ms/upper_ms` are quantized
presentation-interval bounds, not per-frame GPU time. Work periods are not frames.
Unsupported metrics retain an explanation rather than a fabricated zero.

For battery observations, explicitly select `game_check.options.power_profile`:
`disabled` (default), `oplus_milli_positive` for confirmed mV/mA positive-discharge
interfaces, or `linux_micro_negative` for confirmed uV/uA negative-discharge
interfaces. Every included read must be Discharging with USB offline. Units are
never guessed by magnitude; raw reads and conversion evidence are preserved in
`metadata.game_report.telemetry`.

`battery_supply_vi_product_mw` is the arithmetic sample mean of exported voltage
times current. It is neither GPU power nor automatically whole-device power.
The tested OnePlus dual-cell driver exports the highest cell voltage; doubling
it would not measure pack power. Gauge readings may be cached for around ten
seconds; read count is not an independent hardware sample count. Use at least a
30-second observation window and inspect coverage. These observations do not
change fitness or establish a game-performance improvement.

## Capability probing and marked Perfetto integration

The user accepted two integration points after the 2026-11-09 probe: inspect
actual device capabilities and capture/analyze the existing baseline/candidate
windows. Power scoring, new energy providers/instruments, and a new weighted
fitness objective are excluded. The current `score = -operator_latency_ms` stays
unchanged. Repeated game A/A stability and a future multi-objective policy remain
separate research, not acceptance criteria invented for this integration.

Use device Perfetto and the official pinned Python SDK/Trace Processor. The
processor is downloaded and verified from the SDK's architecture-specific manifest
at image build time, with a BuildKit cache and optional mirror URL. Jobs never
download executables dynamically. Retain the existing serialized executor queue,
wireless transport guard and device lease, along with the validated native Mali
UID-work, GED and presentation histogram sources.

Before recording an arm, preserve raw read-only capability observations: physical
phone/boot, registered Perfetto producers, PowerStats framework content, actual
AIDL energy-consumer and meter array replies, and battery gauge fields. Empty
arrays mean unsupported; nonempty arrays are advertised capability rather than
proof of measured energy. Permission errors and unavailable services stay
unavailable. Battery units are not guessed. This firmware's standard voltage 4
and derived zero power cannot be accepted as power measurements.

Persist unique owned paths, config, marker, boot and submission intent before
background-wait startup. Retain its exact process/config identity. Write unique
process-scoped asynchronous ATrace start/end markers inside the native A/B
measurement window, excluding
warm-up, screenshots and subsequent camera recordings. Capture CPU scheduling,
CPU/GPU frequency/work events, target FrameTimeline and available standard power
observations. Enable rail/consumer collection only when those interfaces actually
advertise data. Do not write charging controls or infer pack/GPU power.

Stop the exact recorder with SIGINT, wait for it to flush, retrieve original trace
bytes, and confirm owned staging cleanup. A missing receipt or lost transport
keeps the durable recovery obligation and prevents another arm or competing outer
restoration. Reconciliation stops only the journaled recorder on its original
boot and never repeats the experiment. Existing executor quarantine and SDK
recovery remain authoritative; no extra scheduler is introduced.

Run official Trace Processor analysis in a bounded child after device cleanup.
Select the unique complete marker interval. Keep CPU scheduling and raw counter
summaries explicit. Count target application records separately from
SurfaceFlinger records. Derive presentation intervals only from unambiguous
application-to-display token links for one target layer, with display-frame end
timestamps inside the marked window. Missing game SurfaceView records remain
unavailable; native frame evidence remains usable. Trace loss prevents accepting
derived measurements. Analysis/source unavailability does not manufacture a
candidate failure or replace existing operator fitness.

Return arm JSON plus a checksum-bound manifest for trace, analysis, capabilities
and cleanup evidence. The local executor serves only manifest files under that
job's evidence directory. The adapter retrieves the bytes and spools immutable
copies through Reporter.artifact. Gateway upload acknowledgements persist in its
SQLite outbox; final results are reported only after evidence uploads succeed.
The platform stores files separately in RustFS/S3, fences uploads by assigned
gateway/attempt, and exposes downloads only to the owning task user and only for
objects referenced by the completed result manifest. Raw trace bytes never enter
SQL report rows or base64 JSON results. Retry transfers evidence without rerunning
a phone experiment.

References: [PowerStats](https://source.android.com/docs/core/power/power-stats-hal?hl=en),
[Perfetto power sources](https://perfetto.dev/docs/data-sources/battery-counters),
[FrameTimeline](https://perfetto.dev/docs/data-sources/frametimeline),
[official Python analysis](https://perfetto.dev/docs/analysis/trace-processor-python),
and [capture lifecycle](https://perfetto.dev/docs/reference/perfetto-cli).
