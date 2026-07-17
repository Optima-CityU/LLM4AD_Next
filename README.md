# LLM4AD_Next

<p align="center">
  <strong>From problem description to runnable evolutionary algorithm search — in one command.</strong><br>
  LLM-driven automated algorithm design with evolutionary optimization
</p>

<p align="center">
  <a href="https://pypi.org/project/llm4ad-next/">
    <img src="https://img.shields.io/pypi/v/llm4ad-next?color=blue" alt="PyPI Version">
  </a>
  <a href="https://pypi.org/project/llm4ad-next/">
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python Versions">
  </a>
  <a href="https://github.com/Optima-CityU/LLM4AD_Next/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-BSD--3--Clause-blue" alt="License">
  </a>
  <a href="https://github.com/Optima-CityU/LLM4AD_Next/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Optima-CityU/LLM4AD_Next/ci.yml" alt="CI">
  </a>
</p>

---

## 🔥 News

- 🚀 [2026.07][New Release]: **LLM4AD_Next Online Trial** is now available at [https://llm4ad-next.cn/](https://llm4ad-next.cn/) — try the full problem-to-algorithm workflow directly in your browser with no local setup.
- ✨ [2026.07][New Feature]: Introducing an **interactive problem-to-project workflow** that turns natural-language problem descriptions into runnable evolutionary algorithm search projects.
- 🐳 [2026.07][New Feature]: Versioned **Docker Hub deployment images** are now aligned with GitHub Release tags for reproducible local deployment.

## 🚀 Why LLM4AD_Next?

Traditionally, using Large Language Models for Automated Algorithm Design (LLM4AD) required a tedious, multi-step configuration pipeline. **LLM4AD_Next destroys this entry barrier.**

<div align="center">
  <img src="docs/en/process.png" alt="LLM4AD vs LLM4AD_Next Process Overview" width="850">
</div>


With **LLM4AD_Next**, after creating your directory, all of these painful steps are fully automated through an interactive conversational terminal. Just run:

```bash
uv run llm4ad chat
```

Our built-in AI-powered consultant will interview you, instantly understand your requirements, and automatically generate a ready-to-run pipeline (evaluator, algorithm skeleton, configuration, and debugger) so you can leap straight into producing Useful Algorithms.

## 🎯 Key Features Overview

* 🧠 **LLM-Powered Design** & 🧬 **Evolutionary Optimization** combined to automatically evolve top-performing code.
* 💬 **Interactive Configuration (`llm4ad chat`)** — Your conversational AI consultant that generates the entire runnable app framework.
* 🔍 **Evolve-Block Advisor & Recommender** — Point LLM4AD_Next at any repository, and it will scan, score, and recommend exactly *which* blocks of code are most promising to evolve to hit your goals.

## Search Methods (Automatic Heuristic Design)

LLM4AD_Next is progressively migrating the published Automatic Heuristic Design (AHD) search methods from the original [LLM4AD](https://github.com/Optima-CityU/LLM4AD) platform. The table below tracks migration status against the reference papers.

### ✅ Available

| Method | Description | Reference |
|--------|-------------|-----------|
| **EoH** (Evolution of Heuristics) | Evolves heuristic *thoughts* in natural language and their code via five prompt operators (I1 / E1 / E2 / M1 / M2). | Liu et al., "Evolution of Heuristics: Towards Efficient Automatic Algorithm Design Using Large Language Model", ICML 2024 — [arXiv:2401.02051](https://arxiv.org/abs/2401.02051) |
| **MEoH** (Multi-objective EoH) | Multi-objective variant that searches a non-dominated set of heuristics balancing multiple objectives (e.g. quality vs. efficiency). | Yao et al., "Multi-objective Evolution of Heuristic Using Large Language Model" — [arXiv:2409.16867](https://arxiv.org/abs/2409.16867) |
| **ReEvo** (Reflective Evolution) | LLM hyper-heuristics with short- and long-term *reflection* providing verbal gradients to guide evolutionary search. | Ye et al., "Large Language Models as Hyper-Heuristics with Reflective Evolution", NeurIPS 2024 — [arXiv:2402.01145](https://arxiv.org/abs/2402.01145) |
| **MCTS-AHD** (Monte Carlo Tree Search for AHD) | Organizes LLM-generated heuristics in a tree and uses UCT to balance exploration/exploitation, developing temporarily underperforming heuristics. | Zheng et al., "Monte Carlo Tree Search for Comprehensive Exploration in LLM-Based Automatic Heuristic Design" — [arXiv:2501.08603](https://arxiv.org/abs/2501.08603) |

### ⏳ Pending migration

| Method | Description | Reference |
|--------|-------------|-----------|
| **FunSearch** | Pairs a pretrained LLM with a systematic evaluator to search in function space. | Romera-Paredes et al., "Mathematical discoveries from program search with large language models", Nature 2024 — [nature.com](https://www.nature.com/articles/s41586-023-06924-6) |
| **LLaMEA** | Large Language Model Evolutionary Algorithm for automatically generating metaheuristics. | van Stein & Bäck, "LLaMEA: A Large Language Model Evolutionary Algorithm for Automatically Generating Metaheuristics" — [arXiv:2405.20132](https://arxiv.org/abs/2405.20132) |
| **HillClimb / RandSample** | (1+1) hill-climbing and random-sampling baselines for LLM-based evolutionary program search. | Zheng et al., "Understanding the Importance of Evolutionary Search in Automated Heuristic Design with Large Language Models" — [arXiv:2407.10873](https://arxiv.org/abs/2407.10873) |
| **MOEA/D** (LLM variant) | Decomposition-based multi-objective evolutionary search applied to LLM-driven AHD. | Zhang & Li, "MOEA/D: A Multiobjective Evolutionary Algorithm Based on Decomposition", IEEE TEVC 2007 |
| **NSGA-II** (LLM variant) | Fast non-dominated sorting genetic algorithm applied to LLM-driven AHD. | Deb et al., "A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II", IEEE TEVC 2002 |

### Using the migrated methods

Each method is selected in your `config.yaml` through the `evolution.type` (orchestrator), the `planner.type`, and the `planner.samplers` list. The three newly migrated methods wire up as follows. A complete, runnable reference is the `examples/applications/cvrp_construct_python` task.

**EoH** — `island_ga` evolution + `llm_evolution` planner + the five EoH operators as samplers:

```yaml
planner:
  type: "llm_evolution"
  samplers:
    - {name: "eoh_i1_sampler", config: {temperature: 0.7}}   # initialize
    - {name: "eoh_e1_sampler", config: {temperature: 0.7, selection_num: 2}}   # crossover
    - {name: "eoh_e2_sampler", config: {temperature: 0.8, selection_num: 2}}   # crossover
    - {name: "eoh_m1_sampler", config: {temperature: 0.7}}   # mutation
    - {name: "eoh_m2_sampler", config: {temperature: 0.9}}   # mutation

evolution:
  type: "island_ga"
  max_generations: 8
  num_islands: 1
  island_population_size: 4
```

**ReEvo** — `island_ga` evolution + the dedicated `reevo_evolution` planner (adds reflection) + ReEvo samplers:

```yaml
planner:
  type: "reevo_evolution"
  samplers:
    - {name: "reevo_init_sampler", config: {temperature: 0.7}}
    - {name: "reevo_crossover_sampler", config: {temperature: 0.7}}
    - {name: "reevo_mutation_sampler", config: {temperature: 0.8}}

evolution:
  type: "island_ga"
  max_generations: 8
  num_islands: 1
  island_population_size: 4
```

**MCTS-AHD** — the `mcts_ahd` orchestrator replaces the generational loop with a UCT-guided tree (single tree, so `num_islands: 1`); it reuses the `llm_evolution` planner with EoH-style samplers for node expansion:

```yaml
planner:
  type: "llm_evolution"
  samplers:
    - {name: "eoh_i1_sampler", config: {temperature: 0.7}}
    - {name: "eoh_e1_sampler", config: {temperature: 0.8, selection_num: 1}}
    - {name: "eoh_m1_sampler", config: {temperature: 0.7}}

evolution:
  type: "mcts_ahd"
  num_islands: 1
  island_population_size: 4
  lambda0: 0.1          # UCT exploration constant
  alpha: 0.5            # progressive widening (reserved)
  max_depth: 10         # maximum tree depth
  init_size: 4          # initial children under the root
  max_sample_nums: 32   # total evaluation budget (termination)
```

Then run any of them the same way:

```bash
llm4ad run path/to/your/config.yaml
```

## Quick Start

<table>
  <tr>
    <td align="center" width="33%">
      <strong>Try Online</strong><br>
    </td>
    <td align="center" width="33%">
      <strong>Watch Instruction</strong><br>
    </td>
    <td align="center" width="33%">
      <strong>Read Docs</strong><br>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      Run LLM4AD_Next in your browser. No installation or API key required.
    </td>
    <td align="center" width="33%">
      Watch the introduction before installing or configuring a local environment.
    </td>
    <td align="center" width="33%">
      Use the documentation path map for setup, configuration, examples, and Web UI deployment.
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <a href="https://llm4ad-next.cn/">
        <img src="https://img.shields.io/badge/Launch%20Online%20Demo-Open%20Now-2ea44f?style=for-the-badge"
             alt="Launch Online Demo">
      </a>
    </td>
    <td align="center" width="33%">
      <a href="https://youtu.be/x47kEosu0jk" target="_blank" rel="noopener noreferrer">
        <img src="https://img.shields.io/badge/Watch%20Instruction-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white"
             alt="Watch the instruction video on YouTube">
      </a>
    </td>
    <td align="center" width="33%">
      <a href="docs/en/index.md">
        <img src="https://img.shields.io/badge/Open%20Documentation-Read%20Now-0969da?style=for-the-badge"
             alt="Open Documentation">
      </a>
    </td>
  </tr>
</table>

## Instruction Video

<div align="center">
  <a href="https://youtu.be/x47kEosu0jk" target="_blank" rel="noopener noreferrer">
    <img src="https://img.youtube.com/vi/x47kEosu0jk/maxresdefault.jpg"
         alt="LLM4AD_Next instruction video"
         width="720"
         height="405">
  </a>
</div>


## Run LLM4AD

### Option A: Online Demo (No Installation Required)

Use the online demo from [Quick Start](#quick-start), or open it directly:
[Launch Online Demo](https://llm4ad-next.cn/).

No setup, no API key needed — just open the link and start designing algorithms.

### Option B: Local Installation

Requires **Python 3.12+** (pinned in `.python-version`) and [uv](https://github.com/astral-sh/uv) (recommended) or pip. A plain `uv sync` sets up everything, including the `chatv2` AI build agent, out of the box.

```bash
# Clone the repository
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd LLM4AD_Next

# Install dependencies
uv sync

# Configure your LLM provider (see Global Settings section below)
# Or set environment variables directly:
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="your-api-key"
export LLM_MODEL="gpt-4o"

# Option 1: Interactive configuration (recommended for new users)
llm4ad chat

# Option 2: Run with an existing config file
llm4ad run examples/applications/tsp_benchmark_python/config.yaml
```

For optional dependency groups (`infra`, `providers`, `eval`, `dev`, `docs`, `all`) and uv installation, see the [Installation Guide](docs/en/guides/installation.md).

## Global Settings

Create `~/.llm4ad/settings.yaml` to configure shared providers across all projects:

```yaml
providers:
  - name: default
    type: openai
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
  - name: anthropic
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-20250514
```

Task configs then only need the provider name — credentials and model are resolved from global settings automatically.

For CLI commands, the interactive chat workflow, the Evolve-Block Advisor / Recommender, and the Python API, see the [Documentation](docs/en/index.md).

## Documentation

- [Documentation Home](docs/en/index.md)
- [Quick Start Guide](docs/en/guides/quickstart.md)
- [Configuration Guide](docs/en/guides/configuration.md)
- [Writing Evaluators](docs/en/guides/evaluators.md)

### Local Development

```bash
# Serve documentation with live reload
mkdocs serve

# Build static documentation
mkdocs build
```

## Project Structure

```
LLM4AD/
├── src/llm4ad/          # Main source code
│   ├── config/           # Configuration schemas and global settings
│   ├── consultant/       # Interactive configuration wizard
│   ├── builder/          # Task builder (analyzer, creator, validator, writer)
│   ├── advisor/          # Evolve-block advisor and recommender
│   ├── provider/         # LLM provider implementations
│   ├── planner/          # Algorithm planning layer
│   ├── coder/            # Code generation layer
│   ├── evaluator/        # Evaluation layer
│   ├── orchestrator/     # Workflow orchestration
│   ├── infra/            # Infrastructure (Ray, monitoring)
│   └── utils/            # Utilities
├── examples/             # Example configurations and applications
├── tests/                # Test suite
└── docs/                 # Documentation
```

## Contributing

Contributions are welcome! Please read our [Contributing Guide](docs/en/contributing/guidelines.md) for details.

```bash
# Set up development environment
uv sync --extra all

# Run tests
pytest

# Format code
black src/ tests/
ruff check src/ tests/ --fix
```

## License

This project is licensed under the BSD 3-Clause License - see the [LICENSE](LICENSE) file for details.

## Support

- [Documentation](docs/en/index.md)
- [Discussions](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- [Issue Tracker](https://github.com/Optima-CityU/LLM4AD_Next/issues)

## Join the Community

Scan the QR code with WeChat to join the LLM4AD_Next community group.

<div align="center">
  <img src="docs/assets/live-qr.png"
       alt="LLM4AD_Next WeChat community QR code"
       width="220">
</div>

## Star History

<!-- star-history:start -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/star-history/star-history-dark.svg">
  <img alt="Star history" src="docs/assets/star-history/star-history-light.svg">
</picture>
<!-- star-history:end -->
