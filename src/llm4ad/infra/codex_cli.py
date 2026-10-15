"""Run the official Codex CLI using its local ChatGPT login."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from weakref import WeakKeyDictionary

_LOCKS: WeakKeyDictionary = WeakKeyDictionary()


@dataclass
class CodexResult:
    """Completed CLI turn and its reported usage."""

    text: str
    usage: dict[str, int] = field(default_factory=dict)
    thread_id: str = ""


def _login_env() -> dict[str, str]:
    """Preserve CLI login storage while removing API authentication overrides."""
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL"):
        env.pop(key, None)
    return env


async def _communicate(
    command: list[str], env: dict[str, str], cwd: Path, timeout: float, prompt: str = ""
) -> tuple[int, str, str]:
    """Run a process and terminate its process group on timeout or cancellation."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")), timeout=timeout
        )
    except (TimeoutError, asyncio.CancelledError):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()
        raise
    return (
        process.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


def _parse_result(stdout: str, final_text: str) -> CodexResult:
    """Require a completed turn; an exit code alone does not prove success."""
    completed = False
    failed = False
    result = CodexResult(text=final_text)
    errors = []
    messages = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            result.thread_id = event.get("thread_id", "")
        elif kind == "turn.completed":
            completed = True
            result.usage = event.get("usage") or {}
        elif kind == "turn.failed":
            failed = True
            errors.append(str(event.get("error") or "Codex turn failed"))
        elif kind == "error":
            errors.append(str(event.get("message") or event))
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                messages.append(item.get("text", ""))
    if not completed or failed:
        detail = "; ".join(errors) or "CLI stream ended without turn.completed"
        raise RuntimeError(f"Codex did not complete: {detail}")
    result.text = final_text or (messages[-1] if messages else "")
    if not result.text.strip():
        raise RuntimeError("Codex completed without a final response")
    return result


async def run_codex(
    prompt: str,
    *,
    cwd: Path,
    binary_path: str = "codex",
    model: str = "",
    timeout: float = 600,
    sandbox: Literal["read-only", "workspace-write"] = "read-only",
    output_schema: dict[str, Any] | None = None,
    log_file: Path | None = None,
) -> CodexResult:
    """Run one isolated, non-interactive turn through the saved ChatGPT login.

    Args:
        prompt: Task submitted through stdin, without shell interpolation.
        cwd: Working directory visible to Codex.
        binary_path: Executable name or absolute path.
        model: Optional model override; empty preserves the CLI default.
        timeout: Maximum execution time in seconds, excluding queue time.
        sandbox: Read-only for planning, workspace-write for coding.
        output_schema: Optional strict JSON Schema for the final response.
        log_file: Optional destination for CLI diagnostics and JSON events.

    Returns:
        Final response, thread identifier, and token usage.

    Raises:
        RuntimeError: Login, CLI execution, or turn completion failed.
        TimeoutError: The CLI exceeded its time limit.
    """
    binary = shutil.which(binary_path)
    if binary is None:
        raise RuntimeError(
            f"Codex CLI not found: {binary_path!r}. Install Codex and run `codex login`."
        )
    cwd = cwd.resolve()
    env = _login_env()
    loop = asyncio.get_running_loop()
    lock = _LOCKS.setdefault(loop, asyncio.Lock())
    async with lock:
        code, out, err = await _communicate([binary, "login", "status"], env, cwd, min(timeout, 30))
        if code or "Logged in using ChatGPT" not in out + err:
            raise RuntimeError(
                "Codex needs a local ChatGPT login. Run `codex login` in this user account."
            )
        with TemporaryDirectory(prefix="llm4ad-codex-") as temp:
            result_path = Path(temp) / "response.txt"
            command = [
                binary,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--sandbox",
                sandbox,
                "--cd",
                str(cwd),
                "-c",
                'approval_policy="never"',
                "-c",
                'forced_login_method="chatgpt"',
                "-c",
                'model_provider="openai"',
                "--output-last-message",
                str(result_path),
            ]
            if model:
                command.extend(["--model", model])
            if output_schema is not None:
                schema_path = Path(temp) / "schema.json"
                schema_path.write_text(json.dumps(output_schema), encoding="utf-8")
                command.extend(["--output-schema", str(schema_path)])
            command.append("-")
            code, out, err = await _communicate(command, env, cwd, timeout, prompt)
            if log_file is not None:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                log_file.write_text(
                    f"=== stdout ===\n{out}\n=== stderr ===\n{err}", encoding="utf-8"
                )
            if code:
                raise RuntimeError(f"Codex exited with code {code}: {(err or out)[-4000:]}")
            final_text = result_path.read_text(encoding="utf-8") if result_path.exists() else ""
            return _parse_result(out, final_text)
