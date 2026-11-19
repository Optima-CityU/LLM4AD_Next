# phonefarm Architecture Reference

## Invariant kernel + parallel upper paths

```
  run/benchmark/parallel/quest   script   test-batch/cts-fetch   bench   capture   experiment
               │                   │              │                │        │          │
               └───────────────────┴──────────────┴────────────────┴────────┴──────────┘
                                         │
             ┌───────────────────────────┴────────────────────────────┐
             │ Invariant kernel: device abstraction (adb/hdc) ·        │
             │ record contract · telemetry · deterministic gates ·     │
             │ watchdogs · evidence grading                            │
             └────────────────────────────────────────────────────────┘
```

Cross-path invariants the kernel guarantees:

1. **Device abstraction** — adb and hdc share one set of primitives (tap, swipe,
   type, screenshot, element tree, file send/recv, shell). Write once, run on
   both; backend differences are absorbed in `device.rs`.
2. **Record contract** — JSONL, append-only, closed field set. Every path's
   output is structurally identical, so `show` / `stats` / `cat` treat them alike.
3. **Unified telemetry** — one `Telemetry` struct serves every path; fields that
   cannot be read are left empty rather than defaulted, so a script run, a CTS
   run and a VLM run are directly comparable.
4. **Deterministic verdicts** — the model never scores itself. Anything
   unaccounted for is labelled (`NOT_RUN` / `ENV_BLOCKED` / empty), never
   silently zeroed.
5. **Write permissions** — only `tasks/<task>/` plus each path's explicit `--out`.

Upper paths do not depend on each other. Adding a new one should not require
changing the kernel.

## System components

- Kernel: a single Rust binary (main / runtime / brain / device / tree / fold /
  cli / telemetry, plus one module per upper path)
- Text files: `phonefarm.toml` (config + thresholds + prompts + provider chain)
  and the `tasks/<task>/` data directories
- Design contract: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/DESIGN.md` (layering / record contract / six-step loop /
  task isolation / write permissions)

## Directory layout

```
src/                   Rust kernel source
  universal/           Generic core: action protocol, three operators, priority engine, plugin contract
  plugins/             Scenario plugins (all app-specific logic lives here)
  cts.rs               Conformance-test harness (dual protocol, reconciliation, reports, fetch)
  bench.rs capture.rs  On-device model ruler / frame capture pipeline
  hypo.rs caps.rs mtools.rs experiment.rs   Hypothesis-experiment-evidence loop
  keepalive.rs serve.rs script.rs parallel.rs telemetry.rs device.rs
phonefarm.toml         Config: thresholds, prompts, provider chain (keys via secrets.env)
docs/                  Architecture contract + one SPEC per upper path
round.sh               Legacy single-round script (still works; new flows use benchmark directly)
ocr.swift / ocr        OCR text fallback (macOS Vision, auto-compiled on first use)
tasks/<task>/
  lessons.jsonl        Experience base (win/lose counters, atomic rewrite)
  tree.json            Page state-transition graph (recomputed at session end)
  campaign.tsv         Evaluation ledger
  runs/<run-id>/       log.jsonl (ledger) + ctx.log + stepN.jpg/.xml.gz
                       (screenshots/trees are not committed to git)
```

Non-VLM paths (`test-batch`, `cts-fetch`, `bench`, `capture`) write to their own
`--out` directories instead, and do not share the lessons/tree state.

## The six-step execution loop (VLM path)

capture (screenshot + UI tree in parallel) → context assembly (goal + lessons +
last 5 steps + current screen) → model decision (max 4 actions per call) →
three deterministic gates (value-range / prior-offense / blank-click checks) →
execution (coordinate mapping, wait for the screen to settle) → acceptance
(diff the result, judge whether progress was made).

Exits: done → review (normal) | watchdog / budget (stop-loss) | all providers
failed / device failure (abnormal).
After exit: timeline table → experience summary → lessons.jsonl.

## Data contract (log.jsonl record types)

goal / screen / act / diff / note / lesson / ban / hook(verdict|arbit|heal|budget) /
raw / reflect / telemetry / app_event / trace / end

- The model may only write `act` and `note`; anything beyond is treated as a
  format error
- note ≤ 200 chars; lessons ≤ 20 entries; act/diff window of 5 pairs
- `phonefarm schema` prints the complete field contract, generated from code —
  always up to date

## Key mechanisms

- **Probes** (inspect/find/get_state/history): freeze the world and buy time;
  ask for detail from existing observations without re-capturing the screen
- **done pre-check** (#21): the first `done` claim does not end the session; the
  claim plus the current foreground app is echoed back through the alert
  channel, giving the model one chance to self-correct
- **Wobble detection**: repeated tap→back loops or asking the same question
  repeatedly triggers a system warning
- **Telemetry**: a 72-field snapshot per step, written as pure `r=telemetry`
  ledger rows, never enters the model context. Shared by every upper path
- **Multi-device parallel**: `phonefarm parallel` — sub-process fan-out
  (`current_exe()` re-entry into the `run` subcommand), so single-session code
  is untouched and a crashing child cannot take the others down. Run IDs carry
  PID + index so same-second sessions cannot collide on a directory. Per-task
  directory isolation; the same task name across devices is refused by default
  (lessons/tree would collide)
