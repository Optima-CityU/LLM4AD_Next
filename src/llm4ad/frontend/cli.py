"""LLM4AD Command Line Interface."""

from __future__ import annotations

import asyncio
import traceback
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from llm4ad import LLM4AD, __version__
from llm4ad.coder.base import BaseCoder
from llm4ad.evaluator.base import BaseEvaluator
from llm4ad.infra.provider.base import BaseProvider
from llm4ad.memory.cli_commands import app as memory_app
from llm4ad.orchestrator.base import BaseOrchestrator
from llm4ad.planner.base import BasePlanner

app = typer.Typer(
    name="llm4ad",
    help="LLM4AD: A Platform for Algorithm Design with Large Language Model",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
# Diagnostics for the ``--json`` paths go to stderr so stdout stays a
# parseable JSON document for backend integrations.
err_console = Console(stderr=True)

evolve_app = typer.Typer(
    name="evolve",
    help="Inspect and clean EVOLVE markers in a task package.",
    no_args_is_help=True,
)
app.add_typer(evolve_app, name="evolve")

# Register memory management commands (interactive TUI).
app.add_typer(memory_app, name="memory")

load_dotenv()

@app.command("version")
def show_version():
    """Show LLM4AD version information."""
    console.print(f"LLM4AD version: [bold green]{__version__}[/bold green]")


@app.command("list")
def list_components(
    type: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by component type (provider, planner, coder, evaluator, orchestrator)",
    )
):
    """List all available registered components."""
    registries = {
        "provider": ("LLM Providers", BaseProvider),
        "planner": ("Planners", BasePlanner),
        "coder": ("Coders", BaseCoder),
        "evaluator": ("Evaluators", BaseEvaluator),
        "orchestrator": ("Orchestrators", BaseOrchestrator),
    }

    if type:
        if type not in registries:
            console.print(f"[bold red]Error:[/bold red] Unknown component type '{type}'")
            console.print(f"Available types: {', '.join(registries.keys())}")
            raise typer.Exit(code=1)
        registries = {type: registries[type]}

    # Map component types to their module paths for discovery
    component_modules = {
        "provider": "llm4ad.infra.provider",
        "planner": "llm4ad.planner",
        "coder": "llm4ad.coder",
        "evaluator": "llm4ad.evaluator",
        "orchestrator": "llm4ad.orchestrator",
    }

    for component_type, (title, registry) in registries.items():
        # Auto-discover components in the module
        if component_type in component_modules:
            registry.discover(component_modules[component_type])

        table = Table(title=title)
        table.add_column("Name", style="cyan")
        table.add_column("Description", style="magenta")

        component_names = registry.list()
        if not component_names:
            table.add_row("No components registered", "")
        else:
            for name in component_names:
                cls = registry.get(name)
                description = cls.__doc__.split("\n")[0] if cls.__doc__ else "No description"
                table.add_row(name, description.strip())

        console.print(table)
        console.print()


@app.command("run")
def run_pipeline(
    config: str = typer.Argument(..., help="Path to pipeline configuration file (YAML/JSON)"),
    output_dir: str | None = typer.Option(
        None, "--output-dir", "-o", help="Directory to override output base directory"
    ),
    resume: str | None = typer.Option(
        None, "--resume", "-r", help="Resume from checkpoint at this path"
    ),
):
    """Run an algorithm design pipeline with the given configuration."""
    console.print(f"[bold blue]Running pipeline with config:[/bold blue] {config}")

    # Create LLM4AD instance and run
    try:
        llm4ad = LLM4AD(config)

        if output_dir is not None:
            # Override base directory in config
            llm4ad.config = llm4ad.config.model_copy(update={"base_dir": output_dir})

        # Print run summary
        llm4ad.print_run_summary()

        result = asyncio.run(llm4ad.run(resume_from_checkpoint=resume))

        console.print()
        if result.state.value == "completed":
            if result.best_individual is not None:
                # Show multi-objective results when available
                objective_metrics = result.metadata.get("objective_metrics")
                if objective_metrics:
                    per_best = result.metadata.get("per_objective_best", {})
                    obj_values = [
                        per_best.get(m, {}).get("value") for m in objective_metrics
                    ]
                    obj_str = ", ".join(
                        f"{m}={v:.4f}" if v is not None else f"{m}=N/A"
                        for m, v in zip(objective_metrics, obj_values, strict=True)
                    )
                    console.print(
                        f"[bold green]Pipeline completed successfully![/bold green] "
                        f"Best objectives: [bold]\\[{obj_str}][/bold]"
                    )
                else:
                    console.print(
                        f"[bold green]Pipeline completed successfully![/bold green] "
                        f"Best score: [bold]{result.best_individual.score:.4f}[/bold]"
                    )
                # Show elitist archive summary for multi-objective runs
                archive = result.metadata.get("elitist_archive")
                if archive:
                    console.print(
                        f"Elitist archive: [bold]{len(archive)}[/bold] non-dominated solutions"
                    )
                    for entry in archive:
                        objs = entry.get("objectives", {})
                        obj_str = ", ".join(
                            f"{m}={v:.4f}" if v is not None else f"{m}=N/A"
                            for m, v in objs.items()
                        )
                        console.print(f"  - {entry.get('name', entry['id'])}: \\[{obj_str}]")
            else:
                console.print(
                    "[bold green]Pipeline completed successfully![/bold green] "
                    "No valid individual was found."
                )
            if (
                result.best_individual is not None
                and hasattr(result.best_individual, "metadata")
                and "worktree_name" in result.best_individual.metadata
            ):
                wt_name = result.best_individual.metadata["worktree_name"]
                console.print(f"Best algorithm worktree: {wt_name}")
            # Point users at the stable best/ directory written by
            # ``LLM4AD.run()`` so they don't need to chase the worktree.
            try:
                run_dir = llm4ad.get_run_directory()
                best_subdir = llm4ad.config.workspace.subdirs.get("best", "best")
                best_dir = run_dir / best_subdir
                if best_dir.exists():
                    console.print(f"Best snapshot: [cyan]{best_dir}[/cyan]")
            except Exception:  # noqa: BLE001 - cosmetic hint only
                pass
        else:
            console.print(f"[bold red]Pipeline {result.state.value}[/bold red]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {str(e)}")
        console.print("\n[dim]Full stack trace:[/dim]")
        traceback.print_exc()
        raise typer.Exit(code=1) from e


@app.command("config")
def show_config():
    """Show current LLM4AD configuration."""
    console.print("[yellow]Configuration system is not yet implemented[/yellow]")
    # TODO: Implement config loading and display here


@app.command("chat")
def chat_agent_build(
    provider_name: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider name defined in global settings (~/.llm4ad/settings.yaml). "
        "Uses the first provider if not specified.",
    ),
    output: str = typer.Option(
        "./",
        "--output",
        "-o",
        help="Output directory where the built LLM4AD task package will be written.",
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help="Problem description passed directly to the agent. "
        "If omitted, the agent will ask you interactively.",
    ),
    max_iters: int = typer.Option(
        40,
        "--max-iters",
        help="Maximum agent ReAct loop iterations.",
    ),
):
    """Build an LLM4AD task package using an AI agent.

    Launches a single AgentScope ReAct agent that gathers your requirements,
    generates a complete task package (evaluator, algorithm, config, test scripts)
    via the proven build engine, and self-verifies the result by running the
    generated scripts.

    The agent reads and runs files inside the output directory; it cannot access
    anything outside it. The generated code runs with your local user privileges —
    intended for local developer use.

    Requires Python >=3.12. agentscope is a base dependency, so a plain ``uv sync``
    (or ``pip install llm4ad``) installs everything needed — no extra step.

    Configure a provider in ~/.llm4ad/settings.yaml, for example::

        providers:
          - name: deepseek
            type: anthropic
            base_url: https://api.deepseek.com/anthropic
            auth_token: sk-...
            model: deepseek-v4-pro
    """
    import contextlib
    import sys
    from pathlib import Path

    # Reconfigure stdout/stderr to UTF-8 so streamed agent output (which may contain
    # non-ASCII / emoji) does not crash on a legacy Windows cp1252 console.
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")

    # ---- Dependency check (friendly error before any import hang) ----
    try:
        import agentscope  # noqa: F401
    except ImportError:
        console.print(
            "[bold red]Error:[/bold red] [bold]agentscope[/bold] is not installed.\n"
            "Reinstall dependencies with:\n\n"
            "    [bold]uv sync[/bold]   (or: pip install llm4ad)\n\n"
            "Note: this project requires Python >=3.12.\n"
            f"You are running Python {sys.version_info.major}.{sys.version_info.minor}."
        )
        raise typer.Exit(code=1) from None

    from llm4ad.config.settings import load_global_settings
    from llm4ad.infra.provider.base import BaseProvider

    # ---- Provider resolution (same pattern as original chat) ----
    global_data = load_global_settings()
    global_providers = global_data.get("providers", [])

    if not global_providers:
        console.print(
            "[bold red]Error:[/bold red] No providers found in global settings.\n"
            "Please configure a provider in [bold]~/.llm4ad/settings.yaml[/bold].\n"
            "Example:\n"
            "  providers:\n"
            '    - name: "deepseek"\n'
            '      type: "anthropic"\n'
            '      base_url: "https://api.deepseek.com/anthropic"\n'
            '      auth_token: "${DEEPSEEK_API_KEY}"\n'
            '      model: "deepseek-v4-pro"'
        )
        raise typer.Exit(code=1)

    providers_by_name = {p.get("name", "default"): p for p in global_providers}

    if provider_name is None:
        provider_cfg = global_providers[0]
        provider_name = provider_cfg.get("name", "default")
        console.print(f"[dim]Using provider: {provider_name}[/dim]")
    else:
        if provider_name not in providers_by_name:
            console.print(
                f"[bold red]Error:[/bold red] Provider '{provider_name}' not found.\n"
                f"Available: {', '.join(providers_by_name.keys())}"
            )
            raise typer.Exit(code=1)
        provider_cfg = providers_by_name[provider_name]

    # Validate base_dir
    base_dir = str(Path(output).resolve())
    Path(base_dir).mkdir(parents=True, exist_ok=True)

    # ---- Collect problem description ----
    user_content = prompt or ""
    if not user_content.strip():
        console.print(
            "[bold]AI Build[/bold] — describe your optimization problem\n"
            "(or press Ctrl-C to cancel)"
        )
        user_content = typer.prompt("Problem description")

    # ---- Security notice ----
    console.print(
        f"\n[yellow]! The agent will run generated Python code inside "
        f"[bold]{base_dir}[/bold] with your local privileges.[/yellow]\n"
    )

    # Need a BaseProvider for the build engine; agentscope model is built inside core.
    BaseProvider.discover("llm4ad.infra.provider")

    # ---- Run agent loop (multi-turn: gather -> confirm -> build) ----
    from llm4ad.agent.runner import AgentBuildConfig, run_agent_build

    console.print("[bold green]Starting agent...[/bold green]\n")

    async def _run_turn(
        turn_input: str,
        *,
        allow_build: bool,
        prior_state: dict | None,
        proposed: dict | None,
    ) -> dict:
        """Run one agent turn; return {state, proposed, card, built, project_name}."""
        cfg = AgentBuildConfig(
            provider_config=provider_cfg,
            base_dir=base_dir,
            user_content=turn_input,
            allow_build=allow_build,
            prior_state=prior_state,
            proposed=proposed,
            max_iters=max_iters,
            surface="cli",
        )
        result: dict = {
            "state": prior_state,
            "proposed": None,
            "card": None,
            "built": False,
            "project_name": "",
        }
        async for event in run_agent_build(cfg):
            etype = event.get("type")
            if etype == "chunk":
                # Straight to stdout (UTF-8 reconfigured above), bypassing rich's
                # legacy-Windows console path which can crash on emoji.
                sys.stdout.write(event.get("content", ""))
                sys.stdout.flush()
            elif etype == "payload":
                # `proposed` marks a build-plan confirm card; `data` carries the
                # interactive choice card (ask_choice) or the confirm card body.
                result["proposed"] = event.get("proposed")
                result["card"] = event.get("data")
            elif etype == "build_result":
                bp = event.get("blueprint_data", {})
                result["built"] = bool(bp.get("built"))
                result["project_name"] = bp.get("project_name", "")
            elif etype == "agent_state":
                result["state"] = event.get("state")
            elif etype == "error":
                console.print(f"\n[bold red]Error:[/bold red] {event.get('error', '')}")
        return result

    async def _select_from_card(card: dict) -> str:
        """Render a gather-phase choice card as an interactive selector."""
        options = card.get("options") or []
        question = (card.get("prompt") or "").strip()

        # No preset options -> behave like the old plain-text path.
        if not options:
            if question:
                console.print(Markdown(question))
            console.print()
            return typer.prompt("You")

        try:
            from InquirerPy import inquirer
            from InquirerPy.base.control import Choice
        except ImportError:
            # InquirerPy missing: show the question + options as text, then prompt.
            if question:
                console.print(Markdown(question))
            for i, opt in enumerate(options, 1):
                label = opt.get("label", opt.get("value", ""))
                desc = opt.get("description", "")
                console.print(f"  [bold]{i}.[/bold] {label}" + (f" — {desc}" if desc else ""))
            console.print()
            return typer.prompt("You")

        if question:
            console.print()
            console.print(Markdown(question))

        iq_choices = []
        for i, opt in enumerate(options):
            label = opt.get("label", opt.get("value", ""))
            desc = opt.get("description", "")
            name = f"{label} — {desc}" if desc else label
            iq_choices.append(Choice(value=i, name=name))

        try:
            selected = await inquirer.select(  # type: ignore[func-returns-value]
                message="",
                choices=iq_choices,
                pointer=">",
                qmark="",
                amark="",
                instruction="(arrow keys to move, Enter to select)",
            ).execute_async()
        except OSError:
            # Non-interactive terminal: fall back to a plain prompt.
            console.print()
            return typer.prompt("You")

        opt = options[int(selected)]
        # Custom / free-text option -> let the user type their own answer.
        if opt.get("is_custom"):
            console.print()
            return typer.prompt("You")
        # File / directory pick options -> ask for the path, keep the option's
        # meaning so the agent knows why the path was provided.
        if opt.get("ask_for_dir") or opt.get("ask_for_path"):
            what = "目录路径 / directory path" if opt.get("ask_for_dir") else "文件路径 / file path"
            path = typer.prompt(what)
            label = opt.get("label", opt.get("value", ""))
            return f"{label}: {path}" if label and not label.startswith("__") else path
        return opt.get("value", opt.get("label", ""))

    async def _run() -> bool:
        """Gather step-by-step, confirm, then build."""
        prior_state: dict | None = None
        turn_input = user_content
        # Gather loop: keep talking until the agent proposes a plan the user accepts.
        for _ in range(50):
            r = await _run_turn(
                turn_input, allow_build=False, prior_state=prior_state, proposed=None
            )
            prior_state = r["state"]
            proposed = r["proposed"]
            if proposed is None:
                # Agent asked a question. If it offered preset options (ask_choice),
                # render them as an interactive selector; otherwise plain prompt.
                card = r["card"]
                if card and card.get("options"):
                    turn_input = await _select_from_card(card)
                else:
                    console.print()
                    turn_input = typer.prompt("You")
                continue
            # Agent proposed a build plan -> show the plan card, then confirm.
            if r["card"] and (r["card"].get("prompt") or "").strip():
                console.print()
                console.print(Markdown(r["card"]["prompt"]))
            console.print()
            if typer.confirm("确认按此方案开始构建? (Confirm build?)", default=True):
                br = await _run_turn(
                    "", allow_build=True, prior_state=prior_state, proposed=proposed
                )
                if br["built"]:
                    console.print(
                        f"\n\n[bold green]Build complete![/bold green] "
                        f"Project: [bold]{br['project_name'] or '?'}[/bold]\n"
                        f"Output: {base_dir}"
                    )
                    return True
                console.print("\n[yellow]Build finished but no package was produced.[/yellow]")
                return False
            # User declined -> keep adjusting.
            console.print()
            turn_input = typer.prompt("You (keep adjusting)")
        console.print("\n[yellow]Reached max gather turns without building.[/yellow]")
        return False

    try:
        ok = asyncio.run(_run())
        raise typer.Exit(code=0 if ok else 1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(code=1) from None


@app.command("advise")
def advise_block_cmd(
    goal: str | None = typer.Option(
        None,
        "--goal",
        "-g",
        help="The algorithm-evolution goal to analyze against",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-f",
        help="Path to an advisor config YAML (alternative to the flags below)",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Path to the repository containing the block",
    ),
    file: str | None = typer.Option(
        None,
        "--file",
        help="Path (relative to --repo or absolute) of the file with the block",
    ),
    line_range: str | None = typer.Option(
        None,
        "--range",
        help="1-based inclusive line range, format: START:END (e.g. 42:87)",
    ),
    code: str | None = typer.Option(
        None,
        "--code",
        help="Raw snippet to analyze instead of a repo path",
    ),
    block_id: str | None = typer.Option(
        None,
        "--block-id",
        help=(
            "Stable id from `llm4ad evolve check` (e.g. 'algo/sort.py#12-162') "
            "to select a single block in --repo"
        ),
    ),
    all_blocks: bool = typer.Option(
        False,
        "--all",
        help="Analyze every well-formed EVOLVE block in --repo (concurrent)",
    ),
    max_concurrency: int = typer.Option(
        5,
        "--max-concurrency",
        help="Max parallel LLM calls when --all is set (default: 5)",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key for the advisor's LLM (or env: LLM4AD_ADVISE_API_KEY)",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model name for the advisor's LLM (default: gpt-4o)",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Base URL for the advisor's LLM provider",
    ),
    provider_type: str = typer.Option(
        "openai_compatible",
        "--provider-type",
        help="Provider type: openai, anthropic, openai_compatible",
    ),
    provider_name: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Use a named provider from global settings (~/.llm4ad/settings.yaml)",
    ),
    lang: str = typer.Option(
        "en",
        "--lang",
        help="Language for the LLM's free-text answers: 'en' or 'zh' (default: en)",
        case_sensitive=False,
    ),
    pretty: bool = typer.Option(
        False,
        "--pretty",
        help="Render a human-readable Rich panel instead of JSON (default: JSON)",
    ),
):
    """Analyze a user-selected block (or every block) against an evolution goal.

    Returns an envelope ``{"goal", "repo_path", "lang", "count", "results", "errors"}``
    so the frontend never needs to discriminate single-block vs --all output.
    Default output is JSON on stdout. Use --pretty for a human-readable panel.

    Examples:
        llm4ad advise -g "minimize comparisons" -r ./solver --file algo.py --range 42:87
        llm4ad advise -g "reduce tour length"   -r ./solver --block-id 'algo/sort.py#12-162'
        llm4ad advise -g "tune all heuristics"  -r ./solver --all --max-concurrency 8
        llm4ad advise --config advise_config.yaml
    """
    import json as _json

    from llm4ad.advisor.pipeline import (
        AdvisorError,
        advise_block,
        advise_blocks,
        advise_from_config,
    )

    lang_normalized = lang.lower()
    if lang_normalized not in ("en", "zh"):
        console.print(
            f"[bold red]Error:[/bold red] --lang must be 'en' or 'zh' (got {lang!r})"
        )
        raise typer.Exit(code=1)

    # Mutual-exclusion gates so a frontend that builds the wrong combination
    # gets a fast structured failure before any provider is contacted.
    if all_blocks:
        for name, value in (
            ("--code", code),
            ("--file", file),
            ("--range", line_range),
            ("--block-id", block_id),
        ):
            if value:
                console.print(
                    f"[bold red]Error:[/bold red] --all cannot be combined with {name}."
                )
                raise typer.Exit(code=1)
        if not repo:
            console.print("[bold red]Error:[/bold red] --all requires --repo.")
            raise typer.Exit(code=1)

    if block_id is not None:
        for name, value in (
            ("--code", code),
            ("--file", file),
            ("--range", line_range),
        ):
            if value:
                console.print(
                    f"[bold red]Error:[/bold red] --block-id cannot be combined with {name}."
                )
                raise typer.Exit(code=1)

    if code is not None and (repo or file or line_range or block_id):
        console.print(
            "[bold red]Error:[/bold red] --code cannot be combined with "
            "--repo, --file, --range, or --block-id."
        )
        raise typer.Exit(code=1)

    parsed_range: tuple[int, int] | None = None
    if line_range is not None:
        try:
            start_str, end_str = line_range.split(":", 1)
            parsed_range = (int(start_str), int(end_str))
        except ValueError as exc:
            console.print(
                f"[bold red]Error:[/bold red] --range must be START:END (got {line_range!r})"
            )
            raise typer.Exit(code=1) from exc

    try:
        if config is not None:
            result = asyncio.run(advise_from_config(config))
        elif all_blocks:
            if goal is None:
                console.print(
                    "[bold red]Error:[/bold red] --goal is required with --all."
                )
                raise typer.Exit(code=1)
            result = asyncio.run(
                advise_blocks(
                    goal,
                    repo,  # type: ignore[arg-type]
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    provider_type=provider_type,
                    provider_name=provider_name,
                    max_concurrency=max_concurrency,
                    lang=lang_normalized,  # type: ignore[arg-type]
                )
            )
        else:
            if goal is None:
                console.print(
                    "[bold red]Error:[/bold red] Either --goal or --config is required."
                )
                raise typer.Exit(code=1)
            result = asyncio.run(
                advise_block(
                    goal,
                    repo_path=repo,
                    code=code,
                    file_path=file,
                    line_range=parsed_range,
                    block_id=block_id,
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    provider_type=provider_type,
                    provider_name=provider_name,
                    lang=lang_normalized,  # type: ignore[arg-type]
                )
            )
    except AdvisorError as e:
        console.print(f"[bold red]Advisor error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        traceback.print_exc()
        raise typer.Exit(code=1) from e

    if pretty:
        from rich.panel import Panel

        for idx, advice in enumerate(result.results, start=1):
            summary = advice.block_summary or "(no summary)"
            ref = advice.block_ref or {}
            location = (
                f"{ref.get('file_path', '?')}:"
                f"{ref.get('line_start', '?')}-{ref.get('line_end', '?')}"
                if ref
                else "(snippet)"
            )
            title = (
                f"Evolve-Block Advice [{idx}/{result.count}] — {location}"
                if result.count > 1
                else "Evolve-Block Advice"
            )
            body = (
                f"[bold]Summary[/bold]\n{summary}\n\n"
                f"[bold]Feasibility[/bold]: {advice.feasibility or '?'} "
                f"— {advice.feasibility_reason}\n"
                f"[bold]Significance[/bold]: {advice.significance or '?'} "
                f"— {advice.significance_reason}\n"
            )
            if advice.concerns:
                body += "\n[bold]Concerns[/bold]\n"
                body += "\n".join(f"  • {c}" for c in advice.concerns) + "\n"
            if advice.suggestions:
                body += "\n[bold]Suggestions[/bold]\n"
                body += "\n".join(f"  • {s}" for s in advice.suggestions) + "\n"
            if advice.rationale:
                body += f"\n[bold]Rationale[/bold]\n{advice.rationale}\n"
            console.print(Panel(body, title=title, border_style="blue"))
        if result.errors:
            err_body = "\n".join(
                f"  • {e['block_id']}: {e['error']}" for e in result.errors
            )
            console.print(
                Panel(err_body, title="Failed blocks", border_style="red")
            )
    else:
        print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


@app.command("advise-init")
def advise_init_config(
    output: str = typer.Option(
        "advise_config.yaml",
        "--output",
        "-o",
        help="Output path for the advisor config template",
    ),
    goal: str = typer.Option(
        "",
        "--goal",
        "-g",
        help="Pre-fill the evolution goal field",
    ),
):
    """Generate an advisor config YAML template.

    Creates a config file that users fill in with their LLM credentials,
    goal, and block location, then run with: llm4ad advise --config <file>

    Examples:
        llm4ad advise-init
        llm4ad advise-init -o my_advise.yaml
        llm4ad advise-init -g "minimize sort comparisons"
    """
    from llm4ad.advisor.advisor_config import generate_advisor_config

    path = generate_advisor_config(output, goal=goal)
    console.print(f"[bold green]Advisor config template created:[/bold green] {path}")
    console.print(f"[dim]Edit it, then run: llm4ad advise --config {path}[/dim]")


@app.command("recommend")
def recommend_blocks_cmd(
    goal: str | None = typer.Option(
        None,
        "--goal",
        "-g",
        help="The algorithm-evolution goal to analyze against",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Path to the repository to scan",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key for the recommender's LLM (or env: LLM4AD_ADVISE_API_KEY)",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model name for the recommender's LLM (default: gpt-4o)",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Base URL for the recommender's LLM provider",
    ),
    provider_type: str = typer.Option(
        "openai_compatible",
        "--provider-type",
        help="Provider type: openai, anthropic, openai_compatible",
    ),
    provider_name: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Use a named provider from global settings (~/.llm4ad/settings.yaml)",
    ),
    max_concurrency: int = typer.Option(
        5,
        "--max-concurrency",
        help="Max parallel advice calls during enrichment",
    ),
    include_raw: bool = typer.Option(
        False,
        "--include-raw",
        help="Include the raw discovery-LLM text in the output (debug)",
    ),
    lang: str = typer.Option(
        "en",
        "--lang",
        help="Language for the LLM's free-text answers: 'en' or 'zh' (default: en)",
        case_sensitive=False,
    ),
    pretty: bool = typer.Option(
        False,
        "--pretty",
        help="Render a human-readable Rich panel instead of JSON (default: JSON)",
    ),
):
    """Scan a repo against a goal and recommend evolve-block targets.

    Returns three tiers: a core (minimal) block, optional expanded variants
    of core, and optional alternative blocks elsewhere. LLM4AD evolves one
    block per run — the tiers are alternative CHOICES, not co-evolution.

    Examples:
        llm4ad recommend -g "reduce TSP tour length" -r ./solver
        llm4ad recommend -g "improve policy reward" -r ./lander --pretty
    """
    import json as _json

    from llm4ad.advisor.pipeline import AdvisorError
    from llm4ad.advisor.recommender import recommend_blocks

    if goal is None or not goal.strip():
        console.print("[bold red]Error:[/bold red] --goal is required.")
        raise typer.Exit(code=1)
    if repo is None or not repo.strip():
        console.print("[bold red]Error:[/bold red] --repo is required.")
        raise typer.Exit(code=1)

    lang_normalized = lang.lower()
    if lang_normalized not in ("en", "zh"):
        console.print(
            f"[bold red]Error:[/bold red] --lang must be 'en' or 'zh' (got {lang!r})"
        )
        raise typer.Exit(code=1)

    try:
        result = asyncio.run(
            recommend_blocks(
                goal,
                repo,
                api_key=api_key,
                model=model,
                base_url=base_url,
                provider_type=provider_type,
                provider_name=provider_name,
                max_concurrency=max_concurrency,
                include_raw=include_raw,
                lang=lang_normalized,  # type: ignore[arg-type]
            )
        )
    except AdvisorError as e:
        console.print(f"[bold red]Recommender error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        traceback.print_exc()
        raise typer.Exit(code=1) from e

    if pretty:
        _render_recommendations_pretty(result)
    else:
        print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


def _render_recommendations_pretty(result) -> None:
    """Render a RepoRecommendations as a stack of Rich panels."""
    from rich.panel import Panel

    def _block_body(rec) -> str:
        loc = f"{rec.file_path}:{rec.line_start}-{rec.line_end} ({rec.size_lines} lines)"
        body = f"[bold]Location[/bold]: {loc}\n"
        if rec.discovery_rationale:
            body += f"[bold]Why suggested[/bold]: {rec.discovery_rationale}\n"
        if rec.advice is not None:
            a = rec.advice
            if a.feasibility or a.feasibility_reason:
                body += (
                    f"[bold]Feasibility[/bold]: {a.feasibility or '?'} "
                    f"— {a.feasibility_reason}\n"
                )
            if a.significance or a.significance_reason:
                body += (
                    f"[bold]Significance[/bold]: {a.significance or '?'} "
                    f"— {a.significance_reason}\n"
                )
            if a.concerns:
                body += "[bold]Concerns[/bold]\n"
                body += "\n".join(f"  • {c}" for c in a.concerns) + "\n"
            if a.suggestions:
                body += "[bold]Suggestions[/bold]\n"
                body += "\n".join(f"  • {s}" for s in a.suggestions) + "\n"
            if a.rationale:
                body += f"[bold]Rationale[/bold]\n{a.rationale}\n"
        elif rec.advice_error:
            body += f"[yellow]Advice enrichment failed:[/yellow] {rec.advice_error}\n"
        return body

    console.print(f"\n[bold]Goal:[/bold] {result.goal}")
    console.print(f"[bold]Repo:[/bold] {result.repo_path}\n")

    if result.core is not None:
        console.print(
            Panel(_block_body(result.core), title="Core (recommended)", border_style="green")
        )
    for i, rec in enumerate(result.expanded):
        console.print(
            Panel(
                _block_body(rec),
                title=f"Expanded variant {i + 1}",
                border_style="cyan",
            )
        )
    for i, rec in enumerate(result.alternatives):
        console.print(
            Panel(
                _block_body(rec),
                title=f"Alternative {i + 1}",
                border_style="magenta",
            )
        )
    if result.dropped_candidates:
        console.print(
            f"\n[dim]Dropped {len(result.dropped_candidates)} invalid candidate(s) "
            f"(see --pretty=false JSON output for details).[/dim]"
        )
    if result.unreadable_files:
        console.print(
            f"[dim]{len(result.unreadable_files)} file(s) skipped during compaction.[/dim]"
        )


@app.command("init")
def init_config(
    level: str = typer.Argument(
        "minimal",
        help="Configuration level: minimal, standard, or complete",
    ),
    output: str = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file name. Defaults to '<level>.yaml'.",
    ),
):
    """Generate a configuration template in the current directory.

    Copies the chosen template (minimal, standard, or complete) from the
    bundled templates to the current working directory so you can edit it
    directly.
    """
    from llm4ad.consultant.templates import _read_template_file, get_template_info

    valid_levels = ("minimal", "standard", "complete")
    if level not in valid_levels:
        console.print(
            f"[bold red]Error:[/bold red] Unknown level '{level}'. "
            f"Choose from: {', '.join(valid_levels)}"
        )
        raise typer.Exit(code=1)

    try:
        info = get_template_info(level)
        content = _read_template_file(info.filename)
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    from pathlib import Path

    dest = Path(output) if output else Path(f"{level}.yaml")

    if dest.exists():
        from rich.prompt import Confirm

        if not Confirm.ask(f"[yellow]{dest} already exists. Overwrite?[/yellow]", default=False):
            raise typer.Exit()

    dest.write_text(content, encoding="utf-8")
    console.print(f"[bold green]Created {dest}[/bold green] ({info.display_name} template)")
    console.print(f"[dim]Edit it, then run: llm4ad run {dest}[/dim]")


@evolve_app.command("check")
def evolve_check(
    path: str = typer.Argument(
        ".",
        help="Task package directory to inspect (defaults to current directory).",
    ),
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            "-i",
            help="Glob pattern to include (repeatable). Overrides defaults.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            "-e",
            help="Glob pattern to exclude (repeatable). Overrides defaults.",
        ),
    ] = None,
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit InspectResult.to_dict() as JSON on stdout.",
    ),
):
    """Inspect EVOLVE markers in a task package.

    Reports total well-formed blocks, unbalanced or nested markers, and
    per-file details. The ``--json`` flag emits the same structured result
    the Python API returns, suitable for backend consumption.
    """
    import json as _json

    from llm4ad.infra.repo_analyzer import inspect_path

    result = inspect_path(
        path,
        include=list(include) if include else None,
        exclude=list(exclude) if exclude else None,
    )

    if json_output:
        print(_json.dumps(result.to_dict(), ensure_ascii=False))
        raise typer.Exit(code=0 if result.ok else 1)

    if "error" in result.summary:
        console.print(f"[bold red]Error:[/bold red] {result.summary['error']}")
        raise typer.Exit(code=1)

    summary_table = Table(title="EVOLVE Inspection Summary", show_header=False)
    summary_table.add_column("Field", style="cyan")
    summary_table.add_column("Value", style="magenta")
    summary_table.add_row("Root", result.root)
    summary_table.add_row("Files scanned", str(result.summary["files_scanned"]))
    summary_table.add_row("Files with blocks", str(result.summary["files_with_blocks"]))
    summary_table.add_row("Total blocks", str(result.summary["blocks"]))
    summary_table.add_row("Total issues", str(result.summary["issues"]))
    summary_table.add_row(
        "Active block",
        result.active_block_id or "[dim]none[/dim]",
    )
    console.print(summary_table)

    blocks_table = Table(title="Discovered Blocks")
    blocks_table.add_column("Active", style="bold yellow", justify="center")
    blocks_table.add_column("File", style="cyan")
    blocks_table.add_column("Lines", style="green")
    blocks_table.add_column("Style", style="yellow")
    blocks_table.add_column("Name", style="magenta")
    any_block = False
    for f in result.files:
        for b in f.blocks:
            any_block = True
            block_id = f"{f.path}#{b.line_start}-{b.line_end}"
            is_active = block_id == result.active_block_id
            blocks_table.add_row(
                "*" if is_active else "",
                f.path,
                f"{b.line_start}-{b.line_end}",
                b.comment_style,
                b.block_name or "-",
            )
    if any_block:
        console.print(blocks_table)
        if result.active_block_id:
            console.print(
                "[dim]* marks the block planners currently feed to the coder "
                "(evolvable_blocks[0]).[/dim]"
            )
    else:
        console.print("[dim]No EVOLVE blocks found.[/dim]")

    issues_table = Table(title="Issues")
    issues_table.add_column("File", style="cyan")
    issues_table.add_column("Line", style="green")
    issues_table.add_column("Kind", style="red")
    issues_table.add_column("Detail", style="yellow")
    any_issue = False
    for f in result.files:
        for issue in f.issues:
            any_issue = True
            detail = issue.detail or ""
            if issue.related_line is not None:
                detail = f"outer START at line {issue.related_line}"
            issues_table.add_row(f.path, str(issue.line), issue.kind, detail)
    if any_issue:
        console.print(issues_table)

    if result.ok:
        console.print("[bold green]OK[/bold green]: no issues found.")
        raise typer.Exit(code=0)
    console.print(f"[bold red]Found {result.summary['issues']} issue(s).[/bold red]")
    raise typer.Exit(code=1)


@evolve_app.command("clean")
def evolve_clean(
    path: str = typer.Argument(
        ".",
        help="Task package directory to clean (defaults to current directory).",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually rewrite files. Default is dry-run (no writes).",
    ),
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            "-i",
            help="Glob pattern to include (repeatable). Overrides defaults.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            "-e",
            help="Glob pattern to exclude (repeatable). Overrides defaults.",
        ),
    ] = None,
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit CleanResult.to_dict() as JSON on stdout.",
    ),
):
    """Remove EVOLVE START/END marker lines from a task package.

    Marker lines are dropped while block bodies and surrounding context are
    preserved. Default is dry-run; pass ``--apply`` to write changes back
    to disk in place.
    """
    import json as _json

    from llm4ad.infra.repo_analyzer import clean_path

    result = clean_path(
        path,
        apply=apply,
        include=list(include) if include else None,
        exclude=list(exclude) if exclude else None,
    )

    if json_output:
        print(_json.dumps(result.to_dict(), ensure_ascii=False))
        raise typer.Exit(code=0 if result.ok else 1)

    if "error" in result.summary:
        console.print(f"[bold red]Error:[/bold red] {result.summary['error']}")
        raise typer.Exit(code=1)

    summary_table = Table(title="EVOLVE Clean Summary", show_header=False)
    summary_table.add_column("Field", style="cyan")
    summary_table.add_column("Value", style="magenta")
    summary_table.add_row("Root", result.root)
    summary_table.add_row("Mode", "apply" if result.applied else "dry-run")
    summary_table.add_row("Files changed", str(result.summary["files_changed"]))
    summary_table.add_row("Lines removed", str(result.summary["lines_removed"]))
    summary_table.add_row("Errors", str(result.summary["errors"]))
    console.print(summary_table)

    files_table = Table(title="Files")
    files_table.add_column("File", style="cyan")
    files_table.add_column("Removed lines", style="green")
    files_table.add_column("Written", style="yellow")
    files_table.add_column("Error", style="red")
    for entry in result.files:
        files_table.add_row(
            entry["path"],
            ", ".join(str(n) for n in entry["removed_lines"]),
            "yes" if entry.get("written") else "no",
            entry.get("error", ""),
        )
    if result.files:
        console.print(files_table)

    if not result.applied:
        console.print("[dim]Dry-run only. Re-run with --apply to write changes.[/dim]")

    if not result.ok:
        console.print(
            f"[bold red]Encountered {result.summary['errors']} error(s).[/bold red]"
        )
        raise typer.Exit(code=1)


@app.command("chat-legacy")
def chat_consultant_legacy(
    provider_name: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider name defined in global settings (~/.llm4ad/settings.yaml). "
        "Uses the first provider if not specified.",
    ),
    output: str = typer.Option(
        "./",
        "--output",
        "-o",
        help="Output directory where the built LLM4AD task package will be written.",
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help="Problem description passed directly to the agent. "
        "If omitted, the agent will ask you interactively.",
    ),
    max_iters: int = typer.Option(
        40,
        "--max-iters",
        help="Maximum agent ReAct loop iterations.",
    ),
):
    """[DEPRECATED] Legacy consultant-based chat (use 'llm4ad chat' instead).

    This command is deprecated and kept for backward compatibility only.
    The new 'llm4ad chat' command uses an improved AI agent architecture.

    This legacy version will be removed in a future release.
    """
    console.print(
        "[yellow]Warning: 'chat-legacy' is deprecated. Use 'llm4ad chat' instead.[/yellow]\n"
    )
    import asyncio
    import contextlib
    import sys
    from pathlib import Path

    # Reconfigure stdout/stderr to UTF-8 so streamed agent output (which may contain
    # non-ASCII / emoji) does not crash on a legacy Windows cp1252 console.
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")

    # ---- Dependency check (friendly error before any import hang) ----
    try:
        import agentscope  # noqa: F401
    except ImportError:
        console.print(
            "[bold red]Error:[/bold red] [bold]agentscope[/bold] is not installed.\n"
            "Reinstall dependencies with:\n\n"
            "    [bold]uv sync[/bold]   (or: pip install llm4ad)\n\n"
            "Note: this project requires Python >=3.12.\n"
            f"You are running Python {sys.version_info.major}.{sys.version_info.minor}."
        )
        raise typer.Exit(code=1) from None

    from llm4ad.config.settings import load_global_settings
    from llm4ad.infra.provider.base import BaseProvider

    # ---- Provider resolution (same pattern as `chat`) ----
    global_data = load_global_settings()
    global_providers = global_data.get("providers", [])

    if not global_providers:
        console.print(
            "[bold red]Error:[/bold red] No providers found in global settings.\n"
            "Please configure a provider in [bold]~/.llm4ad/settings.yaml[/bold].\n"
            "Example:\n"
            "  providers:\n"
            '    - name: "deepseek"\n'
            '      type: "anthropic"\n'
            '      base_url: "https://api.deepseek.com/anthropic"\n'
            '      auth_token: "${DEEPSEEK_API_KEY}"\n'
            '      model: "deepseek-v4-pro"'
        )
        raise typer.Exit(code=1)

    providers_by_name = {p.get("name", "default"): p for p in global_providers}

    if provider_name is None:
        provider_cfg = global_providers[0]
        provider_name = provider_cfg.get("name", "default")
        console.print(f"[dim]Using provider: {provider_name}[/dim]")
    else:
        if provider_name not in providers_by_name:
            console.print(
                f"[bold red]Error:[/bold red] Provider '{provider_name}' not found.\n"
                f"Available: {', '.join(providers_by_name.keys())}"
            )
            raise typer.Exit(code=1)
        provider_cfg = providers_by_name[provider_name]

    # Validate base_dir
    base_dir = str(Path(output).resolve())
    Path(base_dir).mkdir(parents=True, exist_ok=True)

    # ---- Collect problem description ----
    user_content = prompt or ""
    if not user_content.strip():
        console.print(
            "[bold]AI Build (Beta)[/bold] — describe your optimization problem\n"
            "(or press Ctrl-C to cancel)"
        )
        user_content = typer.prompt("Problem description")

    # ---- Security notice ----
    console.print(
        f"\n[yellow]! The agent will run generated Python code inside "
        f"[bold]{base_dir}[/bold] with your local privileges.[/yellow]\n"
    )

    # Need a BaseProvider for the build engine; agentscope model is built inside core.
    BaseProvider.discover("llm4ad.infra.provider")

    # ---- Run agent loop (multi-turn: gather -> confirm -> build) ----
    from llm4ad.agent.runner import AgentBuildConfig, run_agent_build

    console.print("[bold green]Starting agent...[/bold green]\n")

    async def _run_turn(
        turn_input: str,
        *,
        allow_build: bool,
        prior_state: dict | None,
        proposed: dict | None,
    ) -> dict:
        """Run one agent turn; return {state, proposed, card, built, project_name}."""
        cfg = AgentBuildConfig(
            provider_config=provider_cfg,
            base_dir=base_dir,
            user_content=turn_input,
            allow_build=allow_build,
            prior_state=prior_state,
            proposed=proposed,
            max_iters=max_iters,
            surface="cli",
        )
        result: dict = {
            "state": prior_state,
            "proposed": None,
            "card": None,
            "built": False,
            "project_name": "",
        }
        async for event in run_agent_build(cfg):
            etype = event.get("type")
            if etype == "chunk":
                # Straight to stdout (UTF-8 reconfigured above), bypassing rich's
                # legacy-Windows console path which can crash on emoji.
                sys.stdout.write(event.get("content", ""))
                sys.stdout.flush()
            elif etype == "payload":
                # `proposed` marks a build-plan confirm card; `data` carries the
                # interactive choice card (ask_choice) or the confirm card body.
                result["proposed"] = event.get("proposed")
                result["card"] = event.get("data")
            elif etype == "build_result":
                bp = event.get("blueprint_data", {})
                result["built"] = bool(bp.get("built"))
                result["project_name"] = bp.get("project_name", "")
            elif etype == "agent_state":
                result["state"] = event.get("state")
            elif etype == "error":
                console.print(f"\n[bold red]Error:[/bold red] {event.get('error', '')}")
        return result

    async def _select_from_card(card: dict) -> str:
        """Render a gather-phase choice card as an interactive selector.

        Mirrors the web UI (and ``llm4ad chat``): the agent's ``ask_choice`` tool
        emits a card with preset options; here we show them as an InquirerPy menu
        so the user can arrow-select instead of typing. Options tagged for a file /
        directory pick, or the "enter your own" custom option, drop to a text
        prompt. Falls back to a plain prompt if InquirerPy or the terminal is
        unavailable.

        Args:
            card: The choice card dict ({prompt, options:[{value,label,
                description, is_custom?, ask_for_path?, ask_for_dir?}, ...]}).

        Returns:
            The chosen option value (optionally augmented with a picked path), or
            the user's free-text input.
        """
        options = card.get("options") or []
        question = (card.get("prompt") or "").strip()

        # No preset options -> behave like the old plain-text path.
        if not options:
            if question:
                console.print(Markdown(question))
            console.print()
            return typer.prompt("You")

        try:
            from InquirerPy import inquirer
            from InquirerPy.base.control import Choice
        except ImportError:
            # InquirerPy missing: show the question + options as text, then prompt.
            if question:
                console.print(Markdown(question))
            for i, opt in enumerate(options, 1):
                label = opt.get("label", opt.get("value", ""))
                desc = opt.get("description", "")
                console.print(f"  [bold]{i}.[/bold] {label}" + (f" — {desc}" if desc else ""))
            console.print()
            return typer.prompt("You")

        if question:
            console.print()
            console.print(Markdown(question))

        iq_choices = []
        for i, opt in enumerate(options):
            label = opt.get("label", opt.get("value", ""))
            desc = opt.get("description", "")
            name = f"{label} — {desc}" if desc else label
            iq_choices.append(Choice(value=i, name=name))

        try:
            selected = await inquirer.select(  # type: ignore[func-returns-value]
                message="",
                choices=iq_choices,
                pointer=">",
                qmark="",
                amark="",
                instruction="(arrow keys to move, Enter to select)",
            ).execute_async()
        except OSError:
            # Non-interactive terminal: fall back to a plain prompt.
            console.print()
            return typer.prompt("You")

        opt = options[int(selected)]
        # Custom / free-text option -> let the user type their own answer.
        if opt.get("is_custom"):
            console.print()
            return typer.prompt("You")
        # File / directory pick options -> ask for the path, keep the option's
        # meaning so the agent knows why the path was provided.
        if opt.get("ask_for_dir") or opt.get("ask_for_path"):
            what = "目录路径 / directory path" if opt.get("ask_for_dir") else "文件路径 / file path"
            path = typer.prompt(what)
            label = opt.get("label", opt.get("value", ""))
            return f"{label}: {path}" if label and not label.startswith("__") else path
        return opt.get("value", opt.get("label", ""))

    async def _run() -> bool:
        """Gather step-by-step, confirm, then build."""
        prior_state: dict | None = None
        turn_input = user_content
        # Gather loop: keep talking until the agent proposes a plan the user accepts.
        for _ in range(50):
            r = await _run_turn(
                turn_input, allow_build=False, prior_state=prior_state, proposed=None
            )
            prior_state = r["state"]
            proposed = r["proposed"]
            if proposed is None:
                # Agent asked a question. If it offered preset options (ask_choice),
                # render them as an interactive selector; otherwise plain prompt.
                card = r["card"]
                if card and card.get("options"):
                    turn_input = await _select_from_card(card)
                else:
                    console.print()
                    turn_input = typer.prompt("You")
                continue
            # Agent proposed a build plan -> show the plan card, then confirm.
            if r["card"] and (r["card"].get("prompt") or "").strip():
                console.print()
                console.print(Markdown(r["card"]["prompt"]))
            console.print()
            if typer.confirm("确认按此方案开始构建? (Confirm build?)", default=True):
                br = await _run_turn(
                    "", allow_build=True, prior_state=prior_state, proposed=proposed
                )
                if br["built"]:
                    console.print(
                        f"\n\n[bold green]Build complete![/bold green] "
                        f"Project: [bold]{br['project_name'] or '?'}[/bold]\n"
                        f"Output: {base_dir}"
                    )
                    return True
                console.print("\n[yellow]Build finished but no package was produced.[/yellow]")
                return False
            # User declined -> keep adjusting.
            console.print()
            turn_input = typer.prompt("You (keep adjusting)")
        console.print("\n[yellow]Reached max gather turns without building.[/yellow]")
        return False

    try:
        ok = asyncio.run(_run())
        raise typer.Exit(code=0 if ok else 1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(code=1) from None


def main():
    """Main entrypoint for the CLI."""
    app()


if __name__ == "__main__":
    main()
