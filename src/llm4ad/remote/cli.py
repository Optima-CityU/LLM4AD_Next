"""Portable CLI for the outbound evaluation gateway."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from llm4ad.remote.gateway import Gateway
from llm4ad.remote.journal import Journal, gateway_lease

app = typer.Typer(help="Share a local evaluator with explicitly allowed platform users.")


@app.command()
def start(
    server: Annotated[str, typer.Option()],
    name: Annotated[str, typer.Option()],
    evaluator: Annotated[str, typer.Option()],
    allow_user: Annotated[list[str], typer.Option()],
    state_dir: Path = Path(".llm4ad-gateway"),
    concurrency: Annotated[int, typer.Option(min=1)] = 1,
):
    """Start or reconnect an outbound gateway using its durable local identity."""
    try:
        with gateway_lease(state_dir):
            gateway = Gateway(server, name, allow_user, evaluator, state_dir, concurrency)
            typer.echo(f"Gateway {name} ({gateway.identity['id']}); state: {gateway.root}")
            asyncio.run(gateway.run())
    except sqlite3.OperationalError as exc:
        raise typer.BadParameter("This state directory is already in use or cannot be locked") from exc
    except KeyboardInterrupt:
        typer.echo("Stopped; accepted work remains in the journal for recovery.")


@app.command()
def status(state_dir: Path = Path(".llm4ad-gateway")):
    """Show local jobs and pending reports without exposing recovery credentials."""
    if not (state_dir / "jobs.sqlite3").exists():
        raise typer.BadParameter("No gateway journal found in this directory")
    for row in Journal(state_dir / "jobs.sqlite3").rows(pending_only=True):
        typer.echo(json.dumps({"id": row["id"], "state": row["state"], "report": json.loads(row["report"])}))


@app.command()
def resolve(job_id: str, action: Annotated[str, typer.Option()], state_dir: Path = Path(".llm4ad-gateway")):
    """Confirm uncertain work: retry only after verifying old work has stopped."""
    if action not in {"retry", "cancelled"}:
        raise typer.BadParameter("action must be retry or cancelled")
    journal = Journal(state_dir / "jobs.sqlite3")
    row = next((row for row in journal.rows() if row["id"] == job_id), None)
    if row is None or row["state"] != "recovery_required":
        raise typer.BadParameter("Only uncertain jobs can be explicitly resolved")
    if action == "retry":
        # Server retry creates a fenced new attempt, never silently repeats work.
        journal.update(job_id, "recovery_required", {"message": "Operator confirmed safe retry", "retry_confirmed": True})
    else:
        journal.update(job_id, "cancelled", {"message": "Operator confirmed cancellation"})
