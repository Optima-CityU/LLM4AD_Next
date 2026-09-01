"""UI metadata helpers for frontend dynamic form generation.

Provides a ``ui()`` helper that returns a dict suitable for Pydantic's
``json_schema_extra`` parameter. The frontend reads these annotations
from ``Model.model_json_schema()`` to dynamically render forms.
"""

from typing import Any


def ui(
    *,
    order: int = 999,
    label_zh: str = "",
    label_en: str = "",
    desc_zh: str = "",
    desc_en: str = "",
    hidden: bool = False,
    user_required: bool = False,
    is_path: bool = False,
    multiline: bool = False,
    widget: str | None = None,
    slider_min: int | float | None = None,
    slider_max: int | float | None = None,
    slider_step: int | float | None = None,
) -> dict[str, Any]:
    """Build UI metadata dict for ``Field(json_schema_extra=ui(...))``.

    Args:
        order: Display order among sibling fields (lower = higher priority).
        label_zh: Chinese label displayed on the form.
        label_en: English label displayed on the form.
        desc_zh: Chinese tooltip shown on hover (more detailed than label).
        desc_en: English tooltip shown on hover (more detailed than label).
        hidden: Whether to hide this field from the frontend form.
        user_required: Whether the user must explicitly fill in this field
            (a field can have a schema default yet still need user
            attention, e.g. ``api_key``).
        is_path: Whether the value is a filesystem path. The frontend can
            use this flag to prepend a configurable base-path prefix or
            show a file/directory picker.
        multiline: Whether the input should use a multiline textarea
            instead of a single-line text field.
        widget: Optional frontend widget name, such as ``"slider"``.
        slider_min: Visual minimum when ``widget`` is ``"slider"``.
        slider_max: Visual maximum when ``widget`` is ``"slider"``.
        slider_step: Step size when ``widget`` is ``"slider"``.

    Returns:
        Dict with a single ``"ui"`` key containing the metadata,
        merged into the JSON schema property by Pydantic.
    """
    metadata: dict[str, Any] = {
        "order": order,
        "label": {"zh": label_zh, "en": label_en},
        "description": {"zh": desc_zh, "en": desc_en},
        "hidden": hidden,
        "user_required": user_required,
        "is_path": is_path,
        "multiline": multiline,
    }
    if widget is not None:
        metadata["widget"] = widget
    if slider_min is not None and slider_max is not None and slider_step is not None:
        metadata["slider"] = {
            "min": slider_min,
            "max": slider_max,
            "step": slider_step,
        }
    return {"ui": metadata}
