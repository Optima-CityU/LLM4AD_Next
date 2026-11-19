"""Search SearXNG and read bounded public web evidence without extra dependencies."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import partial
from html.parser import HTMLParser
from typing import Any

import httpx

_ENGINE_NAME = re.compile(r"^[a-zA-Z0-9 _.-]{1,64}$")
_REDIRECTS = {301, 302, 303, 307, 308}
_RETRYABLE = {429, 502, 503, 504}


class SearchError(ValueError):
    """Represent a bounded, user-readable search or evidence retrieval failure."""

    def __init__(self, message: str, *, retryable: bool = False, retry_after: float = 0) -> None:
        """Record a public diagnostic and optional upstream retry policy."""
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def engine_names(value: str) -> tuple[str, ...]:
    """Parse an operator-controlled comma-separated engine list."""
    names = tuple(dict.fromkeys(name.strip() for name in value.split(",") if name.strip()))
    if not names or len(names) > 8 or any(not _ENGINE_NAME.fullmatch(name) for name in names):
        raise SearchError("Specify between one and eight configured engine names")
    return names


@dataclass(frozen=True)
class SearchOptions:
    """Hold bounded configuration shared by stdio MCP tools and tests."""

    searxng_url: str = "http://searxng:8080"
    engines: str = "bing,baidu,sogou,360search"
    default_engines: str = "bing"
    max_concurrency: int = 2
    min_interval_seconds: float = 1.0
    wait_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 20.0
    max_retries: int = 1
    max_results: int = 10
    read_max_bytes: int = 524288
    read_max_chars: int = 16000

    def __post_init__(self) -> None:
        """Reject unsafe limits before starting a workspace MCP process."""
        url = httpx.URL(self.searxng_url)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("SEARXNG_URL must be a configured HTTP(S) service URL")
        if not set(engine_names(self.default_engines)) <= set(engine_names(self.engines)):
            raise ValueError("Default search engines must be enabled by the operator")
        for value, low, high in (
            (self.max_concurrency, 1, 16),
            (self.min_interval_seconds, 0.1, 60),
            (self.wait_timeout_seconds, 0.1, 60),
            (self.request_timeout_seconds, 0.1, 60),
            (self.max_retries, 0, 2),
            (self.max_results, 1, 30),
            (self.read_max_bytes, 1024, 2097152),
            (self.read_max_chars, 256, 50000),
        ):
            if not low <= value <= high:
                raise ValueError("Search configuration exceeds supported bounds")

    @classmethod
    def from_environment(cls) -> SearchOptions:
        """Read only the public configuration passed in the MCP server profile."""
        defaults = cls()
        values: dict[str, Any] = {"searxng_url": os.environ.get("SEARXNG_URL", defaults.searxng_url)}
        for name in cls.__dataclass_fields__:
            if name == "searxng_url":
                continue
            default = getattr(defaults, name)
            values[name] = type(default)(os.environ.get(f"PAPER_SEARCH_{name.upper()}", str(default)))
        return cls(**values)


class _PageText(HTMLParser):
    """Extract visible text and a title without executing remote markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.in_title = False
        self.title_done = False
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden.append(tag)
        if tag == "title" and not self.title_done:
            self.in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag == "title" and self.in_title:
            self.in_title = False
            self.title_done = True
        if tag in {"p", "div", "li", "section", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)
            if self.in_title:
                self.title.append(data)


def _plain_html(value: object, limit: int) -> str:
    """Convert engine markup to bounded text without inventing missing values."""
    parser = _PageText()
    parser.feed(str(value or "")[: limit * 4])
    return " ".join("".join(parser.parts).split())[:limit]


def _is_public(address: str) -> bool:
    """Reject local, reserved, multicast, and mapped non-public IP addresses."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return _is_public(str(ip.ipv4_mapped))
        # Translation and tunneling ranges can embed an internal IPv4 target.
        if ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96"):
            return False
    return ip.is_global and not ip.is_multicast


async def resolve_public_url(value: str) -> tuple[httpx.URL, str]:
    """Validate every DNS answer and return a public IP to pin the connection.

    Args:
        value: Untrusted evidence URL or redirect destination.

    Returns:
        Original URL and a validated IP address for the TCP connection.

    Raises:
        SearchError: The URL or any DNS answer is unsafe or cannot be resolved.
    """
    if len(value) > 4096 or "\\" in value or any(ord(char) < 32 for char in value):
        raise SearchError("Invalid public webpage URL")
    try:
        url = httpx.URL(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.port not in {None, 80 if url.scheme == "http" else 443}
            or "%" in url.host
        ):
            raise SearchError("Only public HTTP(S) URLs on their standard ports are allowed")
        host = url.host.rstrip(".").lower()
        if (
            host == "localhost"
            or host.endswith((".localhost", ".local", ".internal"))
            or "." not in host
            and ":" not in host
        ):
            raise SearchError("Internal webpage addresses are forbidden")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            answers = await asyncio.get_running_loop().getaddrinfo(
                host,
                url.port or (443 if url.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or not all(_is_public(address) for address in addresses):
            raise SearchError("Webpage DNS must resolve exclusively to public addresses")
        return url.copy_with(fragment=None), addresses[0]
    except (httpx.InvalidURL, socket.gaierror, ValueError) as error:
        if isinstance(error, SearchError):
            raise
        raise SearchError("Cannot resolve a safe public webpage URL") from error


def _retry_after(value: str | None) -> float:
    """Interpret Retry-After seconds or an HTTP date with a finite upper bound."""
    if not value:
        return 2.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 2.0
    if seconds != seconds:
        return 2.0
    return min(86400.0, max(0.0, seconds))


class ResearchSearchService:
    """Expose genuine engine responses and bounded, untrusted page excerpts."""

    def __init__(
        self,
        options: SearchOptions,
        gate: Any,
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        resolver: Callable[[str], Awaitable[tuple[httpx.URL, str]]] = resolve_public_url,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Inject coordination and HTTP boundaries for deterministic testing."""
        self.options = options
        self.gate = gate
        self.client_factory = client_factory
        self.resolver = resolver
        self.sleep = sleep

    async def _body(self, response: httpx.Response, limit: int) -> tuple[bytes, bool]:
        """Read at most the allowed bytes and reject unexpected compression."""
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise SearchError("Compressed responses are unsupported by the bounded webpage reader")
        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            remaining = limit - len(content)
            content.extend(chunk[:remaining])
            if len(chunk) > remaining:
                return bytes(content), True
        return bytes(content), False

    async def _check_status(self, response: httpx.Response) -> None:
        """Share throttling before releasing the admitted request slot."""
        if response.status_code in _RETRYABLE:
            delay = _retry_after(response.headers.get("retry-after"))
            if response.status_code in {429, 503}:
                await self.gate.cooldown(delay)
            raise SearchError(
                f"Upstream HTTP {response.status_code}",
                retryable=True,
                retry_after=delay,
            )
        if response.status_code >= 400:
            raise SearchError(f"Upstream HTTP {response.status_code}")

    async def _run(
        self,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        *,
        coordinate: bool = True,
    ) -> dict[str, Any]:
        """Retry only transient failures within fixed attempt and delay bounds."""
        for attempt in range(self.options.max_retries + 1):
            try:
                # Bound the whole attempt, including admission and all redirects.
                async with asyncio.timeout(self.options.wait_timeout_seconds + self.options.request_timeout_seconds):
                    result = await self.gate.run_async(operation) if coordinate else await operation()
                result["attempts"] = attempt + 1
                return result
            except (SearchError, httpx.TransportError, TimeoutError) as error:
                retryable = (
                    isinstance(error, httpx.TransportError) or isinstance(error, SearchError) and error.retryable
                )
                delay = max(1.0 * 2**attempt, getattr(error, "retry_after", 0.0))
                if retryable and attempt < self.options.max_retries and delay <= 5:
                    await self.sleep(delay)
                    continue
                message = (
                    str(error) if isinstance(error, SearchError) else "Search request timed out or transport failed"
                )
                return {"status": "error", "error": message, "attempts": attempt + 1, "results": []}
        raise AssertionError("Unreachable retry state")

    async def search(
        self,
        q: str,
        language: str = "auto",
        engines: str = "",
        page: int = 1,
    ) -> dict[str, Any]:
        """Search configured engines and expose snippets as unverified leads.

        Args:
            q: Nonempty query, optionally containing site filters.
            language: SearXNG language code, such as en or zh-CN.
            engines: Comma-separated subset of operator-enabled engines.
            page: Bounded one-based search page number.

        Returns:
            Titles, URLs, snippets, engine attribution, and failure diagnostics.
        """
        try:
            if not q.strip() or len(q) > 2048 or not 1 <= page <= 10:
                raise SearchError("Use a query of 1-2048 characters and a page from 1 to 10")
            if re.search(r"(^|\s)[!:]\S", q):
                raise SearchError("Select engines with the engines argument, not query engine/category overrides")
            if not re.fullmatch(r"[a-zA-Z_-]{1,20}", language):
                raise SearchError("Invalid search language")
            selected = engine_names(engines or self.options.default_engines)
            if not set(selected) <= set(engine_names(self.options.engines)):
                raise SearchError("Requested engines are not enabled by the operator")
        except SearchError as error:
            return {"status": "error", "error": str(error), "results": [], "attempts": 0}

        async def operation() -> dict[str, Any]:
            async with self.client_factory(
                timeout=self.options.request_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "GET",
                    self.options.searxng_url.rstrip("/") + "/search",
                    params={
                        "q": q,
                        "format": "json",
                        "language": language,
                        "engines": ",".join(selected),
                        "pageno": page,
                    },
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                ) as response:
                    await self._check_status(response)
                    if response.status_code != 200:
                        raise SearchError("SearXNG search did not return HTTP 200")
                    body, truncated = await self._body(response, 1048576)
                    if truncated:
                        raise SearchError("SearXNG JSON response exceeds the size limit")
                    try:
                        data = json.loads(body)
                        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                            raise ValueError("Missing search results")
                    except (ValueError, UnicodeError) as error:
                        raise SearchError(
                            "SearXNG did not return a valid search JSON response; check search.formats"
                        ) from error
            results = []
            for row in data["results"]:
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url") or "")[:4096]
                try:
                    parsed = httpx.URL(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.host or parsed.userinfo:
                        continue
                except httpx.InvalidURL:
                    continue
                attribution = row.get("engines") or [row.get("engine")]
                results.append(
                    {
                        "title": _plain_html(row.get("title"), 500),
                        "url": url,
                        "snippet": _plain_html(row.get("content"), 2000),
                        "engines": [str(name)[:64] for name in attribution[:8] if name]
                        if isinstance(attribution, list)
                        else [],
                        "evidence_status": "unverified_search_snippet",
                    }
                )
                if len(results) >= self.options.max_results:
                    break
            failures = data.get("unresponsive_engines", [])
            return {
                "status": "partial" if failures else "ok",
                "query": q,
                "language": language,
                "page": page,
                "requested_engines": list(selected),
                "results": results,
                "unresponsive_engines": [
                    [str(part)[:500] for part in failure[:2]] if isinstance(failure, list) else str(failure)[:500]
                    for failure in failures[:16]
                ]
                if isinstance(failures, list)
                else [],
                "warnings": [str(value)[:500] for value in data.get("warnings", [])[:8]]
                if isinstance(data.get("warnings", []), list)
                else [],
                "evidence_status": "unverified_search_snippets",
            }

        return await self._run(operation)

    async def read_webpage(self, url: str) -> dict[str, Any]:
        """Read a bounded public text excerpt, validating and pinning every hop.

        Args:
            url: Public HTTP(S) evidence URL on a standard port.

        Returns:
            Fetched text, original and final URLs, title, and truncation status.
        """

        async def fetch(current: str) -> dict[str, Any]:
            original, address = await self.resolver(current)
            # A fresh client prevents IP-pooled TLS sessions crossing hostnames.
            async with self.client_factory(
                timeout=self.options.request_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "GET",
                    original.copy_with(host=address),
                    headers={
                        "Host": original.netloc.decode("ascii"),
                        "Accept-Encoding": "identity",
                        "Accept": "text/html,text/plain,application/xhtml+xml",
                    },
                    extensions={"sni_hostname": original.host},
                ) as response:
                    await self._check_status(response)
                    if response.status_code in _REDIRECTS:
                        target = response.headers.get("location")
                        if not target:
                            raise SearchError("Webpage redirect is missing its destination")
                        return {"redirect_url": str(original.join(target))}
                    if response.status_code != 200:
                        raise SearchError(f"Webpage returned HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                        raise SearchError("Only HTML and plain-text webpages can be read")
                    body, truncated = await self._body(response, self.options.read_max_bytes)
                    encoding = response.charset_encoding or "utf-8"
                    try:
                        text = body.decode(encoding, errors="replace")
                    except LookupError:
                        text = body.decode("utf-8", errors="replace")
            title = ""
            if content_type != "text/plain":
                parser = _PageText()
                parser.feed(text)
                text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
                title = " ".join("".join(parser.title).split())[:500]
            return {
                "status": "ok",
                "url": url,
                "final_url": str(original),
                "title": title,
                "text": text[: self.options.read_max_chars],
                "content_type": content_type,
                "truncated": truncated or len(text) > self.options.read_max_chars,
                "evidence_status": "retrieved_untrusted_webpage_excerpt",
            }

        async def operation() -> dict[str, Any]:
            current = url
            for _hop in range(4):
                # Each redirect consumes global rate and concurrency admission.
                result = await self.gate.run_async(partial(fetch, current))
                if "redirect_url" not in result:
                    return result
                current = result["redirect_url"]
            raise SearchError("Webpage redirect limit exceeded")

        return await self._run(operation, coordinate=False)
