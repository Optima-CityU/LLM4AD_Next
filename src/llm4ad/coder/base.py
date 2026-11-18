"""Base coder interface for LLM4AD."""

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from llm4ad.config.schema import CoderConfig
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.utils.registry import Registrable

if TYPE_CHECKING:
    from llm4ad.infra.provider.base import BaseProvider


class GenerateStatus(Enum):
    """Code generation status."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    PARTIAL = "partial"  # Some files generated, some failed


class GenerateResult(BaseModel):
    """Code generation result.

    Instead of returning code directly, we track:
    - Whether generation succeeded
    - The working directory where files were created
    - Any error messages
    """

    status: GenerateStatus
    working_dir: str  # Directory where code was generated
    generated_files: list[str] = []  # List of generated file paths (relative to working_dir)
    main_file: str | None = None  # Main entry point file
    error_message: str | None = None  # Error details if failed
    metadata: dict[str, Any] = Field(default_factory=dict)  # Additional info (tokens used, etc.)
    token_used: int = 0  # Total tokens used for generation
    timing: ExecutionTiming = Field(default_factory=ExecutionTiming)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def is_success(self) -> bool:
        """Check if generation was successful."""
        return self.status == GenerateStatus.SUCCESS

    @property
    def is_partial(self) -> bool:
        """Check if generation was partial."""
        return self.status == GenerateStatus.PARTIAL

    def get_absolute_paths(self) -> list[str]:
        """Get absolute paths of all generated files."""
        working = Path(self.working_dir)
        return [str(working / f) for f in self.generated_files]


class CodingAgentType(Enum):
    """Supported coding agent types."""

    CLAUDE_CODE = "claude_code"  # Anthropic Claude Code
    OPENCODE = "opencode"  # OpenCode
    CODEX_CLI = "codex_cli"  # Codex CLI with local ChatGPT login
    CLAUDE_CLI = "claude_cli"  # Claude CLI (anthropic)
    CUSTOM = "custom"  # Custom agent


class BaseCoder(ABC, Registrable):
    """Abstract coder interface.

    Wraps existing coding agents to implement algorithms from natural language insights.
    The coder receives an algorithm description and generates code files in a working directory.
    Code execution and evaluation are handled by the sandbox and evaluator layers.
    """

    def __init__(self, config: CoderConfig, provider: "BaseProvider | None" = None):
        """Initialize coder with configuration.

        Args:
            config: Coder configuration containing:
                - type: Type of coding agent to use
                - timeout: Timeout for code generation
                - max_retries: Maximum retry attempts
            provider: Optional LLM provider for coders that use direct LLM calls
                (e.g., CustomNaiveCoder). Agent-based coders (e.g., ClaudeCode)
                manage their own API connections and can ignore this.
        """
        self.config = config
        self.agent_type = CodingAgentType(config.type)
        self.timeout = config.timeout
        self.max_retries = config.max_retries

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of the coder."""
        pass

    @abstractmethod
    async def generate(
        self, prompt: str, context: dict[str, Any], working_dir: str, **kwargs
    ) -> GenerateResult:
        """Generate code from algorithm insight using coding agent.

        The coder writes generated files to the specified working directory.
        We don't return the code itself - we just track success/failure and file locations.

        Args:
            prompt: The prompt for code generation
            context: Additional context containing:
                - task_description: Description of the task
                - constraints: Any constraints on the solution
                - test_cases: Optional test cases
                - parent_code: Code from parent (for mutation)
                - similar_solutions: Similar successful solutions
            working_dir: Directory to write generated code files
            **kwargs: Additional generation options

        Returns:
            GenerateResult with status and generated file paths
        """
        pass

    def prepare_context(self, insight: str, base_context: dict[str, Any]) -> dict[str, Any]:
        """Prepare context for code generation.

        Subclasses can override to add custom context preparation.

        Args:
            insight: Algorithm insight
            base_context: Base context from caller

        Returns:
            Prepared context
        """
        context = base_context.copy()
        context["insight"] = insight
        return context
