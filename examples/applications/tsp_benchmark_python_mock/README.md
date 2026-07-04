# Python TSP Benchmark (Mock) for LLM4AD

This is a **mock version** of the Python TSP benchmark that uses a **mock LLM provider** for fast offline debugging. No API keys or network access required.

## Overview

This example runs the full LLM4AD evolution pipeline without real LLM calls:
- **Base Algorithm**: Nearest Neighbor Heuristic (from `tsp_benchmark_python`)
- **LLM Provider**: Mock provider with hardcoded responses
- **Purpose**: Fast pipeline debugging and CI testing

## Directory Structure

```
tsp_benchmark_python_mock/
├── data/                          # Test datasets
│   └── small/                     # Small test files
│       ├── instance_001.json      # 10 cities
│       ├── instance_002.json      # 15 cities
│       ├── instance_003.json      # 20 cities
│       ├── instance_004.json      # 25 cities
│       └── instance_005.json      # 30 cities
├── tsp_evaluator.py               # Python evaluator (no compilation needed)
├── config.yaml        # Configuration file (mock provider)
└── README.md                      # This file
```

## How to Run

### Prerequisites

- Python 3.12+
- LLM4AD installed
- No API key needed

### Running the Benchmark

```bash
cd examples/applications/tsp_benchmark_python_mock
uv run llm4ad run config.yaml
```

### Expected Output

The system will:
1. Use the mock LLM provider to generate algorithm insights (no network calls)
2. Generate code via the custom coder using mock responses
3. Evaluate generated algorithms on test datasets
4. Complete the full evolution loop in seconds

## Differences from tsp_benchmark_python

| Feature | tsp_benchmark_python | tsp_benchmark_python_mock |
|---------|---------------------|--------------------------|
| LLM Provider | Real API (OpenAI-compatible) | Mock (no API key) |
| Coder | claude_code (agent) | custom (plain LLM) |
| Speed | Minutes per generation | Seconds total |
| Use Case | Real algorithm evolution | Pipeline debugging / CI |

## Customization

### Adjust Evolution Parameters

Edit `config.yaml`:
- `max_generations`: How many generations to run (default: 3)
- `island_population_size`: Population size per island (default: 4)
- `checkpoint_interval`: Checkpoint frequency (default: 1)
