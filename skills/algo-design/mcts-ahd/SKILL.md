---
name: mcts-ahd
description: "Monte Carlo Tree Search for Automatic Heuristic Design (MCTS-AHD) method skill. USE WHEN the user explicitly requests MCTS / Monte Carlo Tree Search for heuristic design, or wants tree-structured search with UCT-based exploration-exploitation balance."
triggers:
  - mcts
  - mcts-ahd
  - monte carlo tree search
  - tree search optimization
---

# MCTS-AHD Skill

> **Paper**: Zheng et al., "Monte Carlo Tree Search for Comprehensive Exploration in LLM-based Automatic Heuristic Design", ICML 2025.

## 1. Method Essence

MCTS-AHD models heuristic search as a **tree**: the root is a template/initial solution, each node is a heuristic implementation, and edges are "mutations producing child nodes". Four-step cycle:

1. **Selection**: From root, select a node using the UCT formula
2. **Expansion**: Use LLM to generate child node variants from the selected node
3. **Evaluation**: Evaluate the child node, obtain reward
4. **Backpropagation**: Update Q-value and visit count for all ancestor nodes along the path

UCT formula:
```
UCT = (Q - q_min) / (q_max - q_min) + λ₀ × eval_remain × √(log(parent.visits + 1) / node.visits)
```
- Term 1: Exploitation — normalized Q-value, favoring high-reward nodes
- Term 2: Exploration — weighted by λ₀×remaining budget, favoring less-visited nodes
- **α and λ₀ jointly control the exploration-exploitation balance** (reference: α=0.5, λ₀=0.1)

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

**Parameter Guidance**:
- `max_generations`: Interpreted as MCTS iterations; 500-2000 typical.
- `init_size`: 4-6 works well for most problems.
- `population_size`: 10-20 for moderate problems; increase for complex landscapes.
- `alpha`: Higher (0.7-1.0) = wider tree; lower (0.3-0.5) = deeper tree.
- `lambda_0`: Higher (0.2-0.5) = more exploration; lower (0.05-0.1) = more exploitation.
- `max_depth`: 10-15 prevents infinite deepening.

### What Happens During Evolution

1. Root node initialized from seed or template
2. Each iteration:
   - Select a node using UCT (balances Q-value and visit count)
   - Expand: generate 1-3 child nodes via LLM mutation
   - Evaluate children, update Q-values
   - Backpropagate: update all ancestors' visit counts and Q-values
3. Tree grows asymmetrically — high-value branches get explored more
4. Late iterations converge to the best branch for refinement

### Common Pitfalls

- Tree too shallow → increase `max_depth` or decrease `lambda_0`
- Tree too wide → decrease `alpha` or increase `lambda_0`
- All nodes similar → increase diversity in mutation prompts
- Budget exhausted before convergence → increase `max_sample_nums`

## 4. Acceptance Criteria

- Tree structure visible in evolution log (parent-child relationships)
- UCT scores reported for each selection decision
- Depth and breadth both explored (not just one path)
- Late iterations focus on refining the best branch
- Final best individual is a leaf node with highest Q-value
