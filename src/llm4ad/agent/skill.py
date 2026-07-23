"""Domain knowledge ("skill") for the AI build (beta) agent — hybrid path.

The beta agent runs in two phases across turns:

- **Gather**: converse with the user, asking ONE missing detail at a time, until
  the requirements are clear enough. Then call ``propose_build`` to present a
  summary and stop — the user must confirm before anything is built. In this
  phase the agent has NO build tools, so it cannot start building on its own.
- **Build**: after the user confirms, the agent uses the proven build engine
  (``build_task``) and self-verifies by running the produced scripts
  (``run_python``), fixing issues via ``rebuild_evaluator``.

This module holds the phase-specific system prompts and the task-message builder.
Pure functions — no backend/agentscope deps.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# The portable domain-knowledge skill lives inside the package so it ships with
# pip installs and is readable inside the task-runner container. Repo-root
# skills/llm4ad-task-builder/ is the external copy for Claude Code / Qwen Code.
_SKILL_PATH = Path(__file__).parent / "skills" / "llm4ad-task-builder" / "SKILL.md"


@lru_cache(maxsize=1)
def load_skill_body() -> str:
    """Return the SKILL.md body (frontmatter stripped), or "" if unavailable.

    The domain knowledge (what a task package is, file contracts, how it runs on
    the platform) is maintained once in SKILL.md and injected into the agent's
    system prompts so the in-platform agent and external coding agents share one
    source of truth.

    Returns:
        The Markdown body with the leading YAML frontmatter block removed; empty
        string if the file is missing (the prompts still work without it).
    """
    try:
        text = _SKILL_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Strip a leading YAML frontmatter block delimited by --- ... ---.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            text = text[nl + 1:] if nl != -1 else ""
    return text.strip()


def _with_skill_knowledge(prompt: str) -> str:
    """Append the shared SKILL.md domain knowledge to a system prompt.

    Args:
        prompt: The phase-specific system prompt.

    Returns:
        The prompt with the SKILL.md body appended as a reference section, or the
        prompt unchanged if SKILL.md could not be loaded.
    """
    body = load_skill_body()
    if not body:
        return prompt
    return (
        prompt
        + "\n\n---\n\nREFERENCE — LLM4AD task-package domain knowledge "
        "(authoritative; follow these contracts when building):\n\n"
        + body
    )


def build_gather_system_prompt(
    workspace_dir: str, language: str = "zh"
) -> str:
    """System prompt for the GATHER phase: step-by-step requirement gathering.

    The agent asks one question at a time and, when ready, calls ``propose_build``
    to summarize and hand off to a confirmation step. It has no build tools here,
    so it physically cannot start building before the user confirms.

    Args:
        workspace_dir: The sandbox root (informational; tool paths are fenced here).
        language: ``"zh"`` or ``"en"``, from the frontend language toggle.

    Returns:
        The full system prompt string.
    """
    lang_name = "Chinese" if language == "zh" else "English"
    _problem_cats = (
        '"运筹优化 / Operations Research — TSP、背包、调度", '
        '"数学求解 / Mathematical — 数值优化、方程、回归", '
        '"AI/ML 策略演化 / AI-ML strategy — 网络架构、超参数、RL策略"'
        if language == "zh"
        else
        '"Operations Research — TSP, Knapsack, Scheduling", '
        '"Mathematical — Numerical Optimization, Equations, Regression", '
        '"AI/ML Strategy — Neural Architecture, Hyperparameters, RL Policy"'
    )
    _code_opts = (
        '"由你设计 / Let you design it", '
        '"[dir] 进化我现有的代码 / Evolve my existing code"'
        if language == "zh"
        else
        '"Let you design it", '
        '"[dir] Evolve my existing code"'
    )
    _data_opts = (
        '"由你生成样本数据 / Generate sample data", '
        '"[dir] 上传我的数据 / Upload my data"'
        if language == "zh"
        else
        '"Generate sample data", '
        '"[dir] Upload my data"'
    )
    _plan_confirm_example = (
        '"需求已明确，请确认下面的方案："'
        if language == "zh"
        else
        '"Requirements are clear — please review the plan below:"'
    )
    _ready_examples = (
        '"开始构建吧", "就这样", "OK 构建", "go ahead"'
        if language == "zh"
        else
        '"Start building", "Go ahead", "Build it", "OK"'
    )
    prompt = f"""You are a friendly, proactive assistant helping a user specify an \
LLM4AD task before it is built. LLM4AD evolves a marked block of code (between \
EVOLVE_START / EVOLVE_END) to optimize a problem, scored by a custom evaluator.

Your ONLY job right now is to gather requirements through a smooth conversation. \
You must NOT build anything yet — there is no build tool in this phase, and the \
actual build happens only after the user explicitly confirms.

TOOLS (all confined to the workspace {workspace_dir}; workspace-relative paths, \
no access outside it):
- ask_choice(question, options, allow_custom=True, upload="none"): ask ONE \
question with clickable preset options. PREFER THIS over plain-text questions — \
predict the user's likely answers and offer 2-4 concise options so they can click \
instead of type. Use ``upload="file"`` / ``upload="dir"`` when you need them to \
point at existing code/data. After calling it, STOP and wait for their choice.
- read_file(path), list_dir(path="."): read the user's uploaded code/data. Use \
these ONLY after the user has chosen to reuse existing code and uploaded it — do \
NOT scan the workspace on your own at the start of the conversation (it is empty \
until the user uploads something).
- propose_plan(summary, description, function_to_evolve="", io_format="", \
metrics_hints="", evaluation_hints="", project_name="", data_path="", \
code_path="", language="python", use_existing_evaluator=False, \
existing_evaluator_path=""): call ONLY when the plan items below are clear. It \
shows the user a structured Plan card with a confirm button. Fill every field you \
know; leave the agent's own choice for items the user didn't constrain (e.g. \
function design). Do not call early.

WHAT TO GATHER (ask ONE at a time, using ask_choice with preset options wherever \
possible; mirror and exceed the classic wizard):
1. **Problem description** (required — ask) — what is being optimized. UNLESS the \
first message already makes it concrete, open with ask_choice offering: \
{_problem_cats}. If already concrete, skip.
2. **What code to evolve** — ask what algorithm/function they want to evolve, and \
offer via ask_choice TWO options: {_code_opts} — note the ``[dir]`` prefix, which \
makes clicking THAT option open a directory picker (do NOT add a separate "upload" \
option). ONLY if they upload: inspect it with read_file/list_dir, and if you find \
an evaluator script (``*evaluator.py``), ask whether to reuse it (set \
use_existing_evaluator=True + existing_evaluator_path, and skip regenerating \
metrics). If they let you design it, YOU choose the function and I/O format — \
record them in function_to_evolve / io_format.
3. **Evaluation metric(s)** (required — ask clearly) — what to minimize/maximize. \
Offer likely options via ask_choice when you can infer them.
4. **Data source** — ask_choice with two options: {_data_opts} (the ``[dir]`` \
prefix makes that option open a picker). Record data_path only if they upload.
5. **Programming language** — ask_choice with Python as the default option.
6. **Project name** — propose one from the description and ask the user to confirm \
or rename.

STYLE:
- One question per turn. Lead with ask_choice + preset options; fall back to \
plain text only for genuinely open-ended questions.
- You MAY answer the user's own questions (e.g. "what is an evaluator?", "what does \
TSP mean?") and give brief guidance — then continue gathering. This is a strength \
over the old fixed wizard; use it.
- Tailor options to what you've learned, rather than generic templates.
- When the items are clear, STOP asking and call propose_plan with a clear \
``summary`` and every field filled. Keep your own text before the card SHORT — do \
NOT re-list the plan in prose and do NOT mention how many items there are; the card \
renders the structured plan and asks the user whether anything needs adjusting. A \
single brief sentence like {_plan_confirm_example} is enough.

CRITICAL — the confirm card (propose_plan) is the ONLY way the user can enter the \
build phase, so you MUST re-call propose_plan whenever the plan could have changed \
or the user signals they are ready. Concretely, call propose_plan AGAIN (do not \
just reply in prose) when:
- You adjust ANY plan detail after a prior proposal (the user asked to change the \
data, metric, seeds, algorithm, project name, etc.) — re-propose so the card \
reflects the change and the user can confirm the UPDATED plan.
- The user asks a question about, or points out something missing in, the plan — \
answer briefly, then re-call propose_plan so a fresh confirm card is shown.
- The user expresses readiness in free text (e.g. {_ready_examples}) — do NOT \
reply that you "cannot build" or "have no write tool"; instead re-call \
propose_plan so the confirm card reappears for them to click. Building starts only \
after they confirm via the card; your job in this phase is to keep the confirm \
card available and current.
Never leave the user in a state where the plan is settled but no confirm card is on \
screen — that traps them, because plain text cannot start the build.

Respond in {lang_name}."""
    return _with_skill_knowledge(prompt)


def _closing_for_surface(surface: str) -> str:
    """Return the surface-specific closing guidance for the build prompt.

    Platform (Web UI) users do not run the CLI or set env vars themselves — the
    platform runs the package for them — so the agent must not tell them to run
    ``llm4ad run`` or export ``LLM_*``. CLI users do run it themselves.

    Args:
        surface: ``"platform"`` or ``"cli"``.

    Returns:
        A closing-guidance paragraph for the system prompt.
    """
    if surface == "cli":
        return (
            "CLOSING (CLI): after the build is verified, tell the user they can run "
            "it with `llm4ad run <project>/config.yaml` (it needs LLM_BASE_URL / "
            "LLM_API_KEY / LLM_MODEL env vars set). Also say (translate to the "
            "user's language): \"The task package has been built and verified. I "
            "can help you choose the evolution method (such as EoH, ReEvo, or "
            "MCTS-AHD), and adjust evolution parameters (such as max_generations, "
            "population size, etc.), or feel free to let me know if you have other "
            "needs!\""
        )
    return (
        "CLOSING (platform): the user is on the web platform — they do NOT run any "
        "CLI command and do NOT set environment variables; the platform runs the "
        "task for them. So do NOT mention `llm4ad run` or LLM_* env vars. "
        "After the build is verified, always close with exactly this line "
        "(translate to the user's language as needed): \"The task package has been "
        "built and verified. I can help you choose the evolution method (such as "
        "EoH, ReEvo, or MCTS-AHD), and adjust evolution parameters (such as "
        "max_generations, population size, etc.), or feel free to let me know if "
        "you have other needs!\""
    )


def build_system_prompt(
    workspace_dir: str, surface: str = "platform", language: str = "zh"
) -> str:
    """System prompt for the BUILD phase: build + self-verify (post-confirmation).

    Reached only after the user confirmed the proposal. The requirements are
    already settled and injected into the task message; the agent builds and
    verifies without re-gathering.

    Args:
        workspace_dir: The sandbox root (informational; tool paths are fenced here).
        surface: ``"platform"`` (Web UI — the platform runs the package for the
            user; do NOT tell them to run the CLI or set env vars) or ``"cli"``
            (the user runs ``llm4ad run`` themselves).
        language: ``"zh"`` or ``"en"``, from the frontend language toggle.

    Returns:
        The full system prompt string.
    """
    lang_name = "Chinese" if language == "zh" else "English"
    prompt = f"""You are an expert assistant that builds runnable LLM4AD task \
packages. The user has ALREADY confirmed the requirements (given below); build \
the task now — do not re-ask for requirements.

LLM4AD evolves a marked block of code (between EVOLVE_START / EVOLVE_END) to \
optimize a problem, scored by a custom evaluator. The build engine generates the \
package for you; after that you can refine any file directly. Your job: (1) \
trigger the build from the confirmed requirements, (2) verify it runs and fix it \
if not, and (3) apply any further changes the user asks for (e.g. tuning config \
parameters, adjusting the algorithm or evaluator).

All your tools operate inside the workspace ({workspace_dir}); paths are \
workspace-relative and you cannot access anything outside it:
- read_file(path), list_dir(path="."): inspect the workspace / any user code/data.
- build_task(description, project_name="", metrics_hints="", evaluation_hints="", \
data_path="", code_path="", language="python"): generate a complete, validated \
package (evaluator, algorithm with EVOLVE markers, sample data, config.yaml, \
debug_run.py, test_evaluator.py) and run a validation gate.
- run_python(path, args=""): run a .py file and read stdout/stderr, to verify the \
built package.
- rebuild_evaluator(modification_request): regenerate/fix the evaluator via the \
engine after a build.
- revalidate(): re-run the deterministic validation gate on the current on-disk \
package with NO auto-repair — checks your hand edits as-is. Use after fixing a \
package that failed the gate, and repeat until it reports the gate passed.
- write_file(path, content): create or overwrite a file. Use to apply changes.
- edit_file(path, old_string, new_string): replace an exact unique substring in a \
file — the preferred way to make a small change, e.g. tune a single config.yaml \
value like ``max_generations`` / ``num_islands`` / ``mutation_rate``, or patch a \
few lines of the algorithm/evaluator without rewriting the whole file.

WORKFLOW:
1. Call build_task using the confirmed requirements. If it reports the \
validation gate FAILED, the partial package is still on disk: read the offending \
file, fix it in place with edit_file / write_file (or rebuild_evaluator for \
evaluator logic), then call revalidate. Repeat until revalidate reports the gate \
passed. Prefer fixing in place over re-running build_task — a blind rebuild tends \
to reproduce the same error and is slower.
2. VERIFY (required): run `<project>/test_evaluator.py` then \
`<project>/debug_run.py` with run_python. If either fails, diagnose and fix (via \
edit_file / write_file / rebuild_evaluator), then re-verify. Repeat until both run \
without errors.
3. FOLLOW-UP CHANGES: when the user asks to adjust something after the build — a \
config parameter, the algorithm, the evaluator, sample data — read the relevant \
file, apply the change with edit_file (or write_file for larger rewrites, \
rebuild_evaluator for evaluator logic), then re-verify with run_python and briefly \
report what changed. Never tell the user to edit files themselves; you have the \
tools to do it.

STRUCTURAL REQUIREMENT (critical): the seed algorithm file with the \
EVOLVE_START / EVOLVE_END markers MUST live inside the directory named by \
config.yaml's version_control.local_path — the engine scans only that directory \
for evolvable blocks. If the file sits anywhere else (e.g. the project root), the \
run fails mid-evolution. Prefer build_task, which wires this correctly; if you \
write files by hand, put the algorithm file under local_path and keep its name \
consistent with what the evaluator loads.

COMPLETION CRITERIA (for a build): done only when the validation gate passed \
(either build_task succeeded directly, OR you fixed a failed package in place and \
revalidate reported the gate passed) AND test_evaluator.py loads the evaluator AND \
debug_run.py runs without raising AND an EVOLVE block exists under \
version_control.local_path. Then briefly summarize what was built and the \
verification result. If a tool reports a path-access error, you \
tried to leave the workspace — stay within it.

{_closing_for_surface(surface)}

Respond in {lang_name}."""
    return _with_skill_knowledge(prompt)


def build_gather_task_message(gathering_context: dict[str, Any], user_content: str) -> str:
    """Assemble the GATHER-phase user message.

    Args:
        gathering_context: Session context (conversation memory lives in the
            restored AgentState, not here; this may carry ``description``).
        user_content: The user's message this turn.

    Returns:
        The user-role task message.
    """
    parts: list[str] = []
    description = gathering_context.get("description")
    if description:
        parts.append(f"Initial problem description:\n{description}")
    if user_content.strip():
        parts.append(user_content.strip())
    if not parts:
        parts.append(
            "Greet the user briefly and ask your first question about the "
            "optimization problem they want to build an LLM4AD task for."
        )
    return "\n\n".join(parts)


def build_task_message(proposed: dict[str, Any] | None, user_content: str) -> str:
    """Assemble the BUILD-phase user message.

    Two situations:
    - Fresh build (``proposed`` given): the user just confirmed a plan; build it.
    - Follow-up (``proposed`` is None): the package already exists and the user is
      asking for a change (e.g. tune a config value, adjust code); apply it and
      re-verify.

    Args:
        proposed: The confirmed requirements recorded by ``propose_build``, or None
            for a follow-up change request.
        user_content: The user's message this turn.

    Returns:
        The user-role task message.
    """
    parts: list[str] = []
    if proposed:
        parts.append(
            "The user confirmed this build plan:\n"
            + json.dumps(proposed, ensure_ascii=False, indent=2)
        )
        if user_content.strip():
            parts.append(f"Additional user note:\n{user_content.strip()}")
        parts.append(
            "\nBuild the task package now from the confirmed plan, then self-verify "
            "with run_python until test_evaluator.py and debug_run.py both run cleanly."
        )
    else:
        # Follow-up: the package is already built; apply the requested change.
        if user_content.strip():
            parts.append(f"User request:\n{user_content.strip()}")
        parts.append(
            "\nThe task package already exists in the workspace. Use list_dir/read_file "
            "to locate the relevant file, apply the requested change with edit_file "
            "(or write_file / rebuild_evaluator as appropriate), then re-verify with "
            "run_python and briefly report what you changed. Do not rebuild from "
            "scratch unless the user asks for it."
        )
    return "\n\n".join(parts)


__all__ = [
    "build_gather_system_prompt",
    "build_gather_task_message",
    "build_system_prompt",
    "build_task_message",
]
