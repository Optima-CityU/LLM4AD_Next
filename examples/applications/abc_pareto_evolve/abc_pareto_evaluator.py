"""Seeded Pareto-frontier ABC evaluator for LLM4AD (IWLS 2026).

The ABC binary is FIXED. This evaluator scores an evolved *optimization
portfolio* (a list of ABC command sequences) by how far it pushes an existing
benchmark's AIGs below the contest's Virtual Best Solver (VBS) frontier.

Per evaluation (one benchmark):
1. Load the evolved portfolio from ``synthesis_pareto/portfolio.py`` (worktree).
2. Locate the benchmark's VBS seed AIGs (``<seeds_dir>/ex2NN_*.aig``) and build
   the FIXED VBS frontier {(level, area)} from them (cached to JSON).
3. Apply every sequence to every (sampled) seed via
   ``source abc.rc; read_aiger <seed>; <seq>; strash; print_stats; write_aiger``.
4. Verify each surviving candidate with ``cec <seed> <out>`` (fast combinational
   check; seeds are VBS-verified vs the truth table, so equivalence is
   transitive).
5. Pareto-prune the verified candidates and score the resulting frontier against
   the fixed VBS frontier using the official IWLS per-delay scoring.

Main objective: mean IWLS score (100 == matches VBS; >100 == beats VBS).

Uses the self-spawning subprocess pattern so a malformed portfolio that makes
ABC error/hang/crash cannot take down the orchestrator.
"""

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DEFAULT_ABC_BINARY = HERE / "abc" / "abc"
DEFAULT_ABC_RC = HERE / "abc" / "abc.rc"
DEFAULT_SEEDS_DIR = HERE / "iwls2026-ls-contest" / "VBS_ref" / "vbs"

RE_STAT = re.compile(r"and\s*=\s*(\d+)\s+lev\s*=\s*(\d+)")
FILENAME_RE = re.compile(r"^ex(2\d{2})_(\d{3})\.aig$")

# Allow-list of ABC commands a portfolio sequence may use. Covers classic
# AIG-restructuring commands, their abc.rc script aliases (resyn2/resyn3/...),
# and the GIA/`&` supercharged variants. Technology-mapping commands (if,
# lutpack, ...) are excluded: they leave the AIG domain and make area/level
# unmeasurable.
ALLOWED_COMMANDS = frozenset({
    # main-network AIG commands + abc.rc aliases
    "balance", "b", "rewrite", "rw", "refactor", "rf", "resub",
    "fraig", "dc2", "dch", "drw", "drf", "compress", "compress2",
    "resyn", "resyn2", "resyn2a", "resyn3", "strash", "st", "ifraig",
    # GIA / &-space commands
    "&get", "&put", "&st", "&b", "&balance", "&dch", "&dc2", "&fraig",
    "&resub", "&syn2", "&syn3", "&syn4", "&if",
})

# Characters that could smuggle extra commands into the assembled `-c` script.
# `&` is NOT forbidden (it is a legitimate ABC command prefix and we exec via
# argv, not a shell). `;` is forbidden because sequences are joined with `;`.
FORBIDDEN_CHARS = ";|`$\n"

# How the evaluator wraps each evolved sequence. `load` is the source-loading
# command: `read_aiger <seed>` (source_mode=vbs) or
# `read_truth -xf <truth>; strash` (source_mode=truth).
PROLOGUE = "source {rc}; {load};"
EPILOGUE = "strash; print_stats; write_aiger {out};"

FRONTIER_CACHE = "vbs_frontier_cache.json"
# Persistent (seed, sequence) result cache, shared across evaluations and
# worktrees. Lives next to the evaluator (stable path), NOT in the ephemeral
# worktree.
DEFAULT_CACHE_DIR = HERE / "abc_pareto_cache"


# ---------------------------------------------------------------------------
# Portfolio loading / validation
# ---------------------------------------------------------------------------


def _load_portfolio(worktree: Path, timeout: int = 30) -> tuple[list[list[str]] | None, str]:
    """Execute ``portfolio.py`` in a subprocess and return the validated portfolio.

    Args:
        worktree: Directory containing ``portfolio.py``.
        timeout: Subprocess timeout in seconds.

    Returns:
        (portfolio, error): list of sequences on success (error empty), or
        (None, message) on failure.
    """
    src = worktree / "portfolio.py"
    if not src.exists():
        return None, f"portfolio.py not found in {worktree}"
    try:
        proc = subprocess.run(
            [sys.executable, str(src)], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"portfolio.py timed out after {timeout}s"
    if proc.returncode != 0:
        return None, f"portfolio.py failed (rc={proc.returncode}): {proc.stderr[:300]}"
    try:
        payload = json.loads(proc.stdout.strip())
        portfolio = payload["portfolio"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None, f"portfolio.py produced invalid output: {proc.stdout[:200]}"
    return _validate_portfolio(portfolio)


def _validate_portfolio(portfolio: object) -> tuple[list[list[str]] | None, str]:
    """Validate the portfolio structure and every command against the allow-list.

    Args:
        portfolio: Candidate portfolio (expected: list of list of str).

    Returns:
        (portfolio, error): cleaned portfolio on success, or (None, message).
    """
    if not isinstance(portfolio, list) or not portfolio:
        return None, "Portfolio must be a non-empty list of command sequences"
    cleaned: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for seq in portfolio:
        if not isinstance(seq, list) or not seq:
            return None, f"Each sequence must be a non-empty list, got {seq!r}"
        clean_seq: list[str] = []
        for entry in seq:
            if not isinstance(entry, str):
                return None, f"Commands must be strings, got {type(entry).__name__}"
            cmd = entry.strip()
            if not cmd:
                continue
            if any(ch in cmd for ch in FORBIDDEN_CHARS):
                return None, f"Illegal character in command: {cmd!r}"
            name = cmd.split()[0]
            if name not in ALLOWED_COMMANDS:
                return None, f"Command not allowed: {name!r} (in {cmd!r})"
            clean_seq.append(cmd)
        # Drop empty and duplicate sequences so identical work is never re-run.
        if clean_seq and tuple(clean_seq) not in seen:
            seen.add(tuple(clean_seq))
            cleaned.append(clean_seq)
    if not cleaned:
        return None, "Portfolio contains no usable sequences"
    return cleaned, ""


# ---------------------------------------------------------------------------
# ABC helpers
# ---------------------------------------------------------------------------


def _run_abc(abc_bin: str, script: str, timeout: int) -> dict:
    """Run the fixed ABC binary with a `-c` script; return parsed output."""
    try:
        proc = subprocess.run(
            [abc_bin, "-c", script.strip()], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"ABC timed out after {timeout}s"}
    except FileNotFoundError:
        return {"error": f"ABC binary not found: {abc_bin}"}
    return {
        "output": (proc.stdout or "") + "\n" + (proc.stderr or ""),
        "returncode": proc.returncode,
    }


def _stats_from_aig(abc_bin: str, rc: str, aig: str, timeout: int) -> tuple[int, int] | None:
    """Return (level, area) of an AIG file via print_stats, or None."""
    r = _run_abc(abc_bin, f"source {rc}; read_aiger {aig}; print_stats;", timeout)
    if "error" in r:
        return None
    m = RE_STAT.search(r["output"])
    return (int(m.group(2)), int(m.group(1))) if m else None


def _optimize(abc_bin: str, rc: str, seq: list[str], load: str, out: str,
              timeout: int) -> tuple[int, int] | None:
    """Apply one sequence to one loaded source; return (level, area) of result.

    ``load`` is the source-loading command, e.g. ``read_aiger <seed>`` (VBS
    mode) or ``read_truth -xf <truth>; strash`` (truth mode).
    """
    body = "; ".join(seq)
    script = (PROLOGUE.format(rc=rc, load=load) + f" {body}; "
              + EPILOGUE.format(out=out))
    r = _run_abc(abc_bin, script, timeout)
    if "error" in r or r.get("returncode") != 0:
        return None
    m = RE_STAT.search(r["output"])
    return (int(m.group(2)), int(m.group(1))) if m else None


def _equivalent(abc_bin: str, seed: str, out: str, timeout: int) -> bool:
    """Combinational equivalence of the optimized AIG vs its seed (cec)."""
    r = _run_abc(abc_bin, f"cec {seed} {out};", timeout)
    if "error" in r:
        return False
    up = r["output"].upper()
    return "NOT EQUIVALENT" not in up and "EQUIVALENT" in up


def _equivalent_truth(abc_bin: str, rc: str, truth: str, out: str, timeout: int) -> bool:
    """Equivalence of the optimized AIG vs the truth table (official &cec -t).

    Used in truth mode, where there is no seed AIG to compare against. Mirrors
    the contest scorer's command: read_truth -xf; st; &get; &cec -t <aig>.
    """
    r = _run_abc(abc_bin, f"source {rc}; read_truth -xf {truth}; st; &get; &cec -t {out};", timeout)
    if "error" in r:
        return False
    up = r["output"].upper()
    return "NOT EQUIVALENT" not in up and "EQUIVALENT" in up


# ---------------------------------------------------------------------------
# Result cache: (abc, rc, seed, sequence) -> (level, area) + cec verdict.
# Deterministic inputs => results are reusable across evaluations. Each entry is
# its own file (atomic rename) so parallel eval subprocesses never corrupt a
# shared JSON. The result AIG is kept so cec can run lazily on frontier points.
# ---------------------------------------------------------------------------


def _cache_key(abc_bin: str, rc: str, source_id: str, seq: list[str]) -> str:
    """Stable 16-hex key for one (binary, rc, source, sequence) tuple.

    ``source_id`` uniquely identifies the loaded source: a VBS seed filename
    (e.g. ``ex220_063.aig``) or ``truth:<bench>`` in truth mode.
    """
    raw = f"{abc_bin}|{rc}|{source_id}|{';'.join(seq)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _atomic_write(path: Path, text: str) -> None:
    """Write text then atomically rename into place (parallel-safe)."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def _optimize_cached(abc_bin: str, rc: str, seq: list[str], source_id: str,
                     load: str, cache_dir: Path, timeout: int) -> tuple[dict | None, Path, Path]:
    """Return (meta, meta_path, aig_path) for one (source, sequence), using cache.

    meta is ``{"level": int, "area": int}`` on success (plus a cached ``equiv``
    once cec has run), or None if the sequence failed on this source. Failures
    are NOT cached (they may be transient timeouts). The result AIG is written
    into the cache dir so cec can run later without re-optimizing.
    """
    key = _cache_key(abc_bin, rc, source_id, seq)
    meta_path = cache_dir / f"{key}.json"
    aig_path = cache_dir / f"{key}.aig"
    if meta_path.exists() and aig_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if "level" in meta and "area" in meta:
                return meta, meta_path, aig_path
        except (json.JSONDecodeError, OSError):
            pass
    st = _optimize(abc_bin, rc, seq, load, str(aig_path), timeout)
    if st is None:
        return None, meta_path, aig_path
    meta = {"level": st[0], "area": st[1]}
    with contextlib.suppress(OSError):
        _atomic_write(meta_path, json.dumps(meta))
    return meta, meta_path, aig_path


def _verify_cached(verify_fn, aig_path: Path, meta_path: Path) -> bool:
    """Run ``verify_fn(aig_path)`` once and cache the boolean verdict in meta.

    ``verify_fn`` is a mode-specific equivalence check (vs the seed AIG in VBS
    mode, vs the truth table in truth mode).
    """
    meta: dict = {}
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        meta = {}
    if "equiv" in meta:
        return bool(meta["equiv"])
    ok = bool(verify_fn(str(aig_path)))
    meta["equiv"] = ok
    with contextlib.suppress(OSError):
        _atomic_write(meta_path, json.dumps(meta))
    return ok


# ---------------------------------------------------------------------------
# Pareto / scoring helpers
# ---------------------------------------------------------------------------


def _pareto(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Pareto-prune (level, area) points. Keeps non-dominated points only.

    (l1,a1) is dominated by (l2,a2) iff l2<=l1 and a2<=a1 and (l2<l1 or a2<a1).
    """
    out: list[tuple[int, int]] = []
    best_area = math.inf
    for (lev, area) in sorted(points, key=lambda t: (t[0], t[1])):
        if area < best_area:
            out.append((lev, area))
            best_area = area
    return out


def _best_area_le(points: list[tuple[int, int]], level_cap: int) -> int | None:
    """Smallest area among points with level <= level_cap."""
    best = None
    for (lev, area) in points:
        if lev <= level_cap and (best is None or area < best):
            best = area
    return best


def _iwls_scores(cand: list[tuple[int, int]], vbs: list[tuple[int, int]]) -> list[float]:
    """Per-VBS-delay IWLS scores of the candidate frontier vs the VBS frontier.

    score(L) = floor(100 * VBS_area(L) / cand_best_area(level<=L)), 0 if the
    candidate has no point with level <= L. Mirrors score_iwls2026.py.
    """
    vbs_levels = sorted({lv for (lv, _a) in vbs})
    scores: list[float] = []
    for lvl in vbs_levels:
        vbs_a = _best_area_le(vbs, lvl)
        cand_a = _best_area_le(cand, lvl)
        if cand_a is None or cand_a <= 0:
            scores.append(0.0)
        else:
            scores.append(math.floor(100.0 * vbs_a / cand_a))
    return scores


def _num_dominating(cand: list[tuple[int, int]], vbs: list[tuple[int, int]]) -> int:
    """Count candidate points not dominated by any VBS point (they beat VBS)."""
    n = 0
    for (cl, ca) in cand:
        if not any(vl <= cl and va <= ca for (vl, va) in vbs):
            n += 1
    return n


# ---------------------------------------------------------------------------
# VBS frontier (fixed, cached)
# ---------------------------------------------------------------------------


def _vbs_frontier(abc_bin: str, rc: str, all_seeds: list[Path], bench: str,
                  cache_dir: Path, timeout: int) -> list[tuple[int, int]]:
    """Build (and cache) the fixed VBS frontier from ALL of a benchmark's seeds.

    The scoring baseline must be the true VBS frontier, so it is built from every
    VBS member, independent of how many seeds optimization later samples.
    """
    cache = cache_dir / FRONTIER_CACHE
    data: dict = {}
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    if bench in data:
        return [tuple(p) for p in data[bench]]
    pts: list[tuple[int, int]] = []
    for s in all_seeds:
        st = _stats_from_aig(abc_bin, rc, str(s), timeout)
        if st is not None:
            pts.append(st)
    frontier = _pareto(pts)
    data[bench] = [list(p) for p in frontier]
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, indent=2))
    except OSError:
        pass
    return frontier


def _sample_seeds(all_seeds: list[Path], max_seeds: int) -> list[Path]:
    """Sample seeds evenly across level values (filenames encode level order)."""
    if max_seeds <= 0 or len(all_seeds) <= max_seeds:
        return all_seeds
    step = len(all_seeds) / max_seeds
    idx = sorted({min(len(all_seeds) - 1, int(i * step)) for i in range(max_seeds)})
    return [all_seeds[i] for i in idx]


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


@BaseEvaluator.register("abc_pareto_evaluator")
class AbcParetoEvaluator(BaseEvaluator):
    """Score an evolved optimization portfolio against a benchmark's VBS frontier."""

    def __init__(self, config: object = None):
        """Initialize from optional config extras (abc_binary, abc_rc, seeds_dir, max_seeds)."""
        extra = getattr(config, "model_extra", None) or {}
        self._abc = str(Path(extra.get("abc_binary", DEFAULT_ABC_BINARY)).expanduser())
        self._rc = str(Path(extra.get("abc_rc", DEFAULT_ABC_RC)).expanduser())
        self._seeds_dir = Path(extra.get("seeds_dir", DEFAULT_SEEDS_DIR)).expanduser()
        self._max_seeds = int(extra.get("max_seeds", 16))
        # Cap for a single ABC invocation; the overall eval budget is cfg.timeout.
        self._per_call_timeout = int(extra.get("per_call_timeout", 60))
        # Persistent (source, sequence) result cache shared across evaluations.
        self._cache_dir = str(Path(extra.get("cache_dir", DEFAULT_CACHE_DIR)).expanduser())
        # Optimization starting point: "vbs" (read_aiger VBS seeds, exp 1) or
        # "truth" (read_truth the truth table, exp 2). Baseline is VBS either way.
        self._source_mode = str(extra.get("source_mode", "vbs"))
        # Scoring mode:
        #   "extend_vbs" (exp 1) — candidate ∪ VBS scored vs VBS. Baseline 100 =
        #       kept VBS; >100 = found a point that extends the frontier. Best for
        #       incremental discovery on top of VBS.
        #   "vs_vbs" (exp 2) — candidate ALONE scored vs VBS. <100 = did not reach
        #       VBS; >100 = beat it standalone. Best for from-scratch comparison.
        # Default tied to source_mode: truth => vs_vbs, vbs => extend_vbs.
        self._score_mode = str(extra.get("score_mode") or (
            "vs_vbs" if self._source_mode == "truth" else "extend_vbs"))
        self._metrics = [
            Metric(name="iwls_score", type=MetricType.MAXIMIZE, weight=1.0,
                   description="Mean IWLS per-delay score vs VBS (100==parity, >100 beats VBS)"),
            Metric(name="num_dominating", type=MetricType.MAXIMIZE, weight=0.0,
                   description="Candidate points not dominated by the VBS frontier"),
            Metric(name="best_area_saved", type=MetricType.MAXIMIZE, weight=0.0,
                   description="Max AND-nodes saved vs VBS at an equal-or-lower level"),
            Metric(name="min_area", type=MetricType.MINIMIZE, weight=0.0,
                   description="Smallest area on the candidate frontier"),
            Metric(name="portfolio_valid", type=MetricType.MAXIMIZE, weight=0.0,
                   description="Whether the portfolio loaded and validated (1/0)"),
        ]

    @property
    def name(self) -> str:
        """Return the evaluator's registry name."""
        return "abc_pareto_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        """Return the list of supported metrics."""
        return self._metrics

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Evaluate the evolved portfolio on one benchmark (via isolated subprocess)."""
        start = time.time()
        try:
            worktree = Path(cfg.project_root)
            truth = Path(cfg.data_path)
            # Benchmark id is the truth-file stem, e.g. "ex220" from "ex220.truth".
            if not re.match(r"^ex2\d{2}$", truth.stem):
                return self._fail(start, f"Cannot derive benchmark id from {truth.name}")
            bench = truth.stem

            payload = json.dumps({
                "worktree": str(worktree),
                "bench": bench,
                "truth_path": str(truth),
                "source_mode": self._source_mode,
                "score_mode": self._score_mode,
                "abc": self._abc,
                "rc": self._rc,
                "seeds_dir": str(self._seeds_dir),
                "max_seeds": self._max_seeds,
                "per_call_timeout": self._per_call_timeout,
                "cache_dir": self._cache_dir,
                "timeout": cfg.timeout,
            })
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(input=payload.encode()), timeout=cfg.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return self._fail(start, f"Evaluation timed out after {cfg.timeout}s",
                                  duration_ms=cfg.timeout * 1000)

            dur = (time.time() - start) * 1000
            if proc.returncode != 0:
                return self._fail(start, f"Subprocess failed (rc={proc.returncode}): "
                                  f"{err_b.decode('utf-8', 'replace')[:400]}", duration_ms=dur)
            try:
                result = json.loads(out_b.decode("utf-8", "replace").strip())
            except json.JSONDecodeError:
                return self._fail(start, f"Invalid JSON: {out_b[:200]!r}", duration_ms=dur)

            if "error" in result:
                return EvaluationResult(
                    score=0.0, metrics={"portfolio_valid": float(result.get("portfolio_valid", 0))},
                    success=False, error_message=result["error"], duration_ms=dur)

            score = float(result.get("iwls_score", 0.0))
            return EvaluationResult(
                score=score,
                metrics={
                    "iwls_score": score,
                    "num_dominating": float(result.get("num_dominating", 0)),
                    "best_area_saved": float(result.get("best_area_saved", 0)),
                    "min_area": float(result.get("min_area", 0)),
                    "portfolio_valid": float(result.get("portfolio_valid", 0)),
                },
                success=True, duration_ms=dur,
                metadata={
                    "bench": bench,
                    "candidate_frontier": result.get("candidate_frontier", []),
                    "optimized_frontier": result.get("optimized_frontier", []),
                    "vbs_frontier": result.get("vbs_frontier", []),
                },
            )
        except Exception as e:  # noqa: BLE001 - report any failure as score 0
            return self._fail(start, f"Evaluation error: {e}")

    @staticmethod
    def _fail(start: float, msg: str, duration_ms: float | None = None) -> EvaluationResult:
        """Build a failed EvaluationResult with score 0."""
        return EvaluationResult(
            score=0.0, metrics={}, success=False, error_message=msg,
            duration_ms=duration_ms if duration_ms is not None else (time.time() - start) * 1000)

    # -----------------------------------------------------------------------
    # Subprocess side
    # -----------------------------------------------------------------------

    @staticmethod
    def _run(data: dict) -> dict:
        """Run one benchmark evaluation (subprocess side)."""
        bench = data["bench"]
        abc_bin, rc = data["abc"], data["rc"]
        seeds_dir = Path(data["seeds_dir"])
        max_seeds = int(data["max_seeds"])
        per_call = int(data.get("per_call_timeout", 60))
        cache_dir = Path(data.get("cache_dir", DEFAULT_CACHE_DIR))
        source_mode = data.get("source_mode", "vbs")
        # Defensive default: if score_mode is absent (e.g. an older caller/payload),
        # tie it to source_mode. truth => vs_vbs (never merge VBS into a from-scratch
        # candidate, which would spuriously read 100); vbs => extend_vbs.
        score_mode = data.get("score_mode") or (
            "vs_vbs" if source_mode == "truth" else "extend_vbs")
        truth_path = data.get("truth_path", "")

        if not Path(abc_bin).exists():
            return {"error": f"ABC binary not found: {abc_bin}", "portfolio_valid": 0}

        portfolio, err = _load_portfolio(Path(data["worktree"]), timeout=per_call)
        if portfolio is None:
            return {"error": f"Portfolio error: {err}", "portfolio_valid": 0}

        # The scoring baseline is ALWAYS the true VBS frontier built from ALL of
        # this benchmark's VBS members — identical in both source modes, so the
        # two experiments are directly comparable.
        all_seeds = sorted(seeds_dir.glob(f"{bench}_*.aig"))
        if not all_seeds:
            return {"error": f"No VBS AIGs for {bench} in {seeds_dir}", "portfolio_valid": 1}

        cache_dir.mkdir(parents=True, exist_ok=True)
        vbs = _vbs_frontier(abc_bin, rc, all_seeds, bench, seeds_dir, per_call)
        if not vbs:
            return {"error": f"Could not build VBS frontier for {bench}", "portfolio_valid": 1}

        # Build the optimization sources. Each source = (id, load_cmd, verify_fn).
        # - vbs mode:   one source per (evenly sampled) VBS seed; cec vs the seed.
        # - truth mode: a single source loading the truth table; cec vs the truth.
        sources: list[tuple[str, str, object]] = []
        if source_mode == "truth":
            if not truth_path or not Path(truth_path).exists():
                return {"error": f"truth mode needs truth_path; got {truth_path!r}",
                        "portfolio_valid": 1}
            sources.append((
                f"truth:{bench}",
                f"read_truth -xf {truth_path}; strash",
                lambda aig: _equivalent_truth(abc_bin, rc, truth_path, aig, per_call),
            ))
        else:  # "vbs"
            for seed in _sample_seeds(all_seeds, max_seeds):
                s = str(seed)
                sources.append((
                    Path(s).name,
                    f"read_aiger {s}",
                    lambda aig, seed_path=s: _equivalent(abc_bin, seed_path, aig, per_call),
                ))

        # Run every (source, sequence) through the persistent cache so identical
        # work is never recomputed. cec is NOT run here — only on the pruned
        # frontier survivors below. rep maps each distinct (level, area) point to
        # one representative {verify, aig, meta}.
        rep: dict[tuple[int, int], dict] = {}
        for (source_id, load_cmd, verify_fn) in sources:
            for seq in portfolio:
                meta, meta_path, aig_path = _optimize_cached(
                    abc_bin, rc, seq, source_id, load_cmd, cache_dir, per_call)
                if meta is None:
                    continue
                pt = (meta["level"], meta["area"])
                rep.setdefault(pt, {"verify": verify_fn, "aig": aig_path, "meta": meta_path})

        if not rep:
            return {"error": f"No valid candidates produced for {bench}",
                    "portfolio_valid": 1, "num_dominating": 0}

        # Pareto-prune first, then cec only the frontier points. If a frontier
        # point fails cec (functionally wrong), drop it and re-prune so points it
        # had masked can re-enter. cec verdicts are cached.
        pool = list(rep.keys())
        checked: dict[tuple[int, int], bool] = {}
        cand: list[tuple[int, int]] = []
        while pool:
            front = _pareto(pool)
            bad = []
            for pt in front:
                if pt not in checked:
                    c = rep[pt]
                    checked[pt] = _verify_cached(c["verify"], c["aig"], c["meta"])
                if not checked[pt]:
                    bad.append(pt)
            if not bad:
                cand = front
                break
            bad_set = set(bad)
            pool = [pt for pt in pool if pt not in bad_set]

        if not cand:
            return {"error": f"No verified candidates for {bench}",
                    "portfolio_valid": 1, "num_dominating": 0}

        # The scored frontier depends on score_mode (the fixed original contest VBS
        # is never changed either way):
        #   extend_vbs (exp 1) — score candidate ∪ VBS vs VBS. Baseline 100 = kept
        #       VBS; > 100 = an evolved point extends the frontier. Rewards
        #       incremental discovery on top of VBS.
        #   vs_vbs    (exp 2) — score the candidate ALONE vs VBS. < 100 = did not
        #       reach VBS; > 100 = beat it standalone. Fair from-scratch comparison.
        scored = _pareto(cand + vbs) if score_mode == "extend_vbs" else cand
        scores = _iwls_scores(scored, vbs)
        mean_score = sum(scores) / len(scores) if scores else 0.0

        # num_dominating / best_area_saved reflect only the NEW optimized points
        # (VBS points can never beat themselves), so they isolate the gain the
        # evolved portfolio actually contributed on top of VBS.
        best_saved = 0
        for (cl, ca) in cand:
            vbs_a = _best_area_le(vbs, cl)
            if vbs_a is not None and vbs_a - ca > best_saved:
                best_saved = vbs_a - ca

        return {
            "iwls_score": mean_score,
            "num_dominating": _num_dominating(cand, vbs),
            "best_area_saved": best_saved,
            "min_area": min(a for (_l, a) in scored),
            "portfolio_valid": 1,
            "candidate_frontier": [list(p) for p in scored],
            "optimized_frontier": [list(p) for p in cand],
            "vbs_frontier": [list(p) for p in vbs],
        }


def _subprocess_main() -> None:
    """Entry point for the isolated evaluation subprocess (reads JSON on stdin)."""
    raw = sys.stdin.read()
    if not raw.strip():
        print("FATAL: empty stdin", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        result = AbcParetoEvaluator._run(data)
    except Exception as e:  # noqa: BLE001
        result = {"error": str(e)}
    print(json.dumps(result))


if __name__ == "__main__":
    _subprocess_main()
