"""Contracts for the embedded research runtime proxy."""

import uuid

import pytest
from fastapi import Response
from sqlmodel import Session

from app import models
from app.api.llm4ad import paper_runtime
from app.core.db import engine
from app.schemas.paper import PaperRuntimeSessionCreate
from tests.utils.user import create_random_user


class _RuntimeResponse:
    """Successful internal runtime-profile response."""

    is_success = True


class _RuntimeClient:
    """Capture the server-only CloudCLI runtime profile."""

    def __init__(self, captured: dict, **_kwargs) -> None:
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def put(self, url: str, **kwargs) -> _RuntimeResponse:
        self.captured["profile_url"] = url
        self.captured["profile"] = kwargs["json"]
        return _RuntimeResponse()


def test_runtime_urls_stay_scoped_to_one_workspace() -> None:
    """Keep browser and Docker addresses tied to the same workspace ID."""
    workspace_id = uuid.UUID("3d56ad31-2ae1-4ca5-9a24-7090dcc916dd")

    assert paper_runtime._runtime_base_path(workspace_id) == (
        "/api/v1/llm4ad/papers/workspaces/3d56ad31-2ae1-4ca5-9a24-7090dcc916dd/cloudcli"
    )
    assert paper_runtime._runtime_upstream_url(workspace_id, "api/projects", "limit=1") == (
        "http://llm4ad-paper-workspace-3d56ad312ae14ca59a247090dcc916dd:3001/api/projects?limit=1"
    )


def test_runtime_cookie_name_is_workspace_specific() -> None:
    """Prevent a ticket cookie from being reused as another workspace cookie."""
    first = uuid.uuid4()
    second = uuid.uuid4()

    assert paper_runtime._runtime_cookie_name(first) != paper_runtime._runtime_cookie_name(second)


def test_runtime_html_anchors_relative_assets_to_owned_proxy() -> None:
    """Resolve a nested native session's relative assets through the backend."""
    workspace_id = uuid.UUID("3d56ad31-2ae1-4ca5-9a24-7090dcc916dd")

    rewritten = paper_runtime._inject_runtime_base(
        b'<html><head><script src="./assets/app.js"></script></head></html>',
        workspace_id,
    ).decode()

    assert (
        '<base href="/api/v1/llm4ad/papers/workspaces/' '3d56ad31-2ae1-4ca5-9a24-7090dcc916dd/cloudcli/">'
    ) in rewritten


def test_runtime_html_keeps_existing_base_tag() -> None:
    """Avoid introducing two conflicting base tags into upstream HTML."""
    workspace_id = uuid.uuid4()
    payload = b'<html><head><base href="/existing/"></head></html>'

    assert paper_runtime._inject_runtime_base(payload, workspace_id) == payload


@pytest.mark.asyncio
async def test_runtime_session_uses_owned_anthropic_binding_and_scoped_gateway_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Keep provider credentials server-side and bind the selected model to Claude."""
    captured: dict = {}
    with Session(engine) as db:
        user = create_random_user(db)
        provider = models.LLMProvider(
            name="research-anthropic",
            user_id=user.id,
            type=models.ProviderType.ANTHROPIC,
            model="claude-sonnet-4-5",
            api_key="upstream-secret",
            base_url="https://anthropic.example",
            timeout=321,
        )
        workspace = models.PaperWorkspace(
            user_id=user.id,
            title="Gateway research",
            mode=models.ResearchWorkspaceMode.PROPOSAL.value,
            proposal_entry_path="proposal.typ",
            analysis_provider_id=provider.id,
            analysis_model_name="claude-sonnet-4-5",
            analysis_context_window_tokens=200_000,
            analysis_max_output_tokens=32_000,
        )
        db.add(provider)
        db.add(workspace)
        db.flush()
        source = models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=1,
            source_kind=models.PaperSourceKind.MARKDOWN.value,
            filename="proposal.typ",
            object_key=f"paper/{user.id}/{workspace.id}/sources/current/proposal.typ",
            content_hash="source",
            content_size=8,
            manifest=["proposal.typ"],
        )
        db.add(source)
        db.flush()
        workspace.active_source_version_id = source.id
        db.add(workspace)
        db.commit()
        db.refresh(provider)
        db.refresh(workspace)

        monkeypatch.setattr(
            paper_runtime.paper_service,
            "sync_paper_workspace_source",
            lambda *_args: None,
        )
        monkeypatch.setattr(
            paper_runtime,
            "ensure_paper_workspace_container",
            lambda spec: captured.setdefault("container_spec", spec),
        )

        async def runtime_ready(_workspace_id: uuid.UUID) -> None:
            return None

        async def store_ticket(ticket: str, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
            captured["ticket"] = (ticket, workspace_id, user_id)

        monkeypatch.setattr(paper_runtime, "_wait_until_ready", runtime_ready)
        monkeypatch.setattr(paper_runtime, "_store_runtime_ticket", store_ticket)

        def issue_token(**kwargs) -> str:
            captured["credentials"] = kwargs
            return "gateway-token"

        monkeypatch.setattr(
            paper_runtime.credential_broker,
            "issue_token",
            issue_token,
        )
        monkeypatch.setattr(
            paper_runtime.httpx,
            "AsyncClient",
            lambda **kwargs: _RuntimeClient(captured, **kwargs),
        )
        monkeypatch.setattr(
            paper_runtime.paper_service,
            "paper_workspace_root",
            lambda *_args: tmp_path,
        )
        response = Response()

        session = await paper_runtime.create_runtime_session(
            workspace.id,
            PaperRuntimeSessionCreate(workflow_stage="formatting"),
            response,
            db,
            user,
            "browser-access-token",
        )

    issued = captured["credentials"]
    profile = captured["profile"]
    assert issued["user_id"] == user.id
    assert issued["provider_type"] == models.ProviderType.ANTHROPIC.value
    assert issued["model"] == "claude-sonnet-4-5"
    assert issued["api_key"] == "upstream-secret"
    assert profile["model"] == "claude-sonnet-4-5"
    assert profile["env"]["ANTHROPIC_AUTH_TOKEN"] == "gateway-token"
    assert profile["contextWindowTokens"] == 200_000
    assert profile["toolsSettings"]["disallowedTools"] == ["Bash", "WebFetch", "WebSearch"]
    assert "upstream-secret" not in session.runtime_url
    assert "gateway-token" not in session.runtime_url
    assert "upstream-secret" not in response.headers.get("set-cookie", "")
