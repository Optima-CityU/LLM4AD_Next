# AlphaEvolve Mathematics Benchmark Suite

This directory provides 11 mathematical optimization cases adapted from LoongFlow examples to LLM4AD Next. Each case has its own directory containing the task configuration, initial algorithm, and evaluator adapter. Model-visible prompts contain only the problem definition, output contract, and constraints; they do not contain published targets or external best-solution code.

When running a YAML file directly with the CLI, use this suite directory as the working directory so its case-relative paths and shared process runtime resolve consistently.

Evaluators validate candidate outputs directly and retain the original mathematical metric. Internal fitness follows each evaluator's documented monotone scoring rule; most existing cases use a target-normalized ratio. Reported metrics retain the raw mathematical value. Compare raw objectives rather than fitness values across different frameworks.

## Comparison results

The table retains historical LLM4AD Next results and their original published comparisons. These runs have not been reevaluated with the v3 contract described below, so the gaps are historical records rather than audited v3 comparison results. Positive gaps indicate an improvement in the original comparison, while negative gaps show the remaining difference. Each result includes the evolved implementation, all active excellent-experience cards retained by the memory system, and a machine-readable objective record.

| Case | Direction | AlphaEvolve | LoongFlow | LLM4AD Next | Gap to best published | Artifacts |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 26 circles in a unit square | Higher | `2.6358627564136983` | `2.6359829624734026` | **`2.635983083325037`** | `+1.208516344e-7` | [code](circle_packing/results/best/solve.py) · [experiences](circle_packing/results/best/experiences/README.md) · [result](circle_packing/results/best/result.json) |
| 21 circles in a perimeter-four rectangle | Higher | `2.3658321334167627` | `2.365832229500823` | **`2.365832375700835`** | `+1.46200012e-7` | [code](circle_rectangle/results/best/solve.py) · [experiences](circle_rectangle/results/best/experiences/README.md) · [result](circle_rectangle/results/best/result.json) |
| 11 unit hexagons in a regular hexagon | Lower | `3.930092` | `3.928906855463712` | **`3.92468841680981`** | `+0.004218438653902` | [code](hexagon_packing/results/best/solve.py) · [experiences](hexagon_packing/results/best/experiences/README.md) · [result](hexagon_packing/results/best/result.json) |
| 16-point maximum/minimum distance ratio | Lower | `12.88926611203463` | `12.889243547212832` | **`12.889229907694045`** | `+1.3639518787e-5` | [code](max_min_distance_ratio/results/best/solve.py) · [experiences](max_min_distance_ratio/results/best/experiences/README.md) · [result](max_min_distance_ratio/results/best/result.json) |
| Uncertainty inequality | Lower | `0.35209910442252773` | `0.352099104421844` | **`0.35209910441916187`** | `+2.68213e-12` | [code](uncertainty_inequality/results/best/solve.py) · [experiences](uncertainty_inequality/results/best/experiences/README.md) · [result](uncertainty_inequality/results/best/result.json) |
| Second autocorrelation inequality | Higher | `0.8962799441554083` | `0.9027021077220739` | **`0.9053043552878318`** | `+0.0026022475657579` | [code](second_autocorrelation/results/best/solve.py) · [experiences](second_autocorrelation/results/best/experiences/README.md) · [result](second_autocorrelation/results/best/result.json) |
| First autocorrelation inequality | Lower | `1.5052939684401607` | `1.509527314861778` | **`1.507459811737381`** | `-0.0021658432972203` | [code](first_autocorrelation/results/best/solve.py) · [experiences](first_autocorrelation/results/best/experiences/README.md) · [result](first_autocorrelation/results/best/result.json) |
| Minimum overlap | Lower | `0.380924` | `0.3809137564083654` | **`0.38092504473534605`** | `-1.128832698065e-5` | [code](minimum_overlap/results/best/solve.py) · [experiences](minimum_overlap/results/best/experiences/README.md) · [result](minimum_overlap/results/best/result.json) |
| Heilbronn problem in an equilateral triangle | Higher | `0.036529889880030156` | `0.0365298898793351` | **`0.0365298881927707`** | `-1.687259456e-9` | [code](heilbronn_triangle/results/best/solve.py) · [experiences](heilbronn_triangle/results/best/experiences/README.md) · [result](heilbronn_triangle/results/best/result.json) |

Comparison tables and retained result artifacts are excluded from task templates and are never passed to the model.

## Experiment setup

| Item | Setting |
| --- | --- |
| Result source | Historical production evolution runs |
| Search | Diverse Island GA with long-term task memory |
| Runtime | Containerized LLM4AD Next environment |
| Evaluation | Case-local evaluator and original benchmark objective |
| Reporting | Raw benchmark objective; no target normalization in the comparison table |

## Objective provenance

| Local case | LoongFlow source | Objective |
| --- | --- | --- |
| `heilbronn_square` | [`heilbronn_problem_for_convex_regions`](https://github.com/baidu-baige/LoongFlow/tree/main/agents/math_agent/examples/heilbronn_problem_for_convex_regions) | Maximize minimum triangle area divided by convex-hull area for 13 points. The square is only a coordinate convention; the directory, registry and result identifiers retain their legacy names. |
| `sums_differences` | [`sums_and_differences_problems_1/eval_program.py`](https://github.com/baidu-baige/LoongFlow/blob/main/agents/math_agent/examples/sums_and_differences_problems_1/eval_program.py) | Maximize `log(S/n) / log(D/n) + (1-1/n)/100`, where `n=|A|`, `S=|A+A|` and `D=|A-A|`. Both the candidate helper and evaluator retain this upstream formula. |

LoongFlow also provides [`sums_and_differences_problems_2/eval_program.py`](https://github.com/baidu-baige/LoongFlow/blob/main/agents/math_agent/examples/sums_and_differences_problems_2/eval_program.py), whose `compute_lower_bound` uses `1 + log(D/S) / log(2*max(U)+1)`. This is the C6 construction bound in [AlphaEvolve Appendix B.6](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf#page=41), for a nonnegative integer set containing zero. It is a separate objective and is not implemented by the local `sums_differences` case. Targets and results from the two objectives are not interchangeable. The local target `1.1319033750264975` is retained from LoongFlow problem 1's evaluator, not presented as a C6 result.

The upstream problem 1 task text asks for a large difference set and a small sumset, although its scorer rewards the opposite direction at fixed cardinality. Its Heilbronn task text describes absolute area in a fixed square, although its returned score is normalized by convex-hull area. The local statements correct these inconsistencies while retaining the scoring objectives; the Heilbronn objective agrees with [AlphaEvolve Appendix B.10](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf#page=42).

## Audited task and evaluation contract (v3)

The task statements now match the actual candidate entry points, JSON wrappers, and raw objectives documented above. The uncertainty case supports the variable-length Hermite vector actually accepted by its verifier.

Numerical verification repairs cover large-coordinate cancellation, circle and hexagon contacts, scale-invariant distance ratios, recomputed autocorrelation values, and exact Hermite sign-changing roots. Valid raw objective formulas remain unchanged. Use the same repaired evaluator and audited statements for all new comparisons; historical results should be checked again with the same contract.

All 12 launch configurations now allow at most 20 population evolution steps, after generation-zero initialization. Patience 21 prevents plateau stopping within these 20 steps. With three islands, five members and one elite per island this is nominally 15 initial candidates plus 240 children, or 255 total; failures and duplicate filtering can reduce completed evaluations. Framework-native outer iterations are a different budget unit, so also report actual evaluations, requests and tokens.

Install numerical evaluation dependencies with `uv sync --extra eval` before using these cases. For the SCIP-based `circle_packing/solver_config.yaml` case, use `uv sync --extra eval --extra scip` to install PySCIPOpt as well. Task statements do not prescribe a code transport format: the native coder selects its block-replacement or complete-file contract from the actual candidate files.

V3 adds bounded contact/containment roundoff tolerances (circle distances and full rectangle perimeter 1e-12; square coordinates and triangle half-space residuals 1e-12; existing hexagon contact tolerance 1e-9). It also fixes huge-angle hexagon directions and mixed-scale distance differences. Nonfinite data, negative radii and genuine violations remain invalid. All providers and coders now use temperature 1.0 and a 600-second API timeout. Historical search runs need the same revised contract before new comparisons.
