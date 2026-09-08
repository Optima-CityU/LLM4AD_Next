---
name: dyca
description: "DyCA (Dynamic Clustering Algorithm) method skill. USE WHEN the user explicitly requests DyCA / dynamic clustering adaptive evolution, or wants instance-aware search that clusters problem instances and allocates specialized algorithm pools via dynamic reclustering and resource allocation."
triggers:
  - dyca
  - dynamic clustering
  - dynamic clustering adaptive
  - instance-aware evolution
---

# DyCA (Dynamic Clustering Adaptive) Skill

> **Reference**: LLM4AD_Next instance-aware multi-pool evolution model (DyCA, Dynamic Clustering Algorithm).

## 1. Method Essence

DyCA is an **instance-aware** search method. Instead of treating all problem instances uniformly, it dynamically partitions the training instances into **clusters** and maintains **heterogeneous algorithm pools** specialized to those clusters. The search then allocates LLM effort across pools based on observed difficulty and cluster stability.

Three heterogeneous pools:

| Pool | Role |
|---|---|
| **Generalist pool** | Global best algorithms that perform well across all clusters |
| **Specialist pools** | Per-cluster optimized algorithms (one pool per cluster) |
| **Complementary pool** | Cross-cluster diversity algorithms that complement others |

Core mechanisms:
- **Per-instance evaluation** and cluster-specific fitness
- **Dynamic reclustering** based on Adjusted Rand Index (ARI) stability
- **SOS (Save Our Specialist)** inter-cluster collaboration to escape local optima
- **Macro (ARI-based) and micro (gap-based) resource allocation**

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

### What Happens During Evolution

1. Build instance feature vectors using `n_anchors` anchor algorithms
2. Cluster instances via `clustering_method` into `n_clusters` clusters
3. Every `recluster_interval` generations, check ARI stability; recluster if unstable
4. Each generation produces `offspring_per_generation` new individuals
5. Allocate effort across generalist / specialist / complementary pools
6. Trigger SOS when a cluster stagnates for `sos_stagnation_threshold` generations
7. `using_mode=true` freezes clustering and runs only specialist evolution (mature clusters)

### Common Pitfalls

- Too few/too many clusters → poor specialization; tune `n_clusters`
- Reclustering too often → instability; raise `recluster_interval` / lower `ari_threshold`
- Anchors too few → weak feature vectors; increase `n_anchors`
- Complementary pool starved → raise `base_complementary_ratio`
- Clusters already stable → enable `using_mode` to stop reclustering

## 4. Acceptance Criteria

- Instances cleanly clustered; each cluster has a specialist pool
- Reclustering only on ARI instability (not every generation)
- Resource allocation responds to per-cluster difficulty
- SOS triggers on stagnation and recovers a better solution
- Using mode works for already-stable datasets