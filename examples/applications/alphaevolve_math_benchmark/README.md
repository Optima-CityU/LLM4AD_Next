# AlphaEvolve Mathematics Benchmark Suite

This directory provides 11 mathematical optimization cases adapted to LLM4AD Next. Each case has its own directory containing the task configuration, initial algorithm, and evaluator adapter. Model-visible prompts contain only the problem definition, output contract, and constraints; they do not contain published targets or external best-solution code.

When running a YAML file directly with the CLI, use this suite directory as the working directory so its case-relative paths and shared process runtime resolve consistently.

Evaluators validate candidate outputs directly and retain the original mathematical metric. For minimization problems, LLM4AD Next uses the negated raw metric internally so that a larger evolution score remains better; reported metrics and result files keep the original value.

## Comparison results

The table lists the suite's current competitive LLM4AD Next results. The final comparison uses the better published value from AlphaEvolve and LoongFlow for each objective. Positive gaps indicate an improvement, while negative gaps show the remaining difference. Each result includes the evolved implementation, all active excellent-experience cards retained by the memory system, and a machine-readable objective record.

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
