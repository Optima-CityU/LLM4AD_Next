"""Filament FSR1 evaluator and its platform task fixture."""

from pathlib import Path

_CASE = Path(__file__).resolve().parent
TASK_TEMPLATE = _CASE / "task"
if not TASK_TEMPLATE.is_dir():
    TASK_TEMPLATE = _CASE.parents[4] / "examples" / "applications" / "filament_fsr1_single_pass"
