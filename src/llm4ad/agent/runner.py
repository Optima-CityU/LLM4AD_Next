"""AI build (beta) agent runner: a single AgentScope ReAct agent (hybrid path).

Core, framework-agnostic implementation shared by the backend (wrapped in an HTTP
SSE endpoint) and the ``llm4ad chatv2`` CLI (run directly on the host). Replaces
the rigid three-stage chat-tune state machine with one agent that drives the whole
flow conversationally.

Hybrid build strategy (two verification layers, no third):

1. The agent gathers requirements (reading the workspace with read-only tools when
   present), then calls ``build_task`` — which runs the existing, proven
   :class:`BuildOrchestrator`. The orchestrator generates the package and runs its
   own deterministic validation gate before returning a blueprint, and
   ``config.yaml`` is produced programmatically inside it (never agent free-text).
2. The agent then *verifies* by running ``debug_run.py`` / ``test_evaluator.py`` via
   ``run_python``, reads any error, and calls ``rebuild_evaluator`` to fix the
   evaluator. This is the agentic self-correction layer on top of the gate.

The agent does NOT hand-write the package files; the engine does. The agent's tools
are read-only inspection plus the stateful build/rebuild operations.

Security model — a tool-level path fence:

- Inspection tools (``read_file``, ``list_dir``) and ``run_python`` resolve every
  path through :func:`llm4ad.agent.sandbox.resolve_within_sandbox` and refuse
  anything escaping ``base_dir``. No general shell, so no Bash escape surface.
  ``build_task``/``rebuild_evaluator`` only ever write into the workspace via the
  engine's ``write_task_directory``.
- In the backend the agent runs inside an isolated, single-use container (defense in
  depth). In the CLI it runs on the host: the path fence still confines file
  read/write to ``base_dir``, but ``run_python`` executes generated code with the
  invoking user's privileges — intended for local developer use, not untrusted
  multi-tenant input.

Multi-provider: ``provider_config['type']`` maps onto the matching AgentScope model
class (OpenAI / OpenAI-compatible such as DeepSeek or Qwen / Anthropic). The same
``provider_config`` also drives the engine's own LLM calls via the llm4ad
``BaseProvider``.

agentscope is a base dependency (the project requires Python >=3.12). It is still
imported lazily inside functions to keep this module's top-level import cheap.

Event protocol (dicts): ``chunk`` / ``build_result`` / ``done`` / ``error``.
"""

from __future__ import annotations

import contextlib
import subprocess
import traceback
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import SecretStr

_MAX_ERROR_LEN = 2000
_MAX_TOOL_OUTPUT = 20000
_RUN_PYTHON_TIMEOUT = 600
_DEFAULT_MAX_ITERS = 40
_MAX_REPAIR_ATTEMPTS = 10
# How many times the BUILD phase re-prompts the agent to fix a structural defect
# (seed EVOLVE file not inside version_control.local_path) before giving up.
_MAX_STRUCTURE_REPAIRS = 2


@dataclass
class AgentBuildConfig:
    """Inputs for one agent build run.

    Attributes:
        provider_config: llm4ad provider config (``type``/``api_key``/``auth_token``/
            ``base_url``/``model``). Drives both the AgentScope model and the engine.
        base_dir: Workspace/sandbox root. All tool file access is fenced here and the
            built package is written under it.
        user_content: The user's instruction for this turn.
        gathering_context: Optional prior context (may carry ``description``).
        allow_build: Phase selector. ``False`` (default) = GATHER phase: the agent
            can only inspect + ``propose_build`` (no build tools). ``True`` = BUILD
            phase (post-confirmation): the agent gets build/verify tools.
        prior_state: JSON-serialized ``AgentState`` from the previous turn, so the
            conversation memory resumes across separate invocations.
        proposed: The confirmed build plan recorded by ``propose_build`` in a prior
            gather turn; injected into the BUILD-phase task message.
        max_iters: Max agent ReAct iterations.
    """

    provider_config: dict[str, Any]
    base_dir: str
    user_content: str = ""
    gathering_context: dict[str, Any] | None = None
    allow_build: bool = False
    prior_state: dict[str, Any] | None = None
    proposed: dict[str, Any] | None = None
    max_iters: int = _DEFAULT_MAX_ITERS
    surface: str = "platform"  # "platform" (Web UI) or "cli"; shapes how the agent
    # talks about running the package (platform users don't set env vars / run the
    # CLI themselves; the platform runs it for them).


# --------------------------------------------------------------------------- #
# Provider config -> AgentScope model                                         #
# --------------------------------------------------------------------------- #


def build_model(provider_config: dict[str, Any]) -> Any:
    """Build an AgentScope chat model from an llm4ad ``provider_config``.

    Maps the provider ``type`` onto the matching AgentScope model class:

    - ``anthropic`` -> ``AnthropicChatModel`` (native Messages API).
    - everything else -> ``OpenAIChatModel`` (OpenAI Chat Completions), covering
      OpenAI, DeepSeek, Qwen, vLLM and most gateways via ``base_url``.

    ``base_url`` / ``api_key`` pass straight through, so pointing them at the backend
    LLM proxy with a one-time token needs no special-casing here.

    Args:
        provider_config: Dict with ``type``, ``api_key``, ``auth_token``,
            ``base_url``, ``model``.

    Returns:
        An AgentScope ``ChatModelBase`` instance with streaming enabled.
    """
    ptype = (provider_config.get("type") or "openai").lower()
    api_key = provider_config.get("api_key") or "EMPTY"
    auth_token = provider_config.get("auth_token") or ""
    base_url = provider_config.get("base_url") or None
    model_name = provider_config.get("model") or ""

    if ptype == "anthropic":
        from agentscope.credential import AnthropicCredential
        from agentscope.model import AnthropicChatModel

        client_kwargs: dict[str, Any] = {}
        if auth_token:
            client_kwargs["auth_token"] = auth_token
        return AnthropicChatModel(
            credential=AnthropicCredential(api_key=SecretStr(api_key), base_url=base_url),
            model=model_name,
            stream=True,
            client_kwargs=client_kwargs or None,
        )

    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    return OpenAIChatModel(
        credential=OpenAICredential(api_key=SecretStr(api_key), base_url=base_url),
        model=model_name,
        stream=True,
    )


def _build_llm_provider(provider_config: dict[str, Any]) -> Any:
    """Build the llm4ad ``BaseProvider`` the engine uses for its own LLM calls.

    Args:
        provider_config: The same provider config used for the agent model.

    Returns:
        A ``BaseProvider`` instance created via the llm4ad registry.
    """
    from llm4ad.infra.provider.base import BaseProvider

    return BaseProvider.create(provider_config["type"], config=provider_config)


def _truncate(text: str, limit: int = _MAX_TOOL_OUTPUT) -> str:
    """Truncate tool output to keep the agent context bounded."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (truncated, {len(text)} total chars)"


# Rough per-LLM-call output-token assumptions for the cost estimate. Each
# evolved candidate costs two calls: a planner call (idea) + a coder call (code).
# These are conservative mid-range guesses for a *rough* range, not a promise.
_PLANNER_TOKENS_PER_CALL = 3000
_CODER_TOKENS_PER_CALL = 3000


def estimate_evolution_cost(evolution: dict[str, Any]) -> dict[str, Any]:
    """Estimate LLM call count and token usage from evolution parameters.

    Formula (island GA, upper bound — early stopping may cut it short):
      initial_population = num_islands * island_population_size
      per_generation_offspring = num_islands * island_population_size * (1 - elite_ratio)
      total_candidates = initial_population + per_generation_offspring * max_generations
      llm_calls = total_candidates * 2   (planner + coder per candidate)
      tokens ≈ llm_calls * (planner + coder tokens per call)

    Args:
        evolution: The ``evolution`` section of a produced config.yaml.

    Returns:
        A dict with the key params, ``total_candidates``, ``llm_calls`` and a
        ``tokens_low``/``tokens_high`` output-token range (upper-bound style).
    """
    num_islands = int(evolution.get("num_islands", 1) or 1)
    pop = int(evolution.get("island_population_size", evolution.get("population_size", 1)) or 1)
    gens = int(evolution.get("max_generations", 1) or 1)
    elite = float(evolution.get("elite_ratio", 0.1) or 0.0)

    initial = num_islands * pop
    per_gen = num_islands * pop * (1.0 - elite)
    total_candidates = int(round(initial + per_gen * gens))
    llm_calls = total_candidates * 2  # planner + coder
    per_call = _PLANNER_TOKENS_PER_CALL + _CODER_TOKENS_PER_CALL
    # Give a range: low ~= half the mid guess, high = the mid guess (upper bound).
    tokens_high = llm_calls * per_call
    tokens_low = tokens_high // 2
    return {
        "num_islands": num_islands,
        "island_population_size": pop,
        "max_generations": gens,
        "elite_ratio": elite,
        "total_candidates": total_candidates,
        "llm_calls": llm_calls,
        "tokens_low": tokens_low,
        "tokens_high": tokens_high,
    }


def _fmt_tokens(n: int) -> str:
    """Format a token count compactly (e.g. 1.2M, 340K)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def build_config_summary_md(
    base_dir: str, project_name: str, language: str = "zh"
) -> str:
    """Read the produced config.yaml and render an evolution-params + cost summary.

    Returns a markdown block (heading + params table + token estimate) to append
    to the build completion message, or "" if the config can't be read/parsed.

    Args:
        base_dir: Workspace root.
        project_name: Produced project directory name.
        language: ``"zh"`` or ``"en"``.

    Returns:
        A markdown string, or "" on failure (the caller still reports success).
    """
    import yaml

    cfg_path = Path(base_dir).resolve() / project_name / "config.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    evo = cfg.get("evolution") or {}
    if not isinstance(evo, dict):
        return ""
    est = estimate_evolution_cost(evo)
    etype = evo.get("type", "island_ga")
    _zh = language == "zh"
    lines = [
        "### ⚙️ 进化参数配置" if _zh else "### ⚙️ Evolution Parameters",
        "",
        "| 参数 | 值 |" if _zh else "| Parameter | Value |",
        "|---|---|",
        f"| {'进化算法' if _zh else 'Evolution algorithm'} | {etype} |",
        f"| {'迭代代数 (max_generations)' if _zh else 'Max generations'} | {est['max_generations']} |",
        f"| {'岛屿数 (num_islands)' if _zh else 'Number of islands'} | {est['num_islands']} |",
        f"| {'每岛种群 (island_population_size)' if _zh else 'Island population size'} | {est['island_population_size']} |",
        f"| {'变异率 (mutation_rate)' if _zh else 'Mutation rate'} | {evo.get('mutation_rate', '—')} |",
        f"| {'交叉率 (crossover_rate)' if _zh else 'Crossover rate'} | {evo.get('crossover_rate', '—')} |",
        f"| {'精英比例 (elite_ratio)' if _zh else 'Elite ratio'} | {evo.get('elite_ratio', '—')} |",
        "",
        "### 💰 预计 token 消耗（粗略上限）" if _zh else "### 💰 Estimated Token Usage (rough upper bound)",
        "",
        f"- 预计生成候选算法：约 **{est['total_candidates']}** 个"
        f"（每个含 planner + coder 两次 LLM 调用，共约 **{est['llm_calls']}** 次调用）"
        if _zh else
        f"- Estimated candidate algorithms: ~**{est['total_candidates']}** "
        f"(each with 2 LLM calls — planner + coder — totalling ~**{est['llm_calls']}** calls)",
        f"- 预计输出 token：约 **{_fmt_tokens(est['tokens_low'])} ~ "
        f"{_fmt_tokens(est['tokens_high'])}**"
        if _zh else
        f"- Estimated output tokens: ~**{_fmt_tokens(est['tokens_low'])} – "
        f"{_fmt_tokens(est['tokens_high'])}**",
        "",
        ("> 这是**按迭代代数满跑**估算的上限；实际会因早停（early stopping）而更少，"
         "且不含输入 token。可通过调小 `max_generations` / `island_population_size` 降低消耗。"
         if _zh else
         "> This is a rough upper bound assuming **all generations run to completion**; "
         "actual usage will be lower due to early stopping, and input tokens are not "
         "included. Reduce `max_generations` / `island_population_size` to lower costs."),
    ]
    return "\n".join(lines)


@dataclass
class _BuildState:
    """Mutable per-run state shared with the tools.

    Holds the current blueprint/needs (so rebuild tools can act on them) and, in
    the gather phase, the plan recorded by ``propose_build`` (so the runner can
    emit a confirmation card after the turn).
    """

    blueprint: Any | None = None
    needs: Any | None = None
    project_name: str = ""
    proposed: dict[str, Any] | None = None
    summary_text: str = ""
    pending_choice: dict[str, Any] | None = None
    files_changed: bool = False


# --------------------------------------------------------------------------- #
# Tools                                                                        #
# --------------------------------------------------------------------------- #


def make_tools(
    base_dir: str,
    provider_config: dict[str, Any],
    state: _BuildState,
    *,
    allow_build: bool = False,
    language: str = "zh",
) -> list[Any]:
    """Build the agent's tools for the current phase.

    - GATHER phase (``allow_build=False``): only ``read_file``, ``list_dir`` and
      ``propose_build``. No build tools, so the agent cannot start building before
      the user confirms — a hard gate, not just a prompt instruction.
    - BUILD phase (``allow_build=True``): adds ``run_python``, ``build_task``,
      ``rebuild_evaluator``, ``revalidate``, ``write_file`` and ``edit_file``.
      ``build_task`` runs the existing :class:`BuildOrchestrator` (engine
      generates everything and runs its deterministic validation gate). If the
      gate fails, the partial package is still written to the workspace so the
      agent can fix it in place (edit_file / write_file / rebuild_evaluator) and
      re-check with ``revalidate`` — which re-runs the gate once with no
      auto-repair, so the agent's hand edits are never overwritten.

    Inspection and execution tools are fenced to ``base_dir``.

    Args:
        base_dir: Sandbox/workspace root.
        provider_config: Provider config driving the engine's LLM calls.
        state: Mutable holder for the produced blueprint / needs / proposal.
        allow_build: Whether to include the build/verify tools (BUILD phase).

    Returns:
        List of ``FunctionTool`` instances to register on the toolkit.
    """
    from agentscope.tool import FunctionTool

    from llm4ad.agent.sandbox import resolve_within_sandbox
    from llm4ad.builder.writer import write_task_directory
    from llm4ad.consultant.build_orchestrator import BuildError, BuildOrchestrator
    from llm4ad.consultant.needs import NeedsProfile

    base = Path(base_dir).resolve()

    # Save UI language under a distinct name so inner functions (which have
    # their own ``language`` parameter for the programming language) don't
    # accidentally shadow the closure variable.
    _ui_lang = language

    def _tool(func: Callable[..., Any], **kwargs: Any) -> FunctionTool:
        """Wrap a workspace tool function as an agentscope ``FunctionTool``.

        agentscope types ``FunctionTool``'s ``func`` parameter narrowly (callables
        returning ``ToolChunk``), but its adapter converts arbitrary return values —
        including the ``str`` these tools return — into a ``ToolChunk`` at runtime
        (see ``FunctionTool._convert_func_result_to_chunk``). The cast pins that one
        external-typing boundary so the tool functions themselves stay fully typed.

        Args:
            func: The workspace tool callable (returns ``str``).
            **kwargs: Extra ``FunctionTool`` options (e.g. ``is_read_only``).

        Returns:
            The constructed ``FunctionTool``.
        """
        return FunctionTool(cast(Any, func), **kwargs)

    def read_file(path: str) -> str:
        """Read a UTF-8 text file from the workspace (for inspecting user code).

        Args:
            path: Workspace-relative path to read.

        Returns:
            The file content, or an error message if missing / out of bounds.
        """
        resolved = resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes workspace: {path}"
        if not resolved.is_file():
            return f"Error: File not found: {path}"
        try:
            return _truncate(resolved.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            return f"Error reading file: {e}"

    def write_file(path: str, content: str) -> str:
        """Create or overwrite a workspace file with the given content.

        Use to apply changes the user asks for — e.g. tweak ``config.yaml`` values,
        adjust generated code, or update data. Confined to the workspace. After
        writing, re-verify with run_python.

        Args:
            path: Workspace-relative path to write (parent dirs are created).
            content: Full new file content (replaces any existing content).

        Returns:
            A confirmation, or an error if the path escapes the workspace.
        """
        resolved = resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes workspace: {path}"
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"
        state.files_changed = True
        return f"Wrote {len(content)} chars to {resolved.relative_to(base).as_posix()}"

    def edit_file(path: str, old_string: str, new_string: str) -> str:
        """Replace an exact substring in a workspace file (targeted edit).

        Prefer this over write_file for small changes (e.g. changing one
        ``config.yaml`` value like ``max_generations: 5`` -> ``max_generations: 20``):
        it avoids rewriting the whole file. ``old_string`` must occur EXACTLY once.

        Args:
            path: Workspace-relative path to edit.
            old_string: Exact text to find (must be unique in the file).
            new_string: Text to replace it with.

        Returns:
            A confirmation, or an error (not found / not unique / out of bounds).
        """
        resolved = resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes workspace: {path}"
        if not resolved.is_file():
            return f"Error: File not found: {path}"
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"Error reading file: {e}"
        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1:
            return f"Error: old_string occurs {count} times in {path}; make it unique."
        try:
            resolved.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"
        state.files_changed = True
        return f"Edited {resolved.relative_to(base).as_posix()}"

    def list_dir(path: str = ".") -> str:
        """List files and directories under a workspace path (recursive, capped).

        Args:
            path: Workspace-relative directory (defaults to the workspace root).

        Returns:
            A newline-separated listing of relative paths, or an error message.
        """
        resolved = resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes workspace: {path}"
        if not resolved.is_dir():
            return f"Error: Directory not found: {path}"
        entries: list[str] = []
        for p in sorted(resolved.rglob("*")):
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                continue
            entries.append(f"{rel}/" if p.is_dir() else rel)
            if len(entries) >= 1000:
                entries.append("... (truncated, >1000 entries)")
                break
        return "\n".join(entries) if entries else "(empty)"

    def run_python(path: str, args: str = "") -> str:
        """Run a python file inside the workspace and capture stdout/stderr.

        Use this to verify the built package: run ``<project>/debug_run.py`` or
        ``<project>/test_evaluator.py`` and read the output to find errors, then call
        ``rebuild_evaluator`` to fix them.

        Args:
            path: Workspace-relative path to the ``.py`` file to execute.
            args: Optional space-separated command-line arguments.

        Returns:
            Combined stdout/stderr and the exit code, or an error message.
        """
        resolved = resolve_within_sandbox(base, path)
        if resolved is None:
            return f"Error: Access denied, path escapes workspace: {path}"
        if not resolved.is_file():
            return f"Error: File not found: {path}"
        cmd = ["python", str(resolved), *args.split()] if args else ["python", str(resolved)]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed interpreter, sandboxed path
                cmd,
                cwd=str(resolved.parent),
                capture_output=True,
                text=True,
                timeout=_RUN_PYTHON_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"Error: execution timed out after {_RUN_PYTHON_TIMEOUT}s"
        except OSError as e:
            return f"Error executing python: {e}"
        return _truncate(
            f"[exit code {proc.returncode}]\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )

    def _reload_edits_onto_blueprint(bp: Any) -> None:
        """Reload the agent's on-disk edits onto ``bp`` in place.

        The inverse of ``write_task_directory``'s field->file mapping: reads each
        artifact file back from the workspace and, when present, overwrites the
        matching blueprint field. Missing files are left untouched (the blueprint
        keeps its in-memory value), so a package that never wrote a given file is
        not clobbered with ``None``. Keeps disk (the source of truth after hand
        edits) and the blueprint object in sync before validation / rebuild.

        Args:
            bp: The blueprint to update in place.
        """
        project_dir = base / bp.project_name

        def _read(rel: str) -> str | None:
            fp = project_dir / rel
            if not fp.is_file():
                return None
            try:
                return str(fp.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                return None

        for rel, attr in (
            ("config.yaml", "config_yaml"),
            (bp.evaluator_file_name, "evaluator_code"),
            (f"{bp.algorithm_dir_name}/{bp.algorithm_file_name}", "algorithm_code"),
            ("debug_run.py", "debug_run_code"),
            ("test_evaluator.py", "test_evaluator_code"),
            ("requirements.txt", "requirements_txt"),
        ):
            content = _read(rel)
            if content is not None:
                setattr(bp, attr, content)

    async def build_task(
        description: str,
        project_name: str = "",
        metrics_hints: str = "",
        evaluation_hints: str = "",
        data_path: str = "",
        code_path: str = "",
        language: str = "python",
        use_existing_evaluator: bool = False,
        existing_evaluator_path: str = "",
    ) -> str:
        """Generate a complete, validated LLM4AD task package from requirements.

        Runs the proven build engine: it analyzes the requirements, generates the
        evaluator, the algorithm with EVOLVE markers, sample data, config.yaml,
        debug_run.py and test_evaluator.py, and runs a deterministic validation
        gate. Call this once requirements are clear. Afterwards, verify with
        run_python and fix issues with rebuild_evaluator.

        Args:
            description: Natural-language problem description (what to optimize,
                inputs/outputs, the function to evolve).
            project_name: Optional project slug (auto-generated if empty).
            metrics_hints: Optional comma-separated metric hints
                (e.g. "minimize tour length, maximize valid").
            evaluation_hints: Optional free-form notes on how to evaluate.
            data_path: Optional workspace-relative path to existing user data.
            code_path: Optional workspace-relative path to existing user code to
                integrate instead of generating an algorithm from scratch.
            language: Algorithm language (default "python").
            use_existing_evaluator: Reuse an evaluator from the user's code instead
                of generating one.
            existing_evaluator_path: Workspace-relative path to that evaluator.

        Returns:
            A summary of the produced package (project dir + files), or the
            validation error if the gate failed.
        """
        needs = NeedsProfile(
            description=description,
            project_name=project_name or None,
            metrics_hints=[h.strip() for h in metrics_hints.split(",") if h.strip()],
            evaluation_hints=evaluation_hints,
            data_path=(str(base / data_path) if data_path else None),
            code_path=(str(base / code_path) if code_path else None),
            language=language,
            use_existing_evaluator=use_existing_evaluator,
            existing_evaluator_path=(
                str(base / existing_evaluator_path) if existing_evaluator_path else None
            ),
        )
        provider = _build_llm_provider(provider_config)
        orchestrator = BuildOrchestrator(provider, console=None, max_repair_attempts=_MAX_REPAIR_ATTEMPTS)
        try:
            blueprint = await orchestrator.build(needs)
        except BuildError as exc:
            # The engine exhausted its auto-repair budget. If a partial package
            # was produced, persist it to the workspace and record it in state so
            # the agent can fix it in place (read_file / edit_file / write_file /
            # rebuild_evaluator) and re-check with revalidate, instead of blindly
            # re-running the whole engine (which tends to reproduce the error).
            if exc.blueprint is None:
                # Failure happened before any blueprint existed (e.g. the LLM
                # analysis/generation call failed). Nothing on disk to fix in
                # place — the agent's only recourse is to retry build_task,
                # ideally with a clearer/simpler description.
                return (
                    f"Build failed before any package was produced: {exc}\n\n"
                    "No files were written, so there is nothing to fix in place. "
                    "Re-run build_task — if it keeps failing here, simplify or "
                    "clarify the description (e.g. narrow the problem, spell out "
                    "the evaluation metric)."
                )
            write_task_directory(exc.blueprint, str(base))
            state.blueprint = exc.blueprint
            state.needs = needs
            state.project_name = exc.blueprint.project_name
            failed_files = sorted(
                p.relative_to(base).as_posix()
                for p in (base / exc.blueprint.project_name).rglob("*")
                if p.is_file()
            )
            # Report which CORE artifacts made it to disk vs are missing, so the
            # agent knows whether it can fix in place (all present) or must
            # regenerate (a core file is empty/absent). A partial blueprint may
            # have generated only some artifacts before the gate failed.
            bp = exc.blueprint
            core_artifacts = {
                "config.yaml": bool(bp.config_yaml.strip()),
                bp.evaluator_file_name: bool(bp.evaluator_code.strip()),
                f"{bp.algorithm_dir_name}/{bp.algorithm_file_name}": bool(
                    bp.algorithm_code.strip()
                ),
            }
            missing = [name for name, present in core_artifacts.items() if not present]
            core_status = "\n".join(
                f"  {'[OK]' if present else '[MISSING]'} {name}"
                for name, present in core_artifacts.items()
            )
            missing_hint = (
                (
                    "\nSome CORE files are missing/empty: "
                    + ", ".join(missing)
                    + ". Fixing in place is unlikely to work — re-run build_task "
                    "to regenerate them.\n"
                )
                if missing
                else "\nAll core files are present, so fixing in place is viable.\n"
            )
            return (
                "Build did NOT pass the validation gate, but a partial package "
                "was written to the workspace so you can fix it in place.\n"
                f"Project: {bp.project_name}\n"
                f"Validation error(s):\n{exc}\n\n"
                f"Core artifacts:\n{core_status}\n"
                f"{missing_hint}\n"
                "Files on disk:\n" + "\n".join(f"  {f}" for f in failed_files) + "\n\n"
                "To fix: read the offending file with read_file, apply a targeted "
                "fix with edit_file/write_file (or rebuild_evaluator for evaluator "
                "logic), then call revalidate to re-run the validation gate. "
                "Repeat until revalidate reports the gate passed. Prefer fixing in "
                "place over calling build_task again."
            )
        write_task_directory(blueprint, str(base))
        state.blueprint = blueprint
        state.needs = needs
        state.project_name = blueprint.project_name
        files = sorted(
            p.relative_to(base).as_posix()
            for p in (base / blueprint.project_name).rglob("*")
            if p.is_file()
        )
        cfg_summary = build_config_summary_md(str(base), blueprint.project_name, _ui_lang)
        return (
            f"Build succeeded and passed the validation gate.\n"
            f"Project: {blueprint.project_name}\n"
            f"Files:\n" + "\n".join(f"  {f}" for f in files) + "\n\n"
            f"Now verify by running `{blueprint.project_name}/test_evaluator.py` and "
            f"`{blueprint.project_name}/debug_run.py` with run_python.\n\n"
            f"When you report completion to the user, INCLUDE the following evolution "
            f"config & cost summary verbatim (it is pre-computed — do not recompute):\n\n"
            + (cfg_summary or "(config summary unavailable)")
        )

    async def rebuild_evaluator(modification_request: str) -> str:
        """Modify the current package's evaluator based on a fix/feedback.

        Use after run_python reveals an evaluator problem, or to apply a user's
        requested change to evaluation logic. Requires a prior successful build_task.

        Args:
            modification_request: What to change about the evaluator (the error to
                fix, or the user's requested behavior change).

        Returns:
            A confirmation, or an error if there is no current build / it failed.
        """
        if state.blueprint is None or state.needs is None:
            return "Error: no current build. Call build_task first."
        # Sync any hand edits from disk onto the blueprint first, so the rebuild
        # starts from what's actually on disk (the agent may have edited files
        # since the last build) rather than a stale in-memory copy.
        _reload_edits_onto_blueprint(state.blueprint)
        provider = _build_llm_provider(provider_config)
        orchestrator = BuildOrchestrator(provider, console=None, max_repair_attempts=_MAX_REPAIR_ATTEMPTS)
        try:
            blueprint = await orchestrator.rebuild_evaluator(
                state.blueprint, modification_request, state.needs
            )
        except Exception as exc:  # noqa: BLE001 - surface any failure to the agent
            return f"Rebuild failed: {type(exc).__name__}: {exc}"
        write_task_directory(blueprint, str(base))
        state.blueprint = blueprint
        return (
            "Evaluator updated and rewritten to the workspace. "
            f"Re-verify with run_python on `{state.project_name}/test_evaluator.py`."
        )

    async def revalidate() -> str:
        """Re-run the deterministic validation gate on the current on-disk package.

        Use after fixing a package that failed the gate (via edit_file /
        write_file / rebuild_evaluator): this reloads your on-disk edits onto the
        current blueprint and runs the validation gate ONCE with no auto-repair,
        so your hand edits are checked as-is (never overwritten). Requires a prior
        build_task (successful or failed) that produced a package.

        Returns:
            A confirmation that the gate passed, or the remaining validation
            error(s) to fix.
        """
        from llm4ad.builder.validator import TaskValidator

        if state.blueprint is None:
            return "Error: no current package. Call build_task first."
        bp = state.blueprint

        # Sync disk edits onto the blueprint before checking.
        _reload_edits_onto_blueprint(bp)

        # Match the validation conditions the original build used, so revalidate
        # neither over- nor under-checks relative to build_task. multimodal is
        # recovered from the recorded needs (defaults False when unavailable).
        multimodal = bool(getattr(state.needs, "multimodal", False))

        # check() needs a provider for TaskValidator.__init__ but never calls the
        # LLM itself (zero auto-repair) — the whole point of this tool.
        provider = _build_llm_provider(provider_config)
        validator = TaskValidator(provider)
        bp = validator.check(bp, multimodal=multimodal)
        state.blueprint = bp

        if bp.is_valid():
            return (
                "Validation gate PASSED on the current on-disk package. "
                f"Now verify at runtime: run `{bp.project_name}/test_evaluator.py` "
                f"and `{bp.project_name}/debug_run.py` with run_python."
            )
        errors = "\n".join(f"  - {e}" for e in bp.validation_errors)
        return (
            f"Validation gate still FAILING:\n{errors}\n\n"
            "Fix the offending file and call revalidate again."
        )

    def propose_plan(
        summary: str,
        description: str,
        function_to_evolve: str = "",
        io_format: str = "",
        metrics_hints: str = "",
        evaluation_hints: str = "",
        project_name: str = "",
        data_path: str = "",
        code_path: str = "",
        language: str = "python",
        use_existing_evaluator: bool = False,
        existing_evaluator_path: str = "",
    ) -> str:
        """Record the structured Plan and hand off to user confirmation (GATHER).

        Call this ONLY when the 7 plan items are clear. It does NOT build — it
        records the Plan and shows the user a confirmation card listing all 7
        items plus a "still anything to add?" prompt. Building happens only after
        the user confirms.

        Args:
            summary: One-paragraph natural-language recap shown atop the card.
            description: Full problem description (item 1). Passed to the engine.
            function_to_evolve: Name/role of the function or algorithm to evolve
                (item 2). Leave the agent's chosen design if the user had no
                constraint.
            io_format: Input/output format of that function (item 2).
            metrics_hints: The evaluation metric(s), min/max (item 3).
            evaluation_hints: Free-form notes on how to evaluate.
            project_name: Project slug the agent proposed (item 7).
            data_path: Workspace-relative path to user-provided data, else "" to
                generate sample data (item 5).
            code_path: Workspace-relative path to user-provided code, else "" to
                generate from scratch (item 4).
            language: Programming language, default python (item 6).
            use_existing_evaluator: Reuse an evaluator found in user code (item 4).
            existing_evaluator_path: Workspace-relative path to that evaluator.

        Returns:
            A confirmation that the Plan was recorded and the user is being asked.
        """
        state.proposed = {
            "description": description,
            "function_to_evolve": function_to_evolve,
            "io_format": io_format,
            "project_name": project_name,
            "metrics_hints": metrics_hints,
            "evaluation_hints": evaluation_hints,
            "data_path": data_path,
            "code_path": code_path,
            "language": language,
            "use_existing_evaluator": use_existing_evaluator,
            "existing_evaluator_path": existing_evaluator_path,
        }
        state.summary_text = summary.strip()
        return (
            "方案已记录。用户现在看到一张包含 7 个规划项的确认卡片。"
            "请停止并等待用户决定——不要做任何其他操作。"
            if _ui_lang == "zh"
            else "Plan recorded. The user is now shown a confirmation card with "
            "the 7 plan items. Stop here and wait for their decision — "
            "do nothing else."
        )

    def ask_choice(
        question: str,
        options: list[str],
        allow_custom: bool = True,
        upload: str = "none",
    ) -> str:
        """Ask the user ONE question, offering clickable preset options (GATHER).

        Prefer this over plain-text questions: predict the likely answers and offer
        them as buttons so the user can click instead of type. Offer 2-4 concise
        options. Only one ask_choice per turn, then stop and wait for the answer.

        Attaching upload to a specific option (PREFERRED): put a ``[dir]`` or
        ``[file]`` tag at the START of that option's text. Clicking THAT option
        then opens a directory / file picker. Example:""" + (
            ' ``["由你来设计 — 我帮你设计求解函数", "[dir] 进化我现有的代码 — 上传我的项目目录让 LLM 优化"]``'
            if _ui_lang == "zh"
            else ' ``["Let you design it — I\'ll create the solver", "[dir] Evolve my existing code — Upload my project for LLM optimization"]``'
        ) + """
        This is better than a separate "upload" option because the upload lives on
        the semantically meaningful choice, so one click does it.

        Args:
            question: The single question to ask.
            options: 2-4 preset answers. Each item may be ``"Label"`` or
                ``"Label — short description"`` (em dash separates label from hint).
                Prefix an option with ``[dir]`` or ``[file]`` to make clicking it
                open a directory / file picker.
            allow_custom: If True (default), a free-text option is appended so
                the user is not boxed in.""" + (
                ' (Shown as "自行输入 / Enter your own".)'
                if _ui_lang == "zh"
                else ' (Shown as "Enter your own".)'
            ) + """
            upload: Legacy fallback: ``"file"`` / ``"dir"`` appends a standalone
                upload option. Prefer the inline ``[dir]``/``[file]`` tag instead.

        Returns:
            Confirmation that the question was shown; then stop and wait.
        """
        import re

        opts: list[dict[str, Any]] = []
        for raw in options:
            text = raw.strip()
            ask_dir = ask_file = False
            # Inline upload tag at the start: [dir] / [file] (also 目录/文件).
            m = re.match(r"^\[(dir|file|目录|文件)\]\s*", text, flags=re.IGNORECASE)
            if m:
                tag = m.group(1).lower()
                ask_dir = tag in ("dir", "目录")
                ask_file = tag in ("file", "文件")
                text = text[m.end():]
            if " — " in text:
                label, desc = text.split(" — ", 1)
            elif " - " in text:
                label, desc = text.split(" - ", 1)
            else:
                label, desc = text, ""
            opt: dict[str, Any] = {
                "value": text,
                "label": label.strip(),
                "description": desc.strip(),
            }
            if ask_dir:
                opt["ask_for_dir"] = True
            elif ask_file:
                opt["ask_for_path"] = True
            opts.append(opt)
        if upload == "file":
            opts.append({
                "value": "__upload_file__",
                "label": "上传文件 / Upload a file" if _ui_lang == "zh" else "Upload a file",
                "description": "选择本地文件提供给助手" if _ui_lang == "zh" else "Select a local file for the assistant",
                "ask_for_path": True,
            })
        elif upload == "dir":
            opts.append({
                "value": "__upload_dir__",
                "label": "上传目录 / Upload a directory" if _ui_lang == "zh" else "Upload a directory",
                "description": "选择本地项目目录提供给助手" if _ui_lang == "zh" else "Select a local project directory for the assistant",
                "ask_for_dir": True,
            })
        if allow_custom:
            opts.append({
                "value": "__custom__",
                "label": "自行输入 / Enter your own" if _ui_lang == "zh" else "Enter your own",
                "description": "",
                "is_custom": True,
            })
        state.pending_choice = {"question": question.strip(), "options": opts}
        return (
            "Options shown to the user. Stop here and wait for their selection — "
            "do not ask anything else or take other actions this turn."
        )

    if not allow_build:
        # GATHER phase: inspection + ask + propose only. No build tools => hard gate.
        return [
            _tool(read_file, is_read_only=True),
            _tool(list_dir, is_read_only=True),
            _tool(ask_choice),
            _tool(propose_plan),
        ]

    # BUILD phase (post-confirmation): full build + verify + edit toolset.
    return [
        _tool(read_file, is_read_only=True),
        _tool(list_dir, is_read_only=True),
        _tool(run_python),
        _tool(build_task),
        _tool(rebuild_evaluator),
        _tool(revalidate),
        _tool(write_file),
        _tool(edit_file),
    ]


# --------------------------------------------------------------------------- #
# Agent loop                                                                  #
# --------------------------------------------------------------------------- #


def _blueprint_data(state: _BuildState, base_dir: str) -> dict[str, Any]:
    """Build the ``build_result`` payload describing what to persist.

    Reports ``built: True`` when either the engine produced a blueprint this turn
    OR the agent edited files (follow-up change), so the backend re-uploads the
    (possibly edited) package. ``project_name`` is taken from the blueprint, else
    detected from the workspace (the top-level dir containing a config.yaml).

    Args:
        state: Build state (blueprint may be None on a pure follow-up edit).
        base_dir: Workspace root, used to detect the project dir when editing.

    Returns:
        A dict with ``project_name`` and ``built``.
    """
    if state.blueprint is not None:
        data = asdict(state.blueprint)
        data["built"] = True
        return data
    if state.files_changed:
        project = state.project_name or _detect_project_dir(base_dir)
        if project:
            return {"project_name": project, "built": True}
    return {"project_name": "", "built": False}


def _detect_project_dir(base_dir: str) -> str:
    """Detect the produced project dir: the top-level dir holding a config.yaml.

    Args:
        base_dir: Workspace root.

    Returns:
        The project directory name, or "" if none found.
    """
    base = Path(base_dir).resolve()
    try:
        for cfg in sorted(base.glob("*/config.yaml")):
            return cfg.parent.relative_to(base).as_posix()
        for p in sorted(base.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                return p.name
    except OSError:
        pass
    return ""


def _confirm_build_card(
    summary: str,
    proposed: dict[str, Any] | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    """Build the confirm/decline card shown after a gather proposal.

    Renders the structured 7-item Plan in the card prompt (the frontend displays
    ``prompt`` as the card body), followed by an "anything to add?" cue, then
    confirm / keep-adjusting buttons. Matches the frontend-recognized
    ``kind:"choice", stage:"confirm_build"`` shape so the existing renderer and
    submission routing work unchanged.

    Args:
        summary: The agent's one-paragraph task recap.
        proposed: The structured plan dict recorded by ``propose_plan``.
        language: ``"zh"`` or ``"en"``.

    Returns:
        The payload dict for a ``{"type": "payload", "data": ...}`` event.
    """
    import uuid

    def _md_cell(text: str) -> str:
        """Escape a value for use inside a markdown table cell."""
        return str(text).replace("|", "\\|").replace("\n", " ")

    _zh = language == "zh"
    lines: list[str] = []
    if proposed:
        # Render the plan as markdown (heading + table), the SAME style the agent
        # uses for its post-build summary, so the card matches that clean look.
        # The frontend renders card.prompt with the markdown component.
        code_src = (
            f"复用已有代码 `{proposed['code_path']}`"
            if proposed.get("code_path")
            else "从零生成"
        ) if _zh else (
            f"Reuse existing code `{proposed['code_path']}`"
            if proposed.get("code_path")
            else "Generate from scratch"
        )
        eval_src = (
            f"复用已有评估器 `{proposed['existing_evaluator_path']}`"
            if proposed.get("use_existing_evaluator")
            else "自动生成"
        ) if _zh else (
            f"Reuse existing evaluator `{proposed['existing_evaluator_path']}`"
            if proposed.get("use_existing_evaluator")
            else "Auto-generate"
        )
        data_src = (
            f"使用已有数据 `{proposed['data_path']}`"
            if proposed.get("data_path")
            else "自动生成样本"
        ) if _zh else (
            f"Use existing data `{proposed['data_path']}`"
            if proposed.get("data_path")
            else "Generate sample data"
        )
        fn = proposed.get("function_to_evolve") or ("由助手设计" if _zh else "Designed by assistant")
        io = proposed.get("io_format") or ("由助手设计" if _zh else "Designed by assistant")
        metric_default = "(未填)" if _zh else "(not specified)"
        metric = proposed.get("metrics_hints") or proposed.get("evaluation_hints") or metric_default
        lines = [
            "## 📋 构建方案确认" if _zh else "## 📋 Build Plan Confirmation",
            "",
            "| # | 项目 | 内容 |" if _zh else "| # | Item | Details |",
            "|---|---|---|",
            f"| 1 | {'问题描述' if _zh else 'Problem description'} | {_md_cell(proposed.get('description') or ('(未填)' if _zh else '(not specified)'))} |",
            f"| 2 | {'进化目标' if _zh else 'Evolution target'} | {_md_cell(fn)} |",
            f"| 3 | {'输入 / 输出' if _zh else 'Input / Output'} | {_md_cell(io)} |",
            f"| 4 | {'评估指标' if _zh else 'Evaluation metric'} | {_md_cell(metric)} |",
            f"| 5 | {'代码来源' if _zh else 'Code source'} | {_md_cell(code_src)}（{'评估器' if _zh else 'Evaluator'}：{_md_cell(eval_src)}） |",
            f"| 6 | {'数据来源' if _zh else 'Data source'} | {_md_cell(data_src)} |",
            f"| 7 | {'语言 / 项目名' if _zh else 'Language / Project'} | {_md_cell(proposed.get('language') or 'python')} / "
            f"`{_md_cell(proposed.get('project_name') or ('自动' if _zh else 'auto'))}` |",
            "",
            "还有要补充或修改的吗？**确认无误我就开始构建** 🚀"
            if _zh else
            "Anything to add or revise? **I'll start building once confirmed** 🚀",
        ]
    elif summary:
        lines.append(summary.strip())

    prompt = "\n".join(lines) if lines else ("需求已明确，是否开始构建？" if _zh else "Requirements are clear — shall I start building?")
    return {
        "cardId": f"card-{uuid.uuid4().hex[:8]}",
        "kind": "choice",
        "stage": "confirm_build",
        "prompt": prompt,
        "hint": "",
        "options": [
            {
                "value": "confirm_build",
                "label": "确认构建" if _zh else "Confirm Build",
                "description": "按上述方案开始构建任务" if _zh else "Start building the task as specified above",
            },
            {
                "value": "decline_build",
                "label": "继续调整" if _zh else "Keep Adjusting",
                "description": "继续对话补充或修改需求" if _zh else "Continue the conversation to refine requirements",
            },
        ],
    }


def _choice_card(pending: dict[str, Any]) -> dict[str, Any]:
    """Build a gather-phase question card with clickable preset options.

    Uses the frontend-recognized ``kind:"choice"`` shape with stage
    ``run_needs_gathering`` (rendered generically as option buttons, plus custom
    free-text and file/dir pickers per option flags).

    Args:
        pending: ``{"question": str, "options": [ {value,label,description,
            is_custom?, ask_for_path?, ask_for_dir?}, ... ]}`` from ``ask_choice``.

    Returns:
        The payload dict for a ``{"type": "payload", "data": ...}`` event.
    """
    import uuid

    return {
        "cardId": f"card-{uuid.uuid4().hex[:8]}",
        "kind": "choice",
        "stage": "run_needs_gathering",
        "prompt": pending.get("question", ""),
        "hint": "",
        "options": pending.get("options", []),
    }


def _validate_package_structure(base_dir: str, project_name: str) -> str | None:
    """Check the produced package can actually be evolved; return a defect or None.

    The evolution engine evolves code between ``EVOLVE_START`` / ``EVOLVE_END``
    markers found by scanning ``version_control.local_path`` (resolved relative to
    the directory holding ``config.yaml``). If the seed algorithm file was written
    somewhere else — the common failure when the agent hand-writes files instead of
    calling ``build_task`` — that directory has no evolvable block, and a real run
    dies mid-evolution with a cryptic ``InitSampler requires ... at least one
    evolvable block`` error. Catching it here, before reporting success, lets the
    agent fix placement while it still has the file tools.

    Args:
        base_dir: Workspace root the package was written under.
        project_name: Produced project dir name (may be "" for a hand-write build;
            detected from the workspace in that case).

    Returns:
        A human-readable defect description if the package cannot be evolved, or
        ``None`` if it looks correct (or could not be checked — fail open, never
        block a build over a validator hiccup).
    """
    import yaml

    from llm4ad.infra.repo_analyzer.evolve_detector import EvolveDetector

    try:
        base = Path(base_dir).resolve()
        project = project_name or _detect_project_dir(base_dir)
        if not project:
            return None  # No project dir detected; nothing we can assert.
        project_dir = base / project
        config_path = project_dir / "config.yaml"
        if not config_path.is_file():
            return None  # No config to reason about; leave it to other checks.

        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        vc = cfg.get("version_control") or {}
        local_path = vc.get("local_path") or "."
        analyzer_cfg = cfg.get("repo_analyzer") or {}

        # local_path is relative to the directory containing config.yaml.
        repo_dir = (project_dir / local_path).resolve()
        if not repo_dir.is_dir():
            return (
                f"The config's version_control.local_path is '{local_path}', but "
                f"'{project}/{local_path}' does not exist. The seed algorithm file "
                f"(with EVOLVE_START/EVOLVE_END markers) must live inside that "
                f"directory so the engine can evolve it."
            )

        detector = EvolveDetector({
            "include": analyzer_cfg.get("include", ["*.py"]),
            "exclude": analyzer_cfg.get("exclude", [".git/**", "__pycache__/**", "*.pyc"]),
        })
        analyzed = detector.analyze(repo_dir)
        if len(analyzed.evolvable_blocks) == 0:
            return (
                f"No EVOLVE_START/EVOLVE_END block was found under the configured "
                f"version_control.local_path ('{local_path}' -> '{project}/{local_path}'). "
                f"The seed algorithm file with the markers must be written INSIDE that "
                f"directory (the engine scans only local_path). It is likely written "
                f"elsewhere in the package (e.g. the project root) instead."
            )
    except Exception:  # noqa: BLE001 - a validator must never break a build report.
        return None
    return None


def _structure_repair_message(defect: str) -> str:
    """Build the corrective instruction re-fed to the agent for a structural defect.

    Args:
        defect: The defect description from :func:`_validate_package_structure`.

    Returns:
        A user-role message telling the agent exactly what to fix and how to verify.
    """
    return (
        "The package is not runnable yet — a structural problem will make the "
        f"evolution run fail:\n\n{defect}\n\n"
        "Fix it now: read config.yaml to confirm version_control.local_path, then "
        "make sure the algorithm file with the EVOLVE_START/EVOLVE_END markers is "
        "written INSIDE that directory (move/write it there with write_file if "
        "needed), keeping the file name consistent with what the evaluator loads. "
        "Then verify with list_dir on that directory and re-run test_evaluator.py. "
        "Do not report completion until an EVOLVE block exists under local_path."
    )


async def run_agent_build(config: AgentBuildConfig) -> AsyncIterator[dict[str, Any]]:
    """Drive the AgentScope build agent for one turn, yielding SSE-shaped events.

    Two-phase, memory-preserving:

    - GATHER (``config.allow_build=False``): the agent asks one question at a time
      (no build tools). If it calls ``propose_build``, a confirm-build ``payload``
      card is emitted at the end of the turn.
    - BUILD (``config.allow_build=True``): the agent builds from the confirmed
      plan and self-verifies.

    The agent's ``AgentState`` is restored from ``config.prior_state`` at the start
    and emitted as an ``agent_state`` event at the end, so conversation memory
    resumes across separate invocations.

    Args:
        config: Inputs for this run.

    Yields:
        Event dicts: ``chunk``, ``payload`` (confirm card), ``build_result``,
        ``agent_state`` (memory snapshot), ``done``, or ``error``.
    """
    from agentscope.agent import Agent, ReActConfig
    from agentscope.event import (
        TextBlockDeltaEvent,
        ToolCallStartEvent,
        ToolResultEndEvent,
    )
    from agentscope.message import UserMsg
    from agentscope.permission import PermissionMode
    from agentscope.state import AgentState
    from agentscope.tool import Toolkit

    from llm4ad.agent.skill import (
        build_gather_system_prompt,
        build_gather_task_message,
        build_system_prompt,
        build_task_message,
    )

    try:
        state = _BuildState()
        model = build_model(config.provider_config)
        language = (config.gathering_context or {}).get("language", "zh")
        toolkit = Toolkit(
            tools=make_tools(
                config.base_dir,
                config.provider_config,
                state,
                allow_build=config.allow_build,
                language=language,
            )
        )
        system_prompt = (
            build_system_prompt(config.base_dir, config.surface, language)
            if config.allow_build
            else build_gather_system_prompt(config.base_dir, language)
        )

        # Restore prior conversation memory if present (resume across turns).
        prior_state = None
        if config.prior_state:
            try:
                prior_state = AgentState.model_validate(config.prior_state)
            except Exception:  # noqa: BLE001 - corrupt/incompatible state: start fresh
                prior_state = None

        agent = Agent(
            name="llm4ad-builder",
            system_prompt=system_prompt,
            model=model,
            toolkit=toolkit,
            react_config=ReActConfig(max_iters=config.max_iters),
            state=prior_state,
        )
        # No human to confirm tool prompts; the only tools are the fenced ones
        # above, so bypass the prompt (the path fence is the guarantee).
        agent.state.permission_context.mode = PermissionMode.BYPASS

        if config.allow_build:
            task_text = build_task_message(config.proposed, config.user_content)
        else:
            task_text = build_gather_task_message(
                config.gathering_context or {}, config.user_content
            )
        # UserMsg factory wraps a plain string in a TextBlock; Msg(content=str)
        # alone would fail validation (content must be a list of blocks).
        inputs = UserMsg(name="user", content=task_text)

        # In the GATHER phase, ask_choice / propose_plan are turn-ending: once the
        # agent has asked the user something (or proposed the plan), the turn is
        # over and we wait for the user's reply. AgentScope's ReAct loop would
        # otherwise keep reasoning and self-answer its own questions. Breaking the
        # stream here is safe: _execute_tool_call saves the tool call AND its
        # result to context BEFORE yielding ToolResultEndEvent, so the persisted
        # AgentState is consistent for the next turn.
        turn_ending_tools = {"ask_choice", "propose_plan"}
        tool_names: dict[str, str] = {}  # tool_call_id -> tool name

        async def _pump(msg: Any) -> AsyncIterator[dict[str, Any]]:
            """Drive one ``reply_stream`` on the shared agent, yielding chunk events.

            Runs the agent's ReAct loop for ``msg`` and forwards text/tool activity
            as ``chunk`` events. In the GATHER phase it breaks after a turn-ending
            tool (``ask_choice`` / ``propose_plan``) so the turn ends and we wait for
            the user — safe because the tool call + result are persisted to context
            before the break. Usable more than once (same agent, memory intact), so
            the BUILD phase can re-drive it to repair a structural defect.

            Args:
                msg: The user-role message to reply to.

            Yields:
                ``chunk`` event dicts.
            """
            _stream = agent.reply_stream(msg)
            try:
                async for event in _stream:
                    if isinstance(event, TextBlockDeltaEvent):
                        if event.delta:
                            yield {"type": "chunk", "content": event.delta}
                    elif isinstance(event, ToolCallStartEvent):
                        tool_names[event.tool_call_id] = event.tool_call_name
                        yield {"type": "chunk", "content": f"\n\n🔧 `{event.tool_call_name}` ...\n"}
                    elif isinstance(event, ToolResultEndEvent):
                        yield {"type": "chunk", "content": "✓\n"}
                        if (
                            not config.allow_build
                            and tool_names.get(event.tool_call_id) in turn_ending_tools
                        ):
                            break
            finally:
                # Close the reply generator cleanly (harmless if already exhausted).
                aclose = getattr(_stream, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(Exception):
                        await aclose()

        async for ev in _pump(inputs):
            yield ev

        # GATHER phase: emit an interactive card. A pending question (ask_choice)
        # takes priority over a proposal; the two are mutually exclusive because
        # the agent stops after either.
        if not config.allow_build and state.pending_choice is not None:
            yield {"type": "payload", "data": _choice_card(state.pending_choice)}
        elif not config.allow_build and state.proposed is not None:
            yield {
                "type": "payload",
                "data": _confirm_build_card(state.summary_text, state.proposed, language),
                "proposed": state.proposed,
            }

        # BUILD phase: before reporting success, verify the package is actually
        # evolvable — the seed EVOLVE file must sit inside version_control.local_path.
        # If not (the common hand-write mistake), re-drive the agent to fix placement
        # rather than shipping a package that dies mid-run with a cryptic InitSampler
        # error. Only check when a build/edit actually happened this turn.
        if config.allow_build:
            if _blueprint_data(state, config.base_dir).get("built"):
                for _ in range(_MAX_STRUCTURE_REPAIRS):
                    defect = _validate_package_structure(config.base_dir, state.project_name)
                    if defect is None:
                        break
                    yield {"type": "chunk", "content": f"\n\n⚠️ {defect}\n"}
                    async for ev in _pump(
                        UserMsg(name="user", content=_structure_repair_message(defect))
                    ):
                        yield ev
            yield {"type": "build_result", "blueprint_data": _blueprint_data(state, config.base_dir)}

        # Persist conversation memory for the next turn (not forwarded to the user).
        state_dump: dict[str, Any] | None = None
        try:
            state_dump = agent.state.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - never fail the turn over serialization
            state_dump = None
        if state_dump is not None:
            yield {"type": "agent_state", "state": state_dump}

        yield {"type": "done"}
    except Exception as exc:
        tb = traceback.format_exc()
        msg = f"{type(exc).__name__}: {exc}\n{tb}"[:_MAX_ERROR_LEN]
        yield {"type": "error", "error": msg}


__all__ = [
    "AgentBuildConfig",
    "build_model",
    "make_tools",
    "run_agent_build",
]
