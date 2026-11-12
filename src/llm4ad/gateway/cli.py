"""Start installed gateway adapters directly, without generating a project."""

import asyncio
import importlib.util
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from llm4ad.gateway.configuration import flag
from llm4ad.gateway.sdk.journal import Journal
from llm4ad.gateway.sdk.lifecycle import resolve_execution
from llm4ad.gateway.sdk.service import create_service, run_service
from llm4ad.gateway.settings import ADAPTERS, load_settings

app = typer.Typer(help="Run an installed adapter or your custom evaluator directly.")


def evaluator_spec(adapter: str, evaluator: str | None) -> str:
    """Resolve one explicit custom evaluator or an installed built-in adapter."""
    if adapter == "custom":
        if not evaluator:
            raise ValueError("Set --evaluator FILE.py:ClassName for a custom gateway")
        return evaluator
    if adapter not in ADAPTERS:
        raise ValueError("adapter must be custom, example or hgamey")
    if evaluator:
        raise ValueError("Choose --adapter or --evaluator, not both")
    return ADAPTERS[adapter]


@contextmanager
def _runtime_environment(environment):
    """Share explicit runtime configuration with adapters and spawned workers."""
    previous = dict(os.environ)
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


@app.command("init")
def init_workspace(
    directory: Annotated[
        Path, typer.Argument(help="Optional editable example and Docker workspace")
    ],
    server: str | None = None,
    name: str = "local-evaluator",
    allow_user: Annotated[list[str] | None, typer.Option()] = None,
):
    """Optionally create starter files; direct SDK startup does not require them."""
    from llm4ad.gateway.scaffold import initialize

    try:
        created = initialize(directory, server=server, name=name, allowed_users=allow_user or [])
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Created {len(created)} optional starter files in {directory.resolve()}")


@app.command()
def start(
    server: Annotated[str | None, typer.Option()] = None,
    name: Annotated[str | None, typer.Option()] = None,
    adapter: Annotated[str | None, typer.Option(help="custom, example or hgamey")] = None,
    evaluator: Annotated[
        str | None, typer.Option(help="Custom FILE.py:Class or package.module:Class")
    ] = None,
    allow_user: Annotated[list[str] | None, typer.Option()] = None,
    state_dir: Annotated[Path | None, typer.Option()] = None,
    concurrency: Annotated[int | None, typer.Option(min=1)] = None,
    config: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, help="Optional JSON environment settings"),
    ] = None,
    ui: Annotated[
        bool | None, typer.Option("--ui/--no-ui", help="Optional browser control panel")
    ] = None,
    open_browser: Annotated[bool | None, typer.Option("--open-browser/--no-open-browser")] = None,
    ui_host: str | None = None,
    ui_port: Annotated[int | None, typer.Option(min=0, max=65535)] = None,
):
    """Start the SDK gateway or the packaged HGamey gateway/executor service."""
    try:
        environment = dict(os.environ)
        values = {}
        if config:
            values = json.loads(config.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError("config must be a JSON object of environment settings")
            environment.update(
                {
                    key: json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
                    for key, value in values.items()
                }
            )
        root = (
            (
                state_dir
                or Path(
                    environment.get("LLM4AD_GATEWAY_STATE_DIR")
                    or environment.get("HGAMEY_RUNTIME_DIR", ".llm4ad-gateway")
                )
            )
            .expanduser()
            .resolve()
        )
        settings = load_settings(
            root,
            environment,
            config=values,
            explicit={
                "server": server,
                "name": name,
                "adapter": adapter or ("custom" if evaluator else None),
                "evaluator": evaluator,
                "allowed_users": allow_user,
                "state_dir": str(state_dir) if state_dir else None,
                "concurrency": concurrency,
                "open_browser": open_browser,
                "ui_host": ui_host,
                "ui_port": ui_port,
            },
        )
        available = importlib.util.find_spec("nicegui") is not None
        enabled = ui if ui is not None else flag(environment, "LLM4AD_GATEWAY_UI", available)
        if enabled:
            if not available:
                raise ValueError(
                    'Install the optional UI with: pip install "llm4ad-next[gateway-ui]"'
                )
            from llm4ad.gateway.sdk.controller import GatewayController
            from llm4ad.gateway.ui import run_dashboard

            controller = GatewayController(settings, root, environment=environment)
            asyncio.run(run_dashboard(controller))
            raise typer.Exit(code=0)
        if (root / "dashboard-settings.json").is_file():
            from llm4ad.gateway.sdk.controller import GatewayController, run_headless

            controller = GatewayController(settings, root, environment=environment)
            raise typer.Exit(code=asyncio.run(run_headless(controller)))
        settings.require_ready()
        with _runtime_environment(settings.environment(environment)):
            service = create_service(
                settings.environment(environment),
                evaluator_spec(settings.adapter, settings.evaluator),
            )
            typer.echo(f"Starting evaluator gateway; state: {service.options['root']}")
            raise typer.Exit(code=asyncio.run(run_service(service)))
    except sqlite3.OperationalError as exc:
        raise typer.BadParameter("State directory is in use or cannot be locked") from exc
    except (ValueError, OSError, ImportError, AttributeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    except (KeyboardInterrupt, asyncio.CancelledError):
        typer.echo("Stopped; accepted work remains in the journal for recovery.")


@app.command()
def source(adapter: str = "example"):
    """Show installed source and deployment paths without creating files or running code."""
    if adapter not in {"example", "hgamey", "custom"}:
        raise typer.BadParameter("adapter must be custom, example or hgamey")
    root = Path(__file__).parent
    case = root / "examples" / ("hgamey" if adapter == "hgamey" else "basic")
    paths = {
        "root": str(root),
        "sdk": str(root / "sdk"),
        "templates": str(root / "templates/custom"),
        **{name: str(root / "sdk" / f"{name}.py") for name in ("service", "health", "retention")},
    }
    if adapter == "custom":
        paths["adapter"] = "--evaluator FILE.py:Class"
    else:
        paths.update(example=str(case), adapter=str(case), deployment=str(case / "deployment"))
        if adapter == "hgamey":
            paths.update(
                {
                    name: str(case / name)
                    for name in ("compilation", "execution", "protocol", "native")
                }
            )
            paths.update({name: str(case / f"{name}.py") for name in ("runtime", "evidence")})
    typer.echo(json.dumps(paths, indent=2, ensure_ascii=False))


@app.command()
def check(
    adapter: str = "custom",
    evaluator: str | None = None,
    candidate: Annotated[Path | None, typer.Option(exists=True, file_okay=False)] = None,
    state_dir: Path = Path(".llm4ad-gateway-check"),
    timeout: Annotated[float, typer.Option(min=0.001)] = 60,
):
    """Run a real isolated local evaluation; the example needs no project files."""
    from llm4ad.gateway.sdk.check import check_evaluator

    try:
        spec = evaluator_spec(adapter, evaluator)
        if candidate is None:
            if adapter != "example":
                raise ValueError("Provide --candidate for this evaluator")
            candidate = Path(__file__).parent / "examples/basic/candidate"
        result = check_evaluator(spec, candidate, state_dir, timeout)
    except (OSError, ValueError, sqlite3.OperationalError, ImportError, AttributeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, allow_nan=False))
    raise typer.Exit(code=0 if result["ok"] else 1)


@app.command()
def health(state_dir: Path | None = None):
    """Probe the shared local health endpoint without running an evaluation."""
    from llm4ad.gateway.sdk.health import probe

    root = state_dir or Path(os.environ.get("LLM4AD_GATEWAY_STATE_DIR", ".llm4ad-gateway"))
    value = probe(root.expanduser().resolve())
    typer.echo(json.dumps(value, ensure_ascii=False, allow_nan=False))
    raise typer.Exit(code=0 if value.get("ready") else 1)


@app.command()
def status(state_dir: Path = Path(".llm4ad-gateway")):
    """Inspect pending jobs without displaying gateway recovery credentials."""
    journals = _journal_paths(state_dir)
    if not journals:
        raise typer.BadParameter("No gateway journal found in this directory")
    for path in journals:
        for row in Journal(path).rows(pending_only=True):
            typer.echo(
                json.dumps(
                    {
                        "id": row["id"],
                        "state": row["state"],
                        "report": json.loads(row["report"]),
                        "state_directory": str(path.parent),
                    }
                )
            )


def _journal_paths(root: Path) -> list[Path]:
    """Find only known legacy/platform/device journal locations."""
    return [
        path
        for path in [
            root / "jobs.sqlite3",
            *root.glob("devices/*/jobs.sqlite3"),
            *root.glob("platforms/*/jobs.sqlite3"),
            *root.glob("platforms/*/devices/*/jobs.sqlite3"),
        ]
        if path.is_file()
    ]


@app.command()
def resolve(
    job_id: str, action: Annotated[str, typer.Option()], state_dir: Path = Path(".llm4ad-gateway")
):
    """Confirm uncertain work only after reconciling external execution."""
    if action not in {"retry", "cancelled"}:
        raise typer.BadParameter("action must be retry or cancelled")
    matches = [Journal(path) for path in _journal_paths(state_dir) if Journal(path).get(job_id)]
    if len(matches) != 1:
        raise typer.BadParameter(
            "Provide a state directory containing exactly one matching execution"
        )
    journal = matches[0]
    try:
        resolve_execution(journal, job_id, action)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
