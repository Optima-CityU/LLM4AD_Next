## Why

The platform can dispatch whole-candidate remote evaluations, but has no runnable mobile GPU example. The extracted HGamey executor supplies real-device measurements; its Shader contract needs compilation, a fixed baseline, durable job linkage, and honest result interpretation before evolution can use it.

## What Changes

- Add one Genshin Impact screenshot super-resolution Compute Shader evolution template, reusing the existing task wizard and remote gateway selection.
- Add an SDK-loadable HGamey gateway adapter with local reference frames, fixed baseline, reproducible compilation, progress, recovery and cancellation reconciliation.
- Recognize GLSL Shader sources throughout repository analysis, code generation and remote candidate transport.
- Add a reproducible compatibility patch for the user's extracted executor, providing idempotent submission, lookup and cancellation without unsafe device-process termination.
- Document benchmark scope: operator performance on Genshin frames, not measured in-game FPS; no other games in this change.

## Capabilities

### New Capabilities

- `genshin-shader-evolution`: A Genshin-focused Shader case and recoverable HGamey gateway example with faithful measurement semantics.

### Modified Capabilities

None. Existing remote gateway lifecycle and task creation are reused.

## Impact

Affects examples/applications, examples/remote_evaluation, shared Shader file discovery, coder context collection and candidate bundles. The local extracted executor receives scoped changes, distributed as a compatibility patch rather than vendoring its upstream tree. No new platform dependency, database migration or model API protocol is required. Compilation requires glslangValidator and spirv-val on the gateway host; device prerequisites remain owned by the extracted evaluator.
