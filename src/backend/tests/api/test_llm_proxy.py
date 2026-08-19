"""Credential proxy URL construction tests."""

import pytest

from app.api.llm4ad import llm_proxy


@pytest.mark.parametrize(
    ("base_url", "path", "expected"),
    [
        (
            "https://api.openai.com/v1",
            "v1/chat/completions",
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "https://gateway.example",
            "v1/chat/completions",
            "https://gateway.example/v1/chat/completions",
        ),
        (
            "https://api.anthropic.com",
            "v1/messages",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "https://gateway.example/v1/",
            "/chat/completions",
            "https://gateway.example/v1/chat/completions",
        ),
    ],
)
def test_join_upstream_url_avoids_duplicate_api_version(
    base_url: str,
    path: str,
    expected: str,
) -> None:
    assert llm_proxy._join_upstream_url(base_url, path) == expected
