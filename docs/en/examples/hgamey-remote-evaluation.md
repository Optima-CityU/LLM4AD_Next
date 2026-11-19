# HGamey Genshin example

This example evaluates static super-resolution and runs Genshin A/B measurements on phones. Select `hgamey_shader` in the task wizard and configure a model. Once the gateway starts, select its registered evaluator card as described in the gateway guide. Locate installed example files with `llm4ad evaluator-gateway source --adapter hgamey`. [Remote evaluation guide](../guides/remote-evaluation.md) · [Example source](https://github.com/Optima-CityU/LLM4AD_Next/tree/main/src/llm4ad/gateway/examples/hgamey)

## Deployment

| Environment | Requirement |
| --- | --- |
| Host | Currently verified on Linux amd64 with Compose host networking; persistent directories writable by UID 1000 |
| Build | Rust 1.99, OpenJDK 17, C++, NDK r27d, Android SDK API 35 / build-tools 35.0.0; supplied by the Dockerfile |
| Execution tools | phonefarm, ADB 36.0.2, GLSL/SPIR-V tools, FFmpeg, NumPy, Pillow, Perfetto Python 0.59.1 and Trace Processor; included in the image |
| Phone | Rooted Android 11+ ARM64, authorized wireless ADB, Vulkan 1.1 compute, GPU timestamps and accessible KGSL/GED frequency controls |
| Inputs | Static: real reference frames; game: installed/signed-in Genshin and a repeatable scene. Both require a physical serial and local ADB identity; private inputs are not distributed |

The image builds the runner, Vulkan layer and refbench APK from bundled sources. Install the test APK manually when using the refbench carrier. Game evaluation needs a repeatable in-game scene and device-specific resolution, waits and `enter_script` in `config/game-check.json`. Direct system execution requires equivalent tools.

Create `.env` from `env.example` in the example's `deployment/` directory. Set the platform URL, allowed user UUIDs and the example settings below, then run `docker compose up -d --build`. This example uses host networking. `LLM4AD_GATEWAY_UI_HOST` and `LLM4AD_GATEWAY_UI_PORT` set the dashboard's listening address and port. [Deployment environment example](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/src/llm4ad/gateway/examples/hgamey/deployment/env.example)

| Custom setting | Default / purpose |
| --- | --- |
| `HGAMEY_DEVICES_JSON` | Device array with `id`, `host`, `port`, `expected_serial`, `device_profile`; connection port defaults to `5555` |
| `HGAMEY_REFERENCE_PATH` | `/inputs/genshin`, reference frames required only by the static evaluator |
| `HGAMEY_STATIC_DATASET_MANIFEST` | Optional prepared QRISP + GameIR-SR manifest, for example `/inputs/static-sr-train/manifest.json` |
| `HGAMEY_DATA_DIR` / `HGAMEY_PHONEFARM_DIR` | Required host directories for gateway/executor state and device locks |
| `HGAMEY_INPUTS_DIR` / `HGAMEY_CONFIG_DIR` | Host input directory (defaults to `./inputs`; may be empty for game-only use) and required configuration directory |
| `ADB_IDENTITY_FILE` / `ADB_STATE_DIR` | Required authorized ADB private key and pairing state |
| `ADB_BIN` / `ANDROID_ADB_SERVER_PORT` | `/usr/bin/adb` / `5039`, container ADB path and server port, separate from the phone's connection port |
| `HGAMEY_STARTUP_CHECK` | `true`, run a readiness check at startup |
| `HGAMEY_EVALUATOR_MODES` | `static,game` in the deployment example; select either or both independent evaluators |
| `HGAMEY_GAME_CHECK_CONFIG` | Required by `game`; device/scene options from `/config/game-check.json` |
| `HGAMEY_GAME_SCORING_JSON` | Optional JSON policy overriding weights, scales, metric sources or `quality_gap_score` below; frozen in the game contract |
| `HGAMEY_EVAL_SECONDS` / `HGAMEY_EVAL_ROUNDS` | `5` / `8`, static seconds per arm and A/B rounds; game uses `window` / `rounds` in its JSON |
| `HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT` | `1`, minimum percentage for confirming improvement, in `[1, 100)`; unconfirmed improvement remains a valid `no_advantage` evaluation with unchanged raw scores |
| `HGAMEY_COMPILE_TIMEOUT` / `HGAMEY_POLL_INTERVAL` | `60` / `2` seconds, compilation timeout and result polling |
| `RUST_IMAGE` / `PYTHON_IMAGE` | `rust:1.99-slim-bookworm` / `python:3.13.16-slim-bookworm`, build base images |
| `HGAMEY_NATIVE_IMAGE` | `native-runtime`, built from bundled source by default; may select a verified native runtime image |
| `APT_MIRROR` / `CARGO_REGISTRY_MIRROR` | Optional system-package and Rust build mirrors |
| `ANDROID_REPOSITORY_BASE` / `PIP_INDEX_URL` / `TRACE_PROCESSOR_URL` | Optional Android-toolchain, Python-package and Trace Processor download sources |

## Local device scheduling

The HGamey gateway automatically schedules work on the enabled phones in `HGAMEY_DEVICES_JSON`. Phones with the same evaluator, data, scoring rules and device capability profile share one registration. For each execution, the gateway chooses a ready, idle and compatible phone and uses it through measurement and restoration. Static and game evaluations share the execution slot on each phone. Different phones can run concurrently. Once restoration is confirmed, a new attempt can use another compatible phone. Configure device addresses and serials on the gateway.

A normal gateway shutdown drains active device experiments through restoration and preserves queued work and identities. Platform tasks wait and continue when the gateway restarts with the same data directory. Queue waiting and graceful shutdown downtime do not consume execution budgets; consumed execution time and cancellation requests remain intact. After a forced termination, experiments with unconfirmed device restoration require reconciliation rather than blind replay.

## Evaluators and results

| Evaluator | Measurements and fitness | Node image evidence |
| --- | --- | --- |
| Static (`hgamey-genshin-sr-static`) | Measures native GPU operator latency and quality. A dataset manifest selects 16 native LR/HR pairs. The reference-frame configuration selects the first fixed frame's 1920×1080 center crop | Dataset runs upload one collage per dataset. Single-frame runs show the reference, baseline and candidate output |
| Game (`hgamey-genshin-sr-game`) | Measures injected Genshin A/B performance, then replays captured game frames through the exact baseline and candidate compute programs on the phone GPU for official IQ. Unverified or degraded quality receives an automatic low score; no fixed reference-image directory is required | Same-input reference/baseline/candidate GPU outputs, per-frame IQ, original game captures and traces |

### Prepare the public static SR datasets

Extract the [QRISP split archives](https://www.qualcomm.com/developer/software/qualcomm-rasterized-images-dataset/downloads) and the [GameIR-SR archives](https://huggingface.co/datasets/LLLebin/GameIR), then prepare the paired PNGs and manifest offline.

```bash
uv run --with 'Pillow>=10.1' python -m llm4ad.gateway.examples.hgamey.static_dataset \
  --qrisp /data/QRISP --gameir /data/GameIR-SR \
  --qrisp-revision QRISP-2024-08-16 --gameir-revision 86dd997f3c287c419e16d9fb6c199a47a17c82fb \
  --split train --output /data/gateway-inputs/static-sr-train
```

Use `/data/gateway-inputs` as `HGAMEY_INPUTS_DIR`, set `HGAMEY_STATIC_DATASET_MANIFEST=/inputs/static-sr-train/manifest.json`, and build the gateway, phonefarm and Vulkan runner from the same source revision. The suite fixes eight pairs per dataset. The two datasets contribute equally to measured relative GPU latency gain. Candidates that reach the mean PSNR baseline in both datasets are ranked by this gain. Negative gains for qualifying candidates map monotonically to the range above −100. A measured quality regression completes with a fitness of the gain minus 200 plus the worst dataset PSNR delta in dB. Reports retain the raw gain and per-frame PSNR, 8×8-window luminance SSIM and GPU latency. Each collage shows three representative comparisons and colors only the delta numbers green for gains, red for losses, and neutral for zero.

Prepare a separate final evaluation with `--split test`, which samples the official QRISP test scenes and GameIR Town05 under a distinct `data_version`. The runtime scales with the number of image pairs. Use `HGAMEY_EVAL_SECONDS`, `HGAMEY_EVAL_ROUNDS` and the task execution budget to set the measurement duration.

### Game A/B scoring

The example evaluator computes the game score. It normalizes each component and applies the weights below.

| Component | Default weight | Contribution before weighting |
| --- | --- | --- |
| FPS | 0.25 | `100 × (candidate / baseline − 1) / 10` |
| Presentation P95 | 0.35 | `100 × (1 − candidate / baseline) / 10`; default source `display_frame_p95_upper_ms` is a histogram upper bound |
| GPU activity per presented frame | 0.20 | `100 × (1 − candidate / baseline) / 10`, where milliseconds per frame = `10 × gpu_uid_active_pct / display_fps`; this is application UID activity, not isolated shader time |
| Quality | 0.20 | Negative mean excess beyond official `iq-judge` self-noise reference lines, scaled by 1 dB PSNR, 0.001 `1−SSIM`, and 0.15 detail log2; no sharpening bonus |

Nonnegative weights are normalized once, frozen with sources and scales in the contract, and not changed per candidate. `quality_gap_score` is a separate finite, strictly negative policy value, defaulting to `-100.0`; it is not normalized with the weights. Any measured excess beyond an official quality reference line caps the whole score at `min(unpenalized_score, quality_gap_score)`. Missing, incomplete, nonfinite or overflowing quality measurements, a status other than `measured`, or `not_computed` diagnostics produce `unverified` quality and a score of `quality_gap_score` when required performance is available. These low scores are valid completed evaluations, so the automated pipeline continues without manual review or an infrastructure failure.

Missing positive-weight performance measurements still make `score=None`; device performance is never fabricated. Missing quality retains its unavailable contribution, `unpenalized_score=None` and `missing` diagnostics. No input is replaced by zero and weights are never redistributed. An explicit zero quality weight skips IQ assessment and its automatic penalty. `quality_assessment` exposes `status` (`within_reference`, `beyond_reference`, `unverified` or `not_scored`), `penalized`, the frozen `penalty_score`, and `reasons` for result display. When all scored inputs are valid, `unpenalized_score` preserves the original weighted sum. Power is not scored. Selecting another P95 source requires an explicit frozen policy change.

Game mode defaults to `quality_samples=3` and `pan=false`. Each game pair supplies one actual captured baseline frame, center-cropped to 1920×1080 by the existing GPU tool. Three independent baseline and three candidate GPU invocations process that same input. Uploaded PNGs are actual readbacks; identical programs may produce identical hashes. Official IQ runs separately for each input, preserving its original rows and reference lines. The score averages normalized frame penalties; missing required frames retain the existing low-score policy.

Replay runs after game performance collection and restoration. Its timing never enters FPS, P95 or game GPU activity. `same-input-gpu-replay@1` identifies operator quality on replayed game captures; original A/B screenshots provide execution context. This is not an end-to-end capture of the game's actual presented frames. Pan remains an unscored probe. Normal cancellation drains the current replay through verified cleanup before stopping later frames.

Click an evolution node to see image comparisons first in **Node information**, with round selection and enlarged previews. Other uploaded files appear below. Image transfer failures are recorded without discarding otherwise valid measurements. Captures are not fabricated or recaptured on upload retries. Raw traces use the same artifact list when available; recordings are not uploaded automatically. Keep device configuration, ADB keys and input frames local. Static reference/baseline/candidate comparison PNGs are uploaded, so choose non-sensitive test scenes.

Existing frozen composite jobs can use the explicitly selected `legacy` binding; new tasks use `static` and/or `game`.
