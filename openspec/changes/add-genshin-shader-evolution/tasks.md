## 1. Shader source support
- [x] 1.1 Support GLSL/Compute Shader source discovery, coder context and candidate transport, with regression coverage.

## 2. Executor compatibility
- [x] 2.1 Add durable idempotent submit, lookup and safe cancellation reconciliation to the extracted executor; preserve unrelated changes and distribute a scoped compatibility patch.

## 3. Gateway adapter and evolution example
- [x] 3.1 Implement local configuration, benchmark identity, compilation, baseline staging and report conversion.
- [x] 3.2 Implement durable HTTP submission, progress, recovery and cancellation using existing SDK hooks.
- [x] 3.3 Add the Genshin Shader template, fixed baseline source, startup instructions and provenance.

## 4. Verification
- [x] 4.1 Test compilation errors, report validity, contract isolation, lost responses, restart recovery and cancellation without device access.
- [x] 4.2 Validate example configuration and perform a local SDK/evolution smoke test with a fixture executor; label it separately from real-device measurements.
- [x] 4.3 Run required source lint, related regression tests and OpenSpec validation; record remaining real-device acceptance work.

## 5. Single-container gateway and executor
- [x] 5.1 Export a lightweight SDK runtime from canonical platform sources, preserving wire schemas.
- [x] 5.2 Implement environment configuration and one-container queued dispatch, leases, health and graceful recovery lifecycle.
- [x] 5.3 Run one durable startup baseline smoke check through the same executor queue before advertising readiness.
- [x] 5.4 Package the gateway and executor in the extracted repository Docker/Compose deployment and document environment-only startup.
- [x] 5.5 Verify source/export parity, lifecycle and queue/lock behavior; build and smoke-test the image without contacting a real phone.

## 6. Multiple wireless devices
- [x] 6.1 Support wireless device arrays and legacy device files with per-device benchmark identities and stable recovery state.
- [x] 6.2 Bind spawned SDK workers to immutable device profiles and run sequential readiness checks through the shared queue and locks.
- [x] 6.3 Include pairing-capable official ADB, optional existing credentials, persistent Android identity and interactive container pairing instructions.
- [x] 6.4 Verify multiple-device dispatch, legacy recovery, configuration validation and the final rebuilt image.

## 7. Native battery telemetry correction
- [x] 7.1 Preserve multiword battery status and numeric field boundaries, with regression coverage for missing values and supply qualification.
- [x] 7.2 Distribute the scoped native-source patch without replacing unrelated extraction changes or historical reports.
- [x] 7.3 Run affected native tests, rebuild the container and verify the connected OnePlus battery readings.

## 8. Real game integration probe
- [x] 8.1 Run a bounded read-only layer session against the installed Genshin client and preserve process, marker and restoration evidence separately from standalone shader measurements.
- [x] 8.2 Fix Android 15 viewport rotation parsing with regression coverage and a scoped extraction patch.
- [x] 8.3 Rebuild and observe game-scene entry with the layer installed; document the independent USB observation, wireless capture failure, candidate-substitution limitations and final cleanup.

## 9. Experimental present-stage candidate verification
- [x] 9.1 Add an opt-in same-extent, live-toggle candidate path with exact queue identification, GPU completion checks and deferred unsafe teardown; preserve unsupported internal-pass admission.
- [x] 9.2 Freeze real NDK candidate builds and wrap the existing session in a leased, durable two-property lifecycle with focused tests and a scoped reproducible patch.
- [x] 9.3 Verify diagnostic output and the actual example seed on the connected game's world scene, then independently check restoration; distinguish functional postprocessing from internal upscaler replacement and performance evaluation.

## 10. Reuse upstream real-game evaluation
- [x] 10.1 Package the pinned upstream A/B runner, disable optional model navigation, and verify world entry before sampling.
- [x] 10.2 Route real-game trials through the existing executor queue and wireless lease; retain cancellation and recovery evidence.
- [x] 10.3 Add optional candidate-versus-fixed-baseline game validation after the existing operator evaluation, with exact loaded-source identities and separate metrics.
- [x] 10.4 Verify actual game operation, recording, device restoration, relevant regressions and source lint; report unsupported device telemetry explicitly.

## 11. Mali telemetry adaptation
- [x] 11.1 Detect actual device sources and collect application-UID GPU work periods through an owned trace instance, with exact window and cleanup evidence.
- [x] 11.2 Expose device utilization/frequency and application presentation histogram bounds with explicit provenance; never relabel GPU periods as frames or USB input power as device power.
- [x] 11.3 Integrate telemetry into the existing game A/B sampling path, package its helpers and verify on the connected wireless OnePlus and in regression tests.

## 12. Capability probing and marked Perfetto A/B capture
- [x] 12.1 Probe the physical wireless device after unplugging: PowerStats content, standard/vendor battery counters, update behavior, desktop FrameTimeline, GPU frequency, official Trace Processor analysis and final restoration. Preserve failed captures separately.
- [x] 12.2 Record verified limitations and the user-approved scope: integrate capability probing and existing-window Perfetto evidence; do not integrate power scoring or new energy instruments.
- [x] 12.3 Implement attempt-owned Perfetto lifecycle and durable cleanup/reconciliation through the existing executor queue and phone lease; retain uncertainty after interrupted or lost replies.
- [x] 12.4 Mark the existing baseline/candidate windows, analyze only their target-process/layer records and preserve native telemetry when generic records are absent.
- [x] 12.5 Inspect real PowerStats channel/consumer replies and raw battery counters, distinguish unsupported/advertised/invalid observations and never manufacture zero power.
- [x] 12.6 Return parsed JSON and original trace through checksum-bound executor downloads and a durable SDK artifact outbox, with attempt fencing and owner-authorized platform downloads.
- [x] 12.7 Package the pinned official processor at image build time, export source/provenance consistently, document configuration and verify regression/recovery behavior plus an available-device capture.
