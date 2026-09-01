from pathlib import Path

SKILL_ROOT = Path(__file__).parents[2] / "skills" / "document-knowledge-organizer"


def test_initial_extraction_prompt_requires_editable_document_blocks() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    prompt = (SKILL_ROOT / "references" / "organize-mode.md").read_text(encoding="utf-8")
    contract = (SKILL_ROOT / "references" / "contracts.md").read_text(encoding="utf-8")

    assert "name: document-knowledge-organizer" in skill
    assert '"documents"' in contract
    assert "documents/block-001.md" in contract
    assert '"cards"' not in contract
    assert "fixed character or token counts" in skill
    assert "formulas, parameters, code, tables, examples, counterexamples" in prompt
    assert "Markdown knowledge blocks" in prompt


def test_refinement_prompt_keeps_document_block_contract() -> None:
    prompt = (SKILL_ROOT / "references" / "refine-mode.md").read_text(encoding="utf-8")
    contract = (SKILL_ROOT / "references" / "contracts.md").read_text(encoding="utf-8")

    assert '"documents"' in contract
    assert "current blocks" in prompt
    assert "local edits" in prompt
    assert '"cards"' not in contract


def test_plan_contract_uses_flat_documents() -> None:
    prompt = (SKILL_ROOT / "references" / "plan-mode.md").read_text(encoding="utf-8")
    contract = (SKILL_ROOT / "references" / "contracts.md").read_text(encoding="utf-8")
    schema = (SKILL_ROOT / "references" / "plan.schema.json").read_text(encoding="utf-8")

    assert "flat document entries" in contract
    assert '"strategies"' in schema
    assert "main`" not in prompt
    assert "child`" not in prompt
