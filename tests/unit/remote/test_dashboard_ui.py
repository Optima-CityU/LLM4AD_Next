"""Dashboard boundaries work independently of NiceGUI and gateway worker processes."""

import asyncio
import json
import os
import stat
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from llm4ad.gateway.ui.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    DashboardAuth,
    DashboardAuthMiddleware,
    same_origin,
)
from llm4ad.gateway.ui.helpers import (
    PAGES,
    SETTINGS_FIELDS,
    STATES,
    artifact_url,
    dashboard_url,
    is_loopback,
    job_url,
    pretty_json,
    resolve_language,
    settings_payload,
    settings_values,
    should_open_browser,
    state_color,
)
from llm4ad.gateway.ui.presentation import CANCELLATION_WARNING, translate
from llm4ad.gateway.ui.preview import PREVIEW_LIMIT, can_preview, read_preview

pytestmark = pytest.mark.unit


def make_auth(tmp_path, *, host="0.0.0.0", password="independent-ui-password"):
    """Create UI credentials without consulting ambient gateway configuration."""
    return DashboardAuth.load(tmp_path, host=host, password=password, environment={})


def test_import_does_not_require_nicegui_or_fastapi():
    """Core auth helpers and the CLI entry import without optional frontend packages."""
    source = """
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'nicegui', 'fastapi', 'uvicorn'}:
            raise ImportError(fullname)
sys.meta_path.insert(0, BlockOptional())
from llm4ad.gateway.ui import run_dashboard
from llm4ad.gateway.ui.helpers import PAGES
assert callable(run_dashboard) and len(PAGES) == 3
"""
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_private_credentials_are_stable_and_independent(tmp_path):
    """Only salted UI password hashes and an independent session key are persisted."""
    auth = make_auth(tmp_path)
    path = tmp_path / "ui-auth" / "credentials.json"
    data = json.loads(path.read_text())
    assert "independent-ui-password" not in path.read_text()
    assert set(data) == {"salt", "password_hash", "session_secret", "password_configured"}
    assert auth.verify_password("independent-ui-password")
    assert not auth.verify_password("gateway-secret")
    assert "independent-ui-password" not in repr(auth)
    assert make_auth(tmp_path).valid_session(auth.issue_session())
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_password_rotation_revokes_old_sessions(tmp_path):
    """An explicitly changed UI password replaces the signing secret too."""
    first = make_auth(tmp_path)
    token = first.issue_session()
    second = make_auth(tmp_path, password="replacement")
    assert second.verify_password("replacement")
    assert not second.valid_session(token)


def test_remote_generation_and_local_default(tmp_path):
    """Remote listeners generate a one-time password while local desktop access stays local."""
    local = DashboardAuth.load(tmp_path / "local", host="127.0.0.1", environment={})
    assert not local.required
    remote = DashboardAuth.load(tmp_path / "remote", host="::", environment={})
    assert remote.required and remote.initial_password
    assert remote.verify_password(remote.initial_password)
    reloaded = DashboardAuth.load(tmp_path / "remote", host="::", environment={})
    assert reloaded.initial_password is None
    assert reloaded.valid_session(remote.issue_session())


def test_only_ui_environment_password_is_used(tmp_path):
    """Gateway secrets and other environment entries never become dashboard credentials."""
    auth = DashboardAuth.load(
        tmp_path,
        host="0.0.0.0",
        environment={
            "LLM4AD_GATEWAY_SECRET": "gateway-secret",
            "LLM4AD_GATEWAY_UI_PASSWORD": "ui-only",
        },
    )
    assert auth.verify_password("ui-only")
    assert not auth.verify_password("gateway-secret")
    assert auth.initial_password is None


def test_local_to_remote_transition_issues_an_accessible_password(tmp_path):
    """A password never shown in local mode is replaced on first remote launch."""
    local = DashboardAuth.load(tmp_path, host="127.0.0.1", environment={})
    assert not local.required
    remote = DashboardAuth.load(tmp_path, host="0.0.0.0", environment={})
    assert remote.required and remote.initial_password
    assert remote.verify_password(remote.initial_password)


def test_symlink_credential_directory_is_rejected(tmp_path):
    """UI secrets cannot be redirected into an unrelated directory."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "ui-auth").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        make_auth(tmp_path)
    assert not list(target.iterdir())


def test_signed_session_expiry_tamper_future_and_logout(tmp_path):
    """Expired, modified, future and revoked sessions never authenticate controls."""
    auth = make_auth(tmp_path)
    token = auth.issue_session(now=1000)
    assert auth.valid_session(token, now=1001)
    assert not auth.valid_session(token, now=999)
    assert not auth.valid_session(token, now=1000 + SESSION_SECONDS)
    assert not auth.valid_session(token[:-1] + ("a" if token[-1] != "a" else "b"), now=1001)
    assert not auth.valid_session("malformed")
    current = auth.issue_session()
    auth.revoke(current)
    assert not auth.valid_session(current)


async def boundary(
    auth,
    path="/",
    *,
    websocket=False,
    token="",
    origin=None,
    host="127.0.0.1:8768",
    client="127.0.0.1",
):
    """Exercise the ASGI boundary without FastAPI or NiceGUI dependencies."""
    called, sent = [], []

    async def app(scope, receive, send):
        called.append(scope)

    async def receive():
        return {"type": "websocket.connect" if websocket else "http.request"}

    async def send(message):
        sent.append(message)

    headers = [(b"host", host.encode())]
    if token:
        headers.append((b"cookie", f"{COOKIE_NAME}={token}".encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    await DashboardAuthMiddleware(app, auth=auth)(
        {
            "type": "websocket" if websocket else "http",
            "path": path,
            "method": "GET",
            "headers": headers,
            "client": (client, 1234),
        },
        receive,
        send,
    )
    return called, sent


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/settings",
        "/artifacts/a.txt",
        "/_nicegui/1/socket.io/",
        "/_nicegui/client/download/file",
    ],
)
async def test_remote_session_guards_pages_socket_polling_and_downloads(tmp_path, path):
    """There is no blanket NiceGUI bypass at the authentication boundary."""
    auth = make_auth(tmp_path)
    called, sent = await boundary(auth, path)
    assert not called
    assert sent[0]["status"] in {303, 401}
    called, sent = await boundary(auth, path, token=auth.issue_session())
    assert called and not sent


async def test_websocket_requires_session_and_same_origin(tmp_path):
    """Direct Socket.IO WebSocket connections cannot bypass dashboard login."""
    auth = make_auth(tmp_path)
    called, sent = await boundary(auth, "/_nicegui/1/socket.io/", websocket=True)
    assert not called and sent == [{"type": "websocket.close", "code": 1008}]
    called, sent = await boundary(auth, websocket=True, token=auth.issue_session(), origin="https://evil.example")
    assert not called and sent[0]["code"] == 1008
    called, sent = await boundary(auth, websocket=True, token=auth.issue_session(), origin="http://127.0.0.1:8768")
    assert called and not sent


async def test_logged_out_socket_rejects_subsequent_control_frames(tmp_path):
    """A previously accepted socket rechecks its session on every incoming message."""
    auth = make_auth(tmp_path)
    token = auth.issue_session()
    received, sent = [], []

    async def app(scope, receive, send):
        auth.revoke(token)
        received.append(await receive())

    async def receive():
        return {"type": "websocket.receive", "text": "control"}

    async def send(message):
        sent.append(message)

    await DashboardAuthMiddleware(app, auth=auth)(
        {
            "type": "websocket",
            "path": "/_nicegui/1/socket.io/",
            "headers": [
                (b"host", b"127.0.0.1:8768"),
                (b"cookie", f"{COOKIE_NAME}={token}".encode()),
            ],
        },
        receive,
        send,
    )
    assert received == [{"type": "websocket.disconnect", "code": 1008}]
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def test_local_access_still_rejects_cross_site_and_remote_clients(tmp_path):
    """Local passwordless mode is restricted to literal loopback peers and hosts."""
    auth = DashboardAuth.load(tmp_path, host="127.0.0.1", environment={})
    assert (await boundary(auth))[0]
    assert not (await boundary(auth, origin="http://evil.example"))[0]
    assert not (await boundary(auth, host="evil.example:8768"))[0]
    assert not (await boundary(auth, client="10.0.0.2"))[0]
    assert (await boundary(auth, "/login"))[0]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("192.168.1.1", False),
        ("localhost.evil", False),
    ],
)
def test_loopback_is_not_resolved_via_dns(host, expected):
    """Remote bind addresses and hostnames never receive loopback exemptions."""
    assert is_loopback(host) is expected


def test_browser_open_policy_and_urls():
    """Automatic opening requires a desktop terminal and excludes SSH or CI."""
    assert should_open_browser(None, environ={"DISPLAY": ":1"}, interactive=True)
    assert not should_open_browser(None, environ={"DISPLAY": ":1", "SSH_CONNECTION": "present"}, interactive=True)
    assert not should_open_browser(None, environ={"DISPLAY": ":1"}, interactive=False)
    assert should_open_browser(True, environ={}, interactive=False)
    assert not should_open_browser(False, environ={"DISPLAY": ":1"}, interactive=True)
    assert dashboard_url("::", 8768) == "http://[::1]:8768"
    assert dashboard_url("0.0.0.0", 8768) == "http://127.0.0.1:8768"


def test_settings_form_fields_and_hgamey_capacity():
    """Every universal field is selectable, and no device or secret fields are forwarded."""
    values = {
        "server": "https://llm4ad-next.cn",
        "name": "local-evaluator",
        "allowed_users": "one, two\none",
        "adapter": "hgamey",
        "evaluator": None,
        "state_dir": "state",
        "concurrency": 8,
        "cleanup_enabled": True,
        "cleanup_interval": 30,
        "cleanup_keep_seconds": 0,
        "local_log_keep_seconds": 604800,
        "ui_host": "127.0.0.1",
        "ui_port": 8768.0,
        "open_browser": None,
        "language": "auto",
        "theme": "auto",
    }
    result = settings_payload({**values, "gateway_secret": "never", "device_id": "never"})
    assert set(result) == set(SETTINGS_FIELDS)
    assert result["allowed_users"] == ["one", "two"]
    assert result["concurrency"] == 1
    assert result["ui_port"] == 8768
    assert settings_values(SimpleNamespace(**values, secret="never")) == values


@pytest.mark.parametrize(
    "values",
    [
        {"concurrency": 1.5},
        {"ui_port": 65536},
        {"cleanup_interval": 0},
        {"cleanup_keep_seconds": float("nan")},
        {"theme": "unknown"},
        {"server": "https://user:password@example.com"},
        {"open_browser": "false"},
    ],
)
def test_invalid_settings_are_rejected(values):
    """Invalid numeric, enum, boolean and credential-bearing form values fail before apply."""
    with pytest.raises(ValueError):
        settings_payload(values)


def test_i18n_pages_and_real_states():
    """Navigation, cancellation wording and badges use real controller states."""
    assert [path for path, _ in PAGES] == ["/", "/tasks", "/settings"]
    assert all(translate("zh", label) != label for _, label in PAGES)
    assert resolve_language("auto", "en;q=0.4,zh-CN;q=0.9") == "zh"
    assert resolve_language("en", "zh-CN") == "en"
    assert resolve_language("auto", "zh;q=0,en;q=0.5") == "en"
    assert "parent evolution" in CANCELLATION_WARNING
    assert "skip" in CANCELLATION_WARNING
    assert {"pending", "success", "cancelling", "recovery_required", "pending_upload"} <= set(STATES)
    assert state_color("unexpected") == "grey"
    assert state_color("failed") == "negative"
    assert translate("en", "configuration_required") == "Setup required"
    assert "_" not in translate("zh", "draining_for_settings")


async def test_plain_text_preview_is_bounded_and_never_executes_markup(tmp_path):
    """Preview bytes remain plain text while original files are left intact."""
    path = tmp_path / "result.md"
    source = "<script>alert('escaped')</script>\n# report\n" + "x" * PREVIEW_LIMIT
    path.write_text(source)
    text, truncated = await read_preview(path)
    assert truncated and text.startswith("<script>")
    assert len(text.encode()) == PREVIEW_LIMIT
    assert path.read_text() == source
    assert can_preview("result.md")
    assert can_preview("result", "application/json")
    assert not can_preview("capture.png", "image/png")
    path.write_bytes(b"binary\x00file")
    with pytest.raises(ValueError, match="Binary"):
        await read_preview(path)


def test_opaque_job_and_artifact_links_and_redaction():
    """Identifiers remain encoded arguments and diagnostic secrets are not rendered."""
    job = {"binding": "a&b", "job_id": "../x?y", "attempt_id": "#attempt"}
    query = parse_qs(urlsplit(job_url(job)).query)
    assert query == {key: [value] for key, value in job.items()}
    url = artifact_url(*job.values(), "folder/evidence file.json")
    assert url.startswith("/artifacts/folder%2Fevidence%20file.json?")
    assert "never" not in pretty_json({"metadata": {"api_key": "never", "secret": "never"}, "score": 1})
    assert same_origin({"host": "[::1]:8768", "origin": "http://[::1]:8768"})
    assert not same_origin({"host": "127.0.0.1:8768", "origin": "http://127.0.0.1:8769"})
    assert same_origin({"host": "gateway.example", "origin": "https://gateway.example"})
    assert not same_origin({"host": "gateway.example:8768", "origin": "https://gateway.example"})
    assert not same_origin({"host": "gateway.example", "origin": "https://other.example"})


async def test_http_routes_and_lifespan_are_server_owned(tmp_path):
    """Login, downloads and repeated requests never create additional gateway services."""
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    from llm4ad.gateway.ui.server import create_dashboard_app

    class Controller:
        settings = SimpleNamespace(ui_host="0.0.0.0", language="en")
        bootstrap_root = tmp_path
        environment = {"LLM4AD_GATEWAY_UI_PASSWORD": "independent-ui-password"}

        def __init__(self):
            self.starts = self.closes = 0
            self.lookups = []

        async def start(self):
            self.starts += 1

        async def close(self):
            self.closes += 1

        async def artifact_path(self, *identity):
            self.lookups.append(identity)
            if identity[-1] != "evidence.txt":
                raise LookupError()
            return tmp_path / "evidence.txt"

        async def local_log_snapshot(self, *identity, purpose="task"):
            self.lookups.append((*identity, purpose))
            if identity[-1] != "compiler/foo.log":
                raise LookupError()
            snapshot = tmp_path / "private-snapshot.log"
            snapshot.write_text("compiler stdout")
            snapshot.chmod(0o600)
            return snapshot

    controller = Controller()
    (tmp_path / "evidence.txt").write_text("evidence")
    app = create_dashboard_app(controller, mount_ui=False)
    assert controller.starts == 0
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.get("/login")).status_code == 200
            assert (await client.get("/artifacts/evidence.txt?binding=b&job_id=j&attempt_id=a")).status_code == 303
            response = await client.post("/login", data={"password": "independent-ui-password"})
            assert response.status_code == 303
            assert "httponly" in response.headers["set-cookie"].lower()
            response = await client.get("/artifacts/evidence.txt?binding=b&job_id=j&attempt_id=a")
            assert response.status_code == 200 and response.text == "evidence"
            assert "attachment" in response.headers["content-disposition"]
            assert controller.lookups == [("b", "j", "a", "evidence.txt")]
            response = await client.get("/local-logs/compiler%2Ffoo.log?binding=b&job_id=j&attempt_id=a&purpose=startup")
            assert response.status_code == 200 and response.text == "compiler stdout"
            assert controller.lookups[-1] == ("b", "j", "a", "compiler/foo.log", "startup")
            assert not (tmp_path / "private-snapshot.log").exists()
            for source in ("%2Fetc%2Fpasswd", "compiler%2F..%2Ffoo.log", "compiler%2F.%2Ffoo.log", "compiler%5Cfoo.log"):
                assert (await client.get(f"/local-logs/{source}?binding=b&job_id=j&attempt_id=a")).status_code == 404
            assert (await client.get("/artifacts/missing?binding=b&job_id=j&attempt_id=a")).status_code == 404
            assert (await client.get("/_nicegui/1/socket.io/", headers={"origin": "https://evil.example"})).status_code == 403
            assert (await client.post("/logout")).status_code == 303
            assert (await client.get("/artifacts/evidence.txt?binding=b&job_id=j&attempt_id=a")).status_code == 303
        assert controller.starts == 1 and controller.closes == 0
    assert controller.closes == 1


async def test_control_directory_failure_prevents_starting_an_unowned_dashboard(tmp_path):
    """A controller lease failure aborts ASGI startup instead of leaving unusable controls."""
    pytest.importorskip("fastapi")
    from llm4ad.gateway.ui.server import create_dashboard_app

    class Controller:
        settings = SimpleNamespace(ui_host="127.0.0.1", language="en")
        bootstrap_root = tmp_path
        environment = {}
        closes = 0

        async def start(self):
            raise RuntimeError("Synthetic control directory already owned")

        async def close(self):
            self.closes += 1

    controller = Controller()
    app = create_dashboard_app(controller, mount_ui=False)
    with pytest.raises(RuntimeError, match="already owned"):
        async with app.router.lifespan_context(app):
            pytest.fail("The dashboard must not accept traffic without control ownership")
    assert controller.closes == 1
