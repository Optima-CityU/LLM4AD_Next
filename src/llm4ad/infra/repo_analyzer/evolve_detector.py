"""Default repository analyzer that detects EVOLVE blocks marked with special comments.

Walks through a repository, finds all EVOLVE blocks using Python file walking,
extracts them with their surrounding context, and returns the analysis result for use
during evolution.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from llm4ad.infra.repo_analyzer.base import (
    AnalyzedRepository,
    BaseRepositoryAnalyzer,
    EvolveBlock,
)


@BaseRepositoryAnalyzer.register("evolve_detector")
class EvolveDetector(BaseRepositoryAnalyzer):
    """Default repository analyzer that detects EVOLVE blocks marked with special comments.

    Uses Python file walking to scan all files matching include/exclude patterns
    and searches for EVOLVE markers line by line.

    Supports multiple comment styles for different programming languages:
    - Python/shell: # EVOLVE_START / # EVOLVE_END
    - C/C++/Java/JS: // EVOLVE_START / // EVOLVE_END
    - C-style block comments: /* EVOLVE_START */ ... /* EVOLVE_END */
    - HTML/XML: <!-- EVOLVE_START --> ... <!-- EVOLVE_END -->

    The separator between EVOLVE and START/END can be underscore, space, or
    hyphen (e.g. EVOLVE_START, EVOLVE START, EVOLVE-START are all accepted).

    Configurable context capture (number of lines before/after) and
    glob-based filtering of which files to include/exclude.
    """

    # Default language mapping from file extension
    # Extensions are lowercase
    DEFAULT_LANGUAGE_MAP: dict[str, str] = {
        ".py": "python",
        ".pyw": "python",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "h",
        ".hpp": "h",
        ".java": "java",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".html": "html",
        ".xml": "xml",
        ".sh": "shell",
        ".rb": "ruby",
        ".php": "php",
        ".comp": "glsl",
        ".glsl": "glsl",
        ".vert": "glsl",
        ".frag": "glsl",
    }

    def __init__(self, config: dict[str, Any]):
        """Initialize the EvolveDetector.

        Args:
            config: Configuration dictionary:
                - context_lines_before: Number of context lines to capture before the block
                  (default: 5)
                - context_lines_after: Number of context lines to capture after the block
                  (default: 5)
                - include: List of glob patterns to include (default: common code extensions)
                - exclude: List of glob patterns to exclude (default: excludes VCS, caches, builds)
        """
        self.context_lines_before = config.get("context_lines_before", 5)
        self.context_lines_after = config.get("context_lines_after", 5)

        # Default include patterns - common code file extensions
        self.include_patterns = config.get(
            "include",
            list(self.DEFAULT_INCLUDE_PATTERNS),
        )

        # Default exclude patterns - VCS, caches, build artifacts
        self.exclude_patterns = config.get(
            "exclude",
            list(self.DEFAULT_EXCLUDE_PATTERNS),
        )

        self.language_map = dict(self.DEFAULT_LANGUAGE_MAP)
        # Allow user to override language mapping
        if "language_map" in config:
            self.language_map.update(config["language_map"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, repo_path: Path | str) -> AnalyzedRepository:
        """Analyze the repository at the given path and find all evolvable regions.

        Uses Python file walking to scan all files matching include/exclude patterns
        and searches for EVOLVE markers line by line.

        Args:
            repo_path: Path to the repository root

        Returns:
            AnalyzedRepository containing all discovered EVOLVE blocks
        """
        repo_path = Path(repo_path).expanduser().resolve()
        return self._analyze_with_python(repo_path)

    def _analyze_with_python(self, repo_path: Path) -> AnalyzedRepository:
        """Analyze repository using pure-Python file walking and line scanning.

        Args:
            repo_path: Resolved absolute path to the repository root.

        Returns:
            AnalyzedRepository containing all discovered EVOLVE blocks.
        """
        all_blocks: list[EvolveBlock] = []
        files_analyzed = 0
        files_with_blocks = 0
        file_type_counts: dict[str, int] = {}

        for path in repo_path.rglob("*"):
            if not path.is_file():
                continue
            if not self._should_process(path, repo_path):
                continue

            ext = path.suffix.lower()
            files_analyzed += 1
            file_type_counts[ext] = file_type_counts.get(ext, 0) + 1

            blocks = self.analyze_file(path, repo_path)
            if blocks:
                files_with_blocks += 1
                all_blocks.extend(blocks)

        return AnalyzedRepository(
            repo_path=repo_path,
            evolvable_blocks=all_blocks,
            files_analyzed=files_analyzed,
            files_with_blocks=files_with_blocks,
            file_type_counts=file_type_counts,
        )

    def analyze_file(self, file_path: Path, repo_root: Path) -> list[EvolveBlock]:
        """Analyze a single file for EVOLVE blocks using line-by-line scanning.

        Args:
            file_path: Absolute path to the file to analyze
            repo_root: Repository root for computing relative paths

        Returns:
            List of EvolveBlock objects for all discovered blocks in this file
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
                lines = content.splitlines(keepends=True)
        except (UnicodeDecodeError, PermissionError):
            return []

        relative_path = str(file_path.relative_to(repo_root))
        language = self._detect_language(file_path)

        # Find markers line by line
        starts: list[tuple[int, str]] = []
        ends: list[tuple[int, str]] = []

        for i, line in enumerate(lines, 1):
            marker_type = self._classify_marker_line(line)
            if marker_type == "START":
                starts.append((i, line))
            elif marker_type == "END":
                ends.append((i, line))

        pairs = self._pair_start_end(starts, ends)

        blocks: list[EvolveBlock] = []
        for start_line, start_content, end_line, _end_content in pairs:
            comment_style = self._detect_comment_style(start_content)
            block_name = self._extract_block_name(start_content)
            original_content = "".join(lines[start_line : end_line - 1]).strip()
            context_before = self._get_context_before(lines, start_line)
            context_after = self._get_context_after(lines, end_line)

            blocks.append(
                EvolveBlock(
                    file_path=relative_path,
                    absolute_path=file_path,
                    line_start=start_line,
                    line_end=end_line,
                    comment_style=comment_style,
                    block_name=block_name,
                    original_content=original_content,
                    context_before=context_before,
                    context_after=context_after,
                    language=language,
                )
            )

        return blocks

    # ------------------------------------------------------------------
    # Marker line classification and parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_marker_line(line: str) -> Literal["START", "END"] | None:
        """Classify a line as a START marker, END marker, or neither.

        A marker line must consist of a comment leader (``#``, ``//``,
        ``/*``, or ``<!--``) immediately followed by ``EVOLVE_START`` or
        ``EVOLVE_END`` (case-insensitive). The separator after ``EVOLVE``
        may be underscore, space, hyphen, or tab. Leading whitespace is
        ignored. This excludes prose mentions of ``EVOLVE_START`` /
        ``EVOLVE_END`` inside docstrings or string literals, which the
        previous lax rule incorrectly accepted.

        Args:
            line: A single line of source code.

        Returns:
            ``"START"``, ``"END"``, or ``None``.
        """
        stripped = line.lstrip()
        # Strip the comment leader so what remains begins with EVOLVE.
        for leader in ("<!--", "/*", "//", "#"):
            if stripped.startswith(leader):
                rest = stripped[len(leader):].lstrip()
                break
        else:
            return None

        upper = rest.upper()
        if not upper.startswith("EVOLVE"):
            return None
        after_evolve = len("EVOLVE")
        if after_evolve >= len(upper):
            return None
        if upper[after_evolve] not in {" ", "_", "-", "\t"}:
            return None
        keyword = upper[after_evolve + 1 :].lstrip()
        if keyword.startswith("START"):
            return "START"
        if keyword.startswith("END"):
            return "END"
        return None

    @staticmethod
    def _detect_comment_style(line: str) -> str:
        """Detect comment style from a marker line.

        Args:
            line: A line containing an EVOLVE marker.

        Returns:
            Comment style string: ``"#"``, ``"//"``, ``"/* */"``, or ``"<!-- -->"``.
        """
        stripped = line.lstrip()
        if stripped.startswith("<!--"):
            return "<!-- -->"
        if stripped.startswith("/*"):
            return "/* */"
        if stripped.startswith("//"):
            return "//"
        return "#"

    @staticmethod
    def _extract_block_name(line: str) -> str:
        """Extract the optional block name from an EVOLVE_START line.

        The block name is any text following ``START`` up to a comment
        closer (``*/`` or ``-->``).

        Args:
            line: A line containing an EVOLVE START marker.

        Returns:
            Trimmed block name, or empty string if none.
        """
        upper = line.upper()
        idx = upper.find("START")
        if idx < 0:
            return ""
        after_start = line[idx + len("START") :]
        # Strip trailing comment closers
        for closer in ("*/", "-->"):
            pos = after_start.find(closer)
            if pos >= 0:
                after_start = after_start[:pos]
        return after_start.strip()

    @staticmethod
    def _pair_start_end(
        starts: list[tuple[int, str]],
        ends: list[tuple[int, str]],
    ) -> list[tuple[int, str, int, str]]:
        """Pair each START marker with its nearest following END marker.

        Each START is paired with the first available END that appears on a
        later line. Unmatched START markers (no subsequent END) are skipped.

        Args:
            starts: List of (line_number, line_content) for START markers.
            ends: List of (line_number, line_content) for END markers.

        Returns:
            List of (start_line, start_content, end_line, end_content) tuples.
        """
        pairs: list[tuple[int, str, int, str]] = []
        end_idx = 0
        for start_line, start_content in starts:
            # Advance past any END markers that precede this START
            while end_idx < len(ends) and ends[end_idx][0] <= start_line:
                end_idx += 1
            if end_idx < len(ends):
                end_line, end_content = ends[end_idx]
                pairs.append((start_line, start_content, end_line, end_content))
                end_idx += 1
        return pairs

    # ------------------------------------------------------------------
    # File filtering and language detection
    # ------------------------------------------------------------------

    def _should_process(self, file_path: Path, repo_root: Path) -> bool:
        """Check if file should be processed based on include/exclude patterns.

        Args:
            file_path: Absolute path to the file.
            repo_root: Repository root.

        Returns:
            True if the file matches include patterns and does not match
            any exclude pattern.
        """
        relative = file_path.relative_to(repo_root)

        # Check exclude patterns first
        for pattern in self.exclude_patterns:
            if self._path_matches(relative, pattern):
                return False

        # Must match at least one include pattern
        return any(self._path_matches(relative, p) for p in self.include_patterns)

    @staticmethod
    def _path_matches(relative: Path, pattern: str) -> bool:
        """Match a relative path against a glob pattern.

        Handles ``**`` patterns correctly across Python versions by checking
        if any parent directory matches the prefix before ``/**``.
        ``Path.match()`` only supports ``**`` from Python 3.12 onwards.

        Args:
            relative: Relative path from the repository root.
            pattern: Glob pattern (e.g. ``"node_modules/**"``, ``"*.py"``).

        Returns:
            True if the path matches the pattern.
        """
        if "/**" in pattern:
            # e.g. "node_modules/**" -> check if path starts with "node_modules/"
            prefix = pattern.split("/**")[0]
            rel_str = relative.as_posix()
            return rel_str == prefix or rel_str.startswith(prefix + "/")
        return relative.match(pattern)

    def _detect_language(self, file_path: Path) -> str:
        """Detect programming language from file extension.

        Args:
            file_path: Path to the source file.

        Returns:
            Language name string.
        """
        ext = file_path.suffix.lower()
        return self.language_map.get(ext, ext.lstrip("."))

    @classmethod
    def _language_for(cls, file_path: Path) -> str:
        """Resolve language from file extension using only built-in defaults.

        Public-facing helpers (e.g. the ``llm4ad evolve`` CLI) need a
        language label without instantiating a full detector with a
        config dict. Mirrors :meth:`_detect_language` but uses the
        class-level :attr:`DEFAULT_LANGUAGE_MAP`.

        Args:
            file_path: Path to the source file.

        Returns:
            Language name string.
        """
        ext = file_path.suffix.lower()
        return cls.DEFAULT_LANGUAGE_MAP.get(ext, ext.lstrip("."))

    # Default include/exclude patterns shared with downstream tools so
    # the ``llm4ad evolve`` CLI scans the same set of files the
    # orchestrator analyses during evolution.
    DEFAULT_INCLUDE_PATTERNS: list[str] = [
        "*.py",
        "*.cpp",
        "*.cc",
        "*.c",
        "*.h",
        "*.hpp",
        "*.java",
        "*.js",
        "*.ts",
        "*.go",
        "*.rs",
        "*.rb",
        "*.sh",
        "*.php",
        "*.html",
        "*.xml",
        "*.comp",
        "*.glsl",
        "*.vert",
        "*.frag",
    ]

    DEFAULT_EXCLUDE_PATTERNS: list[str] = [
        ".git/**",
        ".gitignore",
        "__pycache__/**",
        "*.pyc",
        "node_modules/**",
        "build/**",
        "dist/**",
        "*.egg-info/**",
        ".venv/**",
        "venv/**",
        ".idea/**",
        ".vscode/**",
        "*.log",
        "*.tmp",
        "*.bak",
    ]

    @classmethod
    def iter_source_files(
        cls,
        root: Path | str,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> Iterator[Path]:
        """Yield files under ``root`` matching include/exclude globs.

        Wraps :meth:`_should_process` and :meth:`_path_matches` so callers
        outside the analyzer (e.g. the ``llm4ad evolve`` CLI and backend
        helpers) walk the same file set the orchestrator scans during
        evolution. When ``include``/``exclude`` are ``None`` the
        :attr:`DEFAULT_INCLUDE_PATTERNS` / :attr:`DEFAULT_EXCLUDE_PATTERNS`
        are used.

        Args:
            root: Directory to walk; resolved before walking.
            include: Optional include glob patterns.
            exclude: Optional exclude glob patterns.

        Yields:
            Absolute paths to files passing the filters.
        """
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            return

        includes = include if include is not None else cls.DEFAULT_INCLUDE_PATTERNS
        excludes = exclude if exclude is not None else cls.DEFAULT_EXCLUDE_PATTERNS

        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root_path)
            if any(cls._path_matches(relative, p) for p in excludes):
                continue
            if not any(cls._path_matches(relative, p) for p in includes):
                continue
            yield path

    def _get_context_before(self, lines: list[str], line_start: int) -> str:
        """Get context lines before the EVOLVE block starts.

        Args:
            lines: All lines of the file (with line endings).
            line_start: 1-based line number of the START marker.

        Returns:
            Concatenated context lines.
        """
        start_line = max(0, line_start - self.context_lines_before - 1)
        end_line = line_start - 1
        return "".join(lines[start_line:end_line])

    def _get_context_after(self, lines: list[str], line_end: int) -> str:
        """Get context lines after the EVOLVE block ends.

        Args:
            lines: All lines of the file (with line endings).
            line_end: 1-based line number of the END marker.

        Returns:
            Concatenated context lines.
        """
        start_line = line_end
        end_line = min(len(lines), line_end + self.context_lines_after)
        return "".join(lines[start_line:end_line])
