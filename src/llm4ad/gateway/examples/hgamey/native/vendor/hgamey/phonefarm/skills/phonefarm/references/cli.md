# phonefarm CLI Reference

All inspection commands are read-only, burn zero model tokens, and support
`--json`. Run IDs accept prefix matching; ambiguous prefixes list candidates.

**Only `run` / `benchmark` / `parallel` and the `[evolution]` quota spend model
tokens.** Every other command below is free.

## run / benchmark / parallel / quest — VLM path (BURNS TOKENS)

```bash
# Single session
phonefarm run --task <T> [--serial S] [--endless] [--budget-calls N] [--app P] [--assert "a,b"] "<goal>"

# Multi-round evaluation (round.sh is folded into this)
phonefarm benchmark --task <T> [--rounds N] [--budget-calls N] [--app P] [--assert "a,b"] [--json] "<goal>"

# Multi-device parallel
phonefarm parallel --job "<task>|<goal>|<serial>|<app>|<assert>" [--job ...] [--budget-calls N]
#   or share one goal/task across several --serial values:
phonefarm parallel --task <T> --budget-calls N --serial S1 --serial S2 "<goal>"

# Scenario plugins and the standalone long-running agent
phonefarm plugins                             # list registered scenario plugins
phonefarm quest [--mode auto|dialogue|interact|navigate] [--sec N] [--serial S] [--no-shutdown]

# run / benchmark / script accept --detach: return a run ID immediately,
# then poll with `phonefarm status`.
phonefarm status [<run-id>|--task T]          # running / died / finished
```

## script — deterministic execution & replay (free)

```bash
phonefarm script [--task T] [--serial S] [--app P] [--repeat N] [--settle-ms M]
                 [--no-screen] [--detach] <script-file | run-id>
```

Runs a fixed action sequence, or replays a past session's action stream
verbatim. No model calls, but the same execution and telemetry pipeline — so
its records are structurally identical to VLM sessions and `show` / `stats` /
`cat` treat them the same. Spec: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_SCRIPT_MODE.md`.

The `instrument` primitive inside a script follows the device backend: `aa test`
on hdc devices, `am instrument` on adb devices.

## test-batch / cts-fetch — conformance tests (free)

```bash
phonefarm test-batch (--profile P.json
                    | --module pkg/runner                     # Android
                    | --module oh:bundle/module/Runner        # OpenHarmony
                    | --dir APK-dir)
    [--environment E.json] [--serial S] [--include RE] [--exclude RE]
    [--resume] [--retry N] [--timeout-ms N] [--idle-timeout-ms N]
    [--heal-script PATH] [--install-cmd 'tmpl{apk}'] [--out DIR] [--detach]

phonefarm cts-fetch --remote <device-side path> [--serial S] [--out DIR]
                    [--pattern RE] [--max-mb N]
```

`test-batch` runs the batch; `cts-fetch` pulls results another tool already left
on the device and scans them for assertions (device side read-only). Both
platforms produce the same report format. Full contract: the `phonefarm-cts`
skill, or `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_CTS_HARNESS.md`.

## bench / capture — on-device measurement (free, needs root)

```bash
phonefarm bench --serial S --model <PATH.tflite> [--runs 3] [--json]
                [--limit-ms 4.0] [--metric gpu|invoke] [--gpu-level N] [--no-lock] [--out DIR]
phonefarm bench --serial S --unlock           # roll back a leftover freq lock

phonefarm capture --serial S [--out DIR] [--frames 200] [--max-steps N]
                  [--settle-ms 800] [--mode auto|navigate|dialogue]
                  [--no-shutdown] [--ready-only] [--json]
```

`bench` exit codes: 0/1/2 = PASS/FAIL/ERROR. Spec: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_SR_LOOP.md`.

`capture --ready-only` observes the existing screen up to eight times and saves at most one frame;
explicit `--frames` must be 1. It does not start the game or fall back to navigation, and new requests
do not stop the game or put the screen to sleep. A not-ready result exits 1. The normal lease and
stayon restoration still apply, so the whole call is not read-only. Historical recovery uses its saved policy.
For ordinary capture, `--mode` constrains the capture steps; automatic startup remains separate.
`navigate` suppresses deliberate dialogue/interaction inputs, but movement or classification errors
can still advance game state. Contract: https://github.com/HGamey/docs/blob/main/plans/2026-10-22-agent-platform/CAPTURE-POLICY.md .

## collect — bounded scroll & collect (free)

```bash
phonefarm collect --query "关于我们" --max-swipes 8   # scroll-find: stop on hit, report position
phonefarm collect --max-swipes 12 --json              # list collect: dedupe + honest coverage boundary
```

No model call. At most `--max-swipes` screens per invocation, and it stops the moment a
screen yields nothing new — never scrolls blindly. Overlapping rows between adjacent
screens are de-duplicated by exact text. `coverage.complete` says whether the whole list
was covered (`found` / `end_of_list`) or a bounded limit was hit (`max_swipes` /
`max_items`, more content may remain below). It produces an **inventory**, not a
**verdict** — judging stays on the judging side. Spec: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_OBSERVATION_BUDGET.md`.

## decide — typed decisions, zero tokens

```bash
phonefarm decide --local --seed 7 --request request.json      # degraded tier
phonefarm decide --sidecar "your bridge" --request request.json
phonefarm decide --verify model-dir --manifest manifest.json  # assets only, no inference
```

A Core ML sidecar already exists: `tools/decision-sidecar/` (Swift + Core ML, `make build && make test`).
It ships a 137-byte *reference* model that proves the path end to end -- it is a lexical scorer,
not a trained model. See its README for how to swap in real weights.

## screen — live device screen (free, read-only)

```bash
phonefarm screen                                        # browser: live screen + tap/drag highlights
phonefarm screen --interval-ms 1000 --actions <log.jsonl>
```

A std-only local HTTP server plus one self-contained HTML page (no CDN, no build step).
It does **not** take the device lock -- taking it would make the window useless for watching
someone else's run, which is its whole purpose. The price is written in `--help` and on the page:
**one screencap per interval moves the GPU and CPU, so do not leave it open during a judged
measurement.** Spec: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_SCREEN_WIDGET.md`.

One `state` + a list of typed questions (`choice` / `score` / `noul`) -> a list of typed answers.
**This repo ships no model.** `--local` is a *degraded placeholder*, not a model: its
probabilities are uniform and uncalibrated, and it exists only so the loop keeps moving when
the cloud is down. Same request + same seed -> bit-identical answers. `--sidecar` is the socket
a real (Core ML) model plugs into. Spec: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_DECISION_PATH.md`.

## Evolution loop — hypothesis / experiment / evidence

```bash
phonefarm hyp [--retract id] [--supersede id]      # competing explanations, manual adjudication
phonefarm pred                                      # prediction ledger (registered before the test)
phonefarm caps [--adopt id] [--rollback id]        # capability candidates: adopt / roll back
phonefarm tools [--propose def.json] [--retire id] # measurement-tool lifecycle
phonefarm experiment <spec.toml> [--arm A|B|C] [--ablate no-active-testing|no-cap-screening]
                                 [--resume|--report-only] [--json]
phonefarm export [--task T] --split train|heldout --out <file> [--redact-config toml]
phonefarm eval --set <evalset.toml>                 # model evaluation interface (skeleton)
```

Bypass entirely with `[evolution] enabled = false` in `phonefarm.toml`.
Specs: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_EVOLUTION.md`, ops guide `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/EVOLUTION_OPS.md`.

## serve — MCP tool service (free)

```bash
phonefarm serve [--root DIR]     # 22 phonefarm_* tools: 17 read-only + 5 device-touching
```

Spec: `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_MCP_SERVE.md`.

## Inspection layer

```bash
phonefarm devices                           # adb and hdc devices in one list
phonefarm ios-probe [--serial ios:<udid>]   # iOS real-device checkup: probes every WDA endpoint and
                                            # verifies the UI-source shape against the parser's assumptions
phonefarm last [--task T] [--json]          # latest session verdict (entry point)
phonefarm runs [--task T] [--limit N]       # all sessions of a task
phonefarm status [<run-id>] [--task T]      # liveness: running / died / finished
phonefarm show <run-id> [--task T]          # session summary: goal/actions/verdicts/file list
phonefarm show <run-id> --step N            # single step: screenshot+elements+full dump+raw XML+telemetry
phonefarm show <run-id> --raw               # raw model replies
phonefarm show <run-id> --hooks             # system verdicts (r=hook)
phonefarm show <run-id> --events            # event stream (app_event: crash/ANR/fd growth/network changes)
phonefarm show <run-id> --crashes           # crash artifacts in depth
phonefarm show <run-id> --anr               # ANR traces
phonefarm show <run-id> --trace             # system trace
phonefarm cat <path> [--grep word] [--tail N] [--head N]  # universal printer: .gz decompress / .jsonl pretty / .jpg dimensions
phonefarm stats <run-id>                    # telemetry summary: fps/CPU/memory/temperature percentiles
phonefarm schema [--type R] [--markdown]    # log.jsonl contract docs
phonefarm tree|lessons|campaign [--task T]  # cross-session artifacts
phonefarm tasks [--json]                    # all tasks with stats
phonefarm config [--key k] [--json]         # effective configuration
phonefarm probe --serial S "read-only cmd"  # read-only direct device channel
phonefarm exec --serial S "cmd" --yes       # arbitrary device command (dangerous)
```

## Device keep-alive

```bash
phonefarm keepalive [--serial S] [--json]   # one-shot patrol: wake + unlock + never-sleep for all devices
phonefarm keepalive --status                # read-only report: online / screen-on / policy-effective
phonefarm keepalive --watch [sec]           # resident watchdog (default 300s, re-enumerates each cycle)
```

Idempotent and token-free. HDC patrol order is contractual (unlock swipe
BEFORE the screen-off override — the OH lockscreen stomps the override
otherwise); see `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_KEEPALIVE.md`. Not exposed over MCP (device
write op, same boundary as probe/exec).

## Drill-down path

`phonefarm last` → `show <run-id> --step N` → `cat .../stepN.xml.gz`
