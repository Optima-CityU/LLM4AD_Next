import json

from loguru import logger

from app.tasks.container_runner import EventsSink


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
