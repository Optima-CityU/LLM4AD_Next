# Python Multimodal Design Task Template

This template provides a reference for creating **multimodal** Python design tasks in LLM4AD. It extends the base template (`task_template_python/`) with behavior visualization support — the evaluator generates images showing algorithm behavior, and the LLM uses these images to make visually-grounded improvements.

**When to use this template instead of the base template:**
- Your task produces visual output the LLM can diagnose (tours, trajectories, plots)
- You want the LLM to "see" algorithm behavior, not just read metrics
- You need deferred rendering (raw data storage with on-demand visualization)

## Directory Structure

```
my_task_python_multimodal/
  config.yaml      # Task configuration with multimodal section
  my_evaluator.py             # Evaluator + renderer + visualization helpers (all-in-one)
  my_algorithm/               # Algorithm code directory (version-controlled)
    my_function.py            # Algorithm file with EVOLVE markers
  data/                       # Evaluation data
    sample/
      instance_001.json
  generate_data.py            # Data generation script
  test_evaluator.py           # Tests both rendered and raw modes
  debug_run.py                # Full pipeline entry point
  runs/                       # (auto-created) Run outputs
```

### What's New vs. Base Template

| File | Base Template | Multimodal Template |
|------|--------------|---------------------|
| `my_evaluator.py` | Returns score + metrics | Also includes renderer + visualization helpers + `BehaviorData` |
| `config.yaml` | No `multimodal:` section | Adds `multimodal:` config + multimodal samplers |
| `test_evaluator.py` | Tests metrics only | Also tests behavior data + deferred rendering |

## Quick Start

1. **Copy this directory** and rename it for your task
2. **Search for `TODO`** in all files — each marks a location you must customize
3. **Generate test data**: `uv run python generate_data.py`
4. **Test the evaluator**: `uv run python test_evaluator.py`
5. **Run full pipeline**: `uv run python debug_run.py` (requires LLM provider)

## Multimodal Architecture Overview

```
                    Evaluator
                       |
            +----------+----------+
            |                     |
        Score/Metrics       BehaviorData
                          /             \
               observation text    visualizations[]
               (LLM-readable)      (images for LLM)
                                        |
                    +---------+---------+---------+
                    |         |                   |
                "rendered"  "raw"              "none"
                (base64     (compact data      (no data
                 PNG)        + renderer name)    saved)
```

### How Behavior Images Reach the LLM

1. **Evaluator** runs the algorithm and generates `BehaviorData` (including renderer for raw mode)
2. **Dispatcher** aggregates behavior from multiple instances
3. **Multimodal sampler** (mutation/crossover) extracts images from `BehaviorData`
4. **Prompt builder** embeds images + observation text into the LLM prompt
5. **LLM** analyzes the images and proposes targeted improvements

### Three Behavior Storage Modes

| Mode | What's Saved | Disk Size | Display Speed | Best For |
|------|-------------|-----------|---------------|----------|
| `"rendered"` | Base64 PNG image | ~40-100 KB | Instant | Debugging, small runs |
| `"raw"` | Compact data + renderer name | ~1-5 KB | Render on demand | Production, long runs |
| `"none"` | Nothing | 0 | Must re-run | Disk-constrained setups |

Set the mode in your YAML config:
```yaml
multimodal:
  behavior_storage: "rendered"   # or "raw" or "none"
```

## BehaviorData Generation (Step-by-Step)

The key multimodal addition is **Step 6** in the evaluator's `evaluate()` method (after the standard Steps 1-5 from the base template). The evaluator file (`my_evaluator.py`) contains all three components in one file:
1. **Visualization helpers** — `_render_result_image()` and `_build_observation_text()`
2. **Deferred renderer** — `MyTaskContourRenderer` class registered as `"my_task_contour"`
3. **Evaluator class** — `MyTaskEvaluatorMultimodal` with Steps 1-6

```python
# Step 6: Build BehaviorData based on behavior_storage mode

behavior = None
behavior_storage = cfg.behavior_storage
obs_text = _build_observation_text(...)

if behavior_storage == "rendered":
    image_b64 = _render_result_image(...)
    behavior = BehaviorData(
        observation=obs_text,
        visualizations=[BehaviorVisualization(
            label="My Task",
            media_type="image/png",
            data_base64=image_b64,          # Pre-rendered image
        )],
        instance_id=str(data_path),
    )

elif behavior_storage == "raw":
    behavior = BehaviorData(
        observation=obs_text,
        visualizations=[BehaviorVisualization(
            label="My Task",
            media_type="image/png",
            raw_data={...},                  # Compact structured data
            renderer="my_task_contour",      # Must match registered name
        )],
        instance_id=str(data_path),
    )

# behavior_storage == "none" → behavior stays None

return EvaluationResult(
    score=score, metrics=metrics, success=True,
    behavior=behavior,                       # ← multimodal addition
)
```

## Custom Renderer Guide

A renderer converts compact raw data back into an image on demand. You need one when using `behavior_storage: "raw"`. In this template (and the TSP/LunarLander examples), the renderer lives in the same file as the evaluator for simplicity.

### How to Create a Renderer

1. **Inherit from `BaseRenderer`** and register with a unique name (inside `my_evaluator.py`):
   ```python
   from llm4ad.evaluator.renderer import BaseRenderer

   @BaseRenderer.register("my_task_contour")
   class MyTaskContourRenderer(BaseRenderer):
       def render(self, raw_data, **kwargs):
           # Convert raw_data dict -> base64 PNG string
           ...
           return base64_string
   ```

2. **Use matching names** — the `renderer` field in `BehaviorVisualization` must match the `@BaseRenderer.register("...")` name exactly.

### raw_data Schema Contract

Document your raw_data schema in the renderer's docstring. This template uses:
```python
{
    "grid": list[list[float]],        # 2D grid of function values
    "x_range": [float, float],
    "y_range": [float, float],
    "grid_size": [int, int],
    "trajectory": list[list[float]],  # [[x, y, f_val], ...]
    "best_point": [float, float],
    "best_value": float
}
```

### When Rendering Happens

Rendering is **lazy** — `render_visualization(viz)` is called only when an image is actually needed (e.g., building an LLM prompt or displaying in the frontend). The result is cached in `viz.data_base64`.

## YAML Configuration Changes

Compared to the base template, the multimodal YAML adds two sections:

### 1. `multimodal:` Section (NEW)
```yaml
multimodal:
  enabled: true
  max_images_per_prompt: 3
  image_max_size_kb: 512
  include_observation_text: true
  behavior_storage: "rendered"
```

### 2. Multimodal Samplers in `planner:` (CHANGED)
```yaml
# Base template uses:
planner:
  samplers:
    - name: "init_sampler"
    - name: "mutation_sampler"
    - name: "crossover_sampler"

# Multimodal template uses:
planner:
  samplers:
    - name: "init_sampler"
    - name: "multimodal_mutation_sampler"      # ← includes images
    - name: "multimodal_crossover_sampler"     # ← includes images
```

## Observation Text Best Practices

Observation text is a compact, pipe-separated summary of key metrics. The LLM reads this alongside the behavior image.

**Pattern:** `{instance} | {primary}={value} | {secondary}={value} | ...`

**Examples from existing tasks:**
```
# TSP
instance_003 | N=20 | Length=342.15 | Time=5.2ms | Valid=Yes

# LunarLander
Seed=6 | Reward=150.9 | Fuel=45.2 | Steps=180 | Success=No

# This template (function optimization)
instance_001 | Best=0.3420 | Optimum=0.0000 | Gap=0.3420 | Evals=50 | Time=3.1ms
```

**Guidelines:**
- Keep it to 1-2 lines
- Include the instance identifier
- Include the primary metric the LLM should optimize
- Include secondary diagnostics that explain behavior

## Testing Your Evaluator

`test_evaluator.py` tests both storage modes:

```bash
uv run python test_evaluator.py
```

This will:
1. Run the evaluator in **rendered mode** — validates metrics + pre-rendered PNG
2. Run the evaluator in **raw mode** — validates raw data + tests deferred rendering
3. Save test output images (`test_output_rendered.png`, `test_output_raw.png`) for visual inspection

## Checklist for Adding a New Multimodal Task

1. **Create task directory** under `examples/applications/`
2. **Write algorithm file** with EVOLVE markers and baseline implementation
3. **Prepare data files** — one JSON per instance in a `data/` subdirectory
4. **Implement evaluator** with subprocess isolation + renderer + BehaviorData generation (all in one file)
5. **Write YAML config** with `multimodal:` section and multimodal samplers
6. **Write test_evaluator.py** to verify both rendered and raw modes
7. **Write debug_run.py** for full pipeline testing
8. **Test locally:**
   ```bash
   cd examples/applications/my_task_python_multimodal/
   uv run python test_evaluator.py      # Test evaluator + behavior data
   uv run python debug_run.py           # Test full pipeline
   ```
9. **Lint check:**
    ```bash
    uv run --python 3.12 ruff check examples/applications/my_task_python_multimodal/
    ```

## Existing Multimodal Task Reference

| Task | Renderer Name | Visualization | Raw Data Schema | Observation Format |
|------|--------------|---------------|-----------------|-------------------|
| TSP | `tsp_tour` | Tour plot (cities + edges) | `{nodes, tour, tour_length, n_nodes}` | `inst \| N=.. \| Length=..` |
| LunarLander | `lunarlander_trajectory` | Trajectory overlay | `{canvas_data}` | `Seed=.. \| Reward=..` |
| Template | `my_task_contour` | Contour + trajectory | `{grid, trajectory, best_point, ...}` | `inst \| Best=.. \| Gap=..` |
