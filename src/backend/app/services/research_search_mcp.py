"""Build the read-only research search MCP profile for native runtimes."""

from __future__ import annotations

from typing import Any


def research_search_mcp_configuration(
    settings: Any,
    *,
    workspace_mode: str,
) -> tuple[dict[str, object], list[str]]:
    """Return search tools for proposal and rebuttal conversations.

    Args:
        settings: Backend Settings instance with PAPER_SEARCH configuration.
        workspace_mode: Research workflow mode, independent of current stage.

    Returns:
        MCP servers and allowed tool names to merge into the runtime profile.
    """
    if not settings.PAPER_SEARCH_ENABLED or workspace_mode not in {"proposal", "manuscript"}:
        return {}, []
    environment = {
        "LLM4AD_SEARCH_REDIS_URL": f"{settings.REDIS_BASE_URL}/4",
        "SEARXNG_URL": settings.PAPER_SEARCH_SEARXNG_URL,
    }
    for name in (
        "ENGINES",
        "DEFAULT_ENGINES",
        "MAX_CONCURRENCY",
        "MIN_INTERVAL_SECONDS",
        "WAIT_TIMEOUT_SECONDS",
        "REQUEST_TIMEOUT_SECONDS",
        "MAX_RETRIES",
        "MAX_RESULTS",
        "READ_MAX_BYTES",
        "READ_MAX_CHARS",
    ):
        environment[f"PAPER_SEARCH_{name}"] = str(getattr(settings, f"PAPER_SEARCH_{name}"))
    return {
        "research_search": {
            "type": "stdio",
            "command": "/app/backend/.venv/bin/python",
            "args": ["/app/paper-agent/searxng_entrypoint.py"],
            "env": environment,
        }
    }, ["mcp__research_search__search", "mcp__research_search__read_webpage"]
