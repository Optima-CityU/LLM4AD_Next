"""Small authenticated HTTP surface around the mounted NiceGUI application."""

from __future__ import annotations

import asyncio
import html
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .auth import COOKIE_NAME, SESSION_SECONDS, DashboardAuth, session_cookie
from .helpers import legacy_task_target, resolve_language
from .presentation import CSS, translate

if TYPE_CHECKING:
    from llm4ad.gateway.sdk.controller import GatewayController


class _TemporaryLogResponse(FileResponse):
    """Remove a private log snapshot even if streaming ends with a disconnected client."""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def register_routes(app: FastAPI, controller: GatewayController, auth: DashboardAuth) -> None:
    """Register login and exact attempt artifact lookup before the NiceGUI mount."""
    failures: dict[str, list[float]] = {}

    @app.get("/task", include_in_schema=False)
    async def legacy_task(request: Request):
        return RedirectResponse(legacy_task_target(dict(request.query_params)), status_code=303)

    def login_html(request: Request, message: str = "", status_code: int = 200) -> HTMLResponse:
        language = resolve_language(controller.settings.language, request.headers.get("accept-language", ""))

        def tr(text: str) -> str:
            return html.escape(translate(language, text))

        response = HTMLResponse(
            f"""<!doctype html><html lang="{language}"><head>
            <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
            <meta name="color-scheme" content="light dark"><title>LLM4AD · Gateway</title>
            <style>{CSS}
            @media(prefers-color-scheme:dark){{:root{{--lab-bg:#141c1b;--lab-panel:#1d2725;--lab-ink:#e4ebe5;
            --lab-muted:#a2b1a7;--lab-line:#35423d}}}}
            *{{box-sizing:border-box}}main{{max-width:440px;margin:12vh auto;padding:24px}}
            form{{display:grid;gap:18px}}input,button{{font:inherit;padding:12px;border-radius:5px}}
            input{{width:100%;border:1px solid var(--lab-line);background:var(--lab-bg);color:var(--lab-ink)}}
            button{{background:#277a6c;color:white;border:0;cursor:pointer}}label{{display:grid;gap:8px}}
            a{{color:#277a6c}}.error{{color:#b64e42}}:focus-visible{{outline:2px solid #277a6c;outline-offset:3px}}
            </style></head><body><main><section class="lab-panel">
            <p class="lab-eyebrow">{tr("Evaluation gateway")}</p><h1 class="lab-brand">LLM4AD</h1>
            <p>{tr("Independent dashboard access")}</p><p role="alert" class="error">{tr(message)}</p>
            <form method="post" action="/login"><label for="password">{tr("Dashboard password")}
            <input id="password" name="password" type="password" autocomplete="current-password"
            required autofocus maxlength="4096">
            </label><button type="submit">{tr("Sign in")}</button></form></section></main></body></html>""",
            status_code=status_code,
        )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
                ),
            }
        )
        return response

    @app.get("/login", include_in_schema=False)
    async def login(request: Request):
        if not auth.required or auth.valid_session(session_cookie(dict(request.headers))):
            return RedirectResponse("/", status_code=303)
        return login_html(request)

    @app.post("/login", include_in_schema=False)
    async def submit_login(request: Request):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        recent = [stamp for stamp in failures.get(client, []) if now - stamp < 60]
        if len(recent) >= 5:
            response = login_html(request, "Try again shortly", 429)
            response.headers["Retry-After"] = "60"
            return response
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise HTTPException(413, "Login request too large")
        try:
            values = parse_qs(body.decode("utf-8"), max_num_fields=4)
            password = values.get("password", [""])[0]
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid login request") from None
        if not await asyncio.to_thread(auth.verify_password, password):
            if len(failures) >= 256 and client not in failures:
                failures.pop(next(iter(failures)))
            failures[client] = [*recent, now]
            return login_html(request, "Incorrect password", 401)
        failures.pop(client, None)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            auth.issue_session(),
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        auth.revoke(session_cookie(dict(request.headers)))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/artifacts/{name:path}", include_in_schema=False)
    async def download(name: str, binding: str, job_id: str, attempt_id: str):
        if not name or "\\" in name or "\x00" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
            raise HTTPException(404, "Artifact not found")
        try:
            path = await controller.artifact_path(binding, job_id, attempt_id, name)
        except (OSError, ValueError, LookupError):
            raise HTTPException(404, "Artifact unavailable for this execution attempt") from None
        return FileResponse(
            path,
            filename=PurePosixPath(name).name,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/local-logs/{source:path}", include_in_schema=False)
    async def download_local_log(source: str, binding: str, job_id: str, attempt_id: str, purpose: str = "task"):
        if (
            purpose not in {"task", "startup"}
            or not source
            or "\x00" in source
            or "\\" in source
            or PurePosixPath(source).is_absolute()
            or any(part in {"", ".", ".."} for part in source.split("/"))
        ):
            raise HTTPException(404, "Local log source unavailable")
        try:
            snapshot = Path(await controller.local_log_snapshot(binding, job_id, attempt_id, source, purpose=purpose))
        except (OSError, ValueError, LookupError):
            raise HTTPException(404, "Local log source unavailable for this execution attempt") from None
        return _TemporaryLogResponse(
            snapshot,
            filename=f"{PurePosixPath(source).name}.log",
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
