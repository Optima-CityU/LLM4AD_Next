---
name: phonefarm-cts
description: Drive the phonefarm conformance-test harness (CTS / XTS) on Android and OpenHarmony devices. Use when running instrumentation test batches via `phonefarm test-batch` — starting/stopping batches, querying status, exporting JUnit/summary reports, resuming interrupted batches, interpreting the PASS / ASSERTION_FAIL / ENV_BLOCKED / TIMEOUT / NOT_RUN verdicts — or when pulling and scanning results another tool already left on a device via `phonefarm cts-fetch`. Covers both wire protocols (Android `am instrument` / INSTRUMENTATION_*, OpenHarmony `aa test` / OHOS_REPORT_*), the A2OH bridge environment, and the same environment.json / per-API profile files as the A2OH team's CTS_FAST_TESTING.md flow.
license: MIT
metadata:
  version: 2.0
  source-repo: github.com/BH3GEI/phonefarm
---

# phonefarm-cts

Batch conformance-test execution: `phonefarm test-batch` is the long-running
unattended executor — "run these N modules overnight, survive deadlocks, and
give me per-case evidence bundles plus a standard report in the morning".

**Two platforms, one report format.** The harness parses both official runner
protocols and normalises them into the same verdicts, reconciliation and
reports, so nothing downstream has to care which platform ran:

| Platform | Channel | Command | Wire protocol |
|---|---|---|---|
| Android | `adb` | `am instrument -r -w` | `INSTRUMENTATION_*` |
| OpenHarmony | `hdc` | `aa test` (arkxtest Delegator) | `OHOS_REPORT_*` |

Its origin is the A2OH bridge, where there is no cts-tradefed infrastructure
and a CTS module is really just a test APK plus `am instrument`. It is no
longer limited to that: **any instrumentation-style test suite** (CTS, XTS,
in-house packages) on real Android devices, emulators, or OH devices works.

Relative to the A2OH team's single-API quick loop (CTS_FAST_TESTING.md): the
colleague's tool answers "did my adapter change break this API slice?"; this
harness is the batch executor on top of it. Both speak the same contract:

- **Granularity**: `Class#method` slices, never blind whole-suite runs.
- **Verdicts** (literal strings, do not paraphrase): `PASS`,
  `ASSERTION_FAIL`, `ENV_BLOCKED`, `TIMEOUT`, `NOT_RUN`. Skipped or
  zero-case runs NEVER count as PASS.
- **Recovery discipline**: on disconnect — stop device writes, leave a
  recovery record, re-verify `boot_id` after reconnect, never reuse old
  PIDs. A batch is not "complete" while recovery is `PENDING`.
- **Config**: consumes the colleague's `environment.json` and per-API
  profile JSON directly.

## When to use this skill

- Launching a batch from an APK directory, explicit module, or per-API profile
  — installed modules on either platform; local APK installation only on Android
- Pulling results that **another** tool already produced on the device, and
  scanning them for assertion failures (`cts-fetch`), without re-running
- Checking progress / exporting reports of a running or finished batch
- Resuming an interrupted batch (`--resume`) or retrying flaky cases
  (`--retry`)
- Deciding whether a batch outcome is claimable (recovery VERIFIED, no
  ENV_BLOCKED/TIMEOUT) vs. diagnostic-only

Local APK installation requires successful aapt/aapt2 metadata, transport and remote completion, exact installer success, and current installed APK verification. Evidence is retained under `<out>/installation`. Unknown completion is not automatically retried and does not yet create a typed recovery barrier; transport termination does not prove remote stop or rollback. Legacy replace installation is distinct from the typed games fresh-install transaction.

## Commands

```bash
# 1. Already-installed OH profile (Class#method slices); omit apk_path
phonefarm test-batch --profile .work/cts-fast/profiles/getPackageInfo.json \
  --environment .work/cts-fast/environment.json --serial hdc:<key>

# 2. Android APK directory. Skip installation only after current installed
#    APK identity and bytes are verified; transfer cache alone is insufficient.
phonefarm test-batch --dir ./cts-apks/ --serial <android-serial> \
  --include 'android\.(content|graphics)\..*' --exclude '.*\.perftest\..*' \
  --retry 1

# 3. Single module, ad-hoc — Android
phonefarm test-batch --module android.content.cts/androidx.test.runner.AndroidJUnitRunner

# 4. Single module — OpenHarmony. All THREE segments are required
#    (stage model needs the HAP module name); a malformed value is an error,
#    never a silent fallback to Android.
phonefarm test-batch --module oh:com.example.demo/entry_test/OpenHarmonyTestRunner \
  --serial hdc:<key>

# 5. Resume an interrupted batch (same --out dir)
phonefarm test-batch --dir ./cts-apks/ --resume --out cts-batch-20260908-101500

# 6. Run it in the background and poll the summary
phonefarm test-batch --profile P.json --out results/ --detach
```

Flags:

| Flag | Meaning |
|---|---|
| `--profile` | Colleague's per-API JSON: package/runner/cases(Class#method list) |
| `--environment` | Colleague's environment.json (serial/runtime/abi/cts version) |
| `--dir` | Directory of test APKs; auto install + runner discovery |
| `--module` | `pkg/runner` (Android) or `oh:bundle/module/Runner` (OpenHarmony) |
| `--include/--exclude` | Regex on module name `pkg/runner` |
| `--resume` | Skip modules marked done in `batch_state.json` |
| `--retry` | Per-case retries for ASSERTION_FAIL/TIMEOUT slices |
| `--timeout-ms` | Total watchdog per instrument run (default 600000) |
| `--idle-timeout-ms` | Silent-pipe watchdog (default 90000) |
| `--heal-script` | External A2OH bridge restart script, fired on crash/disconnect |
| `--install-cmd` | Only exact `pm install -r {apk}` is supported, equivalent to the Android default. HAP/A2OH/custom installation requires a future identity/completion adapter; run already-installed modules without apk_path. |
| `--detach` | Run in the background; poll `<out>/summary.json` |
| `--out` | Output directory (default `cts-batch-<ts>`) |

Profile fields for OpenHarmony (both optional, Android is the default):

| Field | Meaning |
|---|---|
| `platform` | `"android"` (default) or `"oh"`. An unrecognised value is an error, not a fallback |
| `hap_module` (aliases `hap`, `oh_module`) | OH stage-model HAP module name. Distinct from the existing `module` field, which is the original CTS module name used only as a report label |

## Pulling results someone else produced (`cts-fetch`)

When the results are already on the device — produced by the official CTS/XTS
suite or a colleague's flow — do not re-run them. Pull and scan:

```bash
phonefarm cts-fetch --remote /data/local/tmp/cts_result --serial hdc:<key> --out fetched/
```

| Flag | Meaning |
|---|---|
| `--remote` | **Required.** Device-side file or directory. Missing → exit 2, no path guessing |
| `--serial` | Target device; both adb and hdc |
| `--out` | Local output dir (default `cts-fetch-<ts>`) |
| `--pattern` | Custom assertion-line regex (default matches common assertion-failure text) |
| `--max-mb` | Per-file size cap (default 16) |

Discipline:

- **Device side is read-only** — `file recv` only. Nothing is written to the
  device, no state is changed.
- **Failure is reported, not faked** — no device heartbeat, or a remote path
  that is missing/empty, is an error. It will not emit a zero-hit report and
  call that a successful scan.
- **Skips are counted** — files over `--max-mb`, binary sniffs (NUL in the
  first 8 KB), and media/archive extensions are skipped but counted in
  `skipped_files`, so "scanned and found nothing" stays distinguishable from
  "never scanned". Per-file hits cap at 500 lines with a `truncated` flag.

Output:

```
<out>/raw/              the result tree pulled off the device
<out>/assertions.json   scanned_files / skipped_files / total_matches /
                        files[] { path, truncated, hits[{line, text}] }
```

If what you pulled happens to be one of this harness's own batch dirs, the run
also points out the `summary.json` / `junit_*.xml` inside it — those are
structured reports and strictly better than line scanning. Read them first.

## Output contract

```
<out>/
  summary.json                 # batch totals, per-verdict counts, recovery VERIFIED|PENDING
  junit_<module>.xml           # standard JUnit per module (failures≠errors≠skipped)
  batch_state.json             # resume bookkeeping
  recovery_pending.json        # present ONLY if a recovery is still owed
  <module>_stdout.log          # full raw runner output per module
  artifacts/<module>/<case>/   # per failed case Crash Bundle:
    stdout.log                 #   case-window runner output
    hilog_slice.log            #   device log sliced to case window ±5s
    telemetry.json             #   before/after mem/FD/thread deltas
    meta.json                  #   verdict, timing, window anchors
```

## Status query & report export

Batches print progress to stdout; for detached operation run with your
platform's backgrounding and poll the out dir:

- `cat <out>/batch_state.json` — per-module done/failed
- `cat <out>/summary.json` — final verdicts (only trust it when
  `recovery == "VERIFIED"`)
- JUnit XML files import directly into CI test reporting

## Reconciliation entries (never silently absent)

Cases must never vanish without a record. The harness synthesizes:

- In-flight case at watchdog kill → `TIMEOUT`; at process crash / broken pipe →
  `ENV_BLOCKED` with the real `Class#method` (profile reconciliation matches it).
- `numtests` shortfall on whole-module runs → one `NOT_RUN` entry
  `(runner)#unaccounted_cases_xN`.
- Runner-level errors (package not installed, bad component), even when the
  device drops the `Error=` line — `ENV_BLOCKED` entry `(runner)#runner_error`
  with the raw message. Zero-case silence is never a pass.
- `--resume`: done modules are skipped **and their previous report is carried
  forward** into the new summary (evidence is preserved, totals stay honest);
  failed modules rerun automatically.

## OpenHarmony protocol notes

OH runner lines are normalised at the parser entry, so everything above applies
unchanged. Two OH-specific behaviours are worth knowing when reading results:

- **`OHOS_REPORT_CODE: -1` is a case error** (uncaught exception), not "no
  result". It is recorded as `ASSERTION_FAIL` with the stack preserved from the
  `stack` field — never silently dropped. Android's own `-1` path is untouched
  by this and behaves exactly as before.
- **Some OH builds emit two closing lines** for one case (`OHOS_REPORT_CODE`
  *and* `STATUS_CODE`). These are de-duplicated; one case yields one result.

Code mapping (`OHOS_REPORT_CODE` → verdict): `0` → PASS, `-1` → ASSERTION_FAIL,
`-2` → ASSERTION_FAIL, other negatives → NOT_RUN, non-numeric → line ignored.

Command shape: `aa test -b <bundle> [-m <module>] -s unittest <Runner>
[-s class C#m] [-s k v]…`. The OH runner class name is used **as-is** (no
package prefix is added, unlike Android's relative `.FooRunner` form), and OH
timeouts are in **seconds** — convert before passing them through `env_args`;
the harness will not guess units for you.

**Evidence status**: both protocols are covered by unit tests against captured
fixtures, including a regression guard proving the Android path is byte-for-byte
unchanged. Real-device end-to-end verification on the OH side is still
outstanding — do not claim "dual protocol verified in production" until a real
`aa test` run has been compared against official results.

## Claim discipline (matches CTS_FAST_TESTING.md §4)

- Runner errors, missing cases, zero-case runs, and skips must NOT be
  reported as passes — the harness already maps these to
  ENV_BLOCKED / NOT_RUN; don't paper over them in summaries.
- If `recovery == "PENDING"`, say the batch is INCOMPLETE, not failed and
  not passed.
- A green batch on selected slices is evidence for those APIs only — never
  claim "CTS certified" from slice runs.
