# Source ownership

The adapter, benchmark and telemetry were moved from examples/remote_evaluation/hgamey in this repository. The serialized executor and exact-shader layer builder were imported from the user-supplied hgamey-evaluator extraction after its verified November 2026 compatibility/integrity fixes. Native sources are included below; Android binaries and toolchains are built/installed by the optional case deployment. No credentials, device configuration, reference frames or runtime state are packaged.


The former flat Python executor is partitioned without changing the evaluation
algorithm: queue/idempotency/recovery in `execution/queue.py`, private HTTP in
`execution/api.py`, leased dispatch in `execution/backend.py`, wireless identity
and transport in `execution/adb.py`, device/foreground restoration in
`execution/device.py`, and atomic input records in `execution/storage.py`.
GLSL/SPIR-V validation lives in `compilation/shader.py`; the exact native layer
builder is `compilation/game_layer.py`. Pinned upstream protocol bytes and their
source hashes remain under `protocol/upstream/`. Reusable gateway templates are
outside this case, and this case's deployment is in its own `deployment/`.


The native source used by that extraction is now included in `native/vendor/hgamey`:
phonefarm (Rust, device helpers, Vulkan layer and runner), refbench (Android test
application) and original evaluation contracts. `native/source-manifest.json`
records the upstream base revision, included local compatibility changes, scope,
source sizes and hashes. Generated binaries, private device files, raw captures,
and upstream optional dashboard/optimization services are excluded. Original
source comments and phonefarm's third-party notices are retained. This snapshot
has not been relabeled as an unmodified upstream checkout.
