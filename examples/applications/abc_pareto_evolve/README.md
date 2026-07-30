# abc_pareto_evolve

An LLM4AD example that evolves an **ABC optimization portfolio** to push a
benchmark's And-Inverter Graphs (AIGs) below the IWLS 2026 contest Virtual Best
Solver (VBS) frontier. The ABC binary is **fixed** — only the portfolio (a list
of ABC command sequences) is evolved.

Target benchmark: **ex222**.

## How it works

The evaluator (`abc_pareto_evaluator.py`) takes the evolved portfolio from
`synthesis_pareto/portfolio.py` and, for each VBS seed AIG in `seeds/`, applies
every command sequence via:

```
source abc.rc; read_aiger <seed>; <sequence>; strash; print_stats; write_aiger <out>
```

Each candidate is verified with `cec <seed> <out>` (non-equivalent candidates
score 0). The verified candidates are Pareto-pruned into a frontier and scored
against the fixed VBS frontier using the official IWLS per-delay rule. A mean
`iwls_score > 100` beats VBS.

## Layout

```
abc_pareto_evolve/
├── config_pareto.yaml         # LLM4AD run config (benchmark ex222)
├── abc_pareto_evaluator.py    # custom evaluator (registry: abc_pareto_evaluator)
├── synthesis_pareto/
│   └── portfolio.py           # EVOLVE TARGET — build_portfolio() -> list[list[str]]
├── data/truth_pareto_ex222/
│   └── ex222.truth            # truth table (dataset)
├── seeds/                     # ex222_*.aig VBS seed AIGs (43 files)
└── abc/
    ├── abc.rc                 # ABC script aliases (resyn2, resyn3, ...) — required
    └── abc                    # ABC binary — NOT in git, provide it (see abc/README.md)
```

## Setup

1. Provide the ABC binary at `abc/abc` — see [`abc/README.md`](abc/README.md).
   It is ~166MB and is intentionally not committed.
2. Ensure `default`/`coder` providers are configured in
   `~/.llm4ad/settings.yaml` (this config uses `relay2` and `relay2-coder`).

## Running

From the repository root:

```bash
llm4ad run examples/applications/abc_pareto_evolve/config_pareto.yaml
```

Outputs land under `runs_pareto/<project_name>/<run_id>/`. A persistent
`(seed, sequence)` cache is written to `abc_pareto_cache/` to avoid recomputing
optimize+cec across evaluations; delete it to force a clean recompute.

## The evolve-target contract

`synthesis_pareto/portfolio.py` is a standalone file with a single evolved
function between `# EVOLVE_START` / `# EVOLVE_END`:

```python
def build_portfolio() -> list[list[str]]:
    return [["resyn2", "resub -K 8", "dc2"], ["balance", "rewrite -z", ...], ...]
```

Each inner list is one optimization sequence; each element is exactly one ABC
command. The evaluator prepends `source abc.rc; read_aiger <seed>` and appends
`strash; print_stats; write_aiger`, so the function supplies **only** the
optimization steps.

### ABC command gotchas

- `resyn2`, `resyn3`, `compress2` are `abc.rc` aliases, not built-ins — the
  evaluator `source`s `abc.rc` so they work here.
- Technology-mapping commands (`if`, `lutpack`, `renode`, `speedup`, ...) are
  excluded from the allow-list: they leave the AIG domain and make area/level
  unmeasurable.
- GIA (`&`) sequences must start with `&get` and end with `&put`.
- Allow-lists forbid `;`, `|`, backticks, `$`, and newlines to prevent injection
  into ABC's `-c` script string.
