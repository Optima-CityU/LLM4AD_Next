import asyncio
import sys
from types import ModuleType

from llm4ad.infra.provider.base import GenerationResult

# These unit tests exercise pure report-summary helpers. Avoid importing the task
# service's storage-backed package when the test is run outside the full app stack.
if "app.services.task_service" not in sys.modules:
    task_service_stub = ModuleType("app.services.task_service")
    task_service_stub.get_task_with_auth = lambda *args, **kwargs: None
    sys.modules["app.services.task_service"] = task_service_stub

from app.services import report_service


def test_build_evolution_evidence_keeps_analysis_fields_and_drops_code():
    logs = [
        {
            "type": "generated",
            "timestamp": "2026-07-28T00:00:00Z",
            "data": {
                "id": "node-1",
                "generation": 7,
                "parent_ids": ["node-0"],
                "name": "Adaptive local search",
                "description": "Use an adaptive neighbourhood schedule.",
                "key_innovations": ["adaptive neighbourhood"],
                "evaluation": {"score": 9.5, "metrics": {"time": 1.2}},
                "generation_meta": {
                    "operator_name": "mutation",
                    "change_description": "Add stagnation detection.",
                },
                "code_artifacts": [{"file_path": "solve.py", "content": "very long code"}],
                "worktree": {"path": "/tmp/worktree"},
            },
        }
    ]

    evidence = report_service._build_evolution_evidence(logs)

    assert evidence == [
        {
            "node_id": "node-1",
            "generation": 7,
            "parent_ids": ["node-0"],
            "name": "Adaptive local search",
            "description": "Use an adaptive neighbourhood schedule.",
            "key_innovations": ["adaptive neighbourhood"],
            "score": 9.5,
            "metrics": {"time": 1.2},
            "operator": "mutation",
            "change_description": "Add stagnation detection.",
        }
    ]


def test_pack_summary_items_respects_budget_without_losing_order():
    items = ["node-a:" + "a" * 45, "node-b:" + "b" * 45, "node-c:" + "c" * 45]

    chunks = report_service._pack_summary_items(items, max_chars=60)

    assert chunks == items


async def test_hierarchical_summary_recursively_merges_chunks_and_reports_progress():
    class FakeProvider:
        def __init__(self):
            self.calls: list[dict] = []

        async def generate(self, prompt: str, **kwargs):
            self.calls.append({"prompt": prompt, **kwargs})
            return GenerationResult(text=f"summary-{len(self.calls)}")

    provider = FakeProvider()
    progress: list[dict] = []

    result = await report_service._summarize_evolution_evidence(
        provider,
        [
            {"node_id": "a", "description": "a" * 80},
            {"node_id": "b", "description": "b" * 80},
            {"node_id": "c", "description": "c" * 80},
        ],
        on_progress=progress.append,
        max_chars=100,
        max_tokens=321,
    )

    assert result == "summary-4"
    assert len(provider.calls) == 4
    assert all(call["max_tokens"] == 321 for call in provider.calls)
    assert [event["stage"] for event in progress] == ["summarize"] * 3 + ["merge"]
    assert [event["round"] for event in progress] == [1, 1, 1, 2]


async def test_hierarchical_summary_runs_at_most_five_requests_in_parallel():
    class BlockingProvider:
        def __init__(self):
            self.active = 0
            self.peak_active = 0
            self.started = 0
            self.first_started = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, _prompt: str, **_kwargs):
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.started += 1
            self.first_started.set()
            try:
                await self.release.wait()
                return GenerationResult(text=f"summary-{self.started}")
            finally:
                self.active -= 1

    provider = BlockingProvider()
    summary_task = asyncio.create_task(
        report_service._summarize_evolution_evidence(
            provider,
            [
                {"node_id": f"node-{index}", "description": "x" * 80}
                for index in range(6)
            ],
            on_progress=lambda _event: None,
            max_chars=80,
        )
    )

    await provider.first_started.wait()
    await asyncio.sleep(0.01)
    try:
        assert provider.started == 5
        assert provider.peak_active == 5
    finally:
        provider.release.set()
        await summary_task
