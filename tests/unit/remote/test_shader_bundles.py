"""Cover source-only transport for shader candidates using the existing bundle API."""

import io
import zipfile

import pytest

from llm4ad.remote.bundles import extract_candidate, pack_candidate


@pytest.mark.parametrize("paths", [None, ["shaders/*.comp", "shaders/*.glsl", "*.vert", "*.frag"]])
def test_shader_bundle_round_trip_excludes_private_and_binary_files(tmp_path, paths):
    """Transfer shader sources unchanged without private references or compiled binaries."""
    sources = {
        "shaders/shader.comp": "#version 450\n// EVOLVE_START\nvoid main() {}\n// EVOLVE_END\n",
        "shaders/helper.glsl": "vec4 sample_pixel() { return vec4(1.0); }\n",
        "fullscreen.vert": "#version 450\nvoid main() { gl_Position = vec4(0.0); }\n",
        "output.frag": "#version 450\nvoid main() {}\n",
    }
    for filename, content in sources.items():
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for filename in (
        "data/reference.comp", "datasets/private.glsl", ".git/private.comp",
        ".env.comp", "config.yaml", "shaders/shader.spv", "frame.png",
    ):
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private or compiled data")

    payload = pack_candidate(str(tmp_path), paths)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == sorted(sources)
    destination = tmp_path / "extracted"
    extract_candidate(payload, destination)
    assert {str(path.relative_to(destination)): path.read_text() for path in destination.rglob("*") if path.is_file()} == sources
