"""Serve coordinated SearXNG search and public evidence reads over stdio MCP."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from redis.asyncio import Redis
from research_search import ResearchSearchService, SearchOptions
from search_coordination import RedisSearchGate


def create_server() -> FastMCP:
    """Build tools using the existing FastMCP SDK and shared Redis database."""
    options = SearchOptions.from_environment()
    redis_url = os.environ.get("LLM4AD_SEARCH_REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("LLM4AD_SEARCH_REDIS_URL is required for global coordination")
    redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
    service = ResearchSearchService(
        options,
        RedisSearchGate(
            redis,
            max_concurrency=options.max_concurrency,
            min_interval=options.min_interval_seconds,
            wait_timeout=options.wait_timeout_seconds,
            request_timeout=options.request_timeout_seconds,
        ),
    )

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        """Close the process-local Redis connection on MCP shutdown."""
        try:
            yield
        finally:
            await redis.aclose()

    mcp = FastMCP("research-search", lifespan=lifespan)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True))
    async def search(q: str, language: str = "auto", engines: str = "", page: int = 1) -> dict:
        """Search public web engines; snippets are leads requiring source verification.

        Args:
            q: Search query, optionally containing site filters.
            language: Language code such as en or zh-CN.
            engines: Optional comma-separated subset of operator-enabled engines.
            page: Search page from 1 to 10.

        Returns:
            Titles, URLs, snippets, engine attribution, and engine diagnostics.
        """
        return await service.search(q, language, engines, page)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True))
    async def read_webpage(url: str) -> dict:
        """Read a bounded public webpage excerpt to verify a search lead.

        Args:
            url: Public HTTP(S) URL, without credentials, on its standard port.

        Returns:
            Source URL, final URL, title, excerpt, and truncation status.
        """
        return await service.read_webpage(url)

    return mcp


if __name__ == "__main__":
    create_server().run(transport="stdio")
