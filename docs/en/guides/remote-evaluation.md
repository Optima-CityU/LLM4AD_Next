# Remote evaluation gateways

The evaluation gateway connects to the platform over outbound HTTPS/SSE and runs candidate code on the gateway side. The platform sends evaluation jobs to the registration selected by the user. The gateway SDK handles local queues, concurrency, progress, recovery, cancellation and reporting. The evaluator defines the data, execution method and scoring rules.

## Quick start

Use Python 3.12+, a platform-compatible SDK release and your evaluator's dependencies; install a locally built wheel for unpublished versions. Copy **My user ID** from account settings or task configuration.

```bash
pip install -U 'llm4ad-next[gateway-ui]'
llm4ad evaluator-gateway start
```

The gateway starts its dashboard by default. Open `http://127.0.0.1:8768` and configure the platform URL, gateway name and allowed user UUIDs. The default platform URL is `https://llm4ad-next.cn`. To try the basic case, select the built-in `example` in the dashboard and start the connection. It registers the `square-error-example` evaluator, which measures mean squared error between `solve(x)` and `x²` on five fixed inputs.

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

## Use a gateway in the platform

Open the task editor and choose **Remote evaluator**. The form shows the local gateway connection steps and **My user ID**. Add that ID to the gateway's allowed users. Once the gateway registers, it appears under **Online gateways**.

Select one gateway card and set the evaluation timeout and candidate concurrency. The card shows the name, evaluator identifier and versions from the gateway's current registration. When the task starts, the platform pins the selected registration UUID and its evaluation contract. All later candidates use those two values to resolve the evaluation target.

The picker shows currently online registrations shared with you. Evaluations wait for capacity when the selected gateway is busy and for the same registration to reconnect when it goes offline. Time in the platform queue or waiting for a connection does not count toward the execution timeout. Disconnections preserve the retry budget. When rerunning an older task without one explicit registration, pick a gateway card again.

```yaml
evaluator:
  type: remote
  gateway_id: YOUR_EVALUATOR_REGISTRATION_UUID
  timeout: 300
  parallel: true
  batch_size: 4
```

| Task setting | Meaning |
| --- | --- |
| `gateway_id` | Unique registration UUID of the selected gateway and evaluator |
| `timeout` | Per-candidate execution timeout in seconds |
| `parallel` / `batch_size` | Platform candidate concurrency, subject to available gateway capacity |
| `candidate_paths` | Optional YAML setting for relative globs that include extra candidate files. Recognized sources and build manifests transfer by default |
| `parameters` | Optional YAML setting for custom evaluator parameters, available as `job.parameters` |

The platform sends candidate sources and build files. Evaluation data and dependencies stay on the gateway. Transfer excludes `config.yaml`, `.env*`, runtime state and dataset directories by default. The platform runtime starts remote evaluation jobs.

## Custom configuration

Use CLI flags, an environment-mapping JSON file passed through `--config gateway.json`, or environment variables. Priority is **CLI > explicit JSON > saved dashboard settings > environment > defaults**. Custom JSON environment keys also reach evaluator processes.

### Connection and execution

| Environment variable | CLI flag | Default / purpose |
| --- | --- | --- |
| `LLM4AD_SERVER_URL` | `--server` | `https://llm4ad-next.cn`, platform URL |
| `LLM4AD_GATEWAY_NAME` | `--name` | `local-evaluator`, display name |
| `LLM4AD_ALLOWED_USER_IDS` | `--allow-user` | Required comma-separated UUIDs; repeat the CLI flag for multiple users |
| `LLM4AD_GATEWAY_ADAPTER` | `--adapter` | Defaults to `custom`. Use `example` for the square-error case |
| `LLM4AD_EVALUATOR` | `--evaluator` | Required for custom evaluators: `FILE.py:Class` or `package.module:Class` |
| `LLM4AD_GATEWAY_STATE_DIR` | `--state-dir` | `.llm4ad-gateway`, identity, queue and recovery records |
| `LLM4AD_GATEWAY_CONCURRENCY` | `--concurrency` | `1`, concurrent evaluations for the generic gateway |

Keep the complete state directory across restarts and use a separate directory per instance. The gateway creates its own recovery secret; no platform account password is needed. Share it only with trusted users: evaluation code runs with the local account's permissions.

### Dashboard and cleanup

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `LLM4AD_GATEWAY_UI` | `true` | Disable with `false` or `--no-ui` |
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

Basic and custom containers use bridge networking and publish the dashboard on the configured host address and port. Containers start the dashboard by default. The `--no-open-browser` flag controls automatic browser launch. Pass the dashboard password through the runtime environment.

## Implement a custom evaluator

Subclass `GatewayEvaluator`, declare an `EvaluationContract` and implement `async evaluate(job, reporter)`. The platform maximizes the aggregate `score`. Each metric declares its own direction. The basic case returns negative mean squared error. [SDK interfaces](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/src/llm4ad/gateway/sdk/contracts.py) · [Basic evaluator source](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/src/llm4ad/gateway/examples/basic/evaluator.py)

| Interface | Purpose |
| --- | --- |
| `contract` | `evaluator_id` names the implementation; `version` / `data_version` identify evaluation logic and local data; `metrics` declares metric directions |
| `job.project_root` / `timeout` / `evaluation_profile` | Candidate directory, execution budget and `standard` / `elite` profile |
| `job.parameters` | Custom parameters explicitly set in the task configuration |
| `reporter.progress(...)` / `message(...)` | Progress and status events |
| `reporter.metadata(dict)` | Additional JSON information |
| `reporter.artifact(name, path, media_type)` | Declare reports, images or traces for upload |
| `reporter.local_log(name, description=...)` | Register a local log for the gateway dashboard |
| `EvaluationResult` | Return `score`, `metrics`, `monitor_metrics`, `metadata`, `evolution_feedback`, `duration_ms`, `artifacts` |
| `EvaluationError` | Known evaluation failure, with optional feedback, metadata and artifacts |
| `EvaluationConfigurationError` | Invalid evaluator parameters or configuration. Ends the evaluation |
| `EvaluationUnavailableError` | Retry transient infrastructure failures within the original execution budget |
| `EvaluationPreparationError` | Fail preparation known not to have submitted external execution instead of waiting indefinitely |
| `TaskCheckpoints(job, directory, inputs=..., context=...)` | SDK-owned frozen inputs and checkpoints for local compilation/preprocessing |

Evaluation runs in an isolated process. Locate local tools/data relative to `__file__` or an absolute path; the working directory is the candidate directory.

Use `EvaluationError(..., metadata={"failure_category": "candidate"})` for invalid candidate code or output. Use `EvaluationUnavailableError` for transient infrastructure faults. Unclassified evaluator exceptions use the `evaluator` category.

The following extensions are optional. Ordinary evaluators need no scheduling, recovery or cancellation implementation:

| Extension | Purpose |
| --- | --- |
| `before_task(job, reporter, operation)` | Compile or prepare resources before an operation |
| `after_task(job, reporter, operation, *, result=None, error=None)` | Diagnostics and cleanup; forced termination cannot guarantee this hook runs |
| `reporter.save_state(dict)` / `saved_state` | Persist/read external execution handles |
| `recover(job, reporter)` | Compatibility hook; the SDK now owns bounded fresh executions |
| `cancel(job, saved_state)` | Return `True` only after external execution has stopped |
| `cleanup_timeout` | Positive finite cleanup deadline in seconds; default `30`, covering hooks and external restoration |
| `create_runtime()` / `runtime.local_logs(...)` | Custom runtime, readiness checks and external logs |

`operation` is `evaluate` or `cancel`; `recover` remains for compatibility. Use `job.id` / `attempt_id` plus `job.execution_id` for execution-scoped external idempotency, and save external handles before waiting. `cancel` must restore externally modified resources; the SDK terminates its own processes.

Declare expensive local preparation steps and let the SDK persist and verify their checkpoints. Evaluators do not need to manage checkpoint files. Reopening a step reuses completed outputs and retains interrupted outputs before rebuilding. Changed frozen inputs or completed outputs fail explicitly.

```python
from llm4ad.gateway.sdk import TaskCheckpoints

steps = TaskCheckpoints(job, build_dir, inputs={"source.cpp": source}, context={"version": "1"})
steps.run("compile", lambda: compile_source(steps.path("source.cpp"), build_dir / "binary"),
          outputs=["binary", "compiler.log"], required=["binary"])
```

Checkpoints cover repeatable local preparation. External jobs that have already been submitted resume using their original idempotent identity.

## Logs, artifacts and recovery

| Content | Where to find it / retention |
| --- | --- |
| Status events | Gateway dashboard with filters/history; retained in `metadata.evaluation_messages` |
| Local execution logs | Gateway stdout/stderr views; not automatically uploaded, expire by log-retention policy |
| Declared artifacts | Select an evolution node to see metadata and attachments in the right-hand **Node information** panel; click image thumbnails to enlarge, or preview and download other files from the compact list |
| Lifecycle timing | Actual start/end and total elapsed time including hooks; evaluator `duration_ms` remains unchanged |

Local evaluators write inside `cfg.artifact_dir` and declare `EvaluationResult.artifacts`. Remote evaluators can write inside `job.artifact_dir` and declare files through `reporter.artifact()` or the result object. RustFS/S3 stores declared files and returned behavior visualizations. [Runnable artifact example](https://github.com/Optima-CityU/LLM4AD_Next/tree/main/examples/evaluation_artifacts)

The gateway retries failed file uploads. It cleans up evidence after file and result acknowledgement and after any external restoration required by the evaluator. Local logs have separate retention. Platform files remain until task deletion.

| Situation | Behavior |
| --- | --- |
| Network loss | SSE reconnects automatically. The gateway reconciles execution state over heartbeats and resumes sending pending progress and results |
| Gateway restart | Use the same state directory to reconcile unfinished executions and cleanup, and resume work that can continue |
| Transient infrastructure failure | Within a bounded retry budget, the platform requeues the candidate for the same registration |
| Read failure | Read-only operations may use `retry_read(...)` for up to three calls; invalid candidates are not retried |
| Interrupted preparation | SDK checkpoints retain local preparation outputs; each fresh execution has an isolated workspace |
| Execution timeout / platform stop | The gateway stops the local execution, calls configured cleanup logic and saves its state |
| Resource cleanup failure | The gateway keeps the outstanding cleanup record and checks cleanup status before claiming more work |

The SDK persists the result before acknowledging the local queue entry. It can resend saved results and artifacts.

```bash
llm4ad evaluator-gateway health --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
```

## HGamey Genshin example

[HGamey Genshin example](../examples/hgamey-remote-evaluation.md)

## Platform deployment settings

Gateway registrations publish evaluator and data changes. To use an updated evaluation contract, reconnect the gateway and select its current card for a new task. When upgrading the remote evaluation protocol, update the backend, workers, task runner, frontend and gateway SDK together and apply the Alembic migrations.

Standard `docker/compose.yml` includes `remote-evaluation-worker`. Production deployments can add `compose.remote-evaluation.yml` to their existing Compose files.

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
