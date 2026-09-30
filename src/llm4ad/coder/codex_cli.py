"""Code generation using the official Codex CLI and local ChatGPT login."""

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder, GenerateResult, GenerateStatus
from llm4ad.config.coder import CodexCLIConfig
from llm4ad.infra.codex_cli import run_codex
from llm4ad.infra.timing import ExecutionTiming


def _snapshot(root: Path) -> dict[str, str]:
    """Hash workspace files while skipping VCS, dependencies, and symlinks."""
    skipped = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if name not in skipped and not (Path(directory) / name).is_symlink()
        ]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or name == ".git":
                continue
            with path.open("rb") as source:
                result[str(path.relative_to(root))] = hashlib.file_digest(
                    source, "sha256"
                ).hexdigest()
    return result


@BaseCoder.register("codex_cli")
class CodexCLI(BaseCoder):
    """Let Codex edit the candidate worktree under workspace-write sandboxing."""

    def __init__(self, config: CodexCLIConfig) -> None:
        """Use coder settings and the local CLI account, without an API provider."""
        super().__init__(config)
        self.binary_path = config.binary_path
        self.model = config.model

    @property
    def name(self) -> str:
        """Return the coding agent's display name."""
        return "Codex CLI"

    async def generate(
        self,
        prompt: str,
        context: dict[str, Any],
        working_dir: str,
        log_file: Path | None = None,
        **kwargs: Any,
    ) -> GenerateResult:
        """Apply algorithm changes and report completion, files, and usage."""
        started = time.monotonic()
        root = Path(working_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            before = _snapshot(root)
            result = await run_codex(
                prompt + "\n\nApply the implementation to files in the working directory. "
                "Preserve code outside EVOLVE_START/EVOLVE_END regions when these markers exist. "
                "Do not commit changes. Summarize the edits in your final response.",
                cwd=root,
                binary_path=self.binary_path,
                model=self.model,
                timeout=self.timeout,
                sandbox="workspace-write",
                log_file=log_file,
            )
            after = _snapshot(root)
            if log_file is not None:
                log_path = Path(log_file).resolve()
                if log_path.is_relative_to(root):
                    log_name = str(log_path.relative_to(root))
                    before.pop(log_name, None)
                    after.pop(log_name, None)
            changed = sorted(name for name, digest in after.items() if before.get(name) != digest)
            deleted = sorted(before.keys() - after.keys())
            if not changed and not deleted:
                raise RuntimeError("Codex completed but no workspace files changed")
            if self.config.log_to_console:
                logger.info("Codex: {}", result.text)
            return GenerateResult(
                status=GenerateStatus.SUCCESS,
                working_dir=str(root),
                generated_files=changed,
                main_file=changed[0] if changed else None,
                token_used=result.usage.get("input_tokens", 0)
                + result.usage.get("output_tokens", 0),
                metadata={
                    "thread_id": result.thread_id,
                    "usage": result.usage,
                    "summary": result.text,
                    "deleted_files": deleted,
                },
                timing=ExecutionTiming.from_llm_stage("coder", (time.monotonic() - started) * 1000),
            )
        except (RuntimeError, OSError) as exc:
            return GenerateResult(
                status=(
                    GenerateStatus.TIMEOUT
                    if isinstance(exc, TimeoutError)
                    else GenerateStatus.FAILED
                ),
                working_dir=str(root),
                error_message=(
                    f"Codex timed out after {self.timeout}s"
                    if isinstance(exc, TimeoutError)
                    else str(exc)
                ),
                timing=ExecutionTiming.from_llm_stage("coder", (time.monotonic() - started) * 1000),
            )
