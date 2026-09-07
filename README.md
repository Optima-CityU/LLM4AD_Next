<h1 align="center">OpenLoopX · LLM4AD Next</h1>


<p align="center">
  <strong>From problem description to runnable evolutionary algorithm search — in one command.</strong><br>
  LLM-driven automated algorithm design with evolutionary optimization
</p>

<p align="center">
  <a href="https://llm4ad-next.cn/">Website</a> ·
  <a href="#quick-start">Quickstart</a> ·
  <a href="docs/en/index.md">Docs</a> ·
  <a href="https://github.com/Optima-CityU/LLM4AD_Next/wiki">Wiki</a> 
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

<p align="center">
  <strong>English</strong> | <a href="./README_zh.md">中文</a>
</p>

---

<p align="center">
  <strong>⭐ Star us on GitHub to earn 10$ bonus tokens for the <a href="https://llm4ad-next.cn/">Online Website</a>!</strong>
</p>

---

## 🔥 News

- 🧮 [2026.09][New Dataset]: The **[AlphaEvolve Mathematics Benchmark Suite](examples/applications/alphaevolve_math_benchmark/README.md)** adds 11 independently runnable mathematical optimization cases, case-local evaluators, evolved implementations, and reusable experience artifacts.
- 🏝️ [2026.09][New Search Method]: **Diverse Island GA** is now available, assigning a continuous spectrum of exploitation, correction, and independent-exploration behaviors across any number of islands while coordinating migration and memory use.
- 🔬 [2026.07][New Feature]: **Search methods migrated** — EoH, MEoH, ReEvo, and MCTS-AHD are now available as standalone orchestrators. See [Search Methods](#search-methods-automatic-heuristic-design).
- 🧠 [2026.07][New Feature]: **[MindMemOS](https://github.com/dadastory/MindMemOS)-backed long-term memory** is now available, with global, project, and task memory scopes plus configurable Chat and Embedding model bindings. See the [Memory Guide](docs/en/guides/memory.md).
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

Migration status of the Automatic Heuristic Design (AHD) search methods from the original [LLM4AD](https://github.com/Optima-CityU/LLM4AD/tree/main/llm4ad) platform.

| Method | Status | Method | Status |
|--------|--------|--------|--------|
| **IslandGA** | ✅ Available | **FunSearch** | ⏳ Pending |
| **Diverse Island GA** | ✅ Available | **HillClimb** | ⏳ Pending |
| **MEoH** | ✅ Available | **LHNS** | ⏳ Pending |
| **DyCA** | ✅ Available | **LLaMEA** | ⏳ Pending |
| **EoH** | ✅ Available | **MLES** | ⏳ Pending |
| **ReEvo** | ✅ Available | **MOEA/D** | ⏳ Pending |
| **MCTS-AHD** | ✅ Available | **NSGA-II** | ⏳ Pending |
| | | **PartEvo** | ⏳ Pending |
| | | **RandSample** | ⏳ Pending |

### Using the migrated methods

Set `evolution.type` in your config and run `llm4ad run <config.yaml>`. See `examples/config/config.complete.yaml` for full examples.

```yaml
evolution:
  type: "eoh"  # options include "diverse_island_ga", "island_ga", "eoh", "meoh", "reevo", "mcts_ahd", "dyca"
```

## Mathematical Optimization Highlights

The new [AlphaEvolve Mathematics Benchmark Suite](examples/applications/alphaevolve_math_benchmark/README.md) brings 11 challenging geometry, combinatorics, and inequality problems into a unified LLM-driven evolution workflow. Diverse Island GA combines distinct search behaviors with reusable long-term experience, allowing different islands to preserve strong mechanisms, correct known failures, and explore independent solution families. The comparison uses the better published value from AlphaEvolve and LoongFlow for each objective; every row links directly to the evolved implementation and all active excellent-experience cards retained by the memory system.

| Case | Direction | AlphaEvolve | LoongFlow | LLM4AD | Gap to best published | Best implementation | Evolved experiences |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| 26 circles in a unit square | Higher | `2.6358627564136983` | `2.6359829624734026` | **2.635983083325037** | Ahead by `1.208516344e-7` | [solve.py](examples/applications/alphaevolve_math_benchmark/circle_packing/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/circle_packing/results/best/experiences/README.md) |
| 21 circles in a perimeter-four rectangle | Higher | `2.3658321334167627` | `2.365832229500823` | **2.365832375700835** | Ahead by `1.46200012e-7` | [solve.py](examples/applications/alphaevolve_math_benchmark/circle_rectangle/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/circle_rectangle/results/best/experiences/README.md) |
| 11 unit hexagons in a regular hexagon | Lower | `3.930092` | `3.928906855463712` | **3.92468841680981** | Ahead by `0.004218438653902` | [solve.py](examples/applications/alphaevolve_math_benchmark/hexagon_packing/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/hexagon_packing/results/best/experiences/README.md) |
| 16-point maximum/minimum distance ratio | Lower | `12.88926611203463` | `12.889243547212832` | **12.889229907694045** | Ahead by `1.3639518787e-5` | [solve.py](examples/applications/alphaevolve_math_benchmark/max_min_distance_ratio/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/max_min_distance_ratio/results/best/experiences/README.md) |
| Uncertainty inequality | Lower | `0.35209910442252773` | `0.352099104421844` | **0.35209910441916187** | Ahead by `2.68213e-12` | [solve.py](examples/applications/alphaevolve_math_benchmark/uncertainty_inequality/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/uncertainty_inequality/results/best/experiences/README.md) |
| Second autocorrelation inequality | Higher | `0.8962799441554083` | `0.9027021077220739` | **0.9053043552878318** | Ahead by `0.0026022475657579` | [solve.py](examples/applications/alphaevolve_math_benchmark/second_autocorrelation/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/second_autocorrelation/results/best/experiences/README.md) |
| First autocorrelation inequality | Lower | `1.5052939684401607` | `1.509527314861778` | **1.507459811737381** | Behind by `0.0021658432972203` | [solve.py](examples/applications/alphaevolve_math_benchmark/first_autocorrelation/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/first_autocorrelation/results/best/experiences/README.md) |
| Minimum overlap | Lower | `0.380924` | `0.3809137564083654` | **0.38092504473534605** | Behind by `1.128832698065e-5` | [solve.py](examples/applications/alphaevolve_math_benchmark/minimum_overlap/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/minimum_overlap/results/best/experiences/README.md) |
| Heilbronn problem in an equilateral triangle | Higher | `0.036529889880030156` | `0.0365298898793351` | **0.0365298881927707** | Behind by `1.687259456e-9` | [solve.py](examples/applications/alphaevolve_math_benchmark/heilbronn_triangle/results/best/solve.py) | [memory cards](examples/applications/alphaevolve_math_benchmark/heilbronn_triangle/results/best/experiences/README.md) |

The minimum-overlap and Heilbronn-triangle rows were added as near-reference results; the gap column always compares LLM4AD with the stronger of the two published baselines.

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

## Acknowledgements

The AutoResearch module is based on / adapted from [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) (MIT License). Its original copyright and license notice are retained in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Support

- [Documentation](docs/en/index.md)
- [Discussions](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- [Issue Tracker](https://github.com/Optima-CityU/LLM4AD_Next/issues)

## Join the Community

Scan the QR code with WeChat to join the LLM4AD_Next community group.

<div align="center">
  <img src="docs/assets/live-qr-20260828-101026.png"
       alt="LLM4AD_Next WeChat community QR code"
       width="220">
</div>
