"""Start arxiv-mcp-server with platform-wide request coordination."""

from __future__ import annotations

import asyncio
import importlib
import os

from arxiv_coordination import RedisArxivRateLimiter, retry_delay_seconds


def _install_coordination() -> None:
    """Replace process-local arXiv throttling with the shared Redis gate."""
    redis_url = os.environ.get("LLM4AD_ARXIV_REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("LLM4AD_ARXIV_REDIS_URL is required")

    from redis import Redis
    from redis.asyncio import Redis as AsyncRedis

    limiter = RedisArxivRateLimiter(
        sync_client=Redis.from_url(redis_url, decode_responses=True),
        async_client=AsyncRedis.from_url(redis_url, decode_responses=True),
    )
    module_names = (
        "arxiv_mcp_server.arxiv_api",
        "arxiv_mcp_server.tools.search",
        "arxiv_mcp_server.tools.download",
        "arxiv_mcp_server.tools.semantic_search",
        "arxiv_mcp_server.tools.latex_archive",
        "arxiv_mcp_server.tools.latex",
    )
    for module_name in module_names:
        module = importlib.import_module(module_name)
        if hasattr(module, "ARXIV_RATE_LIMITER"):
            module.ARXIV_RATE_LIMITER = limiter

    search_module = importlib.import_module("arxiv_mcp_server.tools.search")
    search_module._backoff_seconds = retry_delay_seconds


def main() -> None:
    """Install coordination before serving the stdio MCP transport."""
    import arxiv_mcp_server

    _install_coordination()
    asyncio.run(arxiv_mcp_server.server.main())


if __name__ == "__main__":
    main()
