# Sorting Benchmark Example

This example demonstrates how to use LLM4AD to evolve high-performance sorting algorithms implemented in C++.

## Overview

This example includes:

- **`sorting_evaluator.py`**: A custom evaluator that inherits from `ExecutableEvaluator` and handles C++ compilation and execution
- **`sorting_algorithm/`**: A complete C++ project template with build system
- **`config.yaml`**: Complete LLM4AD configuration for the evolutionary pipeline
- **`data/`**: Test datasets of different sizes

## Prerequisites

- C++ compiler with C++17 support (g++ or clang)
- CMake (for building the C++ executable)
- Python 3.12+
- LLM4AD installed
- OpenAI API key (or other LLM provider)

## Setup

1. Install LLM4AD dependencies:

```bash
uv sync
```

2. Generate test datasets (already done):

```bash
cd data
python generate_datasets.py
```

3. Set your OpenAI API key:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

## Running the Evolutionary Pipeline

```bash
llm4ad run examples/applications/sorting_benchmark/config.yaml
```

## How It Works

1. **Problem Definition**: We want to evolve a sorting algorithm that minimizes execution time
2. **Interface**: All generated algorithms must implement the interface defined in `sorting_algorithm/include/sort_interface.h`:
   ```cpp
   struct SortResult {
       int comparisons;
       int swaps;
   };
   SortResult sort(std::vector<int>& data);
   ```
3. **Evaluation**:
   - The LLM generates a C++ implementation of the `sort` function
   - The `SortingEvaluator` writes the implementation to `sort_impl.h`
   - CMake builds the complete benchmark executable
   - The executable runs on the input dataset
   - Correctness is verified by comparing against `std::sort`
   - Metrics (execution time, comparisons, swaps) are parsed from output
   - The fitness score is computed based on the metrics

## Customization

- **Change objectives**: Modify the metric weights in `sorting_evaluator.py` to optimize for different objectives
- **Add larger datasets**: Add more files to the `data/` directory and update the configuration
- **Change problem domain**: Adapt the C++ interface and evaluator for other algorithmic problems
- **Use different LLM provider**: Update the `providers` section in the configuration

## Project Structure

```
sorting_benchmark/
├── README.md                          # This file
├── config.yaml             # LLM4AD configuration
├── sorting_evaluator.py               # Custom ExecutableEvaluator implementation
├── data/
│   ├── generate_datasets.py           # Dataset generation script
│   └── small/                         # Small datasets for quick evaluation
│       ├── 1000.txt
│       └── 10000.txt
└── sorting_algorithm/                 # C++ project template
    ├── CMakeLists.txt                 # CMake build configuration
    ├── build.sh                       # Build script
    ├── include/
    │   └── sort_interface.h           # Sort function interface
    └── src/
        └── main.cpp                   # Main benchmark entry point
```

## Output

Results are stored in `./runs/sorting_benchmark/<run_id>/`:

- `output/`: Best performing sorting algorithms
- `logs/`: Execution logs
- `checkpoints/`: Evolution checkpoints
- `generated/`: All generated C++ code files
- `cache/`: Cached LLM responses

## Example Evaluation

Here's an example of what a successful evaluation outputs:

```
execution_time_ms: 12
comparisons: 8723
swaps: 4321
correct: 1
```

The evaluator automatically parses these metrics using the default regex-based parser from `ExecutableEvaluator`.

## License

This example is part of the LLM4AD project.
