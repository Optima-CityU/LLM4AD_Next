"""Coder layer for LLM4AD.

Wraps existing coding agents (e.g., Claude Code, OpenCode) to implement
algorithms from natural language insights.
"""

from llm4ad.coder.base import (
    BaseCoder,
    CodingAgentType,
    GenerateResult,
    GenerateStatus,
)
from llm4ad.coder.claude_code import ClaudeCode
from llm4ad.coder.codex_cli import CodexCLI
from llm4ad.coder.custom_naive_coder import CustomNaiveCoder
from llm4ad.coder.opencode import OpenCode

__all__ = [
    "BaseCoder",
    "GenerateResult",
    "GenerateStatus",
    "CodingAgentType",
    "ClaudeCode",
    "CodexCLI",
    "CustomNaiveCoder",
    "OpenCode",
]
