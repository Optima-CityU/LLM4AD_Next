# phonefarm Telemetry Reference

One 72-field `Telemetry` struct (`src/telemetry.rs`) serves **every** upper path
— VLM sessions, deterministic scripts, conformance-test batches. Same fields,
same units, so runs from different paths are directly comparable. Fields the
device cannot supply are left empty rather than defaulted to zero.

## Collection mechanics

- One `r=telemetry` record per step, unified into log.jsonl (no separate
  telemetry.log)
- High-frequency fields are sampled every step; heavyweight details are batched
  every `telemetry_interval` steps (default 5)
- An on-device sh script is generated from the whole command batch and uploaded
  once at session start; each step costs a single shell round-trip
- Everything is time-limited, failures never panic, and telemetry never enters
  the model context

## The ten field layers

1. **System**: CPU% / load / per-core frequencies / memory / battery (incl. µA)
   / 16 temperature zones / GPU / storage / WiFi / network connections
2. **Rendering**: cumulative frame count → fps delta / Janky% / p50–p99
   percentiles / MissedVsync / VSYNC period / layer count
3. **App**: pid / topActivity / meminfo details / thread count / VmRSS / VmHWM /
   cold-start latency
4. **Root**: process IO / fd+socket counts / smaps aggregation / process
   network / cgroup / dmesg / tombstones (degraded via id probing at session
   start)
5. **PSI**: cpu/memory pressure some/full avg10/60/300
6. **Per-app traffic**: uid-level network traffic
7. **Sensors/peripherals**: active sensors / location requests
8. **IPC**: IPC statistics
9. **Host**: per-step wall time / API latency / screenshot size / UI node count
   / OCR invocation (annotated when the tree failed)
10. **Events**: crash / ANR / fd growth / connection-count changes (delta of two
    readings → `r=app_event`)

## Implementation notes

- Android: smaps aggregation uses `smaps_rollup`; cold start uses `am start -W`
  to get WaitTime for free
- OpenHarmony: the hidumper service times out on first cold start; heavyweight
  probes are limited to 20s (paid only once every 5 steps)
- Config keys: `telemetry` (on by default) / `telemetry_interval`
- Inspection: `phonefarm show <run-id> --step N` (per-step snapshot) /
  `phonefarm stats <run-id>` (summary)
