# Remote evaluation gateways

Evaluate platform-generated algorithms on your workstation, cluster or laboratory equipment. The gateway connects over outbound HTTPS/SSE. The SDK handles queues, concurrency, progress, recovery, cancellation and reporting; your evaluator handles compilation, execution and scoring.

## Quick start

Use Python 3.12+, a platform-compatible SDK release and your evaluator's dependencies; install a locally built wheel for unpublished versions. Copy **My user ID** from account settings or task configuration.

```bash
pip install -U 'llm4ad-next[gateway-ui]'
llm4ad evaluator-gateway start
```

Open `http://127.0.0.1:8768` and configure the gateway name, allowed user UUIDs and evaluator. The platform defaults to `https://llm4ad-next.cn`. The built-in `example` measures mean squared error between `solve(x)` and `x²` on five fixed inputs; `hgamey` evaluates Genshin workloads on Android devices.

To start an existing evaluator headlessly, install the base package without UI dependencies:

```bash
pip install -U llm4ad-next
llm4ad evaluator-gateway start --no-ui --name lab --allow-user YOUR_USER_UUID --evaluator ./evaluator.py:Evaluator
```

### Docker startup

Generate an editable example and Docker workspace when needed:

```bash
llm4ad evaluator-gateway init ./my-gateway --name lab --allow-user YOUR_USER_UUID
cd my-gateway
llm4ad evaluator-gateway check --evaluator ./evaluator.py:Evaluator --candidate ./candidate
docker compose up -d --build
```

Implement evaluation in `evaluator.py` and add dependencies to `requirements.txt`. Compose serves the dashboard for the same gateway and persists state in `./state`. Before a release is published, place a built `llm4ad_next-*.whl` in `wheels/`.

## Custom configuration

Use CLI flags, an environment-mapping JSON file passed through `--config gateway.json`, or environment variables. Priority is **CLI > explicit JSON > saved dashboard settings > environment > defaults**. Custom JSON environment keys also reach evaluator processes.

### Connection and execution

| Environment variable | CLI flag | Default / purpose |
| --- | --- | --- |
| `LLM4AD_SERVER_URL` | `--server` | `https://llm4ad-next.cn`, platform URL |
| `LLM4AD_GATEWAY_NAME` | `--name` | `local-evaluator`, display name |
| `LLM4AD_ALLOWED_USER_IDS` | `--allow-user` | Required comma-separated UUIDs; repeat the CLI flag for multiple users |
| `LLM4AD_GATEWAY_ADAPTER` | `--adapter` | `custom`; built-in choices are `example` and `hgamey` |
| `LLM4AD_EVALUATOR` | `--evaluator` | Required for custom evaluators: `FILE.py:Class` or `package.module:Class` |
| `LLM4AD_GATEWAY_STATE_DIR` | `--state-dir` | `.llm4ad-gateway`, identity, queue and recovery records |
| `LLM4AD_GATEWAY_CONCURRENCY` | `--concurrency` | `1`, concurrent evaluations; HGamey uses one slot per device |

Keep the complete state directory across restarts and use a separate directory per instance. The gateway creates its own recovery secret; no platform account password is needed. Share it only with trusted users: evaluation code runs with the local account's permissions.

### Dashboard and cleanup

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `LLM4AD_GATEWAY_UI` | Automatic when UI dependencies are installed | Disable with `false` or `--no-ui` |
| `LLM4AD_GATEWAY_UI_HOST` | `127.0.0.1` | Bind address; CLI: `--ui-host` |
| `LLM4AD_GATEWAY_UI_PORT` | `8768` | Dashboard port; CLI: `--ui-port` |
| `LLM4AD_GATEWAY_UI_PASSWORD` | Unset | Independent dashboard password; required for remote access |
| `LLM4AD_GATEWAY_OPEN_BROWSER` | Automatic detection | `--no-open-browser` serves the dashboard without opening a browser |
| `LLM4AD_GATEWAY_UI_LANGUAGE` | `auto` | `auto`, `zh`, `en` |
| `LLM4AD_GATEWAY_UI_THEME` | `auto` | `auto`, `dark`, `light` |
| `LLM4AD_GATEWAY_CLEANUP_ENABLED` | `true` | Clean up completed evidence eligible for removal |
| `LLM4AD_GATEWAY_CLEANUP_INTERVAL_SECONDS` | `30` | Cleanup interval in seconds |
| `LLM4AD_GATEWAY_EVIDENCE_KEEP_SECONDS` | `0` | Additional evidence retention after upload/restoration acknowledgement, in seconds |
| `LLM4AD_GATEWAY_LOG_KEEP_SECONDS` | `604800` | Local execution log retention: seven days |

The dashboard can update the name, allowed users, concurrency and cleanup policy while running. Server, evaluator and state-directory changes wait for active execution, restoration and uploads. Change CLI-pinned values in the startup command.

### Docker settings

| Setting | Purpose |
| --- | --- |
| `LLM4AD_PACKAGE` | Platform-compatible SDK package; local `wheels/` take priority |
| `PIP_INDEX_URL` | Build-time Python package index; default `https://pypi.org/simple` |
| `GATEWAY_BASE_IMAGE` | Built-in basic case image; default `python:3.12-slim` |
| `LLM4AD_GATEWAY_ENV_FILE` | Absolute environment-file path when using the basic case's Compose directly |
| `LLM4AD_GATEWAY_DATA_DIR` | Absolute persistent-data path when using the basic case's Compose directly |

To use the basic case directly, copy `env.example` to `.env` in its `deployment/` directory, fill in allowed users and the environment-file/persistent-data paths, then run `docker compose --env-file .env up -d --build`.

Basic/custom bridge containers use the UI address variables for host port publishing. HGamey uses host networking, so the variables select its listener directly. Containers enable the dashboard without opening a browser; pass its password through the runtime environment.

## Use a gateway in the platform

Select **Remote evaluator** during task creation and choose gateways shared with your user ID. Selected gateways must share the same evaluation contract. Each candidate goes to one gateway; offline gateways can be waited for.

```yaml
evaluator:
  type: remote
  gateway_ids: [YOUR_GATEWAY_UUID]
  timeout: 300
  parallel: true
  batch_size: 4
  candidate_paths: ["solution.py"]
  parameters: {}
```

| Task setting | Meaning |
| --- | --- |
| `timeout` | Per-candidate execution timeout in seconds; excludes waiting for a gateway or capacity |
| `parallel` / `batch_size` | Platform candidate concurrency; execution remains limited by gateway capacity |
| `candidate_paths` | Relative globs for extra candidate files; source/build manifests transfer by default |
| `parameters` | Explicit evaluator parameters, available as `job.parameters` |

Candidate sources and EVOLVE blocks stay on the platform; datasets and evaluation dependencies stay on the gateway. Transfer excludes `config.yaml`, `.env*`, runtime state and dataset directories by default. Remote tasks require the platform runtime; standalone `llm4ad run` does not create a platform remote task.

## Implement a custom evaluator

Subclass [GatewayEvaluator](../../../src/llm4ad/gateway/sdk/contracts.py), declare an `EvaluationContract` and implement `async evaluate(job, reporter)`. The [complete basic example](../../../src/llm4ad/gateway/examples/basic/evaluator.py) returns negative mean squared error. Aggregate `score` is maximized; each metric declares its own direction.

| Interface | Purpose |
| --- | --- |
| `contract` | `evaluator_id` names the implementation; `version` / `data_version` identify evaluation logic and local data; `metrics` declares metric directions |
| `job.project_root` / `timeout` / `evaluation_profile` | Candidate directory, execution budget and `standard` / `elite` profile |
| `job.parameters` | Explicit custom parameters from the web task; unused by the basic case |
| `reporter.progress(...)` / `message(...)` | Progress and status events |
| `reporter.metadata(dict)` | Additional JSON information |
| `reporter.artifact(name, path, media_type)` | Declare reports, images or traces for upload |
| `reporter.local_log(name, description=...)` | Register a local log for the gateway dashboard |
| `EvaluationResult` | Return `score`, `metrics`, `monitor_metrics`, `metadata`, `evolution_feedback`, `duration_ms`, `artifacts` |
| `EvaluationError` | Known evaluation failure, with optional feedback, metadata and artifacts |
| `EvaluationUnavailableError` | Wait for recovery when a resource or external outcome is unavailable |

Evaluation runs in an isolated process. Locate local tools/data relative to `__file__` or an absolute path; the working directory is the candidate directory.

The following extensions are optional. Ordinary evaluators need no scheduling, recovery or cancellation implementation:

| Extension | Purpose |
| --- | --- |
| `before_task(job, reporter, operation)` | Compile or prepare resources before an operation |
| `after_task(job, reporter, operation, *, result=None, error=None)` | Diagnostics and cleanup; forced termination cannot guarantee this hook runs |
| `reporter.save_state(dict)` / `saved_state` | Persist/read external execution handles |
| `recover(job, reporter)` | Reconcile external work and return its result, or `None` if uncertain |
| `cancel(job, saved_state)` | Return `True` only after external execution has stopped |
| `create_runtime()` / `runtime.local_logs(...)` | Persistent device resources, readiness checks and existing external logs |

`operation` is `evaluate`, `recover` or `cancel`. Use `job.id` / `attempt_id` for idempotent external submission and persist execution handles before waiting.

## Logs, artifacts and recovery

| Content | Where to find it / retention |
| --- | --- |
| Status events | Gateway dashboard with filters/history; retained in `metadata.evaluation_messages` |
| Local execution logs | Gateway stdout/stderr views; not automatically uploaded, expire by log-retention policy |
| Declared artifacts | Select an evolution node to see metadata and attachments in the right-hand **Node information** panel; click image thumbnails to enlarge, or preview and download other files from the compact list |
| Lifecycle timing | Actual start/end and total elapsed time including hooks; evaluator `duration_ms` remains unchanged |

Local evaluators write inside `cfg.artifact_dir` and declare `EvaluationResult.artifacts`. Remote evaluators use `reporter.artifact()` or `job.artifact_dir`. RustFS/S3 stores declared files and returned behavior visualizations. See [evaluation_artifacts](../../../examples/evaluation_artifacts/) for a runnable example.

Failed uploads retry file transfer only. Gateway evidence cleanup waits for file/result acknowledgement and external restoration; local logs have separate retention. Platform files remain until task deletion.

| Situation | Behavior |
| --- | --- |
| Network loss | Local evaluation continues and reports retry; SSE reconnects and HTTP heartbeats synchronize state |
| Gateway restart | Reuse the state directory; unfinished local work can replay from the beginning after confirming old processes stopped and no external handle exists |
| Execution timeout | Stop the local process tree; reconcile external work separately |
| Platform stop | Cancel queued work and persist cancellation requests for accepted work |
| Uncertain external state | Wait for recovery; do not repeat external work or reassign accepted jobs automatically |

Confirm old execution has ended before manually resolving uncertain work:

```bash
llm4ad evaluator-gateway health --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action retry --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway resolve JOB_UUID --action cancelled --state-dir ./.llm4ad-gateway
```

## HGamey Genshin example

The [case source](../../../src/llm4ad/gateway/examples/hgamey/) groups compilation, device execution, wireless ADB and deployment. Select `hgamey_shader` in the task wizard, then bind a model and gateway. Locate installed sources with `llm4ad evaluator-gateway source --adapter hgamey`.

| Environment | Requirement |
| --- | --- |
| Host | Currently verified on Linux amd64 with Compose host networking; persistent directories writable by UID 1000 |
| Build | Rust 1.99, OpenJDK 17, C++, NDK r27d, Android SDK API 35 / build-tools 35.0.0; supplied by the Dockerfile |
| Execution tools | phonefarm, ADB 36.0.2, GLSL/SPIR-V tools, FFmpeg, NumPy, Pillow, Perfetto Python 0.59.1 and Trace Processor; included in the image |
| Phone | Rooted Android 11+ ARM64, authorized wireless ADB, Vulkan 1.1 compute, GPU timestamps and accessible KGSL/GED frequency controls |
| Inputs | Installed/signed-in Genshin, real reference frames, physical serial and local ADB identity; not distributed with the SDK |

The image builds the runner, Vulkan layer and refbench APK from bundled sources. Install the test APK manually when using the refbench carrier. Game validation needs a repeatable in-game scene and device-specific resolution, waits and `enter_script` in `config/game-check.json`. Direct system execution requires equivalent tools.

Create `.env` from [env.example](../../../src/llm4ad/gateway/examples/hgamey/deployment/env.example) in the case's `deployment/` directory, configure the following values, then run `docker compose up -d --build`.

| Custom setting | Default / purpose |
| --- | --- |
| `HGAMEY_DEVICES_JSON` | Device array with `id`, `host`, `port`, `expected_serial`, `device_profile`; connection port defaults to `5555` |
| `HGAMEY_REFERENCE_PATH` | `/inputs/genshin`, real reference-frame directory |
| `HGAMEY_DATA_DIR` / `HGAMEY_PHONEFARM_DIR` | Required host directories for gateway/executor state and device locks |
| `HGAMEY_INPUTS_DIR` / `HGAMEY_CONFIG_DIR` | Required host reference-frame and game-configuration directories |
| `ADB_IDENTITY_FILE` / `ADB_STATE_DIR` | Required authorized ADB private key and pairing state |
| `ADB_BIN` / `ANDROID_ADB_SERVER_PORT` | `/usr/bin/adb` / `5039`, container ADB path and server port, separate from the phone's connection port |
| `HGAMEY_STARTUP_CHECK` | `true`, run a readiness check at startup |
| `HGAMEY_GAME_CHECK_CONFIG` | Optional game-validation JSON; deployment environment example uses `/config/game-check.json` |
| `HGAMEY_EVAL_SECONDS` / `HGAMEY_EVAL_ROUNDS` | `5` / `8`, standard seconds per arm and rounds |
| `HGAMEY_ELITE_EVAL_SECONDS` / `HGAMEY_ELITE_EVAL_ROUNDS` | Defaults to at least `5` / `16`, elite measurement budget |
| `HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT` | `1`, minimum percentage for confirming improvement, in `[1, 100)`; unconfirmed improvement remains a valid `no_advantage` evaluation with unchanged raw scores |
| `HGAMEY_COMPILE_TIMEOUT` / `HGAMEY_POLL_INTERVAL` | `60` / `2` seconds, compilation timeout and result polling |
| `RUST_IMAGE` / `PYTHON_IMAGE` | `rust:1.99-slim-bookworm` / `python:3.13.16-slim-bookworm`, build base images |
| `HGAMEY_NATIVE_IMAGE` | `native-runtime`, built from bundled source by default; may select a verified native runtime image |
| `APT_MIRROR` / `CARGO_REGISTRY_MIRROR` | Optional system-package and Rust build mirrors |
| `ANDROID_REPOSITORY_BASE` / `PIP_INDEX_URL` / `TRACE_PROCESSOR_URL` | Optional Android-toolchain, Python-package and Trace Processor download sources |

Each device evaluates serially. The current quality check center-crops the first fixed reference frame, which must be at least 1920×1080. This measures static reconstruction quality; operator latency gains alone do not establish game FPS gains. Power is disabled by default and unavailable metrics remain unmeasured. Keep keys, device configuration and real reference frames local.

Game validation uploads the existing post-window PNGs from its first two measurement pairs, at most four files under `game/shots/`. Descriptions identify the baseline, candidate (or comparison) and round. Select the corresponding evolution node to enlarge or download its thumbnails in the right-hand **Node information** panel. These show the observed scene, not a same-frame PSNR comparison. The screenshot manifest and original-byte snapshots are frozen for retries; optional retrieval failures are recorded as artifact diagnostics without invalidating measured scores. Missing screenshots are not recaptured or generated; recordings and private reference frames are not uploaded automatically.

## Platform deployment settings

Update the backend, workers, task-runner, frontend and SDK together, and apply Alembic migrations. Standard `docker/compose.yml` includes `remote-evaluation-worker`. For the production base, retain existing overrides and add:

```bash
cd docker
docker compose -f compose-cloud.yml -f compose.remote-evaluation.yml up -d --build
```

| Setting | Default / purpose |
| --- | --- |
| `LLM4AD_COMPOSE_BASE` | `compose.yml` for standard deployment; set `compose-cloud.yml` in `.env` for the production command above |
| `REMOTE_EVALUATION_CONCURRENCY` | `10`, platform remote-evaluation worker concurrency |
| `LLM_PROXY_ENABLE` / `LLM_PROXY_BASE_URL` | For model calls after long waits, set `True` and a proxy URL reachable by task containers |
| `EVALUATION_PREVIEW_MAX_BYTES` | `2097152` (2 MiB); limits server-side inline previews. Image thumbnails and enlarged views use authenticated original downloads, which are unaffected |
