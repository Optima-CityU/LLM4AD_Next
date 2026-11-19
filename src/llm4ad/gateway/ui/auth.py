"""Independent UI credentials and an HTTP/WebSocket authentication boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .helpers import is_loopback

COOKIE_NAME = "llm4ad_ui_session"
SESSION_SECONDS = 12 * 60 * 60
_ITERATIONS = 300_000


def _password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()


def _write_private(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(repr=False)
class DashboardAuth:
    """Manage signed UI sessions without reading the gateway shared secret."""

    salt: str = field(repr=False)
    password_hash: str = field(repr=False)
    session_secret: str = field(repr=False)
    required: bool = True
    initial_password: str | None = field(default=None, repr=False)
    revoked: dict[str, float] = field(default_factory=dict, repr=False)

    @classmethod
    def load(
        cls,
        bootstrap_root: Path,
        *,
        host: str,
        password: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> DashboardAuth:
        """Persist owner-only credentials independently of gateway credentials.

        Args:
            bootstrap_root: Stable controller bootstrap directory.
            host: Dashboard listener address.
            password: Explicit UI password, otherwise the dedicated UI environment variable.
            environment: Controller environment; only the dedicated UI password is read.

        Returns:
            UI authentication state; a generated password is available only once.

        Raises:
            ValueError: If an explicit password is empty or a credential file is invalid.
        """
        if password is None:
            password = (os.environ if environment is None else environment).get("LLM4AD_GATEWAY_UI_PASSWORD")
        if password is not None and not password.strip():
            raise ValueError("The dashboard password must not be empty")
        required = not is_loopback(host) or password is not None
        directory = Path(bootstrap_root) / "ui-auth"
        if directory.is_symlink():
            raise ValueError("The UI credentials directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        path = directory / "credentials.json"
        initial_password = None
        data = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            pass
        else:
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Invalid UI credential file")
                os.chmod(path, 0o600)
                data = json.load(stream)
            if not isinstance(data, dict) or any(
                not isinstance(data.get(key), str) or len(data[key]) != length
                for key, length in (("salt", 32), ("password_hash", 64), ("session_secret", 64))
            ):
                raise ValueError("Invalid UI credential file")
            try:
                for key in ("salt", "password_hash", "session_secret"):
                    bytes.fromhex(data[key])
            except ValueError as error:
                raise ValueError("Invalid UI credential file") from error
        if (
            data is None
            or (required and not data.get("password_configured", False))
            or (password is not None and not hmac.compare_digest(_password_hash(password, data["salt"]), data["password_hash"]))
        ):
            if password is None:
                password = initial_password = secrets.token_urlsafe(24)
            salt = secrets.token_hex(16)
            data = {
                "salt": salt,
                "password_hash": _password_hash(password, salt),
                "session_secret": secrets.token_hex(32),
                "password_configured": required,
            }
            _write_private(path, data)
        return cls(
            **{key: data[key] for key in ("salt", "password_hash", "session_secret")},
            required=required,
            initial_password=initial_password,
        )

    def verify_password(self, password: str) -> bool:
        """Check a password using the persisted PBKDF2 digest."""
        return len(password) <= 4096 and hmac.compare_digest(_password_hash(password, self.salt), self.password_hash)

    def issue_session(self, *, now: float | None = None) -> str:
        """Issue a bounded session signed with the independent UI secret."""
        payload = f"{int(time.time() if now is None else now)}.{secrets.token_hex(16)}"
        signature = hmac.new(bytes.fromhex(self.session_secret), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def valid_session(self, token: str, *, now: float | None = None) -> bool:
        """Reject expired, forged, future-dated or explicitly revoked sessions."""
        now = time.time() if now is None else now
        if not token or len(token) > 160:
            return False
        try:
            issued, nonce, signature = token.split(".")
            age = now - int(issued)
        except (ValueError, TypeError):
            return False
        if not 0 <= age < SESSION_SECONDS or len(nonce) != 32 or token in self.revoked:
            return False
        expected = hmac.new(bytes.fromhex(self.session_secret), f"{issued}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def revoke(self, token: str) -> None:
        """Invalidate a session, including subsequent events on its open socket."""
        now = time.time()
        self.revoked = {key: expiry for key, expiry in self.revoked.items() if expiry > now}
        if self.valid_session(token):
            self.revoked[token] = int(token.split(".")[0]) + SESSION_SECONDS


def session_cookie(headers: dict[str, str]) -> str:
    """Read only the UI session cookie from an ASGI header mapping."""
    try:
        cookies: SimpleCookie = SimpleCookie()
        cookies.load(headers.get("cookie", ""))
        return cookies[COOKIE_NAME].value if COOKIE_NAME in cookies else ""
    except Exception:
        return ""


def same_origin(headers: dict[str, str]) -> bool:
    """Compare browser origins to the listener authority to prevent cross-site controls."""
    origin = headers.get("origin")
    if not origin:
        return True
    try:
        source = urlsplit(origin)
        target = urlsplit("//" + headers.get("host", ""))
        return (
            source.scheme in {"http", "https"}
            and source.hostname == target.hostname
            and (source.port or (443 if source.scheme == "https" else 80))
            == (target.port or (443 if source.scheme == "https" else 80))
        )
    except ValueError:
        return False


class DashboardAuthMiddleware:
    """Guard the entire NiceGUI mount, including Socket.IO and downloads."""

    def __init__(self, app: Callable, *, auth: DashboardAuth):
        """Wrap the parent ASGI application with UI session enforcement."""
        self.app, self.auth = app, auth

    async def __call__(self, scope: dict, receive: Callable[..., Awaitable], send: Callable[..., Awaitable]) -> None:
        """Authenticate HTTP requests and every incoming WebSocket control frame."""
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        websocket = scope["type"] == "websocket"
        try:
            host = urlsplit("//" + headers.get("host", "")).hostname or ""
        except ValueError:
            host = ""
        client = scope.get("client") or ("", 0)
        local = is_loopback(host) and is_loopback(client[0])
        token = session_cookie(headers)
        needs_session = self.auth.required or not local
        authenticated = not needs_session or self.auth.valid_session(token)
        origin_ok = same_origin(headers) and headers.get("sec-fetch-site") != "cross-site"
        public = scope.get("path") == "/login" and not websocket
        if not origin_ok or not authenticated and not public:
            if websocket:
                await send({"type": "websocket.close", "code": 1008})
            else:
                redirect = origin_ok and scope.get("method") == "GET" and not scope.get("path", "").startswith("/_nicegui/")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 303 if redirect else 401 if origin_ok else 403,
                        "headers": [(b"location", b"/login"), (b"cache-control", b"no-store")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Dashboard authentication required"})
            return

        async def protected_receive() -> dict[str, Any]:
            message = await receive()
            if websocket and message["type"] != "websocket.disconnect" and needs_session and not self.auth.valid_session(token):
                await send({"type": "websocket.close", "code": 1008})
                return {"type": "websocket.disconnect", "code": 1008}
            return message

        await self.app(scope, protected_receive if websocket else receive, send)
