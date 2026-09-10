# Algorithm Design Skills

This directory contains algorithm design skills that can be loaded by coding agents to autonomously design and evolve algorithms.

## Available Skills

| Skill | Paper | Method | Use Case |
|-------|-------|--------|----------|
| **eoh** | Liu et al., ICML 2024 | Population evolution with E1/E2/M1/M2 operators | General optimization |
| **reevo** | Ye et al., NeurIPS 2024 | Reflective evolution with failure learning | Learning from experience |
| **funsearch** | Romera-Paredes et al., Nature 2024 | Database-driven program search | Mathematical discovery |
| **meoh** | Yao et al., AAAI 2025 | Multi-objective EoH with Pareto archive | Multi-objective optimization |
| **nsga2** | Deb et al., IEEE TEC 2002 | NSGA-II with non-dominated sorting | Multi-objective optimization |
| **moead** | Zhang & Li, IEEE TEC 2007 | MOEA/D decomposition-based | Multi-objective optimization |
| **mcts-ahd** | Zheng et al., ICML 2025 | Monte Carlo Tree Search | Tree-structured search |

## Directory Structure

```
algo-design/
├── README.md              # This file
├── eoh/                   # Evolution of Heuristics
│   ├── SKILL.md           # Skill definition
│   └── params.yaml        # Recommended parameters
├── reevo/                 # Reflective Evolution
├── funsearch/             # FunSearch (Program Search)
├── meoh/                  # Multi-objective EoH
├── nsga2/                 # NSGA-II
├── moead/                 # MOEA/D
├── mcts-ahd/              # MCTS for AHD
├── island-ga/             # Island Genetic Algorithm
├── diverse-island-ga/     # Diversity-oriented Island GA
├── dyca/                  # Dynamic Clustering Adaptive
└── use_example/           # Complete usage example
    ├── README.md          # Example overview
    ├── skill_with_task.md # Integration guide
    └── tsp_eoh_example/   # TSP + EoH task package
```

## How Skills Work

### Skill Definition (SKILL.md)
Each skill contains:
- **Method essence**: Core algorithmic idea
- **Recommended parameters**: Starting-point configurations
- **Operator descriptions**: What each operator does
- **Acceptance criteria**: How to verify the skill is working

### Skill + Task Package Integration
To use a skill, combine it with a task package:

1. **SKILL.md** tells the agent **how to search** (methodology)
2. **Task package** tells the agent **what to search for** (problem definition)

```
User Request → Agent loads SKILL.md (how)
             → Agent reads task package (what)
             → Agent executes evolution loop
```

## Usage Example

See `use_example/` for a complete TSP + EoH example demonstrating:
- How to structure a task package
- How to configure evolution parameters
- How to switch between different skills

## Adding New Skills

To add a new algorithm design skill:

1. Create a new directory: `skills/algo-design/<skill-name>/`
2. Add `SKILL.md` with the skill definition
3. Add `params.yaml` with recommended parameters
4. Follow the template in existing skills (e.g., `eoh/`)

## Parameter Reference

### Common Parameters
- `max_generations`: Maximum evolution generations
- `population_size`: Number of individuals in population
- `max_sample_nums`: LLM call budget cap
- `selection_num`: Parents selected for crossover

### Skill-Specific Parameters
- **EoH**: `use_e2_operator`, `use_m1_operator`, `use_m2_operator`
- **ReEvo**: `mutation_rate`, reflection window size
- **FunSearch**: `num_islands`, `samples_per_prompt`
- **MEoH**: `objective_metrics`, archive size
