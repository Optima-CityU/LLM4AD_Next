# Configuration Guide

This guide provides a comprehensive reference for all LLM4AD configuration options.

## Configuration File Structure

LLM4AD uses YAML configuration files with the following structure:

```yaml
# General Settings
project_name: "my-project"
run_id: "experiment-001"
random_seed: 42
base_dir: "./runs"

# LLM Providers
providers:
  - name: "default"
    type: "openai"
    # ... provider settings

# Evaluator
evaluator:
  # ... evaluator settings

# Evolution
evolution:
  # ... evolution settings

# Coder
coder:
  # ... coder settings

# Memory
memory:
  # ... memory settings

# Workspace
workspace:
  # ... workspace settings

# Logging
logging:
  # ... logging settings
```

## Configuration Sections

### 1. General Settings

#### `project_name`
- **Type**: `string`
- **Required**: Yes
- **Default**: `"llm4ad"`
- **Description**: Name of your project, used for organizing output directories

```yaml
project_name: "sorting-algorithm-design"
```

#### `run_id`
- **Type**: `string`
- **Required**: No
- **Default**: Auto-generated 8-character UUID
- **Description**: Unique identifier for this run. Useful for tracking experiments

```yaml
run_id: "experiment-001"  # Or leave null for auto-generation
```

#### `random_seed`
- **Type**: `integer`
- **Required**: No
- **Default**: `42`
- **Description**: Random seed for reproducibility

```yaml
random_seed: 42
```

#### `base_dir`
- **Type**: `string`
- **Required**: No
- **Default**: `"./runs"`
- **Description**: Base directory where task directories are created

```yaml
base_dir: "./experiments"
```

---

### 2. LLM Providers (`providers`)

Configure one or more LLM providers that can be referenced by other components.

#### `providers[].name`
- **Type**: `string`
- **Required**: Yes
- **Description**: Unique name for this provider, referenced by `planner.provider` and `coder.provider`

```yaml
providers:
  - name: "planner"  # Referenced as "planner"
```

#### `providers[].type`
- **Type**: `enum ["openai", "anthropic", "openai_compatible", "codex_cli"]`
- **Required**: Yes
- **Default**: `"openai"`
- **Description**: Type of LLM provider

```yaml
providers:
  - type: "openai"              # OpenAI API
  - type: "anthropic"           # Anthropic API
  - type: "openai_compatible"   # OpenAI-compatible API (e.g., local models)
```

#### Local Codex CLI for planning and coding

Use `codex_cli` for personal experiments on the host where the official CLI
is already signed in. Run `codex login status` as the same OS user; it must
report `Logged in using ChatGPT`. Otherwise, run `codex login`.
Do not supply API keys, auth tokens, or a base URL.

```yaml
providers:
  - name: local_codex
    type: codex_cli
    model: ""       # Keep the local Codex default.
    timeout: 600
planner:
  provider: local_codex
coder:
  type: codex_cli
  binary_path: codex
  model: ""       # Optional separate coding model.
  timeout: 600
```

Merge these fields into a task config while preserving its evaluator, dataset,
and source repository settings. The Codex coder does not require a provider
reference. Alternatively, the existing `custom` coder can reference the
`local_codex` provider and parse returned code itself. Custom evaluators still
need a declared provider reference; evaluators that construct API clients
directly require separate adaptation.

Run the small TSP example from the repository root:

```bash
uv run llm4ad run examples/applications/tsp_benchmark_python_mock/config.codex.yaml --skip-install
```

This configuration uses real Codex planning and coding, with the existing local
TSP baseline and evaluator. It consumes subscription usage and disables memory
extraction, embeddings, and multimodal sampling.

Planning runs in a temporary read-only directory; coding uses workspace-write
sandboxing in the candidate directory. Calls are serialized within each process.
API authentication overrides are removed from the child environment, and the
runner requires a ChatGPT login without falling back to API billing. CLI failures,
timeouts, and incomplete turns propagate to the experiment's error handling.

The provider supports text and structured JSON. Streaming returns the final
validated text in one chunk. Images, application function calling, and embeddings
are unsupported; API sampling parameters such as temperature and max_tokens are
not forwarded. Run on the host as the logged-in user. Docker deployments can
connect through the local bridge below without copying login files into containers.

#### Bind local Codex in the web UI

Open **LLM Providers → Connect local Codex**, check the connection, then select
**Bind Codex**. The default option sets both planning and coding to Codex. Leave
the selection on “Follow local Codex settings” to use the displayed CLI default.
The picker reads models through Codex model/list; select a model and update the
binding to save it. Manual model entry remains available if discovery fails.
Disconnecting removes the provider
binding without logging out of Codex.

First start the host bridge from the repository root with Python dependencies installed:

```bash
bash docker/start-codex-bridge.sh --host 127.0.0.1
```

The first launch creates a private `docker/.codex-bridge-token` file. Configure
the backend with `LOCAL_CODEX_BRIDGE_URL=http://127.0.0.1:18143` and
`LOCAL_CODEX_BRIDGE_TOKEN` set to that file's contents, then restart the backend.
For a Linux Docker deployment, find the gateway of the backend container's network
with `docker network inspect <network-name>`. Start the bridge with
`--host <gateway-address>` and configure the backend URL as
`http://<gateway-address>:18143`; container loopback does not reach the host.
Run as the OS user signed into Codex, with the CLI on PATH.
Binding is available to administrators only when `ENVIRONMENT=local`. Neither
bridge credentials nor account tokens are returned to the browser.

Web experiments use the host CLI for planning and coding. Coding automatically
uses the `custom` coder to parse generated code and write it inside the experiment
container. New tasks can use default models; existing tasks with explicit provider
selections must switch to local Codex in their model settings. Images, function
calling, and embeddings require other providers. Connection checks make no model
requests; inference remains subject to Codex subscription usage and rate limits.

See the [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

#### `providers[].api_key`
- **Type**: `string`
- **Required**: Yes (unless set via environment variable)
- **Description**: API key for authentication. Can use environment variables with `${VAR_NAME}`

```yaml
providers:
  - api_key: "${OPENAI_API_KEY}"  # From environment variable
  - api_key: "sk-..."            # Direct value (not recommended)
```

#### `providers[].base_url`
- **Type**: `string`
- **Required**: No
- **Default**: Provider-specific default
- **Description**: Custom base URL for API (useful for local models or proxies)

```yaml
providers:
  - type: "openai_compatible"
    base_url: "http://localhost:8000/v1"  # Local model
```

#### `providers[].model`
- **Type**: `string`
- **Required**: Yes
- **Default**: `"gpt-4"`
- **Description**: Model identifier

```yaml
providers:
  - model: "gpt-4o"                    # OpenAI
  - model: "claude-3-5-sonnet-20241022"  # Anthropic
  - model: "llama-3-70b"                # Local model
```

#### `providers[].temperature`
- **Type**: `float`
- **Required**: No
- **Default**: `0.7`
- **Range**: `[0.0, 2.0]`
- **Description**: Sampling temperature. Lower = more deterministic, Higher = more creative

```yaml
providers:
  - temperature: 0.3  # More deterministic (good for code)
  - temperature: 0.8  # More creative (good for planning)
```

#### `providers[].max_tokens`
- **Type**: `integer`
- **Required**: No
- **Default**: `32768`
- **Description**: Maximum tokens to generate per request

```yaml
providers:
  - max_tokens: 4096   # Shorter responses
  - max_tokens: 32768  # Longer responses
```

#### `providers[].timeout`
- **Type**: `float`
- **Required**: No
- **Default**: `60.0`
- **Description**: Request timeout in seconds

```yaml
providers:
  - timeout: 120.0  # 2 minutes
```

#### `providers[].max_retries`
- **Type**: `integer`
- **Required**: No
- **Default**: `3`
- **Description**: Maximum retry attempts for failed requests

```yaml
providers:
  - max_retries: 5
```

---

### 3. Evaluator (`evaluator`)

Configure how algorithms are evaluated.

#### `evaluator.module`
- **Type**: `string`
- **Required**: No (for built-in evaluators)
- **Description**: Import path for custom evaluator. Format: `"module.path:ClassName"`

```yaml
evaluator:
  module: "my_evaluators:CustomEvaluator"
```

#### `evaluator.dataset`
- **Type**: `object`
- **Required**: Yes
- **Description**: Dataset configuration with three modes

**Mode 1: Explicit file list**
```yaml
evaluator:
  dataset:
    mode: "files"
    files:
      - "./data/test1.json"
      - "./data/test2.json"
```

**Mode 2: Directory traversal**
```yaml
evaluator:
  dataset:
    mode: "directory"
    path: "./data/benchmark/"
    recursive: true  # Search subdirectories
```

**Mode 3: Glob pattern**
```yaml
evaluator:
  dataset:
    mode: "glob"
    pattern: "./data/**/*.json"
```

#### `evaluator.metrics`
- **Type**: `list[string]`
- **Required**: No
- **Description**: List of metrics to compute

```yaml
evaluator:
  metrics:
    - "accuracy"
    - "runtime"
    - "memory_usage"
```

#### `evaluator.timeout`
- **Type**: `float`
- **Required**: No
- **Default**: `60.0`
- **Description**: Evaluation timeout per instance in seconds

```yaml
evaluator:
  timeout: 30.0  # 30 seconds per test case
```

#### `evaluator.max_retries`
- **Type**: `integer`
- **Required**: No
- **Default**: `1`
- **Description**: Maximum retries for failed evaluations

```yaml
evaluator:
  max_retries: 3
```

#### `evaluator.parallel`
- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Enable parallel evaluation across CPU cores

```yaml
evaluator:
  parallel: true   # Parallel evaluation
  parallel: false  # Sequential evaluation
```

#### `evaluator.batch_size`
- **Type**: `integer`
- **Required**: No
- **Default**: `10`
- **Description**: Batch size for parallel evaluation

```yaml
evaluator:
  batch_size: 5  # Evaluate 5 instances in parallel
```

---

### 4. Evolution (`evolution`)

Configure the evolutionary algorithm parameters.

#### `evolution.type`
- **Type**: `string`
- **Required**: No
- **Default**: `"island_ga"`
- **Allowed**: `"island_ga"`, `"dyca"`, `"meoh"`
- **Description**: Orchestrator selector. See [Orchestration Methods Overview](orchestration.md) for the decision matrix; per-orchestrator fields live in [Island GA](island-ga.md), [DyCA](dyca.md), and [MEoH](meoh.md).

```yaml
evolution:
  type: "island_ga"  # or "dyca" or "meoh"
```

#### `evolution.planner_type`
- **Type**: `string`
- **Required**: No
- **Default**: `"llm_evolution"`
- **Description**: Type of planner to use

```yaml
evolution:
  planner_type: "llm_evolution"
```

#### `evolution.population_size`
- **Type**: `integer`
- **Required**: No
- **Default**: `50`
- **Range**: `[2, ∞)`
- **Description**: Number of individuals in each generation

```yaml
evolution:
  population_size: 20  # Smaller population
  population_size: 100  # Larger population
```

#### `evolution.max_generations`
- **Type**: `integer`
- **Required**: No
- **Default**: `100`
- **Range**: `[1, ∞)`
- **Description**: Maximum number of generations to run

```yaml
evolution:
  max_generations: 50  # Fewer generations
  max_generations: 200  # More generations
```

#### `evolution.elite_ratio`
- **Type**: `float`
- **Required**: No
- **Default**: `0.1`
- **Range**: `[0.0, 1.0]`
- **Description**: Fraction of top individuals preserved unchanged

```yaml
evolution:
  elite_ratio: 0.1  # Keep top 10%
  elite_ratio: 0.2  # Keep top 20%
```

#### `evolution.mutation_rate`
- **Type**: `float`
- **Required**: No
- **Default**: `0.3`
- **Range**: `[0.0, 1.0]`
- **Description**: Probability of applying mutation

```yaml
evolution:
  mutation_rate: 0.3  # 30% mutation rate
```

#### `evolution.crossover_rate`
- **Type**: `float`
- **Required**: No
- **Default**: `0.5`
- **Range**: `[0.0, 1.0]`
- **Description**: Probability of applying crossover

```yaml
evolution:
  crossover_rate: 0.5  # 50% crossover rate
```

#### `evolution.selection_strategy`
- **Type**: `enum ["tournament", "roulette", "rank"]`
- **Required**: No
- **Default**: `"tournament"`
- **Description**: Selection strategy for choosing parents

```yaml
evolution:
  selection_strategy: "tournament"  # Tournament selection
  selection_strategy: "roulette"     # Roulette wheel selection
  selection_strategy: "rank"         # Rank-based selection
```

#### `evolution.tournament_size`
- **Type**: `integer`
- **Required**: No
- **Default**: `3`
- **Range**: `[2, ∞)`
- **Description**: Tournament size for tournament selection

```yaml
evolution:
  tournament_size: 5  # Select from 5 random individuals
```

#### `planner.provider`
- **Type**: `string`
- **Required**: No
- **Default**: `"default"`
- **Description**: Name of LLM provider for planning

```yaml
planner:
  provider: "planner"  # Must match providers[].name
```

#### `coder.provider`
- **Type**: `string`
- **Required**: No
- **Default**: `"default"`
- **Description**: Name of LLM provider for coding

```yaml
coder:
  provider: "coder"  # Must match providers[].name
```

#### `evolution.early_stop_patience`
- **Type**: `integer`
- **Required**: No
- **Default**: `20`
- **Range**: `[1, ∞)`
- **Description**: Generations without improvement before stopping

```yaml
evolution:
  early_stop_patience: 15  # Stop after 15 generations with no improvement
```

#### `evolution.early_stop_threshold`
- **Type**: `float`
- **Required**: No
- **Default**: `1e-6`
- **Range**: `[0, ∞)`
- **Description**: Minimum improvement to reset early stopping counter

```yaml
evolution:
  early_stop_threshold: 0.001  # Require 0.001 improvement
```

#### `evolution.checkpoint_interval`
- **Type**: `integer`
- **Required**: No
- **Default**: `10`
- **Range**: `[1, ∞)`
- **Description**: Generations between checkpoint saves

```yaml
evolution:
  checkpoint_interval: 5  # Save checkpoint every 5 generations
```

#### `evolution.max_checkpoints`
- **Type**: `integer`
- **Required**: No
- **Default**: `5`
- **Range**: `[1, ∞)`
- **Description**: Maximum number of checkpoints to keep

```yaml
evolution:
  max_checkpoints: 3  # Keep only last 3 checkpoints
```

---

### 5. Coder (`coder`)

Configure code generation settings.

#### `coder.type`
- **Type**: `enum ["claude_code", "opencode", "codex_cli", "custom"]`
- **Required**: No
- **Default**: `"claude_code"`
- **Description**: Type of coder to use

```yaml
coder:
  type: "claude_code"  # Claude Code agent
  type: "opencode"     # OpenCode agent
  type: "custom"       # Custom naive coder
```

#### `coder.timeout`
- **Type**: `float`
- **Required**: No
- **Default**: `300.0`
- **Description**: Maximum time for code generation per attempt (seconds)

```yaml
coder:
  timeout: 120.0  # 2 minutes
```

#### `coder.max_retries`
- **Type**: `integer`
- **Required**: No
- **Default**: `2`
- **Description**: Maximum retries for failed code generation

```yaml
coder:
  max_retries: 3
```

#### `coder.log_to_console`
- **Type**: `boolean`
- **Required**: No
- **Default**: `false`
- **Description**: Enable logging to console

```yaml
coder:
  log_to_console: true
```

---

### 6. Memory (`memory`)

Configure the memory system for storing and retrieving past designs.

#### `memory.max_entries`
- **Type**: `integer`
- **Required**: No
- **Default**: `10000`
- **Description**: Maximum number of entries in memory

```yaml
memory:
  max_entries: 5000  # Smaller memory
  max_entries: 20000  # Larger memory
```

#### `memory.similarity_threshold`
- **Type**: `float`
- **Required**: No
- **Default**: `0.8`
- **Range**: `[0.0, 1.0]`
- **Description**: Similarity threshold for retrieving entries

```yaml
memory:
  similarity_threshold: 0.7  # More permissive matching
  similarity_threshold: 0.9  # Stricter matching
```

#### `memory.decay_factor`
- **Type**: `float`
- **Required**: No
- **Default**: `0.99`
- **Range**: `[0.0, 1.0]`
- **Description**: Decay factor for memory relevance over time

```yaml
memory:
  decay_factor: 0.99  # Slow decay
  decay_factor: 0.95  # Faster decay
```

---

### 7. Workspace (`workspace`)

Configure automatic workspace directory creation.

#### `workspace.auto_create`
- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Automatically create task directory structure

```yaml
workspace:
  auto_create: true
```

#### `workspace.subdirs`
- **Type**: `object`
- **Required**: No
- **Default**: See below
- **Description**: Subdirectory names within task directory

```yaml
workspace:
  subdirs:
    state: "state"
    logs: "logs"
    checkpoints: "checkpoints"
    generated: "generated"
    temp: "temp"
```

---

### 8. Logging (`logging`)

Configure logging behavior.

#### `logging.level`
- **Type**: `enum ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]`
- **Required**: No
- **Default**: `"TRACE"`
- **Description**: Log level

```yaml
logging:
  level: "INFO"        # Standard information
  level: "DEBUG"       # Detailed debugging
  level: "WARNING"     # Only warnings and errors
```

#### `logging.format`
- **Type**: `string`
- **Required**: No
- **Default**: Loguru default format
- **Description**: Log format string

```yaml
logging:
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

#### `logging.file`
- **Type**: `string`
- **Required**: No
- **Default**: Auto-set by workspace
- **Description**: Log file path

```yaml
logging:
  file: "./logs/experiment.log"
```

#### `logging.console`
- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Enable console logging

```yaml
logging:
  console: true
```

#### `logging.json_format`
- **Type**: `boolean`
- **Required**: No
- **Default**: `false`
- **Description**: Use JSON format for structured logging

```yaml
logging:
  json_format: true  # JSON logs for log aggregation systems
```

---

### 9. Embeddings (`embedding`)

The `embedding:` block configures the embedding service used for algorithm-similarity, DyCA clustering, MEoH diversity penalty, and the consultant/advisor/recommender retrieval. Detailed walkthrough: [Embeddings & Trajectory](embeddings.md).

```yaml
embedding:
  type: "openai_compatible"
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"
  model: "text-embedding-3-small"
  dim: 1536                   # must match the model
  embedding_func_max_async: 4
```

Supported `type`: `openai`, `openai_compatible`, `jina`, `mock`, `local`. The `local` mode lets text and code embeddings target different deployments via `text_config:` and `code_config:` sub-blocks (PR #90); see the [Embeddings guide](embeddings.md#local-mode-split-text-and-code).

### 10. Multimodal (`multimodal`)

```yaml
multimodal:
  enabled: false                  # master switch
  max_images_per_prompt: 3
  image_max_size_kb: 512
  include_observation_text: true
  two_step_refinement: false
  behavior_storage: "rendered"    # rendered | raw | none
```

When `enabled: true`, multimodal samplers (`multimodal_init_sampler`, `multimodal_mutation_sampler`, `multimodal_crossover_sampler`) become available and behavior images / trajectories returned by the evaluator are spliced into prompts. See the [Multimodal guide](multimodal.md).

### 11. Version control (`version_control`)

Per-individual git worktrees isolate concurrent candidates from each other and keep `main` clean. Most users never edit this block — defaults are sane.

```yaml
version_control:
  enabled: true
  type: "git_worktree"
  local_path: "tsp_algorithm"     # path to the algorithm code package
  remote_url: null                # optional: clone from remote
  revision: null                  # optional: branch/tag to check out
  auto_initialize: true           # init git if not present
  auto_cleanup: true              # remove worktrees after run
  max_worktree_age_days: 7
  max_worktrees: 100
  default_branch: "main"
  commit_message_template: "Algorithm {algorithm_id}: {description}"
```

The `best/` snapshot directory written at end of run (PR #95) is unaffected by `auto_cleanup`. The CLI prints the path on completion; see [Architecture Data Flow § Run directory](../architecture/data-flow.md#run-directory-layout).

### 12. Orchestrator-specific fields

The `evolution:` block also accepts orchestrator-specific fields under the same key. They are validated only when matching `evolution.type`:

| If `evolution.type` is | See for fields |
|---|---|
| `island_ga` | [Island GA](island-ga.md) — `num_islands`, `island_population_size`, `migration_*`, `parallel_islands`, `per_island_config` |
| `dyca` | [DyCA](dyca.md) — `n_clusters`, `clustering_method`, `recluster_interval`, `ari_threshold`, `n_anchors`, `*_pool_size`, `sos_stagnation_threshold`, `using_mode` |
| `meoh` | [MEoH](meoh.md) — `population_size`, `selection_num`, `max_sample_nums`, `objective_metrics`, `use_e2_operator`, `use_m1_operator`, `use_m2_operator`, `seed_path`, `code_generation_mode`, `active_population_ratio` |

Multi-objective runs are MEoH-only; list `objective_metrics: [...]` to define the Pareto axes.

### 13. Evaluator timing metrics (`evaluator.timing_metrics`)

The `evaluator:` block accepts an optional `timing_metrics:` sub-block that controls fine-grained timing collection and whether time becomes a score component. Defaults are observation-only (no impact on score). Detailed reference: [Timing & Metrics](timing-metrics.md).

```yaml
evaluator:
  timing_metrics:
    enabled: true
    include_in_score: false
    score_components: ["candidate_runtime_ms"]
    aggregation: "sum"
    weights:
      candidate_runtime_ms: 1.0
```

---

## Environment Variables

LLM4AD supports environment variable substitution in configuration files using `${VAR_NAME}` syntax:

```yaml
providers:
  - api_key: "${OPENAI_API_KEY}"
  - base_url: "${CUSTOM_API_URL}"

evaluator:
  dataset:
    path: "${DATA_DIR}/benchmark"
```

Set environment variables before running:

```bash
export OPENAI_API_KEY="your-key"
export CUSTOM_API_URL="http://localhost:8000/v1"
export DATA_DIR="./data"

llm4ad run config.yaml
```

## Example Configurations

### Minimal Configuration

```yaml
project_name: "minimal-example"

providers:
  - name: "default"
    type: "openai"
    api_key: "${OPENAI_API_KEY}"

evaluator:
  module: "my_evaluator:MyEvaluator"

evolution:
  population_size: 10
  max_generations: 5
```

### Multi-Provider Configuration

```yaml
project_name: "multi-provider"

providers:
  - name: "planner"
    type: "anthropic"
    model: "claude-3-5-sonnet-20241022"
    temperature: 0.3  # More deterministic for planning

  - name: "coder"
    type: "openai"
    model: "gpt-4o"
    temperature: 0.1  # Very low for code generation

planner:
  provider: "planner"
coder:
  provider: "coder"
```

### Complete Configuration

See `examples/config/config.complete.yaml` in the project root for a complete example with all options.

## Best Practices

1. **Use Environment Variables**: Never hardcode API keys in config files
2. **Start Small**: Begin with small populations and few generations
3. **Adjust Temperature**: Use lower temperature (0.1-0.3) for code generation
4. **Set Timeouts**: Reasonable timeouts prevent hanging
5. **Enable Checkpointing**: Save checkpoints to resume interrupted runs
6. **Monitor Memory**: Adjust `max_entries` based on your needs
7. **Parallel Evaluation**: Enable for faster evaluation on multi-core systems

## Next Steps

- [Quick Start Guide](quickstart.md) - Run your first experiment
- [Writing Evaluators](evaluators.md) - Create custom evaluators
- [Advanced Configuration](advanced.md) - Advanced usage patterns
