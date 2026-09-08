# Algorithm Design Skill

## Usage

Give a coding agent this prompt:

```
I want you to design a [PROBLEM] solver using the [SKILL] method.

Skill: [SKILL_URL]
Task: [TASK_PATH]

Read the skill, read the task package, run [N] generations, give me the best algorithm.
```

## TSP + EoH Example

The `tsp_eoh/` directory contains a complete TSP task package. Use this prompt:

```
I want you to design a TSP solver using the EoH method.

Skill: https://github.com/Optima-CityU/LLM4AD_Next/blob/develop/skills/algo-design/eoh/SKILL.md
Task: /path/to/use_example/tsp_eoh/

Read the skill, read the task package, run 50 generations, give me the best algorithm.
```

### Task Package Structure

```
tsp_eoh/
├── config.yaml              # Evolution config (eoh, pop=5, 50 gens)
├── tsp_algorithm/
│   └── solve.py             # TSP solver with EVOLVE markers
├── tsp_evaluator.py         # Evaluates tour length
└── data/sample/
    └── instance_001.json    # 10-city test instance
```

## Available Skills

| Skill | Method |
|-------|--------|
| eoh | Population evolution |
| reevo | Reflective evolution |
| funsearch | Database-driven search |
| meoh | Multi-objective evolution |
| nsga2 | NSGA-II |
| moead | MOEA/D |
| mcts-ahd | Monte Carlo Tree Search |

Skill URL: `https://github.com/Optima-CityU/LLM4AD_Next/blob/develop/skills/algo-design/{SKILL_NAME}/SKILL.md`

## Creating Your Own Task

1. Create directory with `config.yaml`, `algorithm/solve.py`, `evaluator.py`, `data/sample/`
2. In `solve.py`, wrap the function to evolve with `# EVOLVE_START` / `# EVOLVE_END`
3. In `config.yaml`, set `evolution.type` to match your chosen skill
4. Point the agent to your task directory
