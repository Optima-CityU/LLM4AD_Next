import uuid

import pytest
from fastapi import HTTPException
from llm4ad.config.app import EmbeddingConfig
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.planner.base import (
    Algorithm,
    CodeArtifact,
    EvaluationResult,
    GenerationMetadata,
    InsightType,
)
from sqlmodel import Session

from app import models
from app.core.config import settings
from app.core.db import engine
from app.models import EmbeddingMode, EmbeddingProviderType, ProviderType
from app.schemas import embedding_provider as embedding_provider_schemas
from app.schemas.result_render import ResultRenderGenerateRequest, ResultRenderType
from app.services import embedding_provider_service, memory_service, user_default_model_service
from app.services.task_service.execution import _resolve_providers
from app.services.task_service.stats import generate_result_render
from tests.utils.user import create_random_user


@pytest.fixture()
def db():
    with Session(engine) as session:
        yield session


def _llm_provider(
    db: Session,
    user_id: uuid.UUID,
    *,
    name: str = "llm-provider",
    model_names: str = "gpt-4o",
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE,
) -> models.LLMProvider:
    provider = models.LLMProvider(
        name=name,
        type=provider_type,
        api_key="provider-key",
        base_url=f"https://{name}.example/v1",
        model=model_names,
        user_id=user_id,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _embedding_provider(
    db: Session,
    user_id: uuid.UUID,
    **kwargs,
) -> models.EmbeddingProvider:
    data = {
        "name": "embedding-provider",
        "type": EmbeddingProviderType.OPENAI_COMPATIBLE,
        "api_key": "embedding-key",
        "base_url": "https://embedding.example/v1",
        "model": "text-embedding-3-large",
        "dim": 3072,
        "timeout": 60.0,
        "embedding_func_max_async": 2,
        "mode": EmbeddingMode.SHARED,
        "user_id": user_id,
    }
    data.update(kwargs)
    provider = models.EmbeddingProvider(**data)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _project_and_task(db: Session, user_id: uuid.UUID) -> models.Task:
    project = models.Project(name="embedding-project", description="", user_id=user_id)
    db.add(project)
    db.commit()
    db.refresh(project)
    task = models.Task(
        name="embedding-task",
        project_id=project.id,
        input_args={
            "planner": {"provider": "mock"},
            "coder": {"provider": "mock"},
            "evaluator": {"provider": "mock"},
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_default_model_embedding_is_disabled_by_default(db: Session):
    user = create_random_user(db)

    defaults = user_default_model_service.get_user_default_model(db, user.id)

    assert defaults.embedding_enabled is False
    assert defaults.embedding_provider_id is None
    assert defaults.embedding_provider_name is None


def test_llm_provider_no_longer_contains_embedding_configuration(db: Session):
    user = create_random_user(db)

    provider = _llm_provider(db, user.id)

    assert not hasattr(provider, "embedding_model")
    assert not hasattr(provider, "embedding_dim")


def test_jina_embedding_provider_defaults_to_domestic_base_url(db: Session):
    user = create_random_user(db)

    provider = embedding_provider_service.create_embedding_provider(
        db,
        models.EmbeddingProviderBase(
            name="Jina",
            type=EmbeddingProviderType.JINA,
            api_key="jina-key",
        ),
        user.id,
    )

    assert provider.base_url == "https://api.jinaai.cn/v1"
    assert provider.mode == EmbeddingMode.SHARED
    assert provider.model
    assert provider.text_task == "text-matching"
    assert provider.code_task == "code.passage"


def test_jina_embedding_provider_persists_model_and_task_modes(db: Session):
    user = create_random_user(db)

    provider = embedding_provider_service.create_embedding_provider(
        db,
        models.EmbeddingProviderBase(
            name="Jina",
            type=EmbeddingProviderType.JINA,
            api_key="jina-key",
            model="jina-embeddings-v4-custom",
            text_task="retrieval.query",
            code_task="code.passage",
        ),
        user.id,
    )

    assert provider.model == "jina-embeddings-v4-custom"
    assert provider.text_task == "retrieval.query"
    assert provider.code_task == "code.passage"


@pytest.mark.parametrize(
    ("base_url", "api_key", "expected_detail"),
    [
        ("gfdasrew", "jina-key", "API 地址"),
        ("https://api.jinaai.cn/v1", "", "API Key"),
    ],
)
def test_jina_embedding_provider_rejects_invalid_memory_connection_fields(
    base_url: str,
    api_key: str,
    expected_detail: str,
):
    """Reject Jina settings that MindMemOS cannot use for embedding calls."""
    with pytest.raises(HTTPException) as exc_info:
        embedding_provider_service._normalize_provider_data(  # noqa: SLF001
            {
                "name": "invalid-jina",
                "type": EmbeddingProviderType.JINA,
                "base_url": base_url,
                "api_key": api_key,
            }
        )

    assert exc_info.value.status_code == 400
    assert expected_detail in exc_info.value.detail


def test_mindmemos_rejects_persisted_invalid_jina_embedding_provider():
    """Do not pass a legacy invalid Jina configuration to MindMemOS."""
    provider = models.EmbeddingProvider(
        name="legacy-invalid-jina",
        user_id=uuid.uuid4(),
        type=EmbeddingProviderType.JINA,
        base_url="gfdasrew",
        api_key="",
        model="jina-embeddings-v4",
        dim=2048,
    )

    with pytest.raises(HTTPException, match="Embedding 配置无效"):
        memory_service._validate_mindmemos_embedding_provider(provider)  # noqa: SLF001


@pytest.mark.asyncio
async def test_embedding_provider_connectivity_tests_selected_task(monkeypatch):
    calls: list[tuple[str, EmbeddingConfig]] = []

    class FakeEmbeddingClient:
        def __init__(self, config: EmbeddingConfig) -> None:
            self.config = config

        async def run_single(self, text: str, task_type: str | None = None) -> list[float]:
            calls.append((task_type or "text", self.config))
            return [0.1, 0.2, 0.3]

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr(embedding_provider_service, "EmbeddingClient", FakeEmbeddingClient)

    response = await embedding_provider_service.test_embedding_provider_connectivity(
        embedding_provider_schemas.EmbeddingProviderTestRequest(
            task_type="code",
            name="split",
            type=EmbeddingProviderType.LOCAL,
            mode=EmbeddingMode.SPLIT,
            dim=3,
            timeout=5,
            embedding_func_max_async=1,
            text_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
            text_base_url="https://text.example/v1",
            text_api_key="text-key",
            text_model="text-model",
            text_task="text-matching",
            code_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
            code_base_url="https://code.example/v1",
            code_api_key="code-key",
            code_model="code-model",
            code_task="code.passage",
        )
    )

    assert response.success is True
    assert response.dimension == 3
    assert calls[0][0] == "code"
    config = calls[0][1]
    assert config.type == "local"
    assert config.text_config is not None
    assert config.text_config.model == "text-model"
    assert config.code_config is not None
    assert config.code_config.model == "code-model"


@pytest.mark.asyncio
async def test_stored_embedding_provider_connectivity_uses_existing_secrets_with_overrides(
    db: Session,
    monkeypatch,
):
    user = create_random_user(db)
    provider = _embedding_provider(
        db,
        user.id,
        type=EmbeddingProviderType.LOCAL,
        mode=EmbeddingMode.SPLIT,
        text_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        text_base_url="https://stored-text.example/v1",
        text_api_key="stored-text-key",
        text_model="stored-text-model",
        code_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        code_base_url="https://stored-code.example/v1",
        code_api_key="stored-code-key",
        code_model="stored-code-model",
    )
    configs: list[EmbeddingConfig] = []

    class FakeEmbeddingClient:
        def __init__(self, config: EmbeddingConfig) -> None:
            configs.append(config)

        async def run_single(self, text: str, task_type: str | None = None) -> list[float]:
            assert task_type == "text"
            return [0.1, 0.2]

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr(embedding_provider_service, "EmbeddingClient", FakeEmbeddingClient)

    response = await embedding_provider_service.test_stored_embedding_provider_connectivity(
        db,
        provider.id,
        user,
        embedding_provider_schemas.EmbeddingProviderTestByIdRequest(
            task_type="text",
            text_model="override-text-model",
            text_api_key="sk-***",
            code_api_key="override-code-key",
        ),
    )

    assert response.success is True
    assert configs[0].text_config is not None
    assert configs[0].text_config.api_key == "stored-text-key"
    assert configs[0].text_config.model == "override-text-model"
    assert configs[0].code_config is not None
    assert configs[0].code_config.api_key == "override-code-key"


def test_resolve_providers_omits_embedding_when_disabled(db: Session):
    user = create_random_user(db)
    args = {
        "planner": {"provider": "mock"},
        "coder": {"provider": "mock"},
        "evaluator": {"provider": "mock"},
    }

    resolved = _resolve_providers(db, args, user)

    assert "embedding" not in resolved


def test_resolve_providers_removes_stale_embedding_when_disabled(db: Session):
    """Discard invalid UI remnants when the user has embedding disabled."""
    user = create_random_user(db)
    args = {
        "planner": {"provider": "mock"},
        "coder": {"provider": "mock"},
        "evaluator": {"provider": "mock"},
        "embedding": {"type": "", "model": ""},
    }

    resolved = _resolve_providers(db, args, user)

    assert "embedding" not in resolved


def test_resolve_providers_builds_jina_embedding_config_from_single_provider(db: Session):
    user = create_random_user(db)
    provider = _embedding_provider(
        db,
        user.id,
        name="jina-provider",
        type=EmbeddingProviderType.JINA,
        api_key="jina-key",
        base_url="",
    )
    user_default_model_service.update_user_default_model(
        db,
        user.id,
        {
            "embedding_enabled": True,
            "embedding_provider_id": provider.id,
        },
    )
    args = {
        "planner": {"provider": "mock"},
        "coder": {"provider": "mock"},
        "evaluator": {"provider": "mock"},
    }

    resolved = _resolve_providers(db, args, user)

    assert resolved["embedding"]["type"] == "jina"
    assert resolved["embedding"]["api_key"] == "jina-key"
    assert resolved["embedding"]["base_url"] == "https://api.jinaai.cn/v1"
    assert resolved["embedding"]["model"] == provider.model
    assert resolved["embedding"]["dim"] == provider.dim
    assert resolved["embedding"]["text_task"] == provider.text_task
    assert resolved["embedding"]["code_task"] == provider.code_task
    assert "text_config" not in resolved["embedding"] or resolved["embedding"]["text_config"] is None
    assert "code_config" not in resolved["embedding"] or resolved["embedding"]["code_config"] is None


def test_resolve_providers_builds_mock_embedding_config(db: Session):
    user = create_random_user(db)
    provider = _embedding_provider(
        db,
        user.id,
        name="mock-embedding",
        type=EmbeddingProviderType.MOCK,
        api_key="",
        base_url="",
        model="mock",
        dim=16,
    )
    user_default_model_service.update_user_default_model(
        db,
        user.id,
        {
            "embedding_enabled": True,
            "embedding_provider_id": provider.id,
        },
    )
    args = {
        "planner": {"provider": "mock"},
        "coder": {"provider": "mock"},
        "evaluator": {"provider": "mock"},
    }

    resolved = _resolve_providers(db, args, user)

    assert resolved["embedding"]["type"] == "mock"
    assert resolved["embedding"]["model"] == "mock"
    assert resolved["embedding"]["dim"] == 16


def test_jina_embedding_client_respects_base_url_and_task_modes():
    client = EmbeddingClient(
        EmbeddingConfig(
            type="jina",
            api_key="jina-key",
            base_url="https://api.jinaai.cn/v1",
            model="jina-embeddings-v4",
            text_task="retrieval.query",
            code_task="code.passage",
        ),
    )

    text_client, text_model, text_task, text_type = client._task_clients["text"]
    code_client, code_model, code_task, code_type = client._task_clients["code"]

    assert str(text_client.base_url) == "https://api.jinaai.cn/v1/"
    assert str(code_client.base_url) == "https://api.jinaai.cn/v1/"
    assert text_model == "jina-embeddings-v4"
    assert code_model == "jina-embeddings-v4"
    assert text_task == "retrieval.query"
    assert code_task == "code.passage"
    assert text_type == "jina"
    assert code_type == "jina"


def test_embedding_provider_rejects_semicolon_model_list(db: Session):
    user = create_random_user(db)

    with pytest.raises(HTTPException) as exc_info:
        embedding_provider_service.create_embedding_provider(
            db,
            models.EmbeddingProviderBase(
                name="bad-embedding",
                type=EmbeddingProviderType.OPENAI_COMPATIBLE,
                api_key="embedding-key",
                base_url="https://embedding.example/v1",
                model="embed-a;embed-b",
            ),
            user.id,
        )

    assert exc_info.value.status_code == 400
    assert "单个" in exc_info.value.detail


def test_embedding_provider_rejects_non_jina_shared_configuration(db: Session):
    user = create_random_user(db)

    with pytest.raises(HTTPException) as exc_info:
        embedding_provider_service.create_embedding_provider(
            db,
            models.EmbeddingProviderBase(
                name="bad-shared",
                type=EmbeddingProviderType.OPENAI_COMPATIBLE,
                api_key="shared-key",
                base_url="https://shared.example/v1",
                model="text-embedding-3-large",
                mode=EmbeddingMode.SHARED,
            ),
            user.id,
        )

    assert exc_info.value.status_code == 400
    assert "text/code" in exc_info.value.detail


def test_resolve_providers_builds_independent_local_embedding_configs(db: Session):
    user = create_random_user(db)
    provider = _embedding_provider(
        db,
        user.id,
        name="local-vllm",
        type=EmbeddingProviderType.LOCAL,
        mode=EmbeddingMode.SPLIT,
        model="",
        text_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        text_base_url="https://text-embedding.example/v1",
        text_api_key="text-key",
        text_auth_token="text-token",
        text_model="bge-text",
        text_task="text-matching",
        code_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        code_base_url="https://code-embedding.example/v1",
        code_api_key="code-key",
        code_auth_token="code-token",
        code_model="bge-code",
        code_task="code.passage",
        dim=2048,
        timeout=45.0,
        embedding_func_max_async=4,
        api_key="legacy-key",
        auth_token="legacy-token",
    )
    user_default_model_service.update_user_default_model(
        db,
        user.id,
        {
            "embedding_enabled": True,
            "embedding_provider_id": provider.id,
        },
    )
    args = {
        "planner": {"provider": "mock"},
        "coder": {"provider": "mock"},
        "evaluator": {"provider": "mock"},
    }

    resolved = _resolve_providers(db, args, user)

    assert resolved["embedding"]["type"] == "local"
    assert resolved["embedding"]["dim"] == 2048
    assert resolved["embedding"]["timeout"] == 45.0
    assert resolved["embedding"]["embedding_func_max_async"] == 4
    assert resolved["embedding"]["text_config"]["type"] == "openai_compatible"
    assert resolved["embedding"]["text_config"]["api_key"] == "text-key"
    assert resolved["embedding"]["text_config"]["auth_token"] == "text-token"
    assert resolved["embedding"]["text_config"]["base_url"] == "https://text-embedding.example/v1"
    assert resolved["embedding"]["text_config"]["model"] == "bge-text"
    assert resolved["embedding"]["text_config"]["timeout"] == 45.0
    assert resolved["embedding"]["text_config"]["task"] == "text-matching"
    assert resolved["embedding"]["code_config"]["type"] == "openai_compatible"
    assert resolved["embedding"]["code_config"]["api_key"] == "code-key"
    assert resolved["embedding"]["code_config"]["auth_token"] == "code-token"
    assert resolved["embedding"]["code_config"]["base_url"] == "https://code-embedding.example/v1"
    assert resolved["embedding"]["code_config"]["model"] == "bge-code"
    assert resolved["embedding"]["code_config"]["timeout"] == 45.0
    assert resolved["embedding"]["code_config"]["task"] == "code.passage"


def test_embedding_default_model_rejects_missing_provider(db: Session):
    user = create_random_user(db)

    with pytest.raises(HTTPException) as exc_info:
        user_default_model_service.update_user_default_model(
            db,
            user.id,
            {
                "embedding_enabled": True,
                "embedding_provider_id": uuid.uuid4(),
            },
        )

    assert exc_info.value.status_code == 404
    assert "embedding" in exc_info.value.detail


def test_deleting_bound_embedding_provider_clears_default_configuration(db: Session):
    user = create_random_user(db)
    provider = _embedding_provider(db, user.id)
    user_default_model_service.update_user_default_model(
        db,
        user.id,
        {
            "embedding_enabled": True,
            "embedding_provider_id": provider.id,
        },
    )

    embedding_provider_service.delete_embedding_provider(db, provider.id, user)

    defaults = user_default_model_service.get_user_default_model(db, user.id)
    assert defaults.embedding_enabled is False
    assert defaults.embedding_provider_id is None


def test_embedding_provider_rejects_incomplete_split_configuration(db: Session):
    user = create_random_user(db)

    with pytest.raises(HTTPException) as exc_info:
        embedding_provider_service.create_embedding_provider(
            db,
            models.EmbeddingProviderBase(
                name="bad-local",
                type=EmbeddingProviderType.OPENAI_COMPATIBLE,
                mode=EmbeddingMode.SPLIT,
                api_key="local-key",
                base_url="https://embedding.example/v1",
                text_model="bge-text",
                code_model="",
            ),
            user.id,
        )

    assert exc_info.value.status_code == 400
    assert "text/code" in exc_info.value.detail


def test_generate_trajectory_reports_embedding_disabled(db: Session):
    user = create_random_user(db)
    task = _project_and_task(db, user.id)

    response = generate_result_render(
        db,
        task.id,
        user,
        ResultRenderGenerateRequest(result_type=ResultRenderType.TRAJECTORY),
    )

    assert response.status == "failed"
    assert response.error_code == "embedding_disabled"


def test_generate_trajectory_backfills_missing_embeddings(
    db: Session,
    tmp_path,
    monkeypatch,
):
    user = create_random_user(db)
    task = _project_and_task(db, user.id)
    provider = _embedding_provider(
        db,
        user.id,
        name="mock-embedding",
        type=EmbeddingProviderType.MOCK,
        api_key="",
        base_url="",
        model="mock",
        dim=8,
    )
    user_default_model_service.update_user_default_model(
        db,
        user.id,
        {
            "embedding_enabled": True,
            "embedding_provider_id": provider.id,
        },
    )
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", f"{tmp_path}/")
    result_dir = tmp_path / f"code_user-{user.id}" / str(task.id) / "llm4ad" / "run"
    generated_dir = result_dir / "generated"
    embedding_dir = result_dir / "embedding"
    algorithm = Algorithm(
        insight_type=InsightType.INITIAL,
        description="Use a simple constructive heuristic.",
        code_artifacts=[
            CodeArtifact(
                file_path="solver.py",
                content="def solve():\n    return 1\n",
                content_mode="full",
            ),
        ],
        generation_meta=GenerationMetadata(
            operator="initial",
            llm_provider="mock",
            llm_model="mock",
            change_description="Create initial solver",
        ),
        evaluation=EvaluationResult(score=1.0, metrics={"score": 1.0}),
        generation=0,
        island_id=0,
    )
    algorithm.write(generated_dir, stage="evaluation", island_id=0, generation=0)

    def fake_generate(self, dark_mode=False):  # noqa: ARG001
        return {"embedding_files": len(list(embedding_dir.glob("*.json")))}

    monkeypatch.setattr(
        "llm4ad.frontend.visualization.VisualizationAPI.generate_evaluation_trace_echarts_config",
        fake_generate,
    )

    response = generate_result_render(
        db,
        task.id,
        user,
        ResultRenderGenerateRequest(result_type=ResultRenderType.TRAJECTORY, force=True),
    )

    assert response.status == "completed"
    assert response.data == {"embedding_files": 1}
