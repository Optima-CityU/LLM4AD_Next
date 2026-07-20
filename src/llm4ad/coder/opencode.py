"""OpenCode coder implementation.

This coder invokes the OpenCode CLI tool (opencode-ai/opencode) in
non-interactive mode to generate code from algorithm insights.
"""

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder, GenerateResult, GenerateStatus
from llm4ad.config.app import ProviderConfig
from llm4ad.config.schema import OpenCodeConfig
from llm4ad.infra.timing import ExecutionTiming


@BaseCoder.register("opencode")
class OpenCode(BaseCoder):
    """OpenCode coder that delegates code generation to the opencode CLI.

    Invokes ``opencode -p "<prompt>" --cwd <dir>`` in non-interactive mode,
    then scans the working directory for generated or modified files.
    """

    # File extensions considered as source code when discovering generated files.
    _SOURCE_EXTENSIONS = {
        ".py", ".cpp", ".cc", ".c", ".h", ".hpp",
        ".java", ".js", ".ts", ".go", ".rs", ".rb",
        ".sh", ".php", ".cmake", ".txt",
    }

    # Directories to skip when scanning for generated files.
    _SKIP_DIRS = {
        "__pycache__", ".git", "node_modules", ".venv", "venv",
        "build", "dist", ".idea", ".vscode",
    }

    # Class-level lock to serialize opencode CLI invocations.
    # Concurrent opencode processes sharing the same git repository
    # compete for git lock files, causing SIGTERM (exit code 143).
    _run_lock = asyncio.Lock()

    def __init__(self, config: OpenCodeConfig, provider_config: ProviderConfig | None = None) -> None:
        """Initialize OpenCode coder.

        Args:
            config: OpenCodeConfig with binary path and CLI settings.
            provider_config: Resolved provider configuration with LLM credentials.
        """
        super().__init__(config)
        self.binary_path = config.binary_path
        self._provider_config = provider_config
        # Derive CLI provider name: use explicit config value if set,
        # otherwise fall back to ProviderConfig.type
        if config.provider_name:
            self.provider_name = config.provider_name
        elif provider_config:
            self.provider_name = provider_config.type
        else:
            self.provider_name = ""

    @property
    def name(self) -> str:
        """Return the coder name."""
        return "OpenCode"

    async def generate(
        self,
        prompt: str,
        context: dict[str, Any],
        working_dir: str,
        log_file: Path | None = None,
        **kwargs: Any,
    ) -> GenerateResult:
        """Generate code by invoking the opencode CLI.

        Args:
            prompt: The prompt for code generation.
            context: Additional context (task_description, parent_code, etc.).
            working_dir: Directory where opencode should write generated files.
            log_file: Optional path to write CLI stdout/stderr for debugging.
            **kwargs: Additional options (unused).

        Returns:
            GenerateResult with status and generated file paths.
        """
        working_path = Path(working_dir)
        working_path.mkdir(parents=True, exist_ok=True)
        model_name = self._provider_config.model if self._provider_config else "unknown"
        logger.debug("OpenCode coder using model: {}", model_name)
        start_time = time.time()

        # Snapshot existing files so we can detect new/changed ones later.
        before_files = self._snapshot_files(working_path)

        # Enrich prompt with project file listing so the agent knows what to modify.
        prompt = self._enrich_prompt(prompt, working_path)
        logger.debug("Enriched prompt for opencode: {} chars", len(prompt))

        async with self._run_lock:
            # Build environment with provider credentials.
            env = self._build_env()

            # Resolve binary path (shutil.which handles .cmd/.bat on Windows).
            resolved_binary = shutil.which(self.binary_path)
            if resolved_binary is None:
                return GenerateResult(
                    status=GenerateStatus.FAILED,
                    working_dir=str(working_path),
                    error_message=(
                        f"OpenCode binary not found at '{self.binary_path}'. "
                        "Install opencode: https://opencode.ai"
                    ),
                )

            # Write prompt to a temporary file to avoid command-line length limits
            # and special character issues on Windows.
            prompt_file = working_path / ".opencode_prompt.md"
            prompt_file.write_text(prompt, encoding="utf-8")

            # Build the opencode command.
            # opencode CLI uses: opencode run [message..] --file <path> --dir <dir> [-m provider/model]
            cmd = [
                resolved_binary,
                "run",
                "Implement the algorithm as described in the attached file. "
                "Read the source files, then modify code between EVOLVE_START "
                "and EVOLVE_END markers. Write the changes to disk.",
                "--file", str(prompt_file),
                "--dir", str(working_path),
            ]

            model = self._provider_config.model if self._provider_config else ""
            if self.provider_name and model:
                cmd.extend(["-m", f"{self.provider_name}/{model}"])
            elif model:
                cmd.extend(["-m", model])

            logger.debug("Running opencode command with prompt file: {}", str(prompt_file))

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(working_path),
                    env=env,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(),
                        timeout=self.timeout,
                    )
                except TimeoutError:
                    process.kill()
                    await process.communicate()
                    return GenerateResult(
                        status=GenerateStatus.TIMEOUT,
                        working_dir=str(working_path),
                        error_message=f"OpenCode timed out after {self.timeout}s",
                    )

                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")

                # Write log if requested.
                if log_file is not None:
                    log_file = Path(log_file)
                    log_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(log_file, "w", encoding="utf-8") as f:
                        f.write(f"=== stdout ===\n{stdout}\n")
                        f.write(f"=== stderr ===\n{stderr}\n")

                if self.config.log_to_console:
                    if stdout.strip():
                        logger.trace("OpenCode stdout:\n{}", stdout)
                    if stderr.strip():
                        logger.trace("OpenCode stderr:\n{}", stderr)

                if process.returncode != 0:
                    return GenerateResult(
                        status=GenerateStatus.FAILED,
                        working_dir=str(working_path),
                        error_message=f"OpenCode exited with code {process.returncode}: {stderr}",
                    )

                # Detect new or modified files.
                generated_files = self._detect_changes(working_path, before_files)

                if not generated_files:
                    return GenerateResult(
                        status=GenerateStatus.FAILED,
                        working_dir=str(working_path),
                        error_message="OpenCode completed but no new or modified files detected",
                    )

                return GenerateResult(
                    status=GenerateStatus.SUCCESS,
                    working_dir=str(working_path),
                    generated_files=generated_files,
                    main_file=generated_files[0],
                    timing=ExecutionTiming(
                        wall_time_ms=(time.time() - start_time) * 1000,
                        llm_coding_ms=(time.time() - start_time) * 1000,
                    ).recompute_overhead(),
                )

            except FileNotFoundError:
                return GenerateResult(
                    status=GenerateStatus.FAILED,
                    working_dir=str(working_path),
                    error_message=(
                        f"OpenCode binary not found at '{self.binary_path}'. "
                        "Install opencode: https://opencode.ai"
                    ),
                )
            except Exception as e:
                return GenerateResult(
                    status=GenerateStatus.FAILED,
                    working_dir=str(working_path),
                    error_message=f"OpenCode error: {e}",
                )
            finally:
                # Clean up temporary prompt file.
                prompt_file = working_path / ".opencode_prompt.md"
                prompt_file.unlink(missing_ok=True)

    def _enrich_prompt(self, prompt: str, working_path: Path) -> str:
        """Enrich the prompt with project file listing and EVOLVE marker locations.

        This helps the opencode agent understand the project structure and know
        exactly which files to modify.

        Args:
            prompt: Original prompt text.
            working_path: Project working directory.

        Returns:
            Enriched prompt with project context appended.
        """
        # Collect source files in the project.
        file_list: list[str] = []
        evolve_files: list[str] = []
        for file_path in sorted(working_path.rglob("*")):
            if not file_path.is_file():
                continue
            rel_parts = file_path.relative_to(working_path).parts
            if any(part in self._SKIP_DIRS for part in rel_parts):
                continue
            if file_path.suffix not in self._SOURCE_EXTENSIONS:
                continue
            rel = str(file_path.relative_to(working_path))
            file_list.append(rel)
            # Check if file contains EVOLVE markers.
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                if "EVOLVE_START" in content:
                    evolve_files.append(rel)
            except OSError:
                pass

        if not file_list:
            return prompt

        context_parts = [
            prompt,
            "\n\n# Project Context",
            f"\nWorking directory: {working_path}",
            "\nSource files in project:\n" + "\n".join(f"  - {f}" for f in file_list),
        ]
        if evolve_files:
            context_parts.append(
                "\nFiles containing EVOLVE_START/EVOLVE_END markers (modify code between these markers):\n"
                + "\n".join(f"  - {f}" for f in evolve_files)
            )
            context_parts.append(
                "\nIMPORTANT: Read the files listed above, then modify the code between "
                "EVOLVE_START and EVOLVE_END markers. Write the changes to disk."
            )
        return "\n".join(context_parts)

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the opencode subprocess.

        Returns:
            Env dict inheriting from current process with opencode overrides.
        """
        env = os.environ.copy()

        pc = self._provider_config
        if pc and pc.api_key:
            env["OPENAI_API_KEY"] = pc.api_key
            env["ANTHROPIC_API_KEY"] = pc.api_key
        if pc and pc.base_url:
            env["OPENAI_BASE_URL"] = pc.base_url

        return env

    def _snapshot_files(self, working_path: Path) -> dict[str, float]:
        """Snapshot file modification times in the working directory.

        Args:
            working_path: Directory to scan.

        Returns:
            Dict mapping relative path strings to mtime.
        """
        snapshot: dict[str, float] = {}
        for file_path in working_path.rglob("*"):
            if not file_path.is_file():
                continue
            if any(part in self._SKIP_DIRS for part in file_path.relative_to(working_path).parts):
                continue
            rel = str(file_path.relative_to(working_path))
            snapshot[rel] = file_path.stat().st_mtime
        return snapshot

    def _detect_changes(
        self, working_path: Path, before: dict[str, float]
    ) -> list[str]:
        """Detect new or modified files by comparing snapshots.

        Args:
            working_path: Directory to scan.
            before: Snapshot taken before generation.

        Returns:
            Sorted list of relative paths for new or modified files.
        """
        changed: list[str] = []
        for file_path in working_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel_parts = file_path.relative_to(working_path).parts
            if any(part in self._SKIP_DIRS for part in rel_parts):
                continue
            if file_path.suffix not in self._SOURCE_EXTENSIONS:
                continue
            rel = str(file_path.relative_to(working_path))
            mtime = file_path.stat().st_mtime
            if rel not in before or mtime > before[rel]:
                changed.append(rel)
        return sorted(changed)
