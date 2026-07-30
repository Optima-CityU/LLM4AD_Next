#!/usr/bin/env python3
"""Evolved ABC optimization *portfolio* for LLM4AD (seeded, Pareto-frontier).

Unlike the recipe task (``synthesis_recipe/recipe.py``) which synthesizes one
AIG from a truth table, this task starts from an *existing* AIG (a VBS member
of the target benchmark) and applies ABC to push it below the VBS frontier.

What evolves is ``build_portfolio()``: a LIST of command sequences. The
evaluator applies every sequence to every seed AIG via::

    source abc.rc; read_aiger <seed>; <one sequence>; strash; print_stats; write_aiger <out>

Each (seed, sequence) yields one (level, area) point. The union of all points
is Pareto-pruned into a candidate frontier and scored against the FIXED VBS
frontier of the benchmark (IWLS per-delay scoring). A mean score above 100
means the portfolio beats VBS on average; any point not dominated by VBS
extends the frontier.

Because sequences start from a fresh reload of the seed, they explore different
delay/area trade-offs: level-preserving area passes stay near the seed's delay,
while aggressive area passes (dc2, GIA &dch) trade delay for smaller area.

Run standalone, this prints the portfolio as JSON so the evaluator can fetch it
from an isolated subprocess::

    {"portfolio": [["resyn2", "resyn2"], ["dc2", "dc2"], ...]}
"""

import json


# EVOLVE_START
def build_portfolio() -> list[list[str]]:
    """Return a portfolio of ABC command sequences to apply to each seed AIG.

    Each inner list is one optimization sequence; each element is exactly one
    ABC command, optionally with parameters (e.g. ``"resub -K 8"`` or
    ``"&dch -f"``). The evaluator prepends ``source abc.rc`` + ``read_aiger``
    and appends ``strash`` + ``print_stats`` + ``write_aiger``, so each
    sequence only supplies the optimization steps.

    Allowed commands (see the evaluator's ALLOWED_COMMANDS) cover AIG
    restructuring (balance, rewrite, refactor, resub, dc2, dch, fraig, resyn2,
    resyn3, ...) and the GIA/`&` supercharged variants (&get, &put, &dch,
    &dc2, &syn2/3/4, &b, &fraig, &resub, &st). GIA commands operate in the
    `&`-space: begin with ``&get`` (or ``&get -n``) and end with ``&put`` to
    return the result to the main network before the epilogue strashes it.

    Diversity is the goal: mix level-preserving area passes (which keep the
    seed's delay and beat VBS at that level) with aggressive area passes (which
    trade delay for much smaller area). More sequences => denser candidate
    frontier => more chances to place a point VBS does not dominate.

    SEQUENCE LENGTH MATTERS: short sequences (2-4 commands) converge too fast and
    under-explore. Prefer LONGER, VARIED sequences — typically 8-16 commands that
    interleave several distinct operators (balance, rewrite, rewrite -z, refactor,
    refactor -z, resub -K N, dc2, dch, fraig) and mix in GIA passes. Repeating a
    single alias (e.g. resyn2 x3) is weak; combining many different restructuring
    moves explores more of the area/delay space. Aim for 8-12 diverse sequences.

    Returns:
        list[list[str]]: One or more ABC command sequences.
    """
    return [
        # Level-preserving, area-focused: long interleaving of distinct moves,
        # keeps depth near the seed so it can beat VBS at the seed's own level.
        ["balance", "rewrite", "refactor", "balance", "rewrite -z",
         "refactor -z", "rewrite -z", "balance", "resub -K 8", "rewrite"],
        ["resyn2", "resub -K 12", "rewrite -z", "refactor -z", "balance",
         "resyn2", "resub -K 16", "balance"],
        ["resyn2", "resyn3", "resub -K 8", "rewrite -z", "resyn2", "refactor -z",
         "balance", "rewrite -z", "resyn3"],
        # Balanced deep restructuring.
        ["dc2", "resub -K 10", "rewrite -z", "dc2", "refactor -z", "balance",
         "rewrite -z", "dc2", "resub -K 12"],
        # Aggressive area reduction (trades delay for much smaller area).
        ["dc2", "dc2", "rewrite -z", "refactor -z", "dc2", "resub -K 16",
         "rewrite -z", "dc2", "refactor -z", "dc2"],
        # GIA-based deep optimization interleaved with main-network cleanup.
        ["&get -n", "&dch -f", "&syn2", "&put", "dc2", "rewrite -z", "refactor -z",
         "balance", "resub -K 12"],
        ["&get -n", "&syn2", "&dch -f", "&syn3", "&resub", "&put", "dc2",
         "rewrite -z", "balance"],
        ["resyn2", "&get -n", "&dch -f", "&syn3", "&put", "resub -K 12",
         "rewrite -z", "refactor -z", "balance", "rewrite -z"],
    ]
# EVOLVE_END


def main() -> None:
    """Print the portfolio as JSON for the evaluator subprocess."""
    print(json.dumps({"portfolio": build_portfolio()}))


if __name__ == "__main__":
    main()
