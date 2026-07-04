# Python Sorting Benchmark for LLM4AD

This is a **Python version** of the sorting benchmark example that **does not require C++ compilation**. It's designed to work on any system with Python installed.

## Overview

This example demonstrates LLM4AD's ability to evolve Python sorting algorithms:
- **Base Algorithm**: Bubble Sort (O(n²))
- **Goal**: Evolve more efficient sorting algorithms using LLM
- **Metrics**: Execution time, comparisons, swaps

## Directory Structure

```
sorting_benchmark_python/
├── sorting_algorithm/      # Template code with EVOLVE markers
│   └── sort.py             # Baseline Bubble Sort implementation
├── data/                   # Test datasets
│   └── small/              # Small test files
│       ├── test_001.json
│       ├── test_002.json
│       └── test_003.json
├── sorting_evaluator.py    # Python evaluator (no compilation needed)
├── config.yaml  # Configuration file
└── README.md               # This file
```

## How to Run

### Prerequisites

- Python 3.12+
- LLM4AD installed
- LLM API key configured

### Running the Benchmark

```bash
cd examples/applications/sorting_benchmark_python
uv run llm4ad run config.yaml
```

### Expected Output

The system will:
1. Analyze the baseline Bubble Sort implementation
2. Use LLM to generate improved algorithm insights
3. Implement new algorithms in Python
4. Evaluate them on test datasets
5. Evolve toward better performance over generations

## Advantages Over C++ Version

- ✅ **No compilation required** - runs directly with Python
- ✅ **Cross-platform** - works on Windows, macOS, Linux
- ✅ **Faster iteration** - no build step
- ✅ **Easier debugging** - Python code is more accessible

## Customization

### Modify the Base Algorithm

Edit `sorting_algorithm/sort.py` to change the baseline algorithm between EVOLVE_START and EVOLVE_END markers.

### Add More Test Data

Add JSON files to `data/small/` with the format:
```json
{
  "input": [5, 2, 8, 1, 9],
  "expected": [1, 2, 5, 8, 9]
}
```

### Adjust Evolution Parameters

Edit `config.yaml`:
- `max_generations`: How many generations to run
- `island_population_size`: Population size per island
- `early_stop_patience`: When to stop if no improvement

## Example Evolution

The system might evolve:
- Generation 0: Bubble Sort (baseline)
- Generation 1: Quick Sort with Lomuto partition
- Generation 2: Quick Sort with median-of-three pivot
- Generation 3: Hybrid Quick Sort + Insertion Sort for small arrays
- ...

Each generation is evaluated on execution time and correctness.
