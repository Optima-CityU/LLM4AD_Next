"""Verify runtime credential renewal without restarting an adapter or extending TTLs."""

import asyncio
import uuid
from copy import deepcopy
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import security
from app.services import credential_broker


class MemoryRedis:
    """Model broker mappings and revocation indexes without a Redis server."""

    def __init__(self):
        self.values = {}
        self.sets = {}
        self.ttls = {}

    def pipeline(self):
        return self

    def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex
        return self

    def get(self, key):
        return self.values.get(key)

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)
        return self

    def expire(self, key, ttl):
        self.ttls[key] = ttl
        return self

    def smembers(self, key):
        return self.sets.get(key, set())

    def delete(self, key):
        self.values.pop(key, None)
        self.sets.pop(key, None)
        return self

    def execute(self):
        return []


@pytest.fixture
def broker(monkeypatch):
    """Keep the broker's real encrypted payload path with an isolated backing store."""
    redis = MemoryRedis()
    monkeypatch.setattr(credential_broker, "get_sync_redis", lambda: redis)
    mint = Mock(side_effect=["access-one", "access-two", "access-three"])
    monkeypatch.setattr(security, "create_access_token", mint)
    return redis, mint


def issue(*, user_id, task_id, builtin=True):
    """Issue credentials just as an authorized runtime route would."""
    return credential_broker.issue_token(
        provider_type="openai",
        base_url="https://gateway/original-browser-token/v1" if builtin else "https://custom/v1",
        upstream_url_template="https://gateway/{accessToken}/v1" if builtin else "",
        api_key="secret",
        user_id=user_id,
        task_id=task_id,
        workspace_id=uuid.UUID(int=5),
        session_id="persisted-session",
        model="test-model",
        ttl=120,
    )


def test_builtin_urls_are_resolved_with_fresh_credentials_on_every_call(broker):
    """An adapter can keep its opaque token as the browser login token ages."""
    redis, mint = broker
    user_id, task_id = uuid.uuid4(), uuid.uuid4()
    token = issue(user_id=user_id, task_id=task_id)
    first = credential_broker.resolve_token(token)
    second = credential_broker.resolve_token(token)
    assert first["base_url"] == "https://gateway/access-one/v1"
    assert second["base_url"] == "https://gateway/access-two/v1"
    assert first["api_key"] == "secret"
    assert first["session_id"] == "persisted-session"
    assert mint.call_count == 2
    assert all(value == 120 for value in redis.ttls.values())


def test_expired_mapping_is_not_revived_by_upstream_url_resolution(broker):
    """Fresh upstream JWTs cannot keep an expired broker capability alive."""
    redis, mint = broker
    token = issue(user_id=uuid.uuid4(), task_id=uuid.uuid4())
    redis.values.clear()
    assert credential_broker.resolve_token(token) is None
    mint.assert_not_called()


def test_custom_urls_are_never_treated_as_trusted_builtin_templates(broker):
    """Only a persisted builtin provider is allowed to receive a platform JWT."""
    _, mint = broker
    token = issue(user_id=uuid.uuid4(), task_id=uuid.uuid4(), builtin=False)
    payload = credential_broker.resolve_token(token)
    assert payload["base_url"] == "https://custom/v1"
    assert "upstream_url_template" not in payload
    mint.assert_not_called()


def test_revocation_removes_the_renewable_mapping_without_resurrecting_it(broker):
    """On-demand URL resolution cannot revive a revoked broker capability."""
    _, mint = broker
    user_id, task_id = uuid.uuid4(), uuid.uuid4()
    token = issue(user_id=user_id, task_id=task_id)
    assert credential_broker.revoke_user_tokens(user_id) == 1
    assert credential_broker.resolve_token(token) is None
    mint.assert_not_called()


@pytest.fixture
def endpoint(monkeypatch, broker, tmp_path):  # noqa: ARG001 - broker installs token dependencies.
    """Exercise the real renewal handler against owner-scoped fake persistence."""
    from app.api.llm4ad import paper_runtime as runtime

    owner = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    workspace = SimpleNamespace(
        id=uuid.uuid4(), user_id=owner.id, analysis_provider_id=uuid.uuid4(),
        analysis_model_name="bound-model",
    )
    provider = SimpleNamespace(
        type="openai", base_url="https://gateway/{accessToken}/v1", is_builtin=True,
        api_key="upstream-secret", auth_token="", timeout=30,
    )
    run = SimpleNamespace(
        id=runtime._native_run_id(workspace.id, "workflow"), session_id="persisted-session",
        request_payload={"native_runtime": "cloudcli"}, status="running",
    )
    objects = {runtime.PaperWorkspace: workspace, runtime.LLMProvider: provider}
    db = Mock()
    db.get.side_effect = lambda model, _id: objects.get(model)
    db.exec.return_value = SimpleNamespace(all=lambda: [run])
    forbidden = Mock(side_effect=AssertionError("Renewal must not mutate source, profile, container or activity"))
    for name in ("add", "commit", "refresh", "flush", "delete"):
        setattr(db, name, forbidden)
    for name in ("ensure_paper_workspace_container", "ensure_paper_protocol_adapter", "touch_paper_workspace_active", "_wait_until_ready"):
        monkeypatch.setattr(runtime, name, forbidden)
    for name in ("sync_paper_workspace_source", "sync_paper_workspace_reviews", "sync_paper_workspace_rebuttal_context"):
        monkeypatch.setattr(runtime.paper_service, name, forbidden)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", forbidden)
    validate = Mock()
    monkeypatch.setattr(runtime.knowledge_service, "validate_parser_binding", validate)
    ticket = AsyncMock()
    monkeypatch.setattr(runtime, "_store_runtime_ticket", ticket)
    issued = Mock(wraps=credential_broker.issue_token)
    monkeypatch.setattr(credential_broker, "issue_token", issued)
    source = tmp_path / "proposal.typ"
    source.write_text("Uncommitted user edits", encoding="utf-8")
    return SimpleNamespace(
        runtime=runtime, owner=owner, workspace=workspace, provider=provider, run=run,
        db=db, objects=objects, forbidden=forbidden, validate=validate, ticket=ticket,
        issued=issued, source=source,
    )


@pytest.mark.parametrize("missing", [False, True])
def test_renew_endpoint_rejects_nonowners_before_issuing_credentials(endpoint, missing):
    """Neither unknown workspaces nor another owner's workspace grants a ticket."""
    from fastapi import HTTPException, Response

    if missing:
        endpoint.objects[endpoint.runtime.PaperWorkspace] = None
    else:
        endpoint.workspace.user_id = uuid.uuid4()
    response = Response()
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(endpoint.runtime.renew_runtime_session(
            endpoint.workspace.id, response, endpoint.db, endpoint.owner,
        ))
    assert rejected.value.status_code == 404
    assert "set-cookie" not in response.headers
    endpoint.ticket.assert_not_called()
    endpoint.issued.assert_not_called()
    endpoint.validate.assert_not_called()
    endpoint.forbidden.assert_not_called()


def test_renew_endpoint_rebinds_expired_channel_without_changing_running_work(endpoint, broker):
    """Authenticated reopen renews only the cookie and the existing opaque channel."""
    from fastapi import Response

    redis, _ = broker
    workspace_before, run_before = deepcopy(vars(endpoint.workspace)), deepcopy(vars(endpoint.run))
    opaque_tokens = []
    cookies = []
    for access_token in ("access-one", "access-two"):
        response = Response()
        assert asyncio.run(endpoint.runtime.renew_runtime_session(
            endpoint.workspace.id, response, endpoint.db, endpoint.owner,
        )) == {"renewed": True}
        binding = endpoint.issued.call_args.kwargs
        opaque_tokens.append(binding["token"])
        assert binding["upstream_url_template"] == endpoint.provider.base_url
        assert binding["ttl"] == endpoint.runtime._RUNTIME_SESSION_TTL_SECONDS + 600
        assert binding["task_id"] == endpoint.run.id
        assert binding["model"] == "bound-model"
        assert credential_broker.resolve_token(binding["token"])["base_url"] == f"https://gateway/{access_token}/v1"
        parsed = SimpleCookie()
        parsed.load(response.headers["set-cookie"])
        cookie = parsed[endpoint.runtime._runtime_cookie_name(endpoint.workspace.id)]
        assert cookie["httponly"] and cookie["samesite"] == "strict"
        assert cookie["path"] == endpoint.runtime._runtime_base_path(endpoint.workspace.id)
        assert int(cookie["max-age"]) == endpoint.runtime._RUNTIME_SESSION_TTL_SECONDS
        cookies.append(cookie.value)
        endpoint.ticket.assert_awaited_with(cookie.value, endpoint.workspace.id, endpoint.owner.id)
        redis.values.clear()
    assert opaque_tokens[0] == opaque_tokens[1]
    assert cookies[0] != cookies[1]
    assert vars(endpoint.workspace) == workspace_before and vars(endpoint.run) == run_before
    assert endpoint.source.read_text(encoding="utf-8") == "Uncommitted user edits"
    endpoint.forbidden.assert_not_called()


def test_renew_endpoint_checks_current_provider_access_before_renewal(endpoint):
    """A revoked model binding cannot mint a new cookie or broker capability."""
    from fastapi import HTTPException, Response

    endpoint.validate.side_effect = HTTPException(status_code=403, detail="Binding revoked")
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(endpoint.runtime.renew_runtime_session(
            endpoint.workspace.id, Response(), endpoint.db, endpoint.owner,
        ))
    assert rejected.value.status_code == 403
    endpoint.issued.assert_not_called()
    endpoint.ticket.assert_not_called()
    endpoint.forbidden.assert_not_called()


def test_renew_endpoint_does_not_mint_platform_jwts_for_custom_providers(endpoint, broker):
    """Custom channels retain their own URL and secret on authorized renewal."""
    from fastapi import Response

    _, mint = broker
    endpoint.provider.is_builtin = False
    endpoint.provider.base_url = "https://custom/v1"
    asyncio.run(endpoint.runtime.renew_runtime_session(
        endpoint.workspace.id, Response(), endpoint.db, endpoint.owner,
    ))
    binding = endpoint.issued.call_args.kwargs
    assert binding["upstream_url_template"] == ""
    payload = credential_broker.resolve_token(binding["token"])
    assert payload["base_url"] == "https://custom/v1"
    assert payload["api_key"] == "upstream-secret"
    mint.assert_not_called()
    endpoint.forbidden.assert_not_called()
