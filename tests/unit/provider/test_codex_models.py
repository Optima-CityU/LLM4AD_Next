"""Read real subprocess protocol messages without running model inference."""

import asyncio
import sys
from unittest.mock import AsyncMock

import pytest
from llm4ad.frontend import codex_bridge
from llm4ad.infra.codex_models import read_models

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_model_discovery_initialization_pagination_and_redaction(monkeypatch):
    """Use effective config, filter hidden models, and discard unrelated settings."""
    create = asyncio.create_subprocess_exec
    processes = []
    script = """
import json, sys
initialized = False
for line in sys.stdin:
    item = json.loads(line)
    method = item['method']
    if method == 'initialized':
        initialized = True
        continue
    if method == 'initialize':
        result = {}
    elif method == 'config/read':
        assert initialized
        result = {'config': {'model': 'model-b', 'secret': 'never-return'}}
    elif method == 'model/list':
        assert item['params']['includeHidden'] is False
        if not item['params'].get('cursor'):
            result = {'data': [{'model': 'model-a', 'displayName': 'A', 'isDefault': True},
                               {'model': 'hidden', 'hidden': True}], 'nextCursor': 'page-2'}
        else:
            assert item['params']['cursor'] == 'page-2'
            result = {'data': [{'model': 'model-b', 'displayName': 'B'}], 'nextCursor': None}
    else:
        raise AssertionError('No thread or inference requests allowed')
    print(json.dumps({'method': 'unrelated/notification'}), flush=True)
    print(json.dumps({'id': item['id'], 'result': result}), flush=True)
"""

    async def launch(*args, **kwargs):
        assert args[1:4] == ("app-server", "--listen", "stdio://")
        assert "OPENAI_API_KEY" not in kwargs["env"]
        process = await create(sys.executable, "-u", "-c", script, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setenv("OPENAI_API_KEY", "never-use-api-billing")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    result = await read_models()
    assert result == {
        "models": [{"id": "model-a", "name": "A"}, {"id": "model-b", "name": "B"}],
        "default_model": "model-b",
    }
    assert processes[0].returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_discovery_timeout_or_cancel_reaps_subprocess(monkeypatch, cancel):
    """Closing the picker request must not leave an app-server running."""
    create = asyncio.create_subprocess_exec
    processes = []

    async def launch(*args, **kwargs):
        process = await create(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    task = asyncio.create_task(read_models(timeout=60 if cancel else 0.05))
    if cancel:
        while not processes:
            await asyncio.sleep(0.01)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_catalog_failure_keeps_login_status_and_manual_selection(monkeypatch):
    """Older/offline CLI catalogs must not make a valid binding unavailable."""
    monkeypatch.setattr(codex_bridge.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(
        codex_bridge,
        "_communicate",
        AsyncMock(
            side_effect=[
                (0, "", "Logged in using ChatGPT"),
                (0, "codex test", ""),
            ]
        ),
    )
    monkeypatch.setattr(codex_bridge, "read_models", AsyncMock(side_effect=TimeoutError))
    status = await codex_bridge.login_status()
    assert status["authenticated"] and status["installed"]
    assert status["models"] == [] and not status["models_available"]
