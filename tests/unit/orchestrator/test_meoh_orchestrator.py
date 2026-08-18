"""Unit tests for the MEoH orchestrator."""
import asyncio
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm4ad.coder.base import BaseCoder, GenerateResult, GenerateStatus
from llm4ad.config.schema import CustomEvaluatorConfig, DatasetConfig
from llm4ad.evaluator import EvaluationDispatcher
from llm4ad.evaluator.base import BaseEvaluator, EvaluationResult, Metric, MetricType
from llm4ad.infra.provider.base import BaseProvider, GenerationResult
from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.infra.state import EvolutionState, StateTracker
from llm4ad.infra.version_control.base import WorktreeInfo
from llm4ad.orchestrator.base import BaseOrchestrator
from llm4ad.orchestrator.meoh import MEoHConfig, MEoHOrchestrator
from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.meoh_evolution import MEoHEvolutionPlanner


class DummyEvaluator(BaseEvaluator):
    """Evaluator stub for orchestrator tests."""

    counter = 0

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def metrics(self) -> list[Metric]:
        return [
            Metric(name="distance", type=MetricType.MINIMIZE),
            Metric(name="candidate_runtime_ms", type=MetricType.MINIMIZE),
        ]

    async def evaluate(self, cfg):  # type: ignore[override]
        DummyEvaluator.counter += 1
        return EvaluationResult(
            score=float(DummyEvaluator.counter),
            metrics={
                "distance": float(DummyEvaluator.counter),
                "candidate_runtime_ms": float(DummyEvaluator.counter + 1),
            },
            success=True,
            duration_ms=5.0,
        )


class DummyProvider(BaseProvider):
    """Provider stub."""

    def __init__(self):
        super().__init__({"model": "mock"})
        self._counter = 0

    async def generate(self, prompt: str, schema=None, **kwargs):  # type: ignore[override]
        self._counter += 1
        suffix = str(self._counter)
        parsed = None
        if schema:
            payload = {"name": f"Algo{suffix}", "description": f"desc {suffix}"}
            schema_fields = getattr(schema, "model_fields", {})
            if "code" in schema_fields:
                payload["code"] = f"def solve():\n    return {suffix}\n"
            parsed = schema(**payload)
        return GenerationResult(text=f"return {suffix}", total_tokens=3, parsed=parsed)

    async def generate_stream(self, prompt: str, **kwargs):  # pragma: no cover
        yield prompt

    async def chat(self, messages, schema=None, **kwargs):  # pragma: no cover
        return GenerationResult(text="chat")

    async def chat_stream(self, messages, **kwargs):  # pragma: no cover
        yield "chat"

    async def count_tokens(self, text: str) -> int:
        return len(text)

    def get_model_info(self):
        return {"model": self.model}


class DummyCoder(BaseCoder):
    """Coder stub."""

    @property
    def name(self) -> str:
        return "dummy"

    async def generate(self, prompt: str, context: dict, working_dir: str, **kwargs):
        solve_path = Path(working_dir) / "solve.py"
        content = solve_path.read_text(encoding="utf-8")
        solve_path.write_text(content.replace("return 0", "return 1"), encoding="utf-8")
        return GenerateResult(
            status=GenerateStatus.SUCCESS,
            working_dir=working_dir,
            generated_files=["solve.py"],
            main_file="solve.py",
            token_used=1,
        )


class DummyVersionControl:
    """Version control stub."""

    def __init__(self) -> None:
        self._counter = 0

    def create_worktree(self, name: str, base_commit=None, base_branch=None):
        path = Path.cwd() / ".pytest_meoh"
        path.mkdir(exist_ok=True)
        (path / "solve.py").write_text("# EVOLVE START\nreturn 0\n# EVOLVE END\n", encoding="utf-8")
        worktree = WorktreeInfo(
            name=name,
            path=str(path),
            branch="b",
            commit_hash="c",
            created_at=0.0,
            last_used_at=0.0,
        )
        return MagicMock(success=True, data={"worktree": worktree})

    def commit_changes(self, worktree, message, author=None):
        return MagicMock(success=True, message="ok")

    def get_changed_files(self, commit_hash: str):
        self._counter += 1
        return [{"file_path": "solve.py", "content": f"def solve(): return {self._counter}"}]

    def list_worktrees(self):
        return []


def _algorithm(identifier: str, score: float = 1.0) -> Algorithm:
    algorithm = Algorithm(
        id=identifier,
        insight_type=InsightType.INITIAL,
        name=identifier,
        description=identifier,
    )
    algorithm.set_evaluation_result(
        score=score,
        metrics={"distance": score, "candidate_runtime_ms": score + 1.0},
    )
    algorithm.add_code_artifact("solve.py", f"def solve(): return '{identifier}'")
    return algorithm


def _create_orchestrator(tmp_path: Path) -> MEoHOrchestrator:
    repo = MagicMock()
    repo.evolvable_blocks = [
        EvolveBlock(
            file_path="solve.py",
            absolute_path=tmp_path / "solve.py",
            line_start=1,
            line_end=3,
            comment_style="#",
            block_name="solver",
            original_content="return 0",
            context_before="# EVOLVE START\n",
            context_after="\n# EVOLVE END",
            language="python",
        )
    ]
    dispatcher = EvaluationDispatcher(
        CustomEvaluatorConfig(
            module=f"{__file__}:DummyEvaluator",
            dataset=DatasetConfig(mode="files", files=[str(tmp_path / "data.json")]),
            parallel=False,
        )
    )
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")

    version_control = DummyVersionControl()
    memory = MagicMock()
    memory.extractor = None
    memory.config = {"auto_extraction": {"enabled": False}}
    planner = MEoHEvolutionPlanner(
        provider=DummyProvider(),
        coder=DummyCoder(config=MagicMock(type="claude_code", timeout=1, max_retries=0)),
        memory=memory,
        config={"selection_num": 2, "max_tokens": 32},
        analyzed_repository=repo,
        version_control=version_control,
        state_tracker=StateTracker(),
    )

    async def noop_build(**kwargs):
        return None

    planner.build = noop_build  # type: ignore[method-assign]
    orchestrator = BaseOrchestrator.create(
        "meoh",
        planner=planner,
        coder=planner.coder,
        dispatcher=dispatcher,
        monitor=MagicMock(log_generation=lambda stats: None),
        version_control=version_control,
        config=MEoHConfig(
            type="meoh",
            planner_type="meoh_evolution",
            objective_metrics=["distance", "candidate_runtime_ms"],
            population_size=2,
            selection_num=2,
            max_generations=2,
            max_sample_nums=20,
            num_samplers=2,
            code_generation_mode="direct_code",
        ),
        state_tracker=StateTracker(),
    )
    assert isinstance(orchestrator, MEoHOrchestrator)
    return orchestrator


def test_meoh_orchestrator_registration_and_step(monkeypatch):
    """MEoH orchestrator should register and complete one survival cycle."""
    monkeypatch.setattr("llm4ad.orchestrator.meoh_population.compute_codebleu_similarity", lambda _a, _b: 0.1)
    monkeypatch.setattr("llm4ad.orchestrator.meoh_population.get_non_dominated_indices", lambda matrix: [0])
    monkeypatch.setattr("llm4ad.orchestrator.meoh_population.get_domination_relation", lambda _a, _b: 0)
    tmp_path = Path("tests/.tmp") / f"meoh_orchestrator_{uuid.uuid4().hex[:8]}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    orchestrator = _create_orchestrator(tmp_path)
    asyncio.run(orchestrator.initialize_population())
    orchestrator.state = EvolutionState.RUNNING
    should_continue, best = asyncio.run(orchestrator.step())

    assert should_continue is True
    assert best is not None
    assert orchestrator.current_generation >= 1


def test_meoh_orchestrator_uses_batch_evaluate_in_init_and_step(monkeypatch):
    """Initialization and step should both route through _batch_evaluate."""
    monkeypatch.setattr("llm4ad.orchestrator.meoh_population.compute_codebleu_similarity", lambda _a, _b: 0.1)
    monkeypatch.setattr("llm4ad.orchestrator.meoh_population.get_non_dominated_indices", lambda matrix: [0])
    monkeypatch.setattr("llm4ad.orchestrator.meoh_population.get_domination_relation", lambda _a, _b: 0)
    tmp_path = Path("tests/.tmp") / f"meoh_batch_eval_{uuid.uuid4().hex[:8]}"
    tmp_path.mkdir(parents=True, exist_ok=True)

    orchestrator = _create_orchestrator(tmp_path)
    original_batch_evaluate = orchestrator._batch_evaluate
    batch_sizes: list[int] = []

    async def tracking_batch_evaluate(algorithms):
        batch_sizes.append(len(algorithms))
        return await original_batch_evaluate(algorithms)

    orchestrator._batch_evaluate = tracking_batch_evaluate  # type: ignore[method-assign]

    asyncio.run(orchestrator.initialize_population())
    orchestrator.state = EvolutionState.RUNNING
    asyncio.run(orchestrator.step())

    assert len(batch_sizes) >= 2
    assert batch_sizes[0] > 0
    assert batch_sizes[1] > 0


def test_meoh_orchestrator_memory_extraction_toggle(monkeypatch):
    """Survival hook should respect the auto-extraction enabled flag."""
    tmp_path = Path("tests/.tmp") / f"meoh_memory_{uuid.uuid4().hex[:8]}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    orchestrator = _create_orchestrator(tmp_path)

    class FakeExtractor:
        def __init__(self):
            self.reset_calls = 0
            self.good_calls = 0
            self.bad_calls = 0

        def reset_generation(self):
            self.reset_calls += 1

        async def extract_from_good(self, *args, **kwargs):
            self.good_calls += 1
            return "good-card"

        async def extract_from_bad(self, *args, **kwargs):
            self.bad_calls += 1
            return None

        async def extract_from_failure(self, *args, **kwargs):
            return None

    class FakeMemory:
        def __init__(self, enabled: bool):
            self.config = {"auto_extraction": {"enabled": enabled}}
            self.extractor = FakeExtractor()
            self.cards = []

        async def add_card(self, card):
            self.cards.append(card)

    valid = _algorithm("valid", 1.0)
    valid.generation = 1
    orchestrator.meoh_population.population = [valid]
    orchestrator.meoh_population.elitist_archive = [valid]
    orchestrator.meoh_population.generation = 1
    orchestrator.meoh_population.last_hv = 0.42

    disabled_memory = FakeMemory(enabled=False)
    orchestrator.planner.memory = disabled_memory
    asyncio.run(orchestrator._on_survival_triggered())
    assert disabled_memory.extractor.reset_calls == 0
    assert disabled_memory.cards == []

    enabled_memory = FakeMemory(enabled=True)
    orchestrator.planner.memory = enabled_memory
    asyncio.run(orchestrator._on_survival_triggered())

    assert enabled_memory.extractor.reset_calls == 1
    assert enabled_memory.extractor.good_calls == 1
    assert enabled_memory.cards == ["good-card"]
    assert orchestrator.history[-1]["hv"] == pytest.approx(0.42)
