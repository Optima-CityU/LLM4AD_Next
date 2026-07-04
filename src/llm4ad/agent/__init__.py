"""AgentScope-based AI build (beta): a single ReAct agent for LLM4AD task packages.

The agent builds and self-verifies an LLM4AD task package. Shared by the backend
(HTTP/SSE endpoint) and the ``llm4ad chatv2`` CLI. agentscope is a base dependency
(the project requires Python >=3.12) and is imported lazily inside
:mod:`llm4ad.agent.runner` to keep imports cheap.
"""

from __future__ import annotations

from llm4ad.agent.runner import (
    AgentBuildConfig,
    build_model,
    make_tools,
    run_agent_build,
)
from llm4ad.agent.sandbox import resolve_within_sandbox, sse_frame
from llm4ad.agent.skill import build_system_prompt, build_task_message

__all__ = [
    "AgentBuildConfig",
    "build_model",
    "build_system_prompt",
    "build_task_message",
    "make_tools",
    "resolve_within_sandbox",
    "run_agent_build",
    "sse_frame",
]
