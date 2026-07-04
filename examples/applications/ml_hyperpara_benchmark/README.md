# ML Hyperparameter Benchmark for LLM4AD

This example evolves **XGBoost hyperparameters only** for a fixed tabular regression pipeline. It uses the Kaggle House Prices dataset and keeps all training and prediction code fixed so evolution focuses only on numeric hyperparameter values.

## Overview

- **What evolves**: numeric values in `HYPERPARAMS = {...}` inside `ml_hyperpara_algorithm/src/classifier.py` between `EVOLVE_START` and `EVOLVE_END`
- **What is fixed**: the `XGBRegressor` training and prediction code outside the EVOLVE block
- **Dataset**: `data/dataset.csv`, a House Prices CSV with numeric and categorical columns plus the continuous target column `SalePrice`
- **Goal**: minimize test-set RMSE. The evaluator returns `-RMSE` as the evolution score, so higher score means lower error.

## Directory Structure

```text
ml_hyperpara_benchmark/
├── ml_hyperpara_algorithm/              # Template code with EVOLVE markers
│   └── src/
│       └── classifier.py                # Fixed XGBRegressor pipeline + evolvable HYPERPARAMS block
├── data/
│   └── dataset.csv                      # House Prices dataset used by the evaluator
├── ml_hyperpara_benchmark_evaluator.py  # Custom Python evaluator
├── config.yaml                          # Configuration file
└── README.md                            # This file
```

## How to Run

### Prerequisites

- Python 3.12+
- LLM4AD installed (recommended: use `uv`)
- A reachable OpenAI-compatible LLM provider configured in `config.yaml`

### Configure LLM Provider

This example uses an OpenAI-compatible provider. Update these fields in `config.yaml` as needed:

- `providers[].base_url`
- `providers[].api_key`
- `providers[].model`
- `coder.base_url`
- `coder.api_key`
- `coder.model`

### Running the Benchmark

From the repository root:

```bash
uv run llm4ad run examples/applications/ml_hyperpara_benchmark/config.yaml
```

Or from this directory:

```bash
cd examples/applications/ml_hyperpara_benchmark
uv run llm4ad run config.yaml
```

## What to Expect

The system will:

1. Analyze the repository at `version_control.local_path` to find EVOLVE blocks
2. Use the planner to generate hyperparameter-tuning insights
3. Use the coder to output an updated `src/classifier.py` EVOLVE region
4. Evaluate candidates on `data/dataset.csv`
5. Iterate for `max_generations` generations

Outputs are written under `base_dir` (default `./runs/`), including logs, checkpoints, generated files, and per-individual git worktrees.

## Constraints for the Evolvable Code

Inside the EVOLVE block:

- Keep exactly one assignment to `HYPERPARAMS = {...}`
- Keep all existing keys unchanged:
  - `n_estimators`
  - `max_depth`
  - `learning_rate`
  - `subsample`
  - `colsample_bytree`
  - `reg_lambda`
  - `min_child_weight`
  - `gamma`
- Only change numeric values
- Keep values within reasonable XGBoost ranges to avoid invalid or wasteful runs

## Customization

### Change the Search Space

Edit `ml_hyperpara_algorithm/src/classifier.py` if you want to change which hyperparameters are tunable. To preserve the benchmark constraints, only modify values inside the EVOLVE block and keep the keys unchanged.

### Use a Different LLM for Coding vs Planning

In `config.yaml`:

- **Planning/coding provider selection**: `evolution.planner_provider` and `evolution.coder_provider`
- **Optional coder override**: `coder.base_url`, `coder.api_key`, `coder.model`

If `coder.api_key` and `coder.model` are set, the custom coder uses them; otherwise it falls back to `providers[evolution.coder_provider]`.

### Use a Different Dataset

Replace `data/dataset.csv` with another House Prices-compatible CSV containing a continuous `SalePrice` target column, or update `evaluator.dataset.files` in `config.yaml` to point to the desired file.

## Notes for Windows

- `version_control.local_path` can be relative; repo-root relative paths are recommended.
- If you run into “EVOLVE blocks found: 0” on Windows, it can be due to drive-letter paths, such as `D:\...`, interacting with tool output parsing. A common workaround is using a UNC path, such as `\\localhost\d$\...`, for `local_path`.
