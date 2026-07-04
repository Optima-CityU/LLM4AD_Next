# Relationship Outcome Prediction

This example uses LLM4AD to evolve **relationship crisis outcome predictions**. Given a psychological profile of two people and a crisis event, the framework evolves prediction text that is psychologically grounded, specific, and internally consistent. Evaluation is performed by an LLM judge using the `LLMJudgeEvaluator` base class.

## Overview

- **`config.yaml`**: LLM4AD pipeline configuration
- **`relationship_evaluator.py`**: Custom evaluator extending `LLMJudgeEvaluator` (judge prompt + metrics)
- **`prep_config.py`**: Syncs case data into the YAML config's `background` field before evolution
- **`export_best.py`**: Exports the best prediction to Markdown
- **`workspace/`**: Git repo template with seed script containing EVOLVE blocks
- **`data/`**: Case files for batch evaluation (optional)

## Prerequisites

- Python 3.12+
- LLM4AD installed (`uv sync` or `pip install -e .`)
- An OpenAI-compatible API key (e.g., DeepSeek, OpenAI)

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Configure your LLM provider in `config.yaml`:

```yaml
providers:
  - name: "predictor_llm"
    type: "openai_compatible"
    base_url: "https://your-api-endpoint"
    api_key: "your-api-key"
    model: "your-model-name"

evaluator:
  api_config:
    base_url: "https://your-api-endpoint"
    api_key: "your-api-key"
    model: "your-model-name"
```

3. Prepare the case data (injects case info into the config's `background` field):

```bash
python examples/applications/relationship_prediction/prep_config.py
# Or specify a case file:
python examples/applications/relationship_prediction/prep_config.py --case data/case_02.yaml
```

## Running the Evolutionary Pipeline

```bash
llm4ad run examples/applications/relationship_prediction/config.yaml
```

## How It Works

1. **Case Preparation**: `prep_config.py` reads a case YAML (personality profiles + crisis event) and injects it into the config's `background` field, so the planner has full context.
2. **EVOLVE Block**: The `workspace/predict_seed.py` contains an EVOLVE block wrapping `get_prediction() -> str`. Evolution optimizes the prediction text itself, not code logic.
3. **Code Generation**: Each generation, the planner proposes improvements to the prediction text (more specific timelines, better psychological grounding, etc.), and the coder modifies the EVOLVE block accordingly.
4. **Evaluation**: `LLMJudgeEvaluator` runs the script, captures the prediction output, and an LLM judge scores it on five dimensions:
   - **trait_specificity** (weight: 0.25): Would the prediction break if you swapped the personality traits? Tests exclusivity to the case.
   - **causal_chain** (weight: 0.20): Is the causal chain from current state to predicted outcome complete, logical, and inevitable?
   - **behavioral_concreteness** (weight: 0.15): Are predictions concrete with specific behaviors, dialogue, and timelines?
   - **critical_gap_check** (weight: 0.20): Does the prediction answer key questions, avoid contradictions, and include realistic friction?
   - **advice_quality** (weight: 0.20): Are the suggestions for both parties tailored to their specific traits and actionable?
5. **Evolution**: Island GA evolves better predictions over generations.

## Exporting Results

After a run completes, export the best prediction:

```bash
# default
python examples/applications/relationship_prediction/export_best.py
# English 
python examples/applications/relationship_prediction/export_best.py --lang en
# Chinese
python examples/applications/relationship_prediction/export_best.py --lang zh
```

## Project Structure

```
relationship_prediction/
├── README.md                        # This file
├── config.yaml     # LLM4AD pipeline configuration
├── relationship_evaluator.py        # Custom evaluator (extends LLMJudgeEvaluator)
├── prep_config.py                   # Sync case data into config
├── export_best.py                   # Export best prediction with language selection
├── intake_web.html                  # Web form case intake tool
├── workspace/                       # Git repo template for worktrees
│   └── predict_seed.py               # Seed script with EVOLVE block
├── data/                            # Case files
└── result/                          # Output directory
```

## Case Intake Tool

A web form is provided to help users create case files without writing YAML manually:

### Web Form

Open `intake_web.html` in any browser. Fill in the form, then download the generated YAML file and place it in the `data/` directory.

The tool guides users through 4 steps:
1. Relationship basics (type + context)
2. Person A profile (MBTI, attachment style, traits, triggers, behavior)
3. Person B profile (same structure)
4. Crisis event (what happened, current state, prediction focus)

## Model Recommendations

**Tip**: Reasoning models consume tokens for internal "thinking", often causing code truncation. Non-reasoning models are strongly recommended.

## License

This example is part of the LLM4AD project.
