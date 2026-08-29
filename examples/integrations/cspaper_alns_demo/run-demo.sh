#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUN_ROOT=""
POPULATION_SIZE=2
MAX_SAMPLES=3
TOP_K=2
CANDIDATE_PYTHON="${TSP_EVALUATOR_PYTHON:-}"
EVOLVE=0
REGENERATE_DATA=0

usage() {
  cat <<'EOF'
Usage: ./run-demo.sh [options]
  --run-root PATH          Output directory (default: sibling demo-runs directory)
  --population-size N      MEoH population size (default: 2)
  --max-samples N          Maximum generated/evaluated samples (default: 3)
  --top-k N                Number of candidates to export (default: 2)
  --candidate-python PATH  Python containing alns, numpy, and tsplib95
  --regenerate-data        Recreate deterministic TSP instances
  --evolve                 Call the configured LLM; omitted means no-cost dry-run
EOF
}

while (($#)); do
  case "$1" in
    --run-root) RUN_ROOT="$2"; shift 2 ;;
    --population-size) POPULATION_SIZE="$2"; shift 2 ;;
    --max-samples) MAX_SAMPLES="$2"; shift 2 ;;
    --top-k) TOP_K="$2"; shift 2 ;;
    --candidate-python) CANDIDATE_PYTHON="$2"; shift 2 ;;
    --regenerate-data) REGENERATE_DATA=1; shift ;;
    --evolve) EVOLVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
  LLM4AD_BIN="${LLM4AD_BIN:-$REPO_ROOT/.venv/bin/llm4ad}"
elif [[ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/Scripts/python.exe}"
  LLM4AD_BIN="${LLM4AD_BIN:-$REPO_ROOT/.venv/Scripts/llm4ad.exe}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  LLM4AD_BIN="${LLM4AD_BIN:-llm4ad}"
fi

native_path() {
  local value="$1"
  if [[ "$PYTHON_BIN" == *.exe ]]; then
    if command -v cygpath >/dev/null 2>&1; then
      cygpath -w "$value"
      return
    elif command -v wslpath >/dev/null 2>&1; then
      if wslpath -w "$value" 2>/dev/null; then
        return
      fi
      printf '%s\\%s\n' "$(wslpath -w "$(dirname "$value")")" "$(basename "$value")"
      return
    fi
  fi
  printf '%s\n' "$value"
}

if [[ -z "$RUN_ROOT" ]]; then
  RUN_ROOT="$(dirname "$REPO_ROOT")/LLM4AD_Next-demo-runs/alns-paper-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$RUN_ROOT"
exec > >(tee "$RUN_ROOT/run-transcript.log") 2>&1

echo "=== 1/8 Copy original paper, review, baseline source, and datasets ==="
for file in config.yaml dataset-manifest.json generate_tsp_datasets.py paper.pdf review.md task_evaluator.py; do
  cp "$SCRIPT_DIR/$file" "$RUN_ROOT/$file"
done
cp -R "$SCRIPT_DIR/algorithm" "$SCRIPT_DIR/data" "$SCRIPT_DIR/private-test" "$RUN_ROOT/"

if [[ -z "$CANDIDATE_PYTHON" ]]; then
  CANDIDATE_PYTHON="$PYTHON_BIN"
fi
"$CANDIDATE_PYTHON" -c 'import alns, numpy, tsplib95' || {
  echo "Candidate dependencies are missing." >&2
  echo "Install: $CANDIDATE_PYTHON -m pip install -r $SCRIPT_DIR/requirements.txt" >&2
  exit 1
}
export TSP_EVALUATOR_PYTHON="$(native_path "$CANDIDATE_PYTHON")"
if command -v wslpath >/dev/null 2>&1; then
  export WSLENV="${WSLENV:+$WSLENV:}TSP_EVALUATOR_PYTHON:LLM_BASE_URL:LLM_API_KEY:LLM_MODEL"
fi

if ((REGENERATE_DATA)); then
  "$PYTHON_BIN" "$(native_path "$RUN_ROOT/generate_tsp_datasets.py")"
fi

echo "=== 2/8 Apply a small reproducible MEoH budget ==="
"$PYTHON_BIN" "$(native_path "$SCRIPT_DIR/configure_task.py")" \
  "$(native_path "$RUN_ROOT/config.yaml")" \
  --population-size "$POPULATION_SIZE" --max-samples "$MAX_SAMPLES"

SPEC_PATH="$RUN_ROOT/algorithm-design-spec.json"
echo "=== 3/8 Compile the CSPaper review into AlgorithmDesignSpec ==="
"$LLM4AD_BIN" cspaper compile \
  --review "$(native_path "$RUN_ROOT/review.md")" \
  --paper "$(native_path "$RUN_ROOT/paper.pdf")" \
  --code-path "$(native_path "$RUN_ROOT/algorithm/ALNS-master")" \
  --train-data "$(native_path "$RUN_ROOT/data/train")" \
  --validation-data "$(native_path "$RUN_ROOT/data/validation")" \
  --hidden-test-data "$(native_path "$RUN_ROOT/private-test")" \
  --output "$(native_path "$SPEC_PATH")"

echo "=== 4/8 Validate and confirm the design specification ==="
"$LLM4AD_BIN" cspaper validate "$(native_path "$SPEC_PATH")" --check-paths
"$LLM4AD_BIN" cspaper confirm "$(native_path "$SPEC_PATH")" \
  --by "LLM4AD CSPaper ALNS example" \
  --notes "Reproducible ALNS/TSP example inputs and evaluator contract verified."
"$LLM4AD_BIN" cspaper validate "$(native_path "$SPEC_PATH")" --strict --check-paths

echo "=== 5/8 Prepare the task and audit the evaluator contract ==="
"$LLM4AD_BIN" cspaper prepare \
  --spec "$(native_path "$SPEC_PATH")" --task-dir "$(native_path "$RUN_ROOT")"

echo "=== 6/8 Dry-run the baseline evaluator on train and validation data ==="
"$PYTHON_BIN" "$(native_path "$SCRIPT_DIR/smoke_evaluator.py")" \
  "$(native_path "$RUN_ROOT")" --include-validation \
  --output "$(native_path "$RUN_ROOT/dry-run-results.json")"

if ((EVOLVE)); then
  echo "=== 7/8 Check LLM credentials ==="
  : "${LLM_BASE_URL:?LLM_BASE_URL is required for --evolve}"
  : "${LLM_API_KEY:?LLM_API_KEY is required for --evolve}"
  : "${LLM_MODEL:?LLM_MODEL is required for --evolve}"
  echo "=== 8/8 Run MEoH evolution and export Top-K ==="
  "$LLM4AD_BIN" cspaper evolve \
    --spec "$(native_path "$SPEC_PATH")" \
    --task-dir "$(native_path "$RUN_ROOT")" \
    --work-dir "$(native_path "$RUN_ROOT/pipeline")" \
    --top-k "$TOP_K"
else
  echo "=== 7-8/8 Evolution skipped; dry-run completed without LLM calls ==="
fi

echo "Demo completed successfully."
echo "Run directory: $RUN_ROOT"
echo "Spec: $SPEC_PATH"
echo "Dry-run results: $RUN_ROOT/dry-run-results.json"
