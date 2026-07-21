import json

from loguru import logger

from app.tasks.container_runner import EventsSink, _sanitize_non_finite


def test_events_sink_writes_structured_memory_events(tmp_path):
    events_path = tmp_path / ".events.jsonl"

    with EventsSink(str(events_path)) as sink:
        sink_id = logger.add(sink, level="INFO")
        try:
            logger.bind(
                event_type="memory_card_created",
                scope="task",
                task_id="task-1",
                memory_id="memory-1",
            ).info("MindMemOS task memory created")
        finally:
            logger.remove(sink_id)

    event = json.loads(events_path.read_text(encoding="utf-8").strip())
    assert event["type"] == "memory_card_created"
    assert event["scope"] == "task"
    assert event["task_id"] == "task-1"
    assert event["memory_id"] == "memory-1"
    assert "timestamp" in event


def test_sanitize_non_finite_replaces_inf_nan_with_none():
    """Test that _sanitize_non_finite converts inf/-inf/nan to None recursively."""
    # Scalars
    assert _sanitize_non_finite(42.5) == 42.5
    assert _sanitize_non_finite(float("inf")) is None
    assert _sanitize_non_finite(float("-inf")) is None
    assert _sanitize_non_finite(float("nan")) is None
    assert _sanitize_non_finite("text") == "text"
    assert _sanitize_non_finite(None) is None

    # Nested structures (matching generated event payload shape)
    payload = {
        "evaluation": {
            "score": float("-inf"),
            "metrics": {"time_ms": 123.4, "cost": float("nan")},
        },
        "name": "algo",
        "nested_list": [1, float("inf"), {"x": float("-inf")}],
    }
    result = _sanitize_non_finite(payload)
    assert result["evaluation"]["score"] is None
    assert result["evaluation"]["metrics"]["time_ms"] == 123.4
    assert result["evaluation"]["metrics"]["cost"] is None
    assert result["name"] == "algo"
    assert result["nested_list"] == [1, None, {"x": None}]
