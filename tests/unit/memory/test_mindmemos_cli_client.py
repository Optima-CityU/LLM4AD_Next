"""Tests for the standalone MindMemOS CLI client."""

from __future__ import annotations

from llm4ad.memory.mindmemos_client import MindMemOSClient


def _client() -> MindMemOSClient:
    return MindMemOSClient(base_url="http://mindmemos.test", jwt_secret="secret")


def test_parse_sse_events_preserves_event_name_and_multiline_data() -> None:
    events = list(
        _client().parse_sse_events(
            [
                "event: progress",
                'data: {"stage":"extracting",',
                'data: "percent": 42}',
                "",
                "event: completed",
                'data: {"data":{"memories":[]}}',
                "",
            ]
        )
    )

    assert events == [
        {"event": "progress", "stage": "extracting", "percent": 42},
        {"event": "completed", "data": {"memories": []}},
    ]


def test_check_ready_requires_a_binding_with_both_routers(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "health_check", lambda: {"ok": True})
    monkeypatch.setattr(
        client,
        "get_binding",
        lambda: {
            "data": {
                "items": [
                    {
                        "binding_id": "binding-1",
                        "routers": {
                            "chat_model_router": {"endpoints": [{"model": "chat-model"}]},
                            "embed_model_router": {"endpoints": [{"model": "embed-model"}]},
                        },
                    }
                ]
            }
        },
    )

    assert client.check_ready() == {
        "binding_id": "binding-1",
        "chat_model": "chat-model",
        "embedding_model": "embed-model",
    }


def test_check_ready_rejects_incomplete_binding(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "health_check", lambda: {"ok": True})
    monkeypatch.setattr(
        client,
        "get_binding",
        lambda: {"data": {"items": [{"binding_id": "binding-1", "routers": {}}]}},
    )

    try:
        client.check_ready()
    except RuntimeError as error:
        assert str(error) == "MindMemOS provider binding is incomplete"
    else:  # pragma: no cover - documents the required failure
        raise AssertionError("Expected incomplete binding to be rejected")
