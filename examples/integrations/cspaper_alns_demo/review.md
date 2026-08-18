---
paper: "ALNS destroy-operator optimization for Euclidean TSP"
problem_name: "alns_tsp_destroy_operator"
problem_type: "combinatorial_optimization"
problem_description: "Optimize the destroy operator of an Adaptive Large Neighborhood Search solver for symmetric Euclidean travelling salesman problems."
function_name: "destroy_operator"
input_format: "TSPLIB95 EUC_2D .tsp instance containing node identifiers and two-dimensional coordinates"
output_format: "JSON object containing an ordered tour of valid node identifiers"
code_path: "algorithm/ALNS-master"
---

# Candidate scope

Only modify the function enclosed by:

`EVOLVE_START destroy_operator`

and:

`EVOLVE_END`

The ALNS framework, repair operator, objective calculation, stopping
criterion, evaluator, dataset loader and output validation must remain fixed.

# Algorithm feedback

<!-- cspaper: category=search_direction; name=relatedness_removal; priority=high -->
- Explore relatedness-based removal strategies that jointly remove geographically close or structurally related edges, allowing the fixed repair operator to reconstruct larger promising tour segments.

<!-- cspaper: category=search_direction; name=randomized_worst_edge_removal; priority=high -->
- Explore randomized worst-edge removal strategies that prefer expensive edges while retaining controlled randomness to avoid repeatedly generating the same neighborhood.

<!-- cspaper: category=search_direction; name=adaptive_destruction; priority=medium -->
- Adapt the number or pattern of removed edges to instance size, edge-cost distribution and recent search characteristics without violating the supplied destruction budget.

<!-- cspaper: category=search_direction; name=diversity_preservation; priority=medium -->
- Balance intensification around expensive tour segments with diversification across different regions of the tour.

# Measurable objectives

<!-- cspaper: category=objective; name=relative_tour_gap_pct; direction=minimize; measurement=mean percentage gap between candidate tour length and the fixed random-removal baseline over all evaluation instances and random seeds; aggregation=mean; unit=percent; weight=1.0 -->
- Minimize the normalized tour-length gap relative to the fixed baseline destroy operator.

<!-- cspaper: category=objective; name=runtime_ms; direction=minimize; measurement=median end-to-end solver runtime under an identical iteration budget; aggregation=median; unit=ms; weight=0.05 -->
- Keep runtime overhead small under the same ALNS iteration budget.

# Hard constraints

<!-- cspaper: category=constraint; name=valid_partial_state; type=hard; check=destroy_operator must return a TspState whose nodes and distance data remain unchanged and whose edges are a valid subset of the original tour edges -->
- The destroy operator must preserve the problem definition and return a structurally valid partial state.

<!-- cspaper: category=constraint; name=effective_destruction; type=hard; check=destroy_operator must remove at least one edge and must not remove more edges than the configured destruction budget -->
- Every destroy operation must make bounded progress.

<!-- cspaper: category=constraint; name=valid_tour; type=hard; check=the repaired final result must contain every input node exactly once and return to the start node -->
- Every evaluated result must be a complete Hamiltonian cycle.

<!-- cspaper: category=constraint; name=valid_node_ids; type=hard; check=the returned tour must contain only node identifiers from the input TSPLIB instance -->
- Candidate output must not add, remove or fabricate cities.

<!-- cspaper: category=constraint; name=finite_objective; type=hard; check=the independently recomputed tour length must be finite and positive -->
- Every candidate must produce an independently verifiable objective value.

<!-- cspaper: category=constraint; name=budget_compliance; type=hard; check=the candidate must run under the fixed iteration and timeout budget and must not access the network or external test files -->
- Candidates must respect the evaluation budget and isolation rules.

# Experiment coverage

<!-- cspaper: category=dataset -->
- Train on uniform, clustered, ring, grid and mixed synthetic EUC_2D instances with 20 to 60 cities.

<!-- cspaper: category=dataset -->
- Validate on unseen uniform, clustered and ring instances with 25 to 70 cities.

<!-- cspaper: category=dataset -->
- Perform final selection on hidden grid, mixed and uniform instances with up to 100 cities.

<!-- cspaper: category=baseline; name=random_edge_removal -->
- Compare every candidate against the original random edge-removal destroy operator under identical seeds, repair operator, acceptance criterion and iteration budget.

# Reproducibility protocol

The evaluator uses fixed seeds 42, 314 and 2026.

The candidate and fixed random-removal baseline use the same initial
solution, repair operator, acceptance criterion and iteration budget.

Tour length is independently recomputed from the TSPLIB coordinates.
The objective value reported by candidate code is not trusted.

Exceptions, invalid tours, timeouts and non-finite values are treated
as failed evaluations.