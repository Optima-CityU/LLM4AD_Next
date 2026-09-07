---
name: funsearch
description: "FunSearch (Program Search) method skill. USE WHEN the user explicitly requests FunSearch / Program Search, or wants search with a programs database that stores and samples from high-scoring programs."
triggers:
  - funsearch
  - program search
  - database-driven search
---

# FunSearch Skill

> **Paper**: Romera-Paredes et al., "Mathematical discoveries from program search with large language models", Nature 625 (2024).

## 1. Method Essence

FunSearch organizes search using a **ProgramsDatabase**: programs are stored and indexed by evaluation score, with sampling prioritizing high-score buckets for mutation. New programs are evaluated and stored by score, forming a "high-score priority, low-score elimination" evolution. It also uses an **islands mechanism** for diversity: multiple independent sub-populations evolve separately, with periodic exchange.

Core components:
- **Database**: Archive of programs + scores, stratified by score (higher score = higher probability of being sampled as parent)
- **Sampler**: Generates mutation prompts from high-score programs (not from empty prompts)
- **Evaluator**: Independent evaluation, results written back to database

## 2. Recommended Parameters

See `params.yaml` in this directory for the recommended parameter configuration.

### What Happens During Evolution

1. Database initialized with seed programs (or random generation)
2. Each generation:
   - Sample parent from high-score bucket
   - Generate N variants via LLM mutation
   - Evaluate variants independently
   - Store scored variants back to database
   - Optionally: eliminate low-score programs
3. Islands evolve independently, periodically exchanging best programs
4. Database naturally stratifies: top programs dominate sampling

### Common Pitfalls

- Database collapses (all programs similar) → increase island count or mutation diversity
- Too aggressive elimination → lose useful building blocks; increase `population_size`
- No improvement → check if sampling always picks the same parent; increase `samples_per_prompt`

## 4. Acceptance Criteria

- Database stratification visible (programs in different score buckets)
- High-score programs repeatedly used as mutation parents (exploitation)
- Periodic sampling from lower buckets (exploration)
- Clear elimination of consistently low-scoring programs
- Final best program in top database bucket
