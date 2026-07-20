"""Tests for MindMemOS provider binding integration."""

import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.db import engine
from app.schemas.memory import MemoryProviderBindingUpdate
from app.services import memory_service
from tests.utils.user import create_random_user


@pytest.fixture(scope="module")
def db():
    with Session(engine) as session:
        yield session


def _enable_mindmemos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")


def _create_chat_provider(
    db: Session,
    user_id: uuid.UUID,
    *,
    model: str = "qwen-plus;qwen-max",
) -> models.LLMProvider:
    provider = models.LLMProvider(
        name="chat",
        user_id=user_id,
        type=models.ProviderType.OPENAI_COMPATIBLE,
        model=model,
        api_key="sk-chat",
        base_url="https://llm.example/v1",
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _create_embedding_provider(
    db: Session,
    user_id: uuid.UUID,
    *,
    model: str = "jina-embeddings-v4",
    dim: int = 1024,
) -> models.EmbeddingProvider:
    provider = models.EmbeddingProvider(
        name=f"embedding-{dim}",
        user_id=user_id,
        type=models.EmbeddingProviderType.JINA,
        model=model,
        dim=dim,
        api_key="sk-embed",
        base_url="https://api.jinaai.cn/v1",
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def test_memory_provider_routers_use_stable_builtin_gateway_urls():
    """MindMemOS bindings must not persist an expiring user access token."""
    chat_provider = models.LLMProvider(
        name="builtin-chat",
        type=models.ProviderType.OPENAI_COMPATIBLE,
        model="qwen-plus",
        api_key="EMPTY",
        base_url="http://gateway:9090/litellm_proxy/team-1/{accessToken}/v1",
        is_builtin=True,
        visible_to_all=True,
    )
    embedding_provider = models.EmbeddingProvider(
        name="builtin-embedding",
        type=models.EmbeddingProviderType.LOCAL,
        mode=models.EmbeddingMode.SPLIT,
        text_type=models.EmbeddingProviderType.OPENAI_COMPATIBLE,
        text_model="text-embedding-3-large",
        text_api_key="EMPTY",
        text_base_url="http://gateway:9090/litellm_proxy/team-1/{accessToken}/v1",
        code_type=models.EmbeddingProviderType.OPENAI_COMPATIBLE,
        code_model="text-embedding-3-large",
        code_api_key="EMPTY",
        code_base_url="http://gateway:9090/litellm_proxy/team-1/{accessToken}/v1",
        dim=3072,
        is_builtin=True,
        visible_to_all=True,
    )
    routers = memory_service._memory_provider_routers(chat_provider, "qwen-plus", embedding_provider)

    assert routers["chat_model_router"]["endpoints"][0]["api_base"] == (
        "http://gateway:9090/litellm_memory_proxy/team-1/{userId}/v1"
    )
    assert routers["embed_model_router"]["endpoints"][0]["api_base"] == (
        "http://gateway:9090/litellm_memory_proxy/team-1/{userId}/v1"
    )
    assert "accessToken" not in str(routers)


def test_upsert_memory_provider_binding_stores_embedding_identity(db: Session, monkeypatch: pytest.MonkeyPatch):
    user = create_random_user(db)
    chat_provider = _create_chat_provider(db, user.id)
    embedding_provider = _create_embedding_provider(db, user.id)
    _enable_mindmemos(monkeypatch)
    calls: list[dict] = []

    def fake_post(current_user, path, payload, *, scopes):
        calls.append({"current_user": current_user, "path": path, "payload": payload, "scopes": scopes})
        return {"code": "ok", "data": {"binding_id": "pb_test"}}

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    binding = memory_service.upsert_memory_provider_binding(
        db,
        user,
        MemoryProviderBindingUpdate(
            chat_provider_id=chat_provider.id,
            chat_model="qwen-plus",
            embedding_provider_id=embedding_provider.id,
        ),
    )

    assert binding.configured is True
    assert binding.binding_id == "pb_test"
    assert binding.embedding_model == "jina_ai/jina-embeddings-v4"
    assert binding.embedding_dim == 1024
    assert calls[0]["scopes"] == ["provider:write"]
    assert calls[0]["payload"]["scope"] == {"user_id": str(user.id)}
    chat_endpoint = calls[0]["payload"]["routers"]["chat_model_router"]["endpoints"][0]
    assert chat_endpoint["model"] == "openai/qwen-plus"
    assert chat_endpoint["timeout"] == 30
    assert chat_endpoint["num_retries"] == 1


def test_ensure_memory_provider_binding_recreates_missing_remote_binding(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    chat_provider = _create_chat_provider(db, user.id)
    embedding_provider = _create_embedding_provider(db, user.id)
    _enable_mindmemos(monkeypatch)

    memory_service.get_user_memory_config(db, user)
    stored = db.exec(select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == user.id)).one()
    stored.mindmemos_binding_id = "pb_missing"
    stored.mindmemos_chat_provider_id = chat_provider.id
    stored.mindmemos_chat_model = "qwen-plus"
    stored.mindmemos_embedding_provider_id = embedding_provider.id
    stored.mindmemos_embedding_model = "jina_ai/jina-embeddings-v4"
    stored.mindmemos_embedding_dim = 1024
    db.add(stored)
    db.commit()

    posts: list[dict] = []

    def fake_get(current_user, path, *, scopes):
        return {"code": "ok", "data": {"items": []}}

    def fake_post(current_user, path, payload, *, scopes):
        posts.append({"path": path, "payload": payload, "scopes": scopes})
        return {"code": "ok", "data": {"binding_id": "pb_recreated"}}

    monkeypatch.setattr(memory_service, "_mindmemos_get", fake_get)
    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    memory_service._ensure_mindmemos_provider_binding(db, user)

    db.refresh(stored)
    assert stored.mindmemos_binding_id == "pb_recreated"
    assert posts[0]["scopes"] == ["provider:write"]
    assert posts[0]["payload"]["scope"] == {"user_id": str(user.id)}
    assert len(posts[0]["payload"]["routers"]["chat_model_router"]["endpoints"]) == 1
    assert len(posts[0]["payload"]["routers"]["embed_model_router"]["endpoints"]) == 1


def test_ensure_memory_provider_binding_refreshes_stale_router(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    chat_provider = _create_chat_provider(db, user.id)
    embedding_provider = _create_embedding_provider(db, user.id)
    _enable_mindmemos(monkeypatch)

    memory_service.get_user_memory_config(db, user)
    stored = db.exec(select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == user.id)).one()
    stored.mindmemos_binding_id = "pb_stale"
    stored.mindmemos_chat_provider_id = chat_provider.id
    stored.mindmemos_chat_model = "qwen-plus"
    stored.mindmemos_embedding_provider_id = embedding_provider.id
    stored.mindmemos_embedding_model = "jina_ai/jina-embeddings-v4"
    stored.mindmemos_embedding_dim = 1024
    db.add(stored)
    db.commit()

    patches: list[dict] = []

    def fake_get(current_user, path, *, scopes):
        return {
            "code": "ok",
            "data": {
                "items": [
                    {
                        "binding_id": "pb_stale",
                        "routers": {
                            "chat_model_router": {"endpoints": [{"model": "qwen-plus"}]},
                            "embed_model_router": {"endpoints": [{"model": "jina_ai/jina-embeddings-v4", "dimensions": 1024}]},
                        },
                    }
                ]
            },
        }

    def fake_patch(current_user, path, payload, *, scopes):
        patches.append({"path": path, "payload": payload, "scopes": scopes})
        return {"code": "ok", "data": {"binding_id": "pb_stale"}}

    monkeypatch.setattr(memory_service, "_mindmemos_get", fake_get)
    monkeypatch.setattr(memory_service, "_mindmemos_patch", fake_patch)

    memory_service._ensure_mindmemos_provider_binding(db, user)

    assert patches[0]["scopes"] == ["provider:write"]
    chat_endpoint = patches[0]["payload"]["routers"]["chat_model_router"]["endpoints"][0]
    assert chat_endpoint["model"] == "openai/qwen-plus"


def test_ensure_memory_provider_binding_refreshes_changed_api_key(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    chat_provider = _create_chat_provider(db, user.id)
    embedding_provider = _create_embedding_provider(db, user.id)
    _enable_mindmemos(monkeypatch)

    memory_service.get_user_memory_config(db, user)
    stored = db.exec(select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == user.id)).one()
    stored.mindmemos_binding_id = "pb_api_key"
    stored.mindmemos_chat_provider_id = chat_provider.id
    stored.mindmemos_chat_model = "qwen-plus"
    stored.mindmemos_embedding_provider_id = embedding_provider.id
    stored.mindmemos_embedding_model = "jina_ai/jina-embeddings-v4"
    stored.mindmemos_embedding_dim = 1024
    db.add(stored)
    db.commit()

    patches: list[dict] = []

    def fake_get(current_user, path, *, scopes):
        expected = memory_service._memory_provider_routers(chat_provider, "qwen-plus", embedding_provider)  # noqa: SLF001
        stale = {
            router_name: {
                "endpoints": [
                    {
                        **endpoint,
                        "api_key": "old-key",
                    }
                    for endpoint in router["endpoints"]
                ]
            }
            for router_name, router in expected.items()
        }
        return {"code": "ok", "data": {"items": [{"binding_id": "pb_api_key", "routers": stale}]}}

    def fake_patch(current_user, path, payload, *, scopes):
        patches.append({"path": path, "payload": payload, "scopes": scopes})
        return {"code": "ok", "data": {"binding_id": "pb_api_key"}}

    monkeypatch.setattr(memory_service, "_mindmemos_get", fake_get)
    monkeypatch.setattr(memory_service, "_mindmemos_patch", fake_patch)

    memory_service._ensure_mindmemos_provider_binding(db, user)

    assert patches
    refreshed_chat_endpoint = patches[0]["payload"]["routers"]["chat_model_router"]["endpoints"][0]
    assert refreshed_chat_endpoint["api_key"] == "sk-chat"


def test_upsert_memory_provider_binding_allows_manual_chat_model_when_provider_model_list_empty(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    chat_provider = _create_chat_provider(db, user.id, model="")
    embedding_provider = _create_embedding_provider(db, user.id)
    _enable_mindmemos(monkeypatch)
    calls: list[dict] = []

    def fake_post(current_user, path, payload, *, scopes):
        calls.append({"path": path, "payload": payload, "scopes": scopes})
        return {"code": "ok", "data": {"binding_id": "pb_manual"}}

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    binding = memory_service.upsert_memory_provider_binding(
        db,
        user,
        MemoryProviderBindingUpdate(
            chat_provider_id=chat_provider.id,
            chat_model="custom-chat-model",
            embedding_provider_id=embedding_provider.id,
        ),
    )

    assert binding.configured is True
    chat_endpoint = calls[0]["payload"]["routers"]["chat_model_router"]["endpoints"][0]
    assert chat_endpoint["model"] == "openai/custom-chat-model"


def test_upsert_memory_provider_binding_rejects_embedding_identity_change(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    chat_provider = _create_chat_provider(db, user.id)
    first_embedding = _create_embedding_provider(db, user.id, model="jina-embeddings-v4", dim=1024)
    second_embedding = _create_embedding_provider(db, user.id, model="text-embedding-3-large", dim=3072)
    _enable_mindmemos(monkeypatch)
    monkeypatch.setattr(memory_service, "_mindmemos_post", lambda *args, **kwargs: {"code": "ok", "data": {"binding_id": "pb_test"}})
    monkeypatch.setattr(memory_service, "_mindmemos_patch", lambda *args, **kwargs: {"code": "ok", "data": {"binding_id": "pb_test"}})

    memory_service.upsert_memory_provider_binding(
        db,
        user,
        MemoryProviderBindingUpdate(
            chat_provider_id=chat_provider.id,
            chat_model="qwen-plus",
            embedding_provider_id=first_embedding.id,
        ),
    )

    with pytest.raises(HTTPException) as exc:
        memory_service.upsert_memory_provider_binding(
            db,
            user,
            MemoryProviderBindingUpdate(
                chat_provider_id=chat_provider.id,
                chat_model="qwen-plus",
                embedding_provider_id=second_embedding.id,
            ),
        )

    assert exc.value.status_code == 409
