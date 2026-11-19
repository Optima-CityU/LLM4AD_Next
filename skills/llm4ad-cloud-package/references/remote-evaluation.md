# Standalone ZIP with a local evaluator gateway

Use this route when the user chooses to evaluate on their machine. Confirm their
OS, required toolchain or devices, and whether they will run the gateway natively
or in Docker. Use the agreed problem, candidate interface and score definition.

## Prepare the task and evaluator

Keep the web task and the evaluator in sibling directories. Export only `my-task/`.

```text
my-task/
├── config.yaml
├── README.md
└── algorithm/         # Candidate sources and any required build files
gateway/
├── evaluator.py       # GatewayEvaluator implementation
├── requirements.txt   # Gateway-side dependencies
└── ...                # Local data, device settings and optional Docker files
```

Use the standard task configuration with `providers: []` and
`version_control.local_path: algorithm`. Configure the remote evaluator as follows.

```yaml
evaluator:
  type: remote
  gateway_id: null
  timeout: 60
  parameters: {}
  candidate_paths: []
```

Choose a timeout based on the actual build and evaluation. Put portable evaluator
parameters under `parameters`. Keep absolute paths, account IDs, credentials and
private data in the gateway workspace or environment. The platform fills the
evaluator contract after gateway selection. Empty `candidate_paths` sends recognized
sources and build manifests. If more files are needed, list relative globs that
include all required candidate files.

Use the SDK example from `llm4ad evaluator-gateway source` to implement a
`GatewayEvaluator` with an `EvaluationContract` and `evaluate(job, reporter)`.
Compile and run candidates from `job.project_root`, using the SDK hooks for
evaluation setup and cleanup where needed. Resolve evaluator resources using an
explicit local directory or relative to the evaluator module. The worker runs
from the candidate work directory. Use representative smoke inputs available
on that machine to exercise the actual evaluator. Keep the smoke inputs and
production data on the gateway.

## Verify and export using the chosen environment

The helper snapshots the task, starts a real SDK worker offline, and runs the
packaged baseline with the configured candidate globs, parameters and timeout.
The ZIP and its validation report are written after execution succeeds. The
report records an offline smoke test. Registration and evaluation of cloud jobs
take place after import.

### Native

In an isolated Python 3.12 environment, install the `llm4ad-next` revision matching
the target platform and containing the offline check support for this Skill.
Install `gateway/requirements.txt` and run `uv pip check --python <venv-python>`.
Use the same Python and evaluator environment when you later start the gateway.

```text
<venv-python> <skill-dir>/scripts/validate_and_pack.py <task-dir> --gateway-evaluator <gateway-dir>/evaluator.py:Evaluator --output <exports-dir>/my-task.zip
```

Quote paths with spaces. On Windows use the environment's `Scripts\python.exe`.

### Docker

Use the installed SDK to initialize the gateway workspace before writing its files.
Replace the example evaluator and update `requirements.txt`, mounts and device
access for the task. A revision that has yet to be published can be built as a
wheel and placed under the gateway's generated `wheels/` directory.

```text
llm4ad evaluator-gateway init ./gateway --server <platform-url> --name <gateway-name>
```

From `gateway/`, build the image and run the packer in that image with the same
gateway resources. Create the output directory first and substitute absolute
host paths for the mounts. Keep the exported files outside the task directory.

```text
docker compose build gateway
docker compose run --rm --no-deps --volume "<absolute-task-dir>:/task/my-task:ro" --volume "<absolute-skill-dir>/scripts/validate_and_pack.py:/tools/validate_and_pack.py:ro" --volume "<absolute-exports-dir>:/exports" gateway python /tools/validate_and_pack.py /task/my-task --gateway-evaluator /evaluator/evaluator.py:Evaluator --output /exports/my-task.zip
```

## Start the gateway after import

1. Import the ZIP using [web-import.md](web-import.md). In task configuration,
   open **Remote evaluator** and copy **My user ID**.
2. For native startup, run the following command in the verified environment.

   ```text
   llm4ad evaluator-gateway start --server <platform-url> --name <gateway-name> --evaluator <gateway-dir>/evaluator.py:Evaluator --allow-user <user-id> --no-ui
   ```

   For Docker startup, set `LLM4AD_SERVER_URL`, `LLM4AD_GATEWAY_NAME`,
   `LLM4AD_EVALUATOR` and `LLM4AD_ALLOWED_USER_IDS` in the gateway's generated
   `.env`, then run `docker compose up -d gateway` from the gateway workspace.
3. Select the authorized online evaluator in the imported task, bind a model,
   review the settings and start evolution.
