# LunarLander Policy Evolution with LLM4AD

This example demonstrates LLM4AD's ability to evolve control policies for the LunarLander reinforcement learning environment. Instead of using RL, we use evolutionary algorithms with LLM to discover heuristic control policies.

## Overview

The LunarLander environment simulates a lunar lander attempting to land safely on a landing pad located at position (0, 0).

**Goal**: Evolve a control policy that safely lands the lander across multiple random initializations.

**Success Criteria**: A touchdown with:
- Low vertical speed (not crashing)
- Upright orientation (angle close to 0)
- Low angular velocity
- Both legs in contact with ground

## Directory Structure

```
lunarlander_python/
├── policy/               # Policy code directory
│   ├── .git/            # Git repository (auto-initialized)
│   └── choose_action.py  # Policy with EVOLVE markers
├── data/                 # Test seeds for environment
│   └── seeds/            # Seed files (one integer per line)
│       └── train_seeds.txt
├── lunarlander_evaluator.py  # Custom evaluator
├── lunarlander_benchmark_config.yaml  # Configuration file
└── README.md             # This file
```

## Policy Function

The policy function `choose_action(s, last_action, s_pre)` is called at each time step:

**State vector (s)**: 8 elements
- `s[0]`: Horizontal position (x-coordinate)
- `s[1]`: Vertical position (y-coordinate)
- `s[2]`: Horizontal velocity (vx)
- `s[3]`: Vertical velocity (vy)
- `s[4]`: Angle in radians (0 is upright)
- `s[5]`: Angular velocity
- `s[6]`: 1 if left leg is in contact with ground, else 0
- `s[7]`: 1 if if right leg is in contact with ground, else 0

**Arguments**:
- `s`: Current state vector
- `last_action`: Action taken in previous step (0-3)
- `s_pre`: State vector before last action was executed

**Actions**:
- `0` - Do nothing (coast)
- `1` - Fire left orientation engine (rotate counter-clockwise)
- `2` - Fire main engine (thrust upward, consumes fuel)
- `3` - Fire right orientation engine (rotate clockwise)

**Return**: Integer action (0, 1, 2, or 3)

## Evaluation Metrics

The evolved policies are evaluated on multiple random seeds:

| Metric | Type | Description |
|---------|-------|-------------|
| `episode_reward` | MAXIMIZE | Total reward accumulated during episode (safe landing ~200) |
| `fuel_consumed` | MINIMIZE | Number of fuel-consuming actions (actions 1, 2, 3) |
| `success` | MAXIMIZE | Whether landing was successful (1.0) or not (0.0) |
| `execution_time_ms` | MINIMIZE | Total policy execution time |

**Scoring**: Weighted combination normalized to 0-1:
- 50% reward (normalized by 200)
- 30% success rate
- 10% fuel efficiency
- 10% execution efficiency

## How to Run

### Prerequisites

- Python 3.12+
- LLM4AD installed
- LLM API key configured
- **Install dependencies**: `uv sync --extra all` (includes gymnasium)

### Installation

Install required dependencies:
```bash
uv sync --extra all
```

This includes:
- `gymnasium`: LunarLander environment
- `numpy`: Array operations

### Running the Benchmark

```bash
cd examples/applications/lunarlander_python
uv run llm4ad run lunarlander_benchmark_config.yaml
```

### Expected Output

The system will:
1. Initialize baseline policy (heuristic control)
2. Use LLM to generate improved policy insights
3. Implement new control policies in Python
4. Evaluate them on multiple random seeds
5. Evolve toward better landing performance

## Customization

### Modify the Baseline Policy

Edit `policy/choose_action.py` to change the baseline between EVOLVE_START and EVOLVE_END markers.

### Add More Test Seeds

Create additional seed files in `data/seeds/` with one integer per line:

```
42
123
456
789
```

### Adjust Evolution Parameters

Edit `lunarlander_benchmark_config.yaml`:
- `max_generations`: How many generations to run
- `island_population_size`: Population size per island
- `early_stop_patience`: When to stop if no improvement
- `num_islands`: Number of parallel evolution islands

### Modify Evaluation

The baseline policy uses a heuristic approach:
1. Target angle towards center
2. Maintain hover altitude proportional to horizontal offset
3. When legs contact ground, focus on reducing fall speed

You can experiment with:
- State machine based on landing phase
- Proportional control with tuned gains
- Bang-bang (on/off) control
- Hybrid approaches

## Example Evolution

The system might evolve:
- Generation 0: Baseline heuristic (angle targeting + hover control)
- Generation 1: Refined gains with velocity feedback
- Generation 2: State machine with approach/descent/landing phases
- Generation 3: Adaptive thresholds based on state
- ...

Each generation is evaluated on:
- Episode reward (safety of landing)
- Fuel efficiency
- Execution speed

## Testing Individual Policies

To test a specific policy manually:

```bash
# Run a quick test with single seed
python -c "
import sys
sys.path.insert(0, 'policy')
from choose_action import choose_action
import json

# Test with sample state
s = [0.1, 1.5, -0.5, 1.2, 0.05, 0.0, 0, 0]
action = choose_action(s, 0, s)
print(f'Action: {action}')
"
```

## Environment Configuration

The evaluator uses standard LunarLander-v3 settings:
- `gravity`: -10.0
- `enable_wind`: False
- `wind_power`: 15.0 (disabled)
- `turbulence_power`: 1.5 (disabled)
- `max_episode_steps`: 200

You can modify these in `lunarlander_evaluator.py` if needed.

## Troubleshooting

### Gymnasium Not Found

Install gymnasium:
```bash
uv sync --extra all
```

### Policy Crashes

Check that:
- Policy returns valid action (0, 1, 2, or 3)
- Policy handles edge cases (empty state, invalid input)
- No infinite loops or recursive calls

### Low Performance

Try:
- Increasing population size
- Running more generations
- Adding diverse seed sets
- Adjusting metric weights in evaluator

### Memory Issues

Reduce:
- `max_generations`
- `island_population_size`
- `memory.max_entries`

## References

- [LunarLander Documentation](https://gymnasium.farama.org/environments/mujoco/lunar_lander/)
- [Control Theory Basics](https://en.wikipedia.org/wiki/Control_theory)
- [Reinforcement Learning](https://en.wikipedia.org/wiki/Reinforcement_learning)
