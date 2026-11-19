"""One Uvicorn listener with a server-owned, browser-independent SDK lifecycle."""

from __future__ import annotations

import asyncio
import webbrowser
from contextlib import asynccontextmanager, closing, suppress
from typing import TYPE_CHECKING, Any

from .auth import DashboardAuth, DashboardAuthMiddleware
from .helpers import dashboard_url, should_open_browser

if TYPE_CHECKING:
    from llm4ad.gateway.sdk.controller import GatewayController


def create_dashboard_app(controller: GatewayController, *, password: str | None = None, mount_ui: bool = True) -> Any:
    """Create the ASGI app without starting a listener or gateway.

    Args:
        controller: Shared SDK administration controller.
        password: Optional explicit UI password; independent of gateway identity.
        mount_ui: Disable only for HTTP boundary tests without NiceGUI installed.

    Returns:
        A FastAPI application with UI authentication around all mounted routes.

    Raises:
        RuntimeError: If the dashboard's optional dependencies are not installed.
    """
    try:
        from fastapi import FastAPI

        from .routes import register_routes

        if mount_ui:
            from nicegui import ui

            from .pages import DashboardPages
    except ImportError as error:
        raise RuntimeError("Install the gateway dashboard optional dependencies to use the browser UI") from error

    auth = DashboardAuth.load(
        controller.bootstrap_root,
        host=controller.settings.ui_host,
        password=password,
        environment=controller.environment,
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            await controller.start()
            yield
        finally:
            await controller.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.dashboard_auth = auth
    app.state.controller = controller
    register_routes(app, controller, auth)
    if mount_ui:
        DashboardPages(controller, ui).register()
        theme = controller.settings.theme
        ui.run_with(
            app,
            title="LLM4AD · Gateway",
            dark=None if theme == "auto" else theme == "dark",
            tailwind=False,
            prod_js=True,
            on_air=None,
            show_welcome_message=False,
        )
    app.add_middleware(DashboardAuthMiddleware, auth=auth)
    return app


async def run_dashboard(controller: GatewayController) -> None:
    """Run a single local-first server until actual ASGI shutdown.

    Args:
        controller: The already configured SDK controller, started only by lifespan.
    """
    app = create_dashboard_app(controller)
    import uvicorn

    settings = controller.settings
    config = uvicorn.Config(
        app,
        host=settings.ui_host,
        port=settings.ui_port,
        reload=False,
        workers=1,
        proxy_headers=False,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    with closing(config.bind_socket()) as listener:
        url = dashboard_url(settings.ui_host, listener.getsockname()[1])
        print(f"LLM4AD Gateway dashboard: {url}", flush=True)
        auth = app.state.dashboard_auth
        if auth.required and auth.initial_password:
            print(f"Initial dashboard password: {auth.initial_password}", flush=True)
        auth.initial_password = None

        async def open_when_ready() -> None:
            while not server.started and not server.should_exit:
                await asyncio.sleep(0.05)
            if server.started and should_open_browser(settings.open_browser):
                await asyncio.to_thread(webbrowser.open, url)

        opener = asyncio.create_task(open_when_ready(), name="gateway-dashboard-browser")
        try:
            await server.serve(sockets=[listener])
            if not server.started:
                raise ValueError("Gateway dashboard startup failed; inspect the startup error above")
        finally:
            opener.cancel()
            with suppress(asyncio.CancelledError):
                await opener
