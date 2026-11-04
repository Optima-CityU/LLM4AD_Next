"""Exercise the real AutoDesign MCP surface and its HTTP forwarding contract."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BRIDGE_PATH = Path(__file__).resolve().parents[3] / "docker/paper-agent/autodesign_bridge.py"
WORKSPACE_ID = "e650e4fc-30a1-4d3d-9a10-a737aaf52550"
TASK_ID = "7879716f-63c8-476b-8bbc-0b567be4d05a"
RUNTIME_URL = "http://runtime.internal/api/v1/llm4ad/autodesign/runtime/operations"
TOKEN = "test-runtime-token-never-return"


@pytest.fixture
def bridge(monkeypatch):
    """Load the standalone server and supply workspace-owned runtime settings."""
    spec = importlib.util.spec_from_file_location("autodesign_bridge", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("LLM4AD_AUTODESIGN_URL", RUNTIME_URL)
    monkeypatch.setenv("LLM4AD_AUTODESIGN_WORKSPACE_ID", WORKSPACE_ID)
    monkeypatch.setenv("LLM4AD_AUTODESIGN_TOKEN", TOKEN)
    monkeypatch.delenv("LLM4AD_AUTODESIGN_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TASK_DEP_INSTALL_TIMEOUT", raising=False)
    return module


@pytest.fixture
def transport(bridge, monkeypatch):
    """Capture actual HTTP requests while keeping tests off the network."""
    original_client = httpx.AsyncClient
    requests, options = [], []

    def install(handler):
        def respond(request):
            requests.append(request)
            return handler(request)

        def client(**kwargs):
            options.append(kwargs)
            return original_client(transport=httpx.MockTransport(respond), **kwargs)

        monkeypatch.setattr(bridge.httpx, "AsyncClient", client)
        return requests, options

    return install


@pytest.mark.parametrize(
    ("tool", "kwargs", "operation", "task_id", "arguments"),
    [
        ("get_task_context", {}, "context", None, {}),
        ("get_memory_context", {}, "memory_context", None, {}),
        ("get_memory_context", {"task_id": TASK_ID}, "memory_context", TASK_ID, {}),
        ("list_memory_cards", {}, "memory_list", None, {"scope": "user", "page": 1, "page_size": 20}),
        ("list_memory_cards", {"scope": "task", "task_id": TASK_ID, "page": 2, "page_size": 10}, "memory_list", TASK_ID, {"scope": "task", "page": 2, "page_size": 10}),
        ("save_memory_card", {"content": "Verified observation", "scope": "task", "task_id": TASK_ID, "memory_id": "existing", "metadata": {"source_path": "logs/run.log"}}, "memory_save", TASK_ID, {"scope": "task", "card": {"id": "existing", "type": "general_insight", "title": "", "content": "Verified observation", "tags": [], "metadata": {"llm4ad_evidence": {"source_path": "logs/run.log"}}}}),
        ("update_project_name", {"name": "Scheduling optimization"}, "rename", None, {"name": "Scheduling optimization"}),
        ("get_task_context", {"task_id": TASK_ID}, "context", TASK_ID, {}),
        ("list_task_files", {}, "list_files", None, {"area": "run", "directory": ""}),
        (
            "list_task_files",
            {"area": "input", "task_id": TASK_ID, "directory": "algorithm"},
            "list_files",
            TASK_ID,
            {"area": "input", "directory": "algorithm"},
        ),
        ("read_task_file", {"path": ".final_state.json"}, "read_file", None, {"path": ".final_state.json", "area": "run"}),
        (
            "read_task_file",
            {"path": "evaluator.py", "area": "input", "task_id": TASK_ID, "start_line": 2, "end_line": 8},
            "read_file",
            TASK_ID,
            {"path": "evaluator.py", "area": "input", "start_line": 2, "end_line": 8},
        ),
        (
            "read_task_file",
            {"path": "config.yaml", "area": "draft", "end_line": 12},
            "read_file",
            None,
            {"path": "config.yaml", "area": "draft", "end_line": 12},
        ),
        ("create_child_task", {"parent_task_id": TASK_ID}, "create_child", TASK_ID, {"config_patch": None, "seed": "input", "name": None}),
        (
            "create_child_task",
            {"parent_task_id": TASK_ID, "seed": "best", "name": "adjusted", "config_patch": {"coder": {"type": "custom"}}},
            "create_child",
            TASK_ID,
            {"config_patch": {"coder": {"type": "custom"}}, "seed": "best", "name": "adjusted"},
        ),
        (
            "patch_child_parameters",
            {
                "task_id": TASK_ID,
                "config_patch": {
                    "evaluator": {"dataset": {"mode": "files", "files": ["data/a.json"]}},
                    "planner": {"build": {"script": "make"}},
                },
            },
            "patch_child",
            TASK_ID,
            {
                "config_patch": {
                    "evaluator": {"dataset": {"mode": "files", "files": ["data/a.json"]}},
                    "planner": {"build": {"script": "make"}},
                }
            },
        ),
        (
            "validate_project",
            {"project_path": "/workspace/source/draft/task"},
            "validate",
            None,
            {"project_path": "/workspace/source/draft/task", "timeout_seconds": 120},
        ),
        ("prepare_task", {}, "publish", None, {"project_id": None, "name": None, "enable_memory": None}),
        (
            "prepare_task",
            {"project_id": WORKSPACE_ID, "task_id": TASK_ID, "name": "child", "enable_memory": False},
            "publish",
            TASK_ID,
            {"project_id": WORKSPACE_ID, "name": "child", "enable_memory": False},
        ),
        ("start_task", {"task_id": TASK_ID}, "start", TASK_ID, {}),
        ("patch_task_parameters", {"task_id": TASK_ID, "config_patch": {"random_seed": 3}}, "patch_child", TASK_ID, {"config_patch": {"random_seed": 3}}),
        ("sync_task_draft", {"task_id": TASK_ID, "resolution": "keep_draft"}, "sync_draft", TASK_ID, {"resolution": "keep_draft"}),
        ("stop_task", {"task_id": TASK_ID}, "stop", TASK_ID, {}),
    ],
)
def test_scoped_operation_envelopes(bridge, transport, tool, kwargs, operation, task_id, arguments):
    """Forward exact scope, nested configuration and explicit optional values."""
    result = {"task_id": task_id, "project_path": "/workspace/source/draft/task", "revision": "verified-revision"}
    requests, options = transport(lambda request: httpx.Response(200, json=result))
    assert asyncio.run(getattr(bridge, tool)(**kwargs)) == result
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == RUNTIME_URL
    assert requests[0].headers["x-llm4ad-runtime-token"] == TOKEN
    assert json.loads(requests[0].content) == {
        "workspace_id": WORKSPACE_ID,
        "operation": operation,
        "task_id": task_id,
        "arguments": arguments,
    }
    assert options[0]["trust_env"] is False


def test_full_read_is_not_truncated(bridge, transport):
    """Return long file content intact when no range was requested."""
    response = {"content": "candidate source\n" * 10000, "total_lines": 10000, "start_line": 1, "end_line": 10000}
    transport(lambda request: httpx.Response(200, json=response))
    assert asyncio.run(bridge.read_task_file("generated/candidate.json")) == response


@pytest.mark.parametrize(("configured", "expected"), [("10", 1230), ("2000", 2000)])
def test_validation_transport_waits_for_worker(bridge, transport, monkeypatch, configured, expected):
    """Allow worker completion and respect a longer configured HTTP timeout."""
    monkeypatch.setenv("LLM4AD_AUTODESIGN_TIMEOUT_SECONDS", configured)
    requests, options = transport(lambda request: httpx.Response(200, json={"status": "passed"}))
    asyncio.run(bridge.validate_project("/workspace/source/draft/task", timeout_seconds=600))
    assert options[0]["timeout"] == expected
    assert json.loads(requests[0].content)["arguments"]["timeout_seconds"] == 600


def test_validation_allows_configured_dependency_installation(bridge, transport, monkeypatch):
    """Match the execution service's allowance for preparing task dependencies."""
    monkeypatch.setenv("TASK_DEP_INSTALL_TIMEOUT", "1000")
    _, options = transport(lambda request: httpx.Response(200, json={"project_ready": True}))
    asyncio.run(bridge.validate_project("/workspace/source/draft/task", timeout_seconds=300))
    assert options[0]["timeout"] == 1330


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_worker_timeout_fails_before_request(bridge, transport, timeout):
    """Reject invalid validation requests without side effects."""
    requests, _ = transport(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="finite positive"):
        asyncio.run(bridge.validate_project("/workspace/source/draft/task", timeout_seconds=timeout))
    assert not requests


@pytest.mark.parametrize("missing", ["URL", "WORKSPACE_ID", "TOKEN"])
def test_missing_runtime_setting_is_actionable(bridge, monkeypatch, missing):
    """Name the absent setting without returning configured secret values."""
    name = f"LLM4AD_AUTODESIGN_{missing}"
    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match=name) as error:
        asyncio.run(bridge.get_task_context())
    assert TOKEN not in str(error.value)


def test_backend_rejection_preserves_safe_diagnostics(bridge, transport):
    """Keep useful validation failures while removing reflected authentication."""
    requests, _ = transport(
        lambda request: httpx.Response(
            422,
            json={
                "detail": {
                    "error": "Evaluator accepted empty candidate",
                    "token": TOKEN,
                    "api_key": "another-secret",
                    "diagnostics": [f"reflected {TOKEN}"],
                }
            },
        )
    )
    with pytest.raises(RuntimeError, match="Evaluator accepted empty candidate") as error:
        asyncio.run(bridge.validate_project("/workspace/source/draft/task"))
    assert "HTTP 422" in str(error.value)
    assert TOKEN not in str(error.value)
    assert "another-secret" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failure_is_safe_and_does_not_retry(bridge, transport, failure):
    """Avoid duplicate starts or credential-bearing request exception output."""

    def fail(request):
        raise failure(f"request failed with {TOKEN}", request=request)

    requests, _ = transport(fail)
    with pytest.raises(RuntimeError) as error:
        asyncio.run(bridge.start_task(TASK_ID))
    assert TOKEN not in str(error.value)
    assert RUNTIME_URL not in str(error.value)
    assert "start" in str(error.value)
    assert len(requests) == 1
    if failure is httpx.ReadTimeout:
        assert "inspect task context" in str(error.value)


@pytest.mark.parametrize("response", [httpx.Response(502, text="internal proxy error"), httpx.Response(200, json=[])])
def test_invalid_response_is_not_mistaken_for_success(bridge, transport, response):
    """Require a backend response object and explain malformed responses."""
    transport(lambda request: response)
    with pytest.raises(RuntimeError, match="response"):
        asyncio.run(bridge.get_task_context())


def test_official_mcp_stdio_startup_and_schemas():
    """Discover the installed tool surface through a real MCP stdio session."""

    async def inspect():
        params = StdioServerParameters(command=sys.executable, args=[str(BRIDGE_PATH)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name: tool.inputSchema for tool in (await session.list_tools()).tools}

    schemas = asyncio.run(inspect())
    assert set(schemas) == {
        "get_task_context",
        "get_memory_context",
        "list_memory_cards",
        "save_memory_card",
        "update_project_name",
        "list_task_files",
        "read_task_file",
        "create_child_task",
        "patch_child_parameters",
        "patch_task_parameters",
        "sync_task_draft",
        "validate_project",
        "prepare_task",
        "start_task",
        "stop_task",
    }
    assert schemas["read_task_file"]["required"] == ["path"]
    assert schemas["patch_child_parameters"]["required"] == ["task_id", "config_patch"]
    assert schemas["list_task_files"]["properties"]["area"]["enum"] == ["input", "run", "draft", "references", "analysis"]
    assert schemas["create_child_task"]["properties"]["seed"]["enum"] == ["input", "best"]
    assert schemas["read_task_file"]["properties"]["end_line"]["default"] is None


def test_mutation_tools_require_explicit_version_identity(bridge):
    """Omitted identities cannot target an old conversation's default task."""
    for tool in (bridge.start_task, bridge.stop_task, bridge.create_child_task, bridge.sync_task_draft):
        with pytest.raises(TypeError):
            asyncio.run(tool())
