"""Restore accurate gateway failures across Redis decoding modes."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import runtime_health


@pytest.mark.parametrize("binary", [False, True])
def test_snapshot_restores_reason_and_retry_progress(monkeypatch, binary):
    """Read production's decoded string keys as well as byte-key Redis clients."""
    state = {"failures": "2", "reason": "timeout", "status": "0", "message": "No response", "ts": "12.5",
             "attempt": "3", "max_attempts": "3", "retrying": "1", "retry_in": "2"}
    if binary:
        state = {key.encode(): value.encode() for key, value in state.items()}
    monkeypatch.setattr(runtime_health, "get_sync_redis", lambda: SimpleNamespace(hgetall=lambda _key: state))
    snapshot = runtime_health.runtime_snapshot("workspace")
    assert snapshot["reason"] == "timeout"
    assert snapshot["failures"] == 2
    assert snapshot["retrying"] and snapshot["attempt"] == 3
    assert snapshot["retry_in"] == 2


@pytest.mark.parametrize("exception", [httpx.ReadTimeout(""), httpx.ConnectTimeout(""), TimeoutError()])
def test_empty_timeout_messages_are_classified_by_type(exception):
    """Empty exception messages must still display the timeout reason."""
    assert runtime_health.classify_upstream_error(exception, None) == "timeout"


def test_stop_clears_own_failure_and_old_requests_stay_cancelled_after_resume(monkeypatch):
    """Persist cancellation across workers without blocking the next user turn."""
    values = {}
    events = []
    clock = [10.0]

    class Redis:
        """Implement only the hash operations used by conversation control."""

        def hgetall(self, key):
            return values.get(key, {})

        def hset(self, key, *, mapping):
            values.setdefault(key, {}).update(mapping)

        def expire(self, *_args):
            return True

        def delete(self, *keys):
            for key in keys:
                values.pop(key, None)

    monkeypatch.setattr(runtime_health, "get_sync_redis", Redis)
    monkeypatch.setattr(runtime_health.time, "time", lambda: clock[0])
    monkeypatch.setattr(runtime_health, "_publish", lambda _workspace, event: events.append(event))
    state_key = runtime_health._state_key("workspace")
    values[state_key] = {"session_id": "session", "failures": 9}
    runtime_health.record_runtime_chat_control("workspace", json.dumps({"type": "chat.abort", "sessionId": "session"}))
    assert state_key not in values
    control = values[runtime_health._control_key("workspace", "session")]
    assert runtime_health._request_cancelled(control, 9.0)
    assert runtime_health._request_cancelled(control, 11.0)
    assert events[-1]["type"] == "proxy_cancelled"

    clock[0] = 12.0
    runtime_health.record_runtime_chat_control("workspace", json.dumps({"type": "chat.send", "sessionId": "session"}))
    assert runtime_health._request_cancelled(control, 9.0)
    assert not runtime_health._request_cancelled(control, 13.0)
    assert events[-1]["type"] == "proxy_started"

    values[state_key] = {"session_id": "other-stage", "failures": 1}
    clock[0] = 14.0
    runtime_health.record_runtime_chat_control("workspace", json.dumps({"type": "chat.abort", "sessionId": "session"}))
    assert values[state_key]["session_id"] == "other-stage"


def test_late_cancelled_failure_cannot_increment_or_notify(monkeypatch):
    """Discard a timed-out request that finishes after a stop was recorded."""
    from unittest.mock import Mock

    client = SimpleNamespace(hgetall=lambda _key: {"started_at": "1", "cancelled_at": "3"}, incr=Mock())
    notify, publish = Mock(), Mock()
    monkeypatch.setattr(runtime_health, "get_sync_redis", lambda: client)
    monkeypatch.setattr(runtime_health, "_try_notify_session", notify)
    monkeypatch.setattr(runtime_health, "_publish", publish)
    assert runtime_health.record_runtime_failure(
        "workspace", "session", reason="timeout", status_code=None,
        message="Late timeout", request_started_at=2,
    ) == 0
    client.incr.assert_not_called()
    notify.assert_not_called()
    publish.assert_not_called()
