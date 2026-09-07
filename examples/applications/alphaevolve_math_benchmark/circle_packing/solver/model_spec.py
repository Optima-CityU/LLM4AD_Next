"""Structured circle-center construction evolved by the language model."""


# EVOLVE_START
MODEL_SPEC = {
    "parameters": {
        "cx": {"initial": 0.5, "lower": 0.4, "upper": 0.6},
        "cy": {"initial": 0.5, "lower": 0.4, "upper": 0.6},
        "middle_radius": {"initial": 0.25, "lower": 0.08, "upper": 0.42},
        "outer_radius": {"initial": 0.45, "lower": 0.2, "upper": 0.49},
        "middle_phase": {"initial": 0.0, "lower": -0.4, "upper": 0.4},
        "outer_phase": {"initial": 0.0, "lower": -0.4, "upper": 0.4},
    },
    "groups": [
        {
            "count": 1,
            "x": "cx",
            "y": "cy",
        },
        {
            "count": 8,
            "x": "cx + middle_radius * cos(2 * pi * i / count + middle_phase)",
            "y": "cy + middle_radius * sin(2 * pi * i / count + middle_phase)",
        },
        {
            "count": 17,
            "x": "cx + outer_radius * cos(2 * pi * i / count + outer_phase)",
            "y": "cy + outer_radius * sin(2 * pi * i / count + outer_phase)",
        },
    ],
}
# EVOLVE_END
