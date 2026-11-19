"""Verify real search diagnostics and the public evidence reader's boundaries."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import replace

import httpx
import pytest
import pytest_asyncio
from app.services.research_search import (
    ResearchSearchService,
    SearchError,
    SearchOptions,
    resolve_public_url,
)


class Gate:
    """Record admission and cooldowns while HTTP behavior is tested separately."""

    def __init__(self) -> None:
        """Initialize counters."""
        self.calls = 0
        self.cooldowns: list[float] = []

    async def run_async(self, operation):
        """Run one admitted request."""
        self.calls += 1
        return await operation()

    async def cooldown(self, seconds):
        """Record an upstream throttle."""
        self.cooldowns.append(seconds)


async def no_sleep(_delay: float) -> None:
    """Make deterministic retry tests immediate."""


def service(handler, *, options=None, gate=None, **kwargs):
    """Create a service with the same HTTPX API but no live HTTP traffic."""

    def client_factory(**client_kwargs):
        assert client_kwargs["trust_env"] is False
        assert client_kwargs["follow_redirects"] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_kwargs)

    return ResearchSearchService(
        options or SearchOptions(),
        gate or Gate(),
        client_factory=client_factory,
        sleep=no_sleep,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_search_forwards_parameters_and_preserves_partial_failures():
    """Return supplied evidence fields and engine failures without synthesis."""

    def handler(request):
        assert request.url.host == "searxng"
        assert dict(request.url.params) == {
            "q": "site:example.org algorithm",
            "language": "zh-CN",
            "engines": "bing,baidu",
            "pageno": "2",
            "format": "json",
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "<b>Real title</b>",
                        "url": "https://example.org/paper",
                        "content": "A <em>real</em> snippet",
                        "engines": ["bing"],
                    },
                    {"url": "https://example.org/no-snippet", "engine": "bing"},
                    {"title": "bad", "url": "file:///etc/passwd"},
                ],
                "unresponsive_engines": [["baidu", "CAPTCHA"]],
            },
        )

    result = await service(handler).search("site:example.org algorithm", "zh-CN", "bing,baidu", 2)
    assert result["status"] == "partial"
    assert result["unresponsive_engines"] == [["baidu", "CAPTCHA"]]
    assert result["results"][0] == {
        "title": "Real title",
        "url": "https://example.org/paper",
        "snippet": "A real snippet",
        "engines": ["bing"],
        "evidence_status": "unverified_search_snippet",
    }
    assert result["results"][1]["snippet"] == ""
    assert len(result["results"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"q": ""},
        {"q": "x" * 2049},
        {"q": "query", "page": 0},
        {"q": "query", "page": 11},
        {"q": "query", "engines": "google"},
        {"q": "!google query"},
        {"q": "query :general"},
        {"q": "query", "language": "en&format=html"},
    ],
)
async def test_invalid_search_never_contacts_an_upstream(arguments):
    """Reject attempts to bypass the operator's engine or argument policy."""
    gate = Gate()
    result = await service(lambda _request: pytest.fail("Unexpected HTTP request"), gate=gate).search(**arguments)
    assert result["status"] == "error"
    assert result["attempts"] == 0
    assert gate.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", [httpx.Response(403), httpx.Response(200, text="<html>Not JSON</html>"), httpx.Response(200, json={})]
)
async def test_invalid_upstream_response_does_not_invent_search_evidence(response):
    """Distinguish unavailable JSON APIs from real, empty search results."""
    result = await service(lambda _request: response).search("algorithm")
    assert result["status"] == "error"
    assert result["results"] == []
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_retry_is_limited_and_throttle_is_shared():
    """Retry transient errors at most the configured number of times."""
    gate = Gate()
    result = await service(lambda _request: httpx.Response(503, headers={"Retry-After": "2"}), gate=gate).search("query")
    assert result["status"] == "error"
    assert result["attempts"] == gate.calls == 2
    assert gate.cooldowns == [2.0, 2.0]


@pytest.mark.asyncio
async def test_long_retry_after_returns_without_waiting_or_shortening_cooldown():
    """Preserve a long server cooldown without tying up a workspace MCP call."""
    gate = Gate()
    result = await service(lambda _request: httpx.Response(429, headers={"Retry-After": "120"}), gate=gate).search("query")
    assert result["attempts"] == 1
    assert gate.cooldowns == [120.0]


@pytest.mark.asyncio
async def test_transport_failure_can_recover_with_one_retry():
    """Do not treat temporary connection failures as empty engine responses."""
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary")
        return httpx.Response(200, json={"results": []})

    result = await service(handler).search("query")
    assert result["status"] == "ok"
    assert result["attempts"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://192.168.0.1",
        "http://169.254.169.254",
        "http://100.64.0.1",
        "http://0.0.0.0",
        "http://224.0.0.1",
        "http://[::1]",
        "http://[fc00::1]",
        "http://[::ffff:127.0.0.1]",
        "http://[64:ff9b::a00:1]",
        "http://[2002:0a00:0001::1]",
        "http://localhost",
        "http://backend",
        "http://backend.local",
        "http://host.internal",
        "file:///etc/passwd",
        "ftp://example.org",
        "http://user:pass@example.org",
        "http://example.org:8000",
        "http://[fe80::1%eth0]",
        "http://example.org\\@127.0.0.1",
        "http://example.org/\nsecret",
    ],
)
async def test_public_reader_rejects_internal_and_non_http_urls(url):
    """Prevent direct and encoded access to local services and metadata."""
    with pytest.raises(SearchError):
        await resolve_public_url(url)


@pytest_asyncio.fixture
async def public_dns(monkeypatch):
    """Resolve a test hostname without using the runner's network."""

    async def getaddrinfo(host, _port, **_kwargs):
        assert host == "source.example.org"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)


@pytest.mark.asyncio
async def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    """Reject the whole target if any resolved address is nonpublic."""

    async def getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    with pytest.raises(SearchError, match="exclusively"):
        await resolve_public_url("https://source.example.org/paper")


@pytest.mark.asyncio
async def test_read_pins_validated_address_and_keeps_hostname_for_tls(public_dns):
    """Close the DNS rebinding gap while retaining hostname certificate checks."""

    def handler(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "source.example.org"
        assert request.extensions["sni_hostname"] == "source.example.org"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<title>Real source</title><svg><title>Menu icon</title></svg><script>ignore</script><p>Verified &amp; bounded excerpt</p>",
        )

    result = await service(handler).read_webpage("https://source.example.org/paper")
    assert result["status"] == "ok"
    assert result["title"] == "Real source"
    assert "Verified & bounded excerpt" in result["text"]
    assert "ignore" not in result["text"]
    assert result["final_url"] == "https://source.example.org/paper"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_redirect_to_private_target_is_rejected_before_second_request(public_dns):
    """Apply public-address validation to redirect destinations as well."""
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    result = await service(handler).read_webpage("https://source.example.org")
    assert result["status"] == "error"
    assert calls == 1


@pytest.mark.asyncio
async def test_redirects_and_text_output_are_bounded(public_dns):
    """Stop redirect loops and cap excerpt bytes and characters."""
    gate = Gate()
    loop_result = await service(lambda _request: httpx.Response(302, headers={"Location": "/again"}), gate=gate).read_webpage(
        "https://source.example.org"
    )
    assert loop_result["status"] == "error"
    assert gate.calls == 4
    options = replace(SearchOptions(), read_max_bytes=1024, read_max_chars=256)
    result = await service(
        lambda _request: httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"a" * 4096),
        options=options,
    ).read_webpage("https://source.example.org")
    assert result["truncated"] is True
    assert len(result["text"]) == 256


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers", [{"Content-Type": "application/pdf"}, {"Content-Type": "text/plain", "Content-Encoding": "br"}]
)
async def test_binary_and_compressed_responses_are_rejected(public_dns, headers):
    """Keep evidence reads within their supported, bounded text format."""
    result = await service(lambda _request: httpx.Response(200, headers=headers, content=b"")).read_webpage(
        "https://source.example.org"
    )
    assert result["status"] == "error"


def test_defaults_must_be_a_subset_of_operator_engines():
    """Fail startup on inconsistent engine configuration."""
    with pytest.raises(ValueError):
        SearchOptions(engines="baidu", default_engines="bing")
