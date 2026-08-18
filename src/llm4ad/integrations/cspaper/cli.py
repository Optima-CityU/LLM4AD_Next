"""Typer commands for the CSPaper algorithm-evolution integration."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from llm4ad.integrations.cspaper.client import CSPaperClient, save_review_artifacts
from llm4ad.integrations.cspaper.pipeline import CSPaperEvolutionPipeline
from llm4ad.integrations.cspaper.schemas import (
    AlgorithmDesignSpec,
    validate_design_spec,
)

app = typer.Typer(
    name="cspaper",
    help="Turn CSPaper reviews into executable LLM4AD algorithm evolution tasks.",
    no_args_is_help=True,
)
console = Console()


@app.command("submit")
def submit_review(
    paper: str = typer.Option(..., "--paper", help="Paper PDF to upload."),
    agent_id: str = typer.Option(..., "--agent-id", help="CSPaper review agent id."),
    output_dir: str = typer.Option(
        "./cspaper-output", "--output-dir", "-o", help="Review artifact directory."
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", envvar="CSPAPER_API_KEY", help="CSPaper API key."
    ),
    api_url: str = typer.Option(
        "https://cspaper-frontend-prod.azurewebsites.net",
        "--api-url",
        envvar="CSPAPER_API_URL",
        help="CSPaper API base URL.",
    ),
    desk_rejection_enabled: bool = typer.Option(
        False,
        "--desk-rejection-enabled/--no-desk-rejection-enabled",
        help="Allow CSPaper to stop at desk rejection instead of always requesting a full review.",
    ),
    poll_interval: float = typer.Option(30.0, "--poll-interval", min=0.1),
    timeout: float = typer.Option(1800.0, "--timeout", min=1.0),
) -> None:
    """Upload a PDF, wait for CSPaper, and save raw JSON plus review Markdown."""
    async def _run(key: str) -> tuple[str, Path, Path]:
        client = CSPaperClient(key, base_url=api_url)
        output = Path(output_dir).expanduser().resolve()
        job = await client.submit_and_wait_cached(
            paper,
            agent_id=agent_id,
            cache_path=output / "submission.json",
            desk_rejection_enabled=desk_rejection_enabled,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        raw_path, review_path = save_review_artifacts(
            job,
            output,
            paper_name=Path(paper).name,
            agent_id=agent_id,
        )
        return job.job_id, raw_path, review_path

    try:
        job_id, raw_path, review_path = asyncio.run(_run(_api_key(api_key)))
    except Exception as exc:
        _fail(str(exc))
    console.print(f"[green]Completed CSPaper review:[/green] {job_id}")
    console.print(f"Raw response: {raw_path}")
    console.print(f"Review Markdown: {review_path}")


@app.command("compile")
def compile_review(
    review: str = typer.Option(..., "--review", help="CSPaper Markdown review."),
    output: str = typer.Option(
        "algorithm-design-spec.json", "--output", "-o", help="Output spec JSON."
    ),
    paper: str | None = typer.Option(None, "--paper", help="Source paper PDF."),
    code_path: str | None = typer.Option(None, "--code-path", help="Baseline code path."),
    train_data: str | None = typer.Option(None, "--train-data"),
    validation_data: str | None = typer.Option(None, "--validation-data"),
    test_data: str | None = typer.Option(None, "--test-data"),
    hidden_test_data: str | None = typer.Option(None, "--hidden-test-data"),
) -> None:
    """Compile a CSPaper Markdown review into ``AlgorithmDesignSpec`` JSON."""
    pipeline = CSPaperEvolutionPipeline()
    try:
        spec = pipeline.compile_review(
            review,
            output,
            paper_path=paper,
            code_path=code_path,
            train_data=train_data,
            validation_data=validation_data,
            test_data=test_data,
            hidden_test_data=hidden_test_data,
        )
    except Exception as exc:
        _fail(str(exc))
    _print_spec_summary(spec, output)


@app.command("validate")
def validate_spec(
    spec_path: str = typer.Argument(..., help="AlgorithmDesignSpec JSON."),
    strict: bool = typer.Option(False, "--strict", help="Require confirmation and no pending items."),
    check_paths: bool = typer.Option(False, "--check-paths"),
) -> None:
    """Validate a compiled design specification."""
    try:
        path = Path(spec_path).expanduser().resolve()
        spec = AlgorithmDesignSpec.load(path)
        report = validate_design_spec(
            spec,
            strict=strict,
            check_paths=check_paths,
            base_dir=path.parent,
        )
    except Exception as exc:
        _fail(str(exc))
    _print_validation(report)
    if not report.valid:
        raise typer.Exit(code=1)


@app.command("confirm")
def confirm_spec(
    spec_path: str = typer.Argument(..., help="AlgorithmDesignSpec JSON."),
    confirmed_by: str = typer.Option(..., "--by", help="Person or automation identity."),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Record explicit approval after checking objectives and evaluator scope."""
    try:
        path = Path(spec_path).expanduser().resolve()
        spec = AlgorithmDesignSpec.load(path)
        spec.confirm(confirmed_by, notes)
        spec.save(path)
    except Exception as exc:
        _fail(str(exc))
    console.print(f"[green]Confirmed:[/green] {path}")


@app.command("prepare")
def prepare_task(
    spec_path: str = typer.Option(..., "--spec", help="Confirmed AlgorithmDesignSpec JSON."),
    task_dir: str = typer.Option(..., "--task-dir", help="Existing runnable LLM4AD task."),
    allow_unconfirmed: bool = typer.Option(
        False, "--allow-unconfirmed", help="Prepare without a confirmation record."
    ),
) -> None:
    """Inject a spec into an existing task and audit its evaluator contract."""
    try:
        spec = AlgorithmDesignSpec.load(spec_path)
        prepared = CSPaperEvolutionPipeline().prepare(
            spec,
            task_dir,
            require_confirmation=not allow_unconfirmed,
        )
    except Exception as exc:
        _fail(str(exc))
    _print_audit(prepared.audit)
    if not prepared.audit.ready:
        raise typer.Exit(code=1)


@app.command("build")
def build_task(
    spec_path: str = typer.Option(..., "--spec", help="AlgorithmDesignSpec JSON."),
    output_dir: str = typer.Option(..., "--output-dir", "-o"),
    code_path: str | None = typer.Option(None, "--code-path"),
    data_path: str | None = typer.Option(None, "--data-path"),
    project_name: str | None = typer.Option(None, "--project-name"),
    provider_name: str | None = typer.Option(None, "--provider-name"),
    api_key: str | None = typer.Option(None, "--builder-api-key", envvar="LLM_API_KEY"),
    model: str | None = typer.Option(None, "--builder-model", envvar="LLM_MODEL"),
    base_url: str | None = typer.Option(None, "--builder-base-url", envvar="LLM_BASE_URL"),
    provider_type: str = typer.Option("openai_compatible", "--builder-provider-type"),
) -> None:
    """Use the existing Task Builder to create an executable task from a spec."""
    try:
        spec = AlgorithmDesignSpec.load(spec_path)
        task_path = asyncio.run(
            CSPaperEvolutionPipeline().build_task(
                spec,
                output_dir,
                code_path=code_path,
                data_path=data_path,
                project_name=project_name,
                provider_name=provider_name,
                api_key=api_key,
                model=model,
                base_url=base_url,
                provider_type=provider_type,
            )
        )
    except Exception as exc:
        _fail(str(exc))
    console.print(f"[green]Built task:[/green] {task_path}")


@app.command("evolve")
def evolve_from_cspaper(
    task_dir: str | None = typer.Option(
        None, "--task-dir", help="Existing LLM4AD task. Omit to build one from the spec."
    ),
    spec_path: str | None = typer.Option(None, "--spec", help="Existing spec JSON."),
    review: str | None = typer.Option(None, "--review", help="Existing CSPaper review Markdown."),
    paper: str | None = typer.Option(None, "--paper", help="Paper PDF to submit to CSPaper."),
    agent_id: str | None = typer.Option(None, "--agent-id"),
    work_dir: str = typer.Option("./cspaper-run", "--work-dir", "-o"),
    code_path: str | None = typer.Option(None, "--code-path"),
    data_path: str | None = typer.Option(None, "--data-path"),
    top_k: int = typer.Option(10, "--top-k", min=1),
    confirm_by: str | None = typer.Option(
        None,
        "--confirm-by",
        help="Record approval after automated compilation; omit to require an already-confirmed spec.",
    ),
    cspaper_api_key: str | None = typer.Option(
        None, "--cspaper-api-key", envvar="CSPAPER_API_KEY"
    ),
    cspaper_api_url: str = typer.Option(
        "https://cspaper-frontend-prod.azurewebsites.net",
        "--cspaper-api-url",
        envvar="CSPAPER_API_URL",
    ),
    builder_api_key: str | None = typer.Option(
        None, "--builder-api-key", envvar="LLM_API_KEY"
    ),
    builder_model: str | None = typer.Option(None, "--builder-model", envvar="LLM_MODEL"),
    builder_base_url: str | None = typer.Option(
        None, "--builder-base-url", envvar="LLM_BASE_URL"
    ),
    builder_provider_name: str | None = typer.Option(None, "--builder-provider-name"),
) -> None:
    """Run review-to-spec-to-task-to-Top-K evolution as one command."""
    pipeline = CSPaperEvolutionPipeline()
    output = Path(work_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    async def _run():
        selected_review = review
        if spec_path:
            spec = AlgorithmDesignSpec.load(spec_path)
        else:
            if selected_review is None:
                if not paper or not agent_id:
                    raise ValueError(
                        "provide --spec, --review, or both --paper and --agent-id"
                    )
                _job, review_path = await pipeline.review_paper(
                    paper,
                    output / "review",
                    api_key=_api_key(cspaper_api_key),
                    agent_id=agent_id,
                    base_url=cspaper_api_url,
                )
                selected_review = str(review_path)
            spec_output = output / "algorithm-design-spec.json"
            spec = pipeline.compile_review(
                selected_review,
                spec_output,
                paper_path=paper,
                code_path=code_path,
                train_data=data_path,
            )
        if confirm_by:
            spec.confirm(confirm_by, "Approved for automated CSPaper-to-LLM4AD evolution.")
            spec.save(output / "algorithm-design-spec.json")
        if spec.confirmation.status != "confirmed":
            raise ValueError(
                "the spec is not confirmed; run `llm4ad cspaper confirm` or pass --confirm-by"
            )

        selected_task = Path(task_dir).resolve() if task_dir else None
        if selected_task is None:
            selected_task = await pipeline.build_task(
                spec,
                output / "tasks",
                code_path=code_path,
                data_path=data_path,
                api_key=builder_api_key,
                model=builder_model,
                base_url=builder_base_url,
                provider_name=builder_provider_name,
            )
        export = await pipeline.evolve(spec, selected_task, top_k=top_k)
        return spec, selected_task, export

    try:
        spec, selected_task, export = asyncio.run(_run())
    except Exception as exc:
        _fail(str(exc))
    console.print(f"[green]Evolution completed:[/green] {spec.problem.name}")
    console.print(f"Task: {selected_task}")
    console.print(f"Candidates: {export.candidates_directory}")
    console.print(f"Leaderboard: {export.leaderboard_path}")
    console.print(f"Top-K exported: {export.candidate_count}")


def _api_key(explicit: str | None) -> str:
    key = explicit or os.getenv("CSPAPER_API_KEY") or os.getenv("API_KEY") or ""
    if not key:
        raise ValueError("set CSPAPER_API_KEY or pass --api-key")
    return key


def _print_spec_summary(spec: AlgorithmDesignSpec, output: str | Path) -> None:
    table = Table(title="AlgorithmDesignSpec")
    table.add_column("Item")
    table.add_column("Count")
    table.add_row("Search directions", str(len(spec.search_directions)))
    table.add_row("Objectives", str(len(spec.objectives)))
    table.add_row("Constraints", str(len(spec.constraints)))
    table.add_row("Dataset requirements", str(len(spec.datasets.requirements)))
    table.add_row("Baselines", str(len(spec.baselines)))
    table.add_row("Excluded", str(len(spec.excluded_suggestions)))
    table.add_row("Pending", str(len(spec.pending_suggestions)))
    console.print(table)
    console.print(f"Spec: {Path(output).resolve()}")
    console.print("Confirmation: [yellow]pending[/yellow]")


def _print_validation(report) -> None:
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    for error in report.errors:
        console.print(f"[red]Error:[/red] {error}")
    if report.valid:
        console.print("[green]Specification is valid.[/green]")


def _print_audit(report) -> None:
    console.print(f"Prepared config: {report.config_path}")
    console.print(f"Repository: {report.repository_path}")
    console.print(f"EVOLVE files: {', '.join(report.evolve_files) or 'none'}")
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    for error in report.errors:
        console.print(f"[red]Error:[/red] {error}")
    if report.ready:
        console.print("[green]Task is ready for CSPaper-guided evolution.[/green]")


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=1)
