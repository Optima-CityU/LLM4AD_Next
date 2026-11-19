"""Check the operator settings, generated SearXNG config, and MCP profile."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.research_search import SearchOptions
from app.services.research_search_mcp import research_search_mcp_configuration

ROOT = Path(__file__).resolve().parents[3]


def settings():
    """Supply the public backend configuration without API/database startup."""
    options = SearchOptions()
    values = {
        f"PAPER_SEARCH_{name.upper()}": getattr(options, name) for name in options.__dataclass_fields__ if name != "searxng_url"
    }
    return SimpleNamespace(
        **values, PAPER_SEARCH_ENABLED=True, PAPER_SEARCH_SEARXNG_URL=options.searxng_url, REDIS_BASE_URL="redis://redis:6379"
    )


@pytest.mark.parametrize("mode", ["proposal", "manuscript"])
def test_search_profile_is_available_for_both_workflows(mode):
    """Bind search independently of which proposal or rebuttal stage is active."""
    servers, tools = research_search_mcp_configuration(settings(), workspace_mode=mode)
    assert tools == ["mcp__research_search__search", "mcp__research_search__read_webpage"]
    server = servers["research_search"]
    assert server["command"] == "/app/backend/.venv/bin/python"
    assert server["args"] == ["/app/paper-agent/searxng_entrypoint.py"]
    assert server["env"]["LLM4AD_SEARCH_REDIS_URL"] == "redis://redis:6379/4"
    assert server["env"]["PAPER_SEARCH_DEFAULT_ENGINES"] == "bing"


def test_search_can_be_disabled_and_is_not_injected_into_unowned_modes():
    """Keep native search out of the returned MCP configuration."""
    config = settings()
    assert research_search_mcp_configuration(config, workspace_mode="algorithm") == ({}, [])
    config.PAPER_SEARCH_ENABLED = False
    assert research_search_mcp_configuration(config, workspace_mode="proposal") == ({}, [])


def test_generated_searxng_config_enables_json_and_operator_engine_subset(monkeypatch):
    """Use upstream engine definitions and avoid enabling arbitrary defaults."""
    spec = importlib.util.spec_from_file_location("search_bootstrap_test", ROOT / "docker/paper-agent/searxng_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("SEARXNG_ENGINES", "bing,baidu,bing")
    config = module.build_settings()
    assert "json" in config["search"]["formats"]
    assert config["use_default_settings"]["engines"]["keep_only"] == ["bing", "baidu"]
    assert config["engines"] == [{"name": "bing", "disabled": False}, {"name": "baidu", "disabled": False}]
    assert config["server"]["public_instance"] is False
    assert config["server"]["secret_key"] != module.build_settings()["server"]["secret_key"]
