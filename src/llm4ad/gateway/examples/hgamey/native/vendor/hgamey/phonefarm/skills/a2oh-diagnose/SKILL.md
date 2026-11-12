---
name: a2oh-diagnose
description: Analyze A2OH (Android-on-OpenHarmony bridge) Crash Bundles produced by `phonefarm test-batch` and propose root-cause hypotheses and fixes. Use when a CTS case came back ASSERTION_FAIL / ENV_BLOCKED / TIMEOUT and you need to read the bundle (stdout.log + hilog_slice.log + telemetry.json), correlate the Java exception stack with OpenHarmony hilog evidence, and identify missing bridge stubs — unimplemented Linux syscalls, bionic libc symbols, SQLite locking semantics, ContentProvider/PackageManager gaps — with concrete repair suggestions for the adapter runtime.
license: MIT
metadata:
  version: 1.0
  source-repo: github.com/BH3GEI/phonefarm
---

# a2oh-diagnose

Diagnostic reasoning over A2OH Crash Bundles. The harness captures the
evidence; this skill turns it into a bridge-engineering action.

## A2OH mental model (load before diagnosing)

A2OH runs Android apps on OpenHarmony **without a VM**: a unified adapter
runtime (`oh-adapter-runtime-unified.jar`, ~76 patched framework classes)
sits between the app's ART and OH system services. Native libs are served
through a bionic-compatible layer and syscall translation. Known historical
fault lines (from the toutiao/wechat reproduction project):

- **SQLite locking**: stock Android SQLite semantics (WAL, file locks) map
  imperfectly onto OH — deadlocks appear as *silent* hangs, fixed by
  `libwlsqlite.so` in that project. Signature: idle-timeout TIMEOUT with
  the main thread parked in `SQLiteDatabase`/`SQLiteConnection` native calls.
- **Native ABI mismatch**: libs like libWCDB / BoringSSL crash on symbol
  gaps. Signature: `Process crashed` + hilog `SIGSEGV`/`SIGABRT` with
  unresolved-symbol or `dlopen` failure lines.
- **ContentProvider stubs**: empty/null provider implementations break
  Application init chains. Signature: NPE / `DeadObjectException`-like
  failures in `ActivityThread.handleBindApplication`.
- **TLS/SSL path**: handshake blocks look like `user_canceled` network
  errors but are cert-store / Conscrypt gaps.

## Diagnostic procedure

1. **Read meta.json first** — verdict, duration, window. Classify:
   - `TIMEOUT` + empty stream → hang (deadlock / parked IPC / silent pipe)
   - `TIMEOUT` + partial stream → slow path, look for retry loops in stdout
   - `ENV_BLOCKED` with `Process crashed` → native crash, go to hilog
   - `ASSERTION_FAIL` → genuine behavioral divergence, go to the stack
   - `NOT_RUN` → runner/skip issue, check fixture & runner availability
2. **stdout.log** — find the first effective error (not the runner's
   summary noise): the first `E/` line, stack top, or
   `INSTRUMENTATION_STATUS_CODE: -2` block. Note the failing frame that is
   inside `android.*` / `com.android.*` — that's the adapter's territory;
   frames inside the test class itself are the contract being asserted.
3. **hilog_slice.log** — correlate by timestamp with the failure window:
   - `SIGSEGV`/`SIGABRT`/`fault addr` → native crash; capture the faulting
     `.so` and PC; unresolved `dlopen`/`undefined symbol` lines above it
     name the missing bionic symbol.
   - `Watchdog`/`Binder`/`IPC` stalls, or the app thread repeatedly in
     `futex`/`__ioctl` → bridge IPC deadlock.
   - `Sqlite`/`database is locked` → SQLite lock-semantics gap.
   - `E A2OH` / adapter-log tags — the bridge's own log lines usually name
     the unimplemented stub directly (`Unimplemented syscall`,
     `No impl for ...`, `return default`). Quote them verbatim.
4. **telemetry.json** — the delta section is the leak story:
   - `fd_count` jump ≥ +20 without return → FD leak (missing close in a
     bridge file/pipe wrapper); cross-reference `socket_count`.
   - `vm_rss_kb` growth across one case ≥ 50MB → native heap or bitmap
     leak in the bridge's graphics path.
   - `crash_count`/`anr_count` increments confirm the hilog signature.

## Fix-suggestion format

For each confirmed root cause, emit:

```
[根因]  <一句话: 哪个桥层组件缺什么>
[证据]  <bundle 里的原文行: 文件 + 行内容,逐字引用>
[类别]  syscall 缺失 | bionic 符号缺失 | SQLite 锁语义 | Provider 桩 | 权限/能力未就绪 | 真断言分歧
[建议]  <具体到桩函数/符号/返回值的修复方向,例如:
         "在 bionic 兼容层补 `fstatfs64`(返回 tmpfs 语义即可);
          或在 oh-adapter-runtime 的 SQLiteOpenHelper 路径强制 journal_mode=TRUNCATE 绕过 WAL 锁映射">
[边界]  <此结论覆盖哪些用例;哪些仍是未知>
```

## Discipline

- Never invent evidence: quote bundle lines verbatim; if the bundle lacks
  the decisive line, say what's missing and which extra capture
  (`hilog -b E`, tombstone pull, `hidumper --ipc`) would settle it.
- Distinguish **bridge gaps** (fix in adapter) from **test-environment
  gaps** (missing fixture/permission → ENV_BLOCKED, fix in profile/setup)
  from **genuine assertion divergence** (the API contract itself differs —
  the most valuable finding; report, don't patch the test).
- A2OH specifics outrank generic Android folklore: prefer bridge-layer
  explanations over app-layer ones when the evidence permits.
