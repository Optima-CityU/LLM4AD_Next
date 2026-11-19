"""Verify shader discovery through the existing analyzer and marker inspection APIs."""

from pathlib import Path

import pytest

from llm4ad.config.app import RepoAnalyzerConfig
from llm4ad.infra.repo_analyzer import EvolveDetector, inspect_path


@pytest.mark.parametrize("extension", ["comp", "glsl", "vert", "frag"])
@pytest.mark.parametrize("typed_config", [False, True])
def test_shader_discovery_uses_glsl_language(tmp_path, extension, typed_config):
    """Both analyzer defaults discover nested shaders and retain their GLSL language."""
    relative = Path("shaders") / f"shader.{extension}"
    shader = tmp_path / relative
    shader.parent.mkdir()
    shader.write_text("#version 450\n// EVOLVE_START sample\nvec4 sample_pixel() { return vec4(1.0); }\n// EVOLVE_END\n")
    (tmp_path / "shader.spv").write_bytes(b"untrusted binary")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / f"ignored.{extension}").write_text(shader.read_text())
    config = RepoAnalyzerConfig().model_dump() if typed_config else {}

    analyzed = EvolveDetector(config).analyze(tmp_path)

    assert analyzed.files_analyzed == 1
    assert analyzed.files_with_blocks == 1
    assert analyzed.file_type_counts == {f".{extension}": 1}
    block = analyzed.evolvable_blocks[0]
    assert block.file_path == str(relative)
    assert block.language == "glsl"
    assert block.comment_style == "//"
    assert block.block_name == "sample"
    assert block.original_content == "vec4 sample_pixel() { return vec4(1.0); }"
    assert list(EvolveDetector.iter_source_files(tmp_path)) == [shader]
    report = inspect_path(tmp_path)
    assert len(report.files) == 1
    assert report.files[0].language == "glsl"


def test_explicit_analyzer_filters_still_control_shader_discovery(tmp_path):
    """Adding shader defaults preserves user include filters and language overrides."""
    shader = tmp_path / "shader.comp"
    shader.write_text("// EVOLVE_START\nvoid main() {}\n// EVOLVE_END\n")
    assert EvolveDetector({"include": ["*.py"]}).analyze(tmp_path).files_analyzed == 0
    result = EvolveDetector({"language_map": {".comp": "custom_glsl"}}).analyze(tmp_path)
    assert result.evolvable_blocks[0].language == "custom_glsl"
