"""Tests for embedding client configuration."""

from types import SimpleNamespace

import pytest

from llm4ad.config.app import AppConfig, EmbeddingConfig, TaskSpecificConfig
from llm4ad.llm4ad import LLM4AD
from llm4ad.orchestrator import base as orchestrator_base_module
from llm4ad.orchestrator import embedding_client as embedding_client_module
from llm4ad.orchestrator.base import BaseOrchestrator, EvolutionCheckpoint, EvolutionResult
from llm4ad.orchestrator.embedding_client import EmbeddingClient


class _FakeAsyncOpenAI:
    instances: list["_FakeAsyncOpenAI"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.instances.append(self)


def _patch_async_openai(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(embedding_client_module, "AsyncOpenAI", _FakeAsyncOpenAI)
    return _FakeAsyncOpenAI


def test_embedding_config_defaults_timeout_to_sixty_seconds():
    config = EmbeddingConfig()

    assert config.timeout == 60.0


def test_embedding_client_passes_timeout_to_standard_openai_client(monkeypatch):
    fake_openai = _patch_async_openai(monkeypatch)

    EmbeddingClient(
        EmbeddingConfig(
            type="openai_compatible",
            api_key="test-key",
            base_url="http://embedding.example/v1",
            model="embedding-model",
            timeout=45.0,
        )
    )

    assert len(fake_openai.instances) == 1
    assert fake_openai.instances[0].kwargs["timeout"] == 45.0


def test_local_embedding_clients_use_task_timeout_or_global_default(monkeypatch):
    fake_openai = _patch_async_openai(monkeypatch)

    EmbeddingClient(
        EmbeddingConfig(
            type="local",
            timeout=60.0,
            text_config=TaskSpecificConfig(
                api_key="text-key",
                base_url="http://text.example/v1",
                model="text-model",
                timeout=12.0,
            ),
            code_config=TaskSpecificConfig(
                api_key="code-key",
                base_url="http://code.example/v1",
                model="code-model",
            ),
        )
    )

    assert len(fake_openai.instances) == 2
    assert fake_openai.instances[0].kwargs["timeout"] == 12.0
    assert fake_openai.instances[1].kwargs["timeout"] == 60.0


def test_llm4ad_initializes_embedding_client_from_app_config(monkeypatch):
    initialized_configs = []

    class _FakeEmbeddingClient:
        def __init__(self, config):
            initialized_configs.append(config)

    monkeypatch.setattr("llm4ad.llm4ad.EmbeddingClient", _FakeEmbeddingClient)

    llm4ad = object.__new__(LLM4AD)
    llm4ad.config = AppConfig(
        embedding=EmbeddingConfig(
            type="openai_compatible",
            api_key="embedding-key",
            base_url="http://embedding.example/v1",
            model="embedding-model",
        ),
    )
    llm4ad._embedding_client = None

    llm4ad._initialize_embedding_client()

    assert len(initialized_configs) == 1
    assert initialized_configs[0].model == "embedding-model"
    assert isinstance(llm4ad._embedding_client, _FakeEmbeddingClient)


class _ConcreteOrchestrator(BaseOrchestrator):
    async def run(self) -> EvolutionResult:
        raise NotImplementedError

    async def step(self) -> tuple[bool, object | None]:
        raise NotImplementedError

    async def initialize_population(self) -> list[object]:
        raise NotImplementedError

    async def evolve_generation(self, parent_population: list[object]) -> list[object]:
        raise NotImplementedError

    async def pause(self) -> None:
        raise NotImplementedError

    async def resume(self) -> None:
        raise NotImplementedError

    async def save_checkpoint(self, path: str | None = None) -> str:
        raise NotImplementedError

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        raise NotImplementedError

    def get_status(self) -> dict[str, object]:
        return {}


@pytest.mark.asyncio
async def test_orchestrator_logs_embedding_background_task_failures(monkeypatch, tmp_path):
    async def failing_save_algorithm_embeddings(_client, _algorithm, _embedding_dir):
        raise RuntimeError("embedding boom")

    warnings = []
    monkeypatch.setattr(
        orchestrator_base_module,
        "save_algorithm_embeddings",
        failing_save_algorithm_embeddings,
    )
    monkeypatch.setattr(
        orchestrator_base_module.logger,
        "warning",
        lambda message, *args, **_kwargs: warnings.append(message.format(*args)),
    )

    orchestrator = _ConcreteOrchestrator(
        planner=None,
        coder=None,
        dispatcher=None,
        monitor=None,
        config=SimpleNamespace(checkpoint_interval=0, early_stop_patience=1),
        state_tracker=SimpleNamespace(embedding_dir=tmp_path),
        embedding_client=object(),
    )

    orchestrator._schedule_embedding_save(
        SimpleNamespace(id="algo-1", island_id=0, generation=1),
    )
    await orchestrator._finish_embedding_tasks()

    assert any("embedding boom" in warning for warning in warnings)


def test_early_stop_reads_canonical_global_best_score_history() -> None:
    """Improving Island-GA history must not be mistaken for a flat zero series."""
    orchestrator = _ConcreteOrchestrator(
        planner=None,
        coder=None,
        dispatcher=None,
        monitor=None,
        config=SimpleNamespace(
            checkpoint_interval=0,
            early_stop_patience=3,
            early_stop_threshold=0.01,
        ),
        state_tracker=SimpleNamespace(),
    )
    orchestrator.history = [
        {"global_best_score": 1.0},
        {"global_best_score": 1.1},
        {"global_best_score": 1.2},
    ]

    assert orchestrator.check_early_stop() == (False, "")
