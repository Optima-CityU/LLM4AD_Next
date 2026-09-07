"""Tests for rendering MindMemOS runtime config from environment variables."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
MINDMEMOS_CONFIG_DIR = REPO_ROOT / "docker" / "mindmemos" / "config"
LLM4AD_SCHEMA_PATH = MINDMEMOS_CONFIG_DIR / "presets" / "llm4ad_memory_card.json"


def _load_render_config_module():
    module_path = Path(__file__).resolve().parents[3] / "docker" / "mindmemos" / "render_config.py"
    spec = importlib.util.spec_from_file_location("mindmemos_render_config", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_template(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "auth": {"mode": "api_key", "api_key_file": "api_keys.yaml"},
                "chat_model_router": {"endpoints": []},
                "embed_model_router": {"endpoints": []},
                "rerank_model_router": {"endpoints": []},
                "database": {"qdrant": {"vector_size": 1024}},
                "algo_config": {"search": {"rerank": {"enabled": False}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_jina_embedding_config_is_normalized_for_litellm(monkeypatch, tmp_path):
    module = _load_render_config_module()
    source = tmp_path / "dev.yaml"
    target = tmp_path / "rendered.yaml"
    _write_template(source)
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "TARGET", target)
    monkeypatch.setenv("MINDMEMOS_EMBED_MODEL", "jina-embeddings-v4")
    monkeypatch.setenv("MINDMEMOS_EMBED_API_BASE", "https://api.jinaai.cn")
    monkeypatch.setenv("MINDMEMOS_EMBED_API_KEY", "sk-test")
    monkeypatch.setenv("MINDMEMOS_EMBED_DIMENSIONS", "2048")

    module.main()

    rendered = yaml.safe_load(target.read_text(encoding="utf-8"))
    endpoint = rendered["embed_model_router"]["endpoints"][0]
    assert endpoint["model"] == "jina_ai/jina-embeddings-v4"
    assert endpoint["api_base"] == "https://api.jinaai.cn/v1"
    assert endpoint["dimensions"] == 2048
    assert rendered["database"]["qdrant"]["vector_size"] == 2048


def test_chat_config_is_rendered_from_environment(monkeypatch, tmp_path):
    module = _load_render_config_module()
    source = tmp_path / "dev.yaml"
    target = tmp_path / "rendered.yaml"
    _write_template(source)
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "TARGET", target)
    monkeypatch.setenv("MINDMEMOS_CHAT_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("MINDMEMOS_CHAT_API_BASE", "https://llm.example/v1")
    monkeypatch.setenv("MINDMEMOS_CHAT_API_KEY", "sk-chat")
    monkeypatch.setenv("MINDMEMOS_CHAT_TIMEOUT", "1200")
    monkeypatch.setenv("MINDMEMOS_CHAT_TEMPERATURE", "0")

    module.main()

    rendered = yaml.safe_load(target.read_text(encoding="utf-8"))
    endpoint = rendered["chat_model_router"]["endpoints"][0]
    assert endpoint["model"] == "openai/gpt-4.1-mini"
    assert endpoint["api_base"] == "https://llm.example/v1"
    assert endpoint["api_key"] == "sk-chat"
    assert endpoint["timeout"] == 1200
    assert endpoint["temperature"] == 0


def test_jina_rerank_config_is_normalized_when_enabled(monkeypatch, tmp_path):
    module = _load_render_config_module()
    source = tmp_path / "dev.yaml"
    target = tmp_path / "rendered.yaml"
    _write_template(source)
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "TARGET", target)
    monkeypatch.setenv("MINDMEMOS_RERANK_ENABLED", "true")
    monkeypatch.setenv("MINDMEMOS_RERANK_MODEL", "jina-reranker-v3")
    monkeypatch.setenv("MINDMEMOS_RERANK_API_BASE", "https://api.jinaai.cn")
    monkeypatch.setenv("MINDMEMOS_RERANK_API_KEY", "sk-test")

    module.main()

    rendered = yaml.safe_load(target.read_text(encoding="utf-8"))
    endpoint = rendered["rerank_model_router"]["endpoints"][0]
    assert endpoint["model"] == "jina_ai/jina-reranker-v3"
    assert endpoint["api_base"] == "https://api.jinaai.cn/v1"
    assert rendered["algo_config"]["search"]["rerank"]["enabled"] is True


def test_existing_provider_prefix_is_preserved(monkeypatch, tmp_path):
    module = _load_render_config_module()
    source = tmp_path / "dev.yaml"
    target = tmp_path / "rendered.yaml"
    _write_template(source)
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "TARGET", target)
    monkeypatch.setenv("MINDMEMOS_EMBED_MODEL", "openai/text-embedding-3-large")
    monkeypatch.setenv("MINDMEMOS_EMBED_API_BASE", "https://embedding.example/v1")
    monkeypatch.setenv("MINDMEMOS_EMBED_API_KEY", "sk-test")

    module.main()

    rendered = yaml.safe_load(target.read_text(encoding="utf-8"))
    endpoint = rendered["embed_model_router"]["endpoints"][0]
    assert endpoint["model"] == "openai/text-embedding-3-large"
    assert endpoint["api_base"] == "https://embedding.example/v1"


def test_api_key_file_is_rendered_as_container_absolute_path(monkeypatch, tmp_path):
    module = _load_render_config_module()
    source = tmp_path / "dev.yaml"
    target = tmp_path / "rendered.yaml"
    _write_template(source)
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "TARGET", target)

    module.main()

    rendered = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert (
        rendered["auth"]["api_key_file"]
        == "/app/mindmemos/config/mindmemos/api_keys.yaml"
    )


def test_mindmemos_uses_llm4ad_memory_card_schema():
    config = yaml.safe_load((MINDMEMOS_CONFIG_DIR / "dev.yaml").read_text(encoding="utf-8"))

    assert (
        config["algo_config"]["add"]["schema"]["entity_modeling_path"]
        == "config/mindmemos/presets/llm4ad_memory_card.json"
    )
    assert LLM4AD_SCHEMA_PATH.exists()


def test_llm4ad_memory_card_schema_matches_legacy_memory_types():
    schema = yaml.safe_load(LLM4AD_SCHEMA_PATH.read_text(encoding="utf-8"))
    entity_types = schema["entity_types"] if isinstance(schema, dict) else schema

    assert [entity["entity_type"] for entity in entity_types] == ["llm4ad_memory_card"]
    dynamic_property = entity_types[0]["dynamic_property"]
    assert set(dynamic_property) >= {
        "good_algorithm",
        "error_reflection",
        "domain_knowledge",
        "general_insight",
    }


def test_llm4ad_memory_card_tags_participate_in_first_order_extraction():
    schema = yaml.safe_load(LLM4AD_SCHEMA_PATH.read_text(encoding="utf-8"))
    entity_types = schema["entity_types"] if isinstance(schema, dict) else schema
    tags_property = entity_types[0]["dynamic_property"]["tags"]

    assert tags_property["order"] < 2


def test_llm4ad_memory_card_schema_requests_grounded_tags_for_every_card():
    schema = yaml.safe_load(LLM4AD_SCHEMA_PATH.read_text(encoding="utf-8"))
    entity_types = schema["entity_types"] if isinstance(schema, dict) else schema
    entity = entity_types[0]
    tags_property = entity["dynamic_property"]["tags"]

    assert "every" in entity["entity_instruction"].lower()
    assert "1-6" in tags_property["desc"]
    assert "grounded" in tags_property["desc"].lower()
