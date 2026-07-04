# Python TSP Benchmark for LLM4AD

This is a **Python version** of the TSP (Traveling Salesman Problem) benchmark example that **does not require C++ compilation**. It's designed to work on any system with Python installed.

## Overview

This example demonstrates LLM4AD's ability to evolve Python TSP solving algorithms:
- **Base Algorithm**: Nearest Neighbor Heuristic (O(n²))
- **Goal**: Evolve more efficient TSP solvers using LLM
- **Metrics**: Tour length, execution time, tour validity

## Directory Structure

```
tsp_benchmark_python/
├── tsp_algorithm/         # Template code with EVOLVE markers
│   └── solve.py          # Baseline Nearest Neighbor implementation
├── data/                  # Test datasets
│   └── small/            # Small test files
│       ├── instance_001.json  # 10 cities
│       ├── instance_002.json  # 15 cities
│       ├── instance_003.json  # 20 cities
│       ├── instance_004.json  # 25 cities
│       └── instance_005.json  # 30 cities
├── tsp_evaluator.py       # Python evaluator (no compilation needed)
├── config.yaml     # Configuration file
├── test_evaluator.py      # Evaluator tests
├── generate_data.py       # Script to generate test instances
└── README.md              # This file
```

## How to Run

### Prerequisites

- Python 3.12+
- LLM4AD installed
- LLM API key configured
- **Install TSP dependencies**: `uv sync --extra tsp` (numpy is required)

### Installation

Install TSP-specific dependencies:
```bash
uv sync --extra tsp
```

For development and testing:
```bash
uv sync --extra dev --extra tsp
```

### Running the Benchmark

```bash
cd examples/applications/tsp_benchmark_python
uv run llm4ad run config.yaml
```

### Expected Output

The system will:
1. Analyze the baseline Nearest Neighbor implementation
2. Use LLM to generate improved algorithm insights
3. Implement new TSP solving algorithms in Python
4. Evaluate them on test datasets
5. Evolve toward better tour lengths over generations

## TSP Problem Description

The Traveling Salesman Problem (TSP) is a classic combinatorial optimization problem:
- **Input**: A set of cities with their (x, y) coordinates
- **Goal**: Find the shortest possible tour that visits each city exactly once and returns to the starting city
- **Challenge**: TSP is NP-hard, so exact solutions are intractable for large instances

## Customization

### Modify the Base Algorithm

Edit `tsp_algorithm/solve.py` to change the baseline algorithm between EVOLVE_START and EVOLVE_END markers.

### Add More Test Data

Run the data generation script:
```bash
python generate_data.py
```

Or add JSON files to `data/small/` with the format:
```json
{
  "nodes": [
    [0.0, 0.0],
    [1.0, 2.0],
    [3.0, 1.0]
  ]
}
```

### Adjust Evolution Parameters

Edit `config.yaml`:
- `max_generations`: How many generations to run
- `island_population_size`: Population size per island
- `early_stop_patience`: When to stop if no improvement

## Example Evolution

The system might evolve:
- Generation 0: Nearest Neighbor (baseline)
- Generation 1: Nearest Neighbor with 2-opt local search
- Generation 2: Multi-start Nearest Neighbor with best result
- Generation 3: Hybrid construction with smart initialization
- ...

Each generation is evaluated on tour length and correctness.

## Data Format

Each test instance is a JSON file with the following structure:

```json
{
  "nodes": [
    [x1, y1],
    [x2, y2],
    ...
  ]
}
```

Where each node is a pair of (x, y) coordinates representing a city's location.

## Algorithm Interface

The evolved algorithm must implement:

```python
def your_tsp_function(nodes):
    """Solve TSP and return tour as list of node indices."""
    # Your implementation here
    return tour  # List of indices [0, 1, 2, ..., n-1] in some order
```

Requirements:
1. Return a valid permutation of all node indices [0, 1, 2, ..., n-1]
2. Each city must be visited exactly once
3. The tour forms a cycle (implicitly returns to start)

## Performance Metrics

The evaluator tracks:
- **tour_length**: Total Euclidean distance of the tour (lower is better)
- **execution_time_ms**: Time taken to solve the instance
- **valid_tour**: Whether the tour is valid (1.0) or invalid (0.0)
