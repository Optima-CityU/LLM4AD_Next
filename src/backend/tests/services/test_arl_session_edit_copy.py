"""ARL session edit / copy helpers.

Pins down: arl_config validation and merge (runtime keys like ``mode_id``
survive an edit), and that copying only carries ``workspace``, not
``paseo-home``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.arl import runtime
from app.services.research_service import sessions


def _cfg(worker_pid: str, validator_pid: str) -> dict:
    return {
        "worker": {"provider_id": worker_pid, "model_name": " claude-opus "},
        "validator": {"provider_id": validator_pid, "model_name": "gpt-5.5"},
    }


@pytest.fixture
def allow_providers(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    checked: list[uuid.UUID] = []
    monkeypatch.setattr(
        sessions, "get_provider_with_auth", lambda _db, pid, _user: checked.append(pid)
    )
    return checked


def test_validate_arl_config_normalizes_and_checks_access(
    allow_providers: list[uuid.UUID],
) -> None:
    wid, vid = uuid.uuid4(), uuid.uuid4()

    result = sessions._validate_arl_config(None, _cfg(str(wid), str(vid)), None)

    assert result == {
        "worker": {"provider_id": str(wid), "model_name": "claude-opus"},
        "validator": {"provider_id": str(vid), "model_name": "gpt-5.5"},
    }
    assert allow_providers == [wid, vid]


@pytest.mark.parametrize(
    "config",
    [
        {"worker": {"provider_id": str(uuid.uuid4()), "model_name": "m"}},
        _cfg("not-a-uuid", str(uuid.uuid4())),
        {
            "worker": {"provider_id": str(uuid.uuid4()), "model_name": "  "},
            "validator": {"provider_id": str(uuid.uuid4()), "model_name": "m"},
        },
    ],
)
@pytest.mark.usefixtures("allow_providers")
def test_validate_arl_config_rejects_malformed(config: dict) -> None:
    with pytest.raises(HTTPException) as exc:
        sessions._validate_arl_config(None, config, None)
    assert exc.value.status_code == 400


def test_merge_arl_config_keeps_runtime_keys() -> None:
    current = {
        "worker": {"provider_id": "a", "model_name": "old", "mode_id": "plan"},
        "validator": {"provider_id": "b", "model_name": "old"},
    }
    update = {
        "worker": {"provider_id": "c", "model_name": "new"},
        "validator": {"provider_id": "b", "model_name": "new"},
    }

    merged = sessions._merge_arl_config(current, update)

    assert merged["worker"] == {"provider_id": "c", "model_name": "new", "mode_id": "plan"}
    assert merged["validator"] == {"provider_id": "b", "model_name": "new"}
    assert current["worker"]["model_name"] == "old"  # input left untouched


def test_copy_session_workspace_skips_paseo_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime, "_session_root", lambda sid: (str(tmp_path / sid), str(tmp_path / sid))
    )
    for role in ("worker", "validator"):
        ws = tmp_path / "src" / role / "workspace" / role
        ws.mkdir(parents=True)
        (ws / "main.py").write_text(role)
        home = tmp_path / "src" / role / "paseo-home"
        home.mkdir()
        (home / "agents.json").write_text("{}")

    runtime.copy_session_workspace("src", "dst")

    for role in ("worker", "validator"):
        dst = tmp_path / "dst" / role
        assert (dst / "workspace" / role / "main.py").read_text() == role
        assert not (dst / "paseo-home").exists()
