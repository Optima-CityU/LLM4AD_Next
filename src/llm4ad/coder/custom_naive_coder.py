"""Custom naive coder implementation with EVOLVE block replacement support.

This coder uses an LLM provider to generate code from natural language insights.
For mutations, it targets specific EVOLVE blocks marked with special comments
and only replaces those blocks, keeping the rest of the code unchanged.
Supports multi-file output and project context injection for compatibility
with plain LLMs (no agent/tool-use required).
"""

import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder, GenerateResult, GenerateStatus
from llm4ad.config.schema import CustomCoderConfig
from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.planner.base import Algorithm


@BaseCoder.register("custom")
class CustomNaiveCoder(BaseCoder):
    """Naive custom coder with EVOLVE block replacement support.

    When generating code from scratch: generates complete code based on insight.
    When mutating parent code: finds EVOLVE blocks marked with special comments
    and only replaces those blocks with newly generated code.

    Supports multiple comment styles for different programming languages:
    - Python/shell: # EVOLVE_START / # EVOLVE_END
    - C/C++/Java/JS: // EVOLVE_START / // EVOLVE_END
    - C-style block comments: /* EVOLVE_START */ ... /* EVOLVE_END */
    - HTML/XML: <!-- EVOLVE_START --> ... <!-- EVOLVE_END -->

    The separator between EVOLVE and START/END can be underscore, space, or
    hyphen (e.g. EVOLVE_START, EVOLVE START, EVOLVE-START are all accepted).
    """

    # Separator between EVOLVE and START/END: underscore, whitespace, or hyphen.
    _SEP = r"[\s_-]"

    # List of (compiled_pattern, comment_style) for EVOLVE block detection
    EVOLVE_PATTERNS = [
        # Python/Shell style (line comments)
        (
            re.compile(
                rf"#\s*EVOLVE{_SEP}START(.*?)\n(.*?)#\s*EVOLVE{_SEP}END",
                re.DOTALL | re.IGNORECASE,
            ),
            "#",
        ),
        # C/C++/JS style (line comments)
        (
            re.compile(
                rf"//\s*EVOLVE{_SEP}START(.*?)\n(.*?)//\s*EVOLVE{_SEP}END",
                re.DOTALL | re.IGNORECASE,
            ),
            "//",
        ),
        # C-style block comments
        (
            re.compile(
                rf"/\*\s*EVOLVE{_SEP}START(.*?)\*/\n(.*?)/\*\s*EVOLVE{_SEP}END\s*\*/",
                re.DOTALL | re.IGNORECASE,
            ),
            "/* */",
        ),
        # HTML/XML style
        (
            re.compile(
                rf"<!--\s*EVOLVE{_SEP}START(.*?)-->\n(.*?)<!--\s*EVOLVE{_SEP}END\s*-->",
                re.DOTALL | re.IGNORECASE,
            ),
            "<!-- -->",
        ),
    ]

    @property
    def name(self) -> str:
        """Return the coder name."""
        return "custom"

    def __init__(self, config: CustomCoderConfig, provider: BaseProvider):
        """Initialize CustomNaiveCoder.

        Args:
            config: CustomCoderConfig with coder settings.
            provider: LLM provider to use for code generation.
        """
        super().__init__(config, provider=provider)
        self.provider = provider

        # Get configuration from typed config
        self.prompt_template = config.prompt_template or self._default_prompt_template()
        self.mutation_prompt_template = (
            config.mutation_prompt_template or self._default_mutation_prompt_template()
        )
        self.temperature = config.temperature
        self.max_tokens = config.max_gen_tokens
        self.default_extension = config.default_extension
        self.context_max_tokens = config.context_max_tokens

        # Regex for extracting code from markdown code blocks
        self.code_block_pattern = re.compile(r"```[\w]*\n(.*?)\n```", re.DOTALL)
        # Regex for extracting named multi-file code blocks: ```lang:path/to/file
        self.named_code_block_pattern = re.compile(
            r"```[\w]*:([^\n]+)\n(.*?)\n```", re.DOTALL
        )

    def _default_prompt_template(self) -> str:
        """Get default prompt template for initial generation.

        Uses the plain LLM template that instructs the model to return
        code in named markdown blocks (```lang:filepath format).
        """
        return """\
Implement the following algorithm in {language}:

{insight}

Task description: {task_description}
Constraints: {constraints}

{project_context}

# Output Format

Return ALL code files using fenced code blocks with a file path annotation:

```{language}:<filepath>
<code>
```

If only one file is needed, use:

```{language}:{file_name}
<code>
```
"""

    def _default_mutation_prompt_template(self) -> str:
        """Get default prompt template for mutation."""
        return """\
Modify the following code block according to this insight:

Insight: {insight}

Original code block (from {file_path}):
```
{original_block}
```

Task description: {task_description}
Constraints: {constraints}

Please provide the modified version of just this code block.
Do NOT include the EVOLVE_START/EVOLVE_END comments in your output.

Return the code in a fenced code block with file path annotation:

```{language}:{file_path}
<modified code>
```
"""

    async def generate(
        self, prompt: str, context: dict[str, Any], working_dir: str, log_file: Any = None, **kwargs
    ) -> GenerateResult:
        """Generate code from algorithm insight.

        - If parent_code is present in context: performs targeted EVOLVE block replacement
        - Otherwise: generates complete code from scratch

        Args:
            prompt: The prompt or natural language description of the algorithm/mutation.
                For initial generation this is the algorithm insight;
                for mutation this may be the mutation insight.
            context: Additional context:
                - parent_code: Optional parent code (can be dict[file_path, content],
                  Algorithm instance, or list[CodeArtifact])
                - task_description: Overall task description
                - constraints: Constraints on the solution
                - language: Programming language (default: python)
                - main_file: Name of main entrypoint file (default: first generated file)
                - file_name: For single-file generation, name of output file
            working_dir: Directory to write generated files
            log_file: Optional log file path (unused, accepted for interface compatibility).
            **kwargs: Additional generation parameters passed to provider

        Returns:
            GenerateResult with status, generated files, and error information
        """
        working_path = Path(working_dir)
        working_path.mkdir(parents=True, exist_ok=True)
        logger.debug("Custom coder using model: {}", self.provider.model)

        start_time = time.time()

        try:
            parent_code = context.get("parent_code")
            if parent_code is None:
                existing_evolve_files = self._collect_evolve_file_map(working_path)
                if existing_evolve_files:
                    parent_code = existing_evolve_files
                    context = {**context, "parent_code": parent_code}

            mode = "mutation" if parent_code is not None else "initial"
            logger.debug(
                f"[Coder] mode={mode} model={self.provider.model} "
                f"temperature={self.temperature} parent_code={'yes' if parent_code else 'no'} "
                f"working_dir={working_path.name}"
            )

            if parent_code is not None:
                # Mutation mode: replace EVOLVE blocks in parent code
                result = await self._generate_mutation(prompt, context, working_path, **kwargs)
            else:
                # Initial generation mode: generate complete code
                result = await self._generate_initial(prompt, context, working_path, **kwargs)

            result.timing.wall_time_ms = (time.time() - start_time) * 1000
            result.timing.recompute_overhead()
            return result
        except Exception as e:
            return GenerateResult(
                status=GenerateStatus.FAILED,
                working_dir=str(working_path),
                error_message=f"Generation failed: {str(e)}",
                timing=ExecutionTiming(wall_time_ms=(time.time() - start_time) * 1000),
            )

    def _collect_evolve_file_map(self, working_path: Path) -> dict[str, str]:
        """Collect existing source files that contain EVOLVE blocks.

        Args:
            working_path: Candidate worktree root.

        Returns:
            Source contents keyed by paths relative to the worktree root.
        """
        source_extensions = {
            ".py", ".cpp", ".cc", ".c", ".h", ".hpp",
            ".java", ".js", ".ts", ".go", ".rs", ".rb",
            ".sh", ".php",
        }
        skip_dirs = {
            "__pycache__", ".git", "node_modules", ".venv", "venv",
            "build", "dist", ".idea", ".vscode",
        }
        files: dict[str, str] = {}
        for file_path in sorted(working_path.rglob("*")):
            if not file_path.is_file() or file_path.suffix not in source_extensions:
                continue
            if any(part in skip_dirs for part in file_path.parts):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if self._find_evolve_blocks(content):
                files[str(file_path.relative_to(working_path))] = content
        return files

    async def _generate_initial(
        self, prompt: str, context: dict[str, Any], working_path: Path, **kwargs
    ) -> GenerateResult:
        """Generate complete code from scratch.

        Collects project context from working_dir, injects it into the prompt,
        and parses multi-file output from the LLM response.
        """
        language = context.get("language", "python")
        task_description = context.get("task_description", "")
        constraints = context.get("constraints", "")

        # Get output file name hint
        if "file_name" in context:
            file_name = context["file_name"]
        elif "main_file" in context:
            file_name = context["main_file"]
        else:
            ext = self._get_extension(language)
            file_name = f"implementation.{ext}"

        # Collect project context with EVOLVE blocks masked so the LLM
        # cannot simply echo the existing baseline implementation.
        project_context = self._collect_project_context(str(working_path), mask_evolve_blocks=True)

        # If a full prompt is already provided (from planner), use it directly.
        # Otherwise build from the template.
        if "{insight}" in self.prompt_template or "{project_context}" in self.prompt_template:
            full_prompt = self.prompt_template.format(
                insight=context.get("insight", prompt),
                language=language,
                task_description=task_description,
                constraints=constraints,
                project_context=project_context,
                file_name=file_name,
            )
        else:
            # The prompt is already fully formed (e.g., from IMPLEMENT_ALGORITHM_PLAIN_LLM)
            full_prompt = prompt

        # Merge generation parameters
        gen_kwargs = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        gen_kwargs.update(kwargs)

        prompt_source = "template" if (
            "{insight}" in self.prompt_template or "{project_context}" in self.prompt_template
        ) else "prebuilt"
        logger.debug(
            f"[Coder-Initial] prompt_source={prompt_source} "
            f"prompt_len={len(full_prompt)} temperature={gen_kwargs.get('temperature')}"
        )

        # Call LLM provider
        result = await self.provider.generate(full_prompt, request_stage="coder", **gen_kwargs)

        # Try multi-file extraction first, then fall back to single file
        file_map = self._extract_multi_file_code(result.text)

        logger.debug(
            f"[Coder-Initial] llm_response_len={len(result.text)} "
            f"extracted_files={list(file_map.keys()) if file_map else 'NONE'} "
            f"tokens={result.total_tokens}"
        )
        if not file_map:
            return GenerateResult(
                status=GenerateStatus.FAILED,
                working_dir=str(working_path),
                error_message="No code found in LLM response",
                metadata={
                    "tokens_used": result.total_tokens,
                    "cost_usd": result.cost_usd,
                },
                timing=result.timing,
            )

        # Write all files and validate
        generated_files: list[str] = []
        errors: list[str] = []
        for fpath, code in file_map.items():
            # Detect language from file extension for validation
            ext = Path(fpath).suffix.lstrip(".")
            file_lang = self._extension_to_language(ext) if ext else language

            is_valid, error = self.validate_code(code, file_lang)
            if not is_valid:
                errors.append(f"{fpath}: {error}")
                continue

            out_path = working_path / fpath
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(code, encoding="utf-8")
            generated_files.append(fpath)

        if not generated_files:
            return GenerateResult(
                status=GenerateStatus.FAILED,
                working_dir=str(working_path),
                error_message=f"All files failed validation: {'; '.join(errors)}",
                metadata={
                    "tokens_used": result.total_tokens,
                    "cost_usd": result.cost_usd,
                },
                timing=result.timing,
            )

        main_file = context.get("main_file", generated_files[0])

        status = GenerateStatus.PARTIAL if errors else GenerateStatus.SUCCESS
        return GenerateResult(
            status=status,
            working_dir=str(working_path),
            generated_files=generated_files,
            main_file=main_file,
            error_message="; ".join(errors) if errors else None,
            metadata={
                "tokens_used": result.total_tokens,
                "completion_tokens": result.completion_tokens,
                "cost_usd": result.cost_usd,
                "latency_ms": result.latency_ms,
                "model": result.model,
            },
            timing=result.timing,
        )

    async def _generate_mutation(
        self, prompt: str, context: dict[str, Any], working_path: Path, **kwargs
    ) -> GenerateResult:
        """Generate mutation by replacing EVOLVE blocks in parent code.

        For each file with EVOLVE blocks, sends the block content to the LLM
        with project context, then replaces the block with the LLM's output.
        Supports multi-file extraction from LLM responses.
        """
        parent_code = context["parent_code"]
        language = context.get("language", "python")
        task_description = context.get("task_description", "")
        constraints = context.get("constraints", "")

        parent_map = self._get_parent_code_map(parent_code)
        logger.debug(
            f"[Coder-Mutation] parent_files={list(parent_map.keys())} "
            f"parent_code_type={type(parent_code).__name__}"
        )
        generated_files: list[str] = []
        errors: list[str] = []
        total_blocks = 0
        llm_timing = ExecutionTiming()

        # Process each parent code file
        for file_path, content in parent_map.items():
            # Find all EVOLVE blocks in the file
            evolve_blocks = self._find_evolve_blocks(content)
            total_blocks += len(evolve_blocks)

            if not evolve_blocks:
                # No EVOLVE blocks, copy file as-is
                output_path = working_path / file_path
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(content, encoding="utf-8")
                generated_files.append(str(file_path))
                continue

            # Process each EVOLVE block
            modified_content = content
            for _pattern, comment_style, start_match, original_block in evolve_blocks:
                block_name = start_match.strip() or f"block_{len(generated_files)}"

                # Build mutation prompt with file path and language info
                mutation_prompt = self.mutation_prompt_template.format(
                    original_block=original_block.strip(),
                    insight=prompt,
                    task_description=task_description,
                    constraints=constraints,
                    block_name=block_name,
                    file_path=file_path,
                    language=language,
                )
                immutable_context = self._mask_evolve_blocks(content)
                serialization_contract = ""
                if Path(file_path).suffix == ".py" and "json.dumps" in immutable_context:
                    serialization_contract = (
                        "The fixed Python adapter serializes returned values with json.dumps. "
                        "Return JSON-native Python values; convert NumPy arrays or scalars to "
                        "lists, floats, or integers before returning them.\n"
                    )
                mutation_prompt = (
                    "Code outside EVOLVE markers is immutable. The replacement must remain "
                    "compatible with imports, callers, return-value unpacking, output adapters, "
                    "and other interfaces shown in the fixed file context below. If a natural-"
                    "language instruction conflicts with this executable interface, preserve the "
                    "executable interface. Return only the replacement block, never the complete "
                    "file or EVOLVE markers.\n"
                    f"{serialization_contract}\n"
                    f"Fixed file context ({file_path}):\n"
                    f"```{language}\n{immutable_context}\n```\n\n"
                    f"{mutation_prompt}"
                )

                # Merge generation parameters
                gen_kwargs = {
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                gen_kwargs.update(kwargs)

                # Call LLM provider
                result = await self.provider.generate(
                    mutation_prompt,
                    request_stage="coder",
                    **gen_kwargs,
                )
                llm_timing.add(result.timing)

                # Extract new code from response (try named block first, then plain)
                extracted_files = self._extract_multi_file_code(result.text)
                new_block = extracted_files.get(file_path) if extracted_files else None
                if new_block is None:
                    # Fallback to single unnamed block extraction
                    new_block = self._extract_code(result.text)

                normalization_error = None
                if new_block:
                    new_block, normalization_error = self._normalize_mutation_block(
                        new_block,
                        block_name,
                    )
                if normalization_error:
                    errors.append(f"[{file_path}] Block '{block_name}': {normalization_error}")
                    continue

                block_changed = new_block and new_block.strip() != original_block.strip()
                logger.debug(
                    f"[Coder-Mutation] file={file_path} block={block_name} "
                    f"original_len={len(original_block)} "
                    f"new_len={len(new_block) if new_block else 0} "
                    f"changed={block_changed} tokens={result.total_tokens}"
                )
                if not new_block:
                    errors.append(f"[{file_path}] Failed to extract code for block '{block_name}'")
                    continue

                # Validate new block
                is_valid, error = self.validate_code(new_block, language)
                if not is_valid:
                    errors.append(f"[{file_path}] Block '{block_name}': {error}")
                    continue

                # Replace the block in content — always output underscore format.
                # Strip trailing newline from regex capture to avoid double-newline.
                orig_stripped = original_block.rstrip("\n")
                if comment_style == "#" or comment_style == "//":
                    prefix = comment_style
                    full_original = (
                        f"{prefix} EVOLVE_START{start_match}\n{orig_stripped}\n{prefix} EVOLVE_END"
                    )
                    full_new = (
                        f"{prefix} EVOLVE_START{start_match}\n{new_block}\n{prefix} EVOLVE_END"
                    )
                elif comment_style == "/* */":
                    full_original = (
                        f"/* EVOLVE_START{start_match} */\n{orig_stripped}\n/* EVOLVE_END */"
                    )
                    full_new = f"/* EVOLVE_START{start_match} */\n{new_block}\n/* EVOLVE_END */"
                elif comment_style == "<!-- -->":
                    full_original = (
                        f"<!-- EVOLVE_START{start_match} -->\n{orig_stripped}\n<!-- EVOLVE_END -->"
                    )
                    full_new = (
                        f"<!-- EVOLVE_START{start_match} -->\n{new_block}\n<!-- EVOLVE_END -->"
                    )
                else:
                    full_original = ""
                    full_new = ""

                if full_original in modified_content:
                    modified_content = modified_content.replace(full_original, full_new)
                else:
                    # Exact match not found, try with different whitespace
                    modified_content = self._replace_block_fuzzy(
                        modified_content, original_block, new_block, comment_style, start_match
                    )

            # Write modified file
            output_path = working_path / file_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(modified_content, encoding="utf-8")
            generated_files.append(str(file_path))

        # Determine final status
        if not generated_files:
            status = GenerateStatus.FAILED
            error_message = "\n".join(errors) if errors else "No files generated"
        elif errors:
            status = GenerateStatus.PARTIAL
            error_message = "\n".join(errors)
        else:
            status = GenerateStatus.SUCCESS
            error_message = None

        main_file = context.get("main_file", generated_files[0] if generated_files else None)

        return GenerateResult(
            status=status,
            working_dir=str(working_path),
            generated_files=generated_files,
            main_file=main_file,
            error_message=error_message,
            metadata={
                "total_files": len(parent_map),
                "total_evolve_blocks": total_blocks,
                "processed_files": len(generated_files),
                "num_errors": len(errors),
            },
            timing=llm_timing,
        )

    def _normalize_mutation_block(
        self,
        code: str,
        block_name: str,
    ) -> tuple[str | None, str | None]:
        """Normalize a mutation response to one EVOLVE block body.

        Models occasionally ignore the block-only output contract and return a
        complete source file. Inserting that response verbatim would nest
        EVOLVE markers and duplicate immutable entrypoints.

        Args:
            code: Extracted code returned by the provider.
            block_name: Name of the EVOLVE block currently being replaced.

        Returns:
            A tuple containing the normalized block and an optional error.
        """
        response_blocks = self._find_evolve_blocks(code)
        if not response_blocks:
            return code.strip(), None

        if len(response_blocks) == 1:
            replacement = response_blocks[0][3].strip()
        else:
            named_matches = [
                block_content.strip()
                for _pattern, _style, start_match, block_content in response_blocks
                if start_match.strip() == block_name
            ]
            if len(named_matches) != 1:
                return None, "full-file response contains ambiguous EVOLVE blocks"
            replacement = named_matches[0]

        if not replacement:
            return None, "full-file response contains an empty EVOLVE block"
        if self._find_evolve_blocks(replacement):
            return None, "replacement contains nested EVOLVE blocks"

        logger.warning(
            "[Coder-Mutation] Unwrapped full-file response for EVOLVE block '{}'",
            block_name,
        )
        return replacement, None

    def _get_parent_code_map(self, parent_code: Any) -> dict[str, str]:
        """Convert parent_code from context into a map of file paths to content.

        Supports multiple input formats:
        - dict[str, str]: Already a map of {file_path: content}
        - Algorithm: Extract code_artifacts
        - list[CodeArtifact]: Direct list of code artifacts

        Args:
            parent_code: Parent code in one of the supported formats

        Returns:
            Dict mapping file paths to content
        """
        if isinstance(parent_code, dict):
            return parent_code
        elif isinstance(parent_code, Algorithm):
            return {a.file_path: a.content for a in parent_code.code_artifacts}
        elif isinstance(parent_code, list) and all(
            hasattr(a, "file_path") and hasattr(a, "content") for a in parent_code
        ):
            return {a.file_path: a.content for a in parent_code}
        else:
            # Treat as single file
            ext = self.default_extension
            return {f"algorithm.{ext}": str(parent_code)}

    def _find_evolve_blocks(self, content: str) -> list[tuple[re.Pattern, str, str, str]]:
        """Find all EVOLVE blocks in the given content.

        Args:
            content: Full file content

        Returns:
            List of tuples (pattern, comment_style, start_match, block_content)
        """
        blocks: list[tuple[re.Pattern, str, str, str]] = []
        for pattern, comment_style in self.EVOLVE_PATTERNS:
            for match in pattern.finditer(content):
                start_match = match.group(1)
                block_content = match.group(2)
                blocks.append((pattern, comment_style, start_match, block_content))
        return blocks

    def _extract_code(self, text: str) -> str | None:
        """Extract code from LLM response, handling markdown code blocks.

        Args:
            text: Raw response text from LLM

        Returns:
            Extracted code, or None if empty
        """
        match = self.code_block_pattern.search(text)
        if match:
            return match.group(1).strip()
        # Fallback: return entire text if no code blocks found
        stripped = text.strip()
        return stripped if stripped else None

    def _extract_multi_file_code(self, text: str) -> dict[str, str]:
        """Extract multiple named code blocks from LLM response.

        Parses code blocks with file path annotations like:
            ```python:src/main.py
            ...code...
            ```

        Falls back to single unnamed block if no named blocks found.

        Args:
            text: Raw response text from LLM

        Returns:
            Dict mapping file paths to code content. Empty dict if no code found.
        """
        files: dict[str, str] = {}
        for match in self.named_code_block_pattern.finditer(text):
            file_path = match.group(1).strip()
            code = match.group(2).strip()
            if file_path and code:
                files[file_path] = code

        if files:
            return files

        # Fallback: try extracting a single unnamed block
        single = self._extract_code(text)
        if single:
            ext = self.default_extension
            return {f"implementation.{ext}": single}

        return {}

    def _collect_project_context(
        self, working_dir: str, max_chars: int | None = None, mask_evolve_blocks: bool = False
    ) -> str:
        """Collect existing project files as context for the LLM prompt.

        Reads files from working_dir and builds a context string showing
        the project structure and file contents. Respects the configured
        context_max_tokens limit (approximated as chars * 0.25 tokens).

        Args:
            working_dir: Project working directory to scan
            max_chars: Maximum characters to include. If None, uses
                context_max_tokens * 4 as approximation.
            mask_evolve_blocks: If True, replace EVOLVE block bodies with
                a placeholder so the LLM cannot copy existing implementations.

        Returns:
            Formatted string with project context, or empty string if no files found.
        """
        if max_chars is None:
            max_chars = self.context_max_tokens * 4  # rough chars-to-tokens ratio

        working_path = Path(working_dir)
        if not working_path.exists():
            return ""

        # Collect relevant source files
        source_extensions = {
            ".py", ".cpp", ".cc", ".c", ".h", ".hpp",
            ".java", ".js", ".ts", ".go", ".rs", ".rb",
            ".sh", ".php",
        }
        skip_dirs = {
            "__pycache__", ".git", "node_modules", ".venv", "venv",
            "build", "dist", ".idea", ".vscode",
        }

        files_content: list[tuple[str, str]] = []
        total_chars = 0

        for file_path in sorted(working_path.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix not in source_extensions:
                continue
            if any(part in skip_dirs for part in file_path.parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            if mask_evolve_blocks:
                content = self._mask_evolve_blocks(content)

            rel_path = file_path.relative_to(working_path)
            entry_chars = len(str(rel_path)) + len(content) + 20  # overhead for formatting

            if total_chars + entry_chars > max_chars:
                break

            files_content.append((str(rel_path), content))
            total_chars += entry_chars

        if not files_content:
            return ""

        lines = ["# Existing Project Files\n"]
        for rel_path, content in files_content:
            # Detect language from extension
            ext = Path(rel_path).suffix.lstrip(".")
            lang = self._extension_to_language(ext)
            lines.append(f"## {rel_path}\n```{lang}\n{content}\n```\n")

        return "\n".join(lines)

    @staticmethod
    def _mask_evolve_blocks(content: str) -> str:
        """Replace EVOLVE block bodies with a placeholder.

        This prevents the LLM from simply copying the existing implementation
        and forces it to generate a new algorithm based on the insight.
        """
        patterns = [
            re.compile(
                r"(#\s*EVOLVE[_ ]?START.*?\n).*?(\n#\s*EVOLVE[_ ]?END)",
                re.DOTALL | re.IGNORECASE,
            ),
            re.compile(
                r"(//\s*EVOLVE[_ ]?START.*?\n).*?(\n//\s*EVOLVE[_ ]?END)",
                re.DOTALL | re.IGNORECASE,
            ),
        ]
        for pattern in patterns:
            content = pattern.sub(
                r"\1# TODO: Implement your algorithm here\n\2",
                content,
            )
        return content

    @staticmethod
    def _extension_to_language(ext: str) -> str:
        """Map file extension to language name for code blocks.

        Args:
            ext: File extension without dot (e.g., "py", "cpp")

        Returns:
            Language identifier for markdown code blocks
        """
        mapping = {
            "py": "python",
            "cpp": "cpp",
            "cc": "cpp",
            "c": "c",
            "h": "c",
            "hpp": "cpp",
            "java": "java",
            "js": "javascript",
            "ts": "typescript",
            "go": "go",
            "rs": "rust",
            "rb": "ruby",
            "sh": "bash",
            "php": "php",
        }
        return mapping.get(ext, ext)

    @staticmethod
    def _comment_pattern(comment_style: str, line: str) -> bool:
        """Check if line contains EVOLVE START comment for fuzzy matching."""
        upper = line.upper()
        return "EVOLVE" in upper and "START" in upper

    @staticmethod
    def _comment_end_pattern(comment_style: str, line: str) -> bool:
        """Check if line contains EVOLVE END comment for fuzzy matching."""
        upper = line.upper()
        return "EVOLVE" in upper and "END" in upper

    def _replace_block_fuzzy(
        self,
        content: str,
        original_block: str,
        new_block: str,
        comment_style: str,
        start_match: str,
    ) -> str:
        """Fuzzy block replacement when exact match fails due to whitespace differences.

        Args:
            content: Original content
            original_block: Original block content
            new_block: New block content to replace with
            comment_style: Comment style (prefix)
            start_match: Start match description

        Returns:
            Content with block replaced
        """
        # For line comment styles, the pattern is more forgiving with newlines
        if comment_style in ["#", "//"]:
            # Look for the start marker anywhere in the content
            lines = content.splitlines(keepends=True)
            found = False
            result_lines: list[str] = []
            in_block = False

            for _i, line in enumerate(lines):
                if not in_block and self._comment_pattern(comment_style, line):
                    # Found start marker
                    in_block = True
                    result_lines.append(line)
                elif in_block and self._comment_end_pattern(comment_style, line):
                    # Found end marker - replace the content between
                    result_lines.append(new_block + "\n")
                    result_lines.append(line)
                    in_block = False
                    found = True
                elif not in_block:
                    result_lines.append(line)

            if found:
                return "".join(result_lines)

        # If fuzzy matching fails, return original
        return content

    def _get_extension(self, language: str) -> str:
        """Get file extension from language name.

        Args:
            language: Language name

        Returns:
            Appropriate file extension
        """
        language = language.lower()
        mapping = {
            "python": "py",
            "py": "py",
            "c++": "cpp",
            "cpp": "cpp",
            "cc": "cpp",
            "c": "c",
            "header": "h",
            "h": "h",
            "javascript": "js",
            "js": "js",
            "typescript": "ts",
            "ts": "ts",
            "java": "java",
            "rust": "rs",
            "go": "go",
        }
        return mapping.get(language, self.default_extension)

    def validate_code(self, code: str, language: str = "python") -> tuple[bool, str | None]:
        """Basic syntax validation of generated code.

        Args:
            code: Code to validate
            language: Programming language

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not code.strip():
            return False, "Empty code"

        if language.lower() in ["python", "py"]:
            try:
                compile(code, "<string>", "exec")
                return True, None
            except SyntaxError as e:
                return False, f"Python syntax error: {e}"
            except Exception as e:
                return False, f"Validation error: {e}"
        else:
            # For other languages, only basic empty check
            if len(code.strip()) == 0:
                return False, "Empty code"
            return True, None
