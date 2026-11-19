"""Exercise shader context, block mutation and generated filenames without model calls."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm4ad.coder.custom_naive_coder import CustomNaiveCoder
from llm4ad.coder.opencode import OpenCode
from llm4ad.config.coder import CustomCoderConfig, OpenCodeConfig
from llm4ad.infra.provider.base import GenerationResult

SHADER_EXTENSIONS = ("comp", "glsl", "vert", "frag")
ORIGINAL_BLOCK = "vec4 sample_pixel(vec2 uv) { return vec4(uv, 0.0, 1.0); }"
REPLACEMENT = "vec4 sample_pixel(vec2 uv) { return vec4(uv * 0.5, 0.0, 1.0); }"
PREFIX = "#version 450\nlayout(local_size_x = 8, local_size_y = 8) in;\n// EVOLVE_START sample\n"
SUFFIX = "\n// EVOLVE_END\nvoid main() { sample_pixel(vec2(0.0)); }\n"
SOURCE = PREFIX + ORIGINAL_BLOCK + SUFFIX


@pytest.fixture
def shader_coder():
    """Create a custom coder with deterministic shader responses."""
    provider = MagicMock(model="shader-test")
    provider.generate = AsyncMock(return_value=GenerationResult(text=f"```glsl\n{REPLACEMENT}\n```"))
    return CustomNaiveCoder(CustomCoderConfig(), provider)


@pytest.mark.parametrize("extension", SHADER_EXTENSIONS)
def test_shader_context_and_evolve_file_collection(tmp_path, shader_coder, extension):
    """Include shader contracts and their EVOLVE blocks in custom coder context."""
    relative = f"shaders/kernel.{extension}"
    target = tmp_path / relative
    target.parent.mkdir()
    target.write_text(SOURCE)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / f"private.{extension}").write_text(SOURCE)

    assert shader_coder._collect_evolve_file_map(tmp_path) == {relative: SOURCE}
    context = shader_coder._collect_project_context(str(tmp_path))
    assert f"## {relative}\n```glsl\n{SOURCE}" in context
    assert "private" not in context
    masked = shader_coder._collect_project_context(str(tmp_path), mask_evolve_blocks=True)
    assert ORIGINAL_BLOCK not in masked
    assert "#version 450" in masked
    assert "void main()" in masked


@pytest.mark.parametrize("extension", SHADER_EXTENSIONS)
@pytest.mark.parametrize("response_format", ["named", "unnamed", "raw", "complete_file"])
async def test_shader_mutation_infers_language_and_preserves_fixed_code(
    tmp_path, shader_coder, extension, response_format
):
    """Discover baseline shaders and mutate only the EVOLVE body as GLSL."""
    filename = f"shader.{extension}"
    (tmp_path / filename).write_text(SOURCE)
    response = {
        "named": f"```glsl:{filename}\n{REPLACEMENT}\n```",
        "unnamed": f"```glsl\n{REPLACEMENT}\n```",
        "raw": REPLACEMENT,
        "complete_file": f"```glsl:{filename}\n{PREFIX}{REPLACEMENT}{SUFFIX}\n```",
    }[response_format]
    shader_coder.provider.generate.return_value = GenerationResult(text=response)

    result = await shader_coder.generate("Improve sampling", {}, str(tmp_path))

    assert result.is_success, result.error_message
    assert result.generated_files == [filename]
    assert result.main_file == filename
    assert result.metadata["total_evolve_blocks"] == 1
    assert (tmp_path / filename).read_text() == PREFIX + REPLACEMENT + SUFFIX
    request = shader_coder.provider.generate.await_args.args[0]
    assert f"```glsl:{filename}" in request
    assert "```glsl\n#version 450" in request
    assert "```python" not in request
    assert not list(tmp_path.rglob("*.py"))


@pytest.mark.parametrize(
    ("config_extension", "context", "filename"),
    [
        ("py", {"language": "glsl"}, "implementation.glsl"),
        ("py", {"language": "comp"}, "implementation.comp"),
        ("py", {"language": "vert"}, "implementation.vert"),
        ("py", {"language": "frag"}, "implementation.frag"),
        ("py", {"language": "glsl", "main_file": "shader.comp"}, "shader.comp"),
        ("py", {"file_name": "shaders/shader.comp"}, "shaders/shader.comp"),
        ("comp", {}, "implementation.comp"),
        ("comp", {"language": "glsl"}, "implementation.comp"),
    ],
)
@pytest.mark.parametrize("fenced", [False, True])
async def test_initial_shader_output_uses_language_and_filename_hints(
    tmp_path, config_extension, context, filename, fenced
):
    """Unnamed shader output keeps the requested stage and avoids Python validation."""
    provider = MagicMock(model="shader-test")
    response = f"```glsl\n{SOURCE}\n```" if fenced else SOURCE
    provider.generate = AsyncMock(return_value=GenerationResult(text=response))
    coder = CustomNaiveCoder(CustomCoderConfig(default_extension=config_extension), provider)

    result = await coder.generate("Implement sampling", context, str(tmp_path))

    assert result.is_success, result.error_message
    assert result.generated_files == [filename]
    assert result.main_file == filename
    assert (tmp_path / filename).read_text() == SOURCE.strip()
    request = provider.generate.await_args.args[0]
    assert "in glsl:" in request
    assert f"```glsl:{filename}" in request
    assert not list(tmp_path.rglob("*.py"))


async def test_raw_shader_parent_keeps_filename_hint(tmp_path, shader_coder):
    """A string parent uses the shader filename supplied by the caller."""
    result = await shader_coder.generate(
        "Improve sampling",
        {"parent_code": SOURCE, "language": "glsl", "file_name": "shader.comp"},
        str(tmp_path),
    )

    assert result.is_success, result.error_message
    assert result.generated_files == ["shader.comp"]
    assert (tmp_path / "shader.comp").read_text() == PREFIX + REPLACEMENT + SUFFIX


@pytest.mark.parametrize("extension", SHADER_EXTENSIONS)
def test_opencode_lists_and_detects_shader_changes(tmp_path, extension):
    """OpenCode context and change detection recognize existing and new shaders."""
    coder = OpenCode(OpenCodeConfig())
    filename = f"shader.{extension}"
    shader = tmp_path / filename
    shader.write_text(SOURCE)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / f"ignored.{extension}").write_text(SOURCE)
    before = coder._snapshot_files(tmp_path)
    prompt = coder._enrich_prompt("Improve sampling", tmp_path)
    assert prompt.count(f"  - {filename}") == 2
    assert "ignored" not in prompt
    assert coder._detect_changes(tmp_path, before) == []

    before[filename] -= 1
    (tmp_path / "helper.glsl").write_text("const float SCALE = 0.5;\n")
    (tmp_path / "shader.spv").write_bytes(b"untrusted binary")
    assert coder._detect_changes(tmp_path, before) == sorted([filename, "helper.glsl"])
