from pathlib import Path


def test_initial_extraction_prompt_requires_editable_document_blocks() -> None:
    prompt = (Path(__file__).parent / "PROMPT.md").read_text(encoding="utf-8")

    assert '"documents"' in prompt
    assert "/workspace/output/documents" in prompt
    assert '"cards"' not in prompt
    assert "四种" not in prompt
    assert "公式、参数、代码、示例、反例" in prompt
    assert "文档块" in prompt


def test_refinement_prompt_keeps_document_block_contract() -> None:
    prompt = (Path(__file__).parent / "REFINEMENT_PROMPT.md").read_text(encoding="utf-8")

    assert '"documents"' in prompt
    assert "/workspace/output/documents" in prompt
    assert '"cards"' not in prompt
