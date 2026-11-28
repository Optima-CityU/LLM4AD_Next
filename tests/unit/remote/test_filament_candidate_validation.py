"""Exercise the Filament source policy on both sides of remote dispatch."""

import pytest

from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
from llm4ad.gateway.examples.filament_fsr import evaluator as filament
from llm4ad.gateway.examples.filament_fsr.evaluator import Evaluator, PerformanceEvaluator
from llm4ad.gateway.examples.filament_fsr.source import SEED, SOURCE_NAME, reference_source, validate_candidate
from llm4ad.gateway.sdk import client, gateway
from llm4ad.gateway.sdk.journal import Journal
from llm4ad.orchestrator.island_ga import is_repairable_evaluation_result

pytestmark = pytest.mark.unit


@pytest.fixture
def invalid_shader(tmp_path):
    """Recreate the generated shader change that removed a repeated branch."""
    path = tmp_path / SOURCE_NAME
    original = (SEED / SOURCE_NAME).read_text(encoding="utf-8")
    modified = original.replace("#ifdef FILAMENT_FSR_DERINGING\n    AF3 min4=", "    AF3 min4=", 1)
    modified = modified.replace("#endif\n\n    AF2 pR=", "\n    AF2 pR=", 1)
    assert modified != original
    path.write_text(modified, encoding="utf-8")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("evaluator", [Evaluator, PerformanceEvaluator])
async def test_directive_failure_reaches_coder_without_remote_job(tmp_path, invalid_shader, monkeypatch, evaluator):
    """A mutation with missing directives returns a repairable result before packaging."""
    for name in ("LLM4AD_REMOTE_URL", "LLM4AD_REMOTE_TOKEN", "LLM4AD_REMOTE_TASK_ID"):
        monkeypatch.setenv(name, "unused")

    def unexpected_pack(*args):
        pytest.fail("An invalid shader reached remote packaging")

    monkeypatch.setattr(client, "pack_candidate", unexpected_pack)
    result = await client.evaluate_remote(
        RemoteEvaluatorConfig(gateway_id="gateway", contract=evaluator.contract.model_dump(mode="json")),
        EvalContext(project_root=str(tmp_path)),
    )

    assert not result.success
    assert is_repairable_evaluation_result(result)
    assert "Expected directives:" in result.error_message
    assert "Candidate directives:" in result.error_message


def test_gateway_rejects_invalid_shader_without_retry(tmp_path, invalid_shader, monkeypatch):
    """Reject the same mutation on the gateway before touching the device."""
    monkeypatch.setenv("FILAMENT_DEVICE", "unused")

    def unexpected_device(*args):
        pytest.fail("An invalid shader reached the device")

    monkeypatch.setattr(filament, "connect_device", unexpected_device)
    job = {
        "id": "job", "attempt_id": "attempt", "task_id": "task",
        "project_root": str(tmp_path), "timeout": 30, "managed_attempt": True, "lease_remaining": 60,
    }
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)

    outcome = gateway.execute(journal.path, "llm4ad.gateway.examples.filament_fsr.evaluator:Evaluator", job)

    assert not outcome["retryable"]
    assert outcome["report"]["metadata"]["failure_category"] == "candidate"
    assert "Expected directives:" in outcome["report"]["message"]


def test_body_rewrite_preserves_original_directives(tmp_path):
    """An edited body with all original branches still reaches the compiler."""
    prefix, body, suffix = reference_source()
    original = (SEED / SOURCE_NAME).read_text(encoding="utf-8")
    edited_body = body.replace("    highp AF2 pp", "    // Allowed body change\n    highp AF2 pp", 1)
    assert edited_body != body
    shader = tmp_path / SOURCE_NAME
    shader.write_text(original.replace(body, edited_body, 1), encoding="utf-8")

    assert validate_candidate(shader, (prefix, body, suffix)) == prefix + edited_body + suffix
