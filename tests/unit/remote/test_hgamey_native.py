"""Keep the optional native build reproducible from the source shipped in the SDK."""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from llm4ad.gateway.examples import hgamey

NATIVE = Path(hgamey.__file__).parent / "native"


def test_native_snapshot_preserves_source_and_compile_time_inputs():
    """Every attributed source and Rust literal include must survive packaging."""
    source = NATIVE / "vendor/hgamey"
    manifest = json.loads((NATIVE / "source-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        data = (source / name).read_bytes()
        assert len(data) == expected["size"]
        assert hashlib.sha256(data).hexdigest() == expected["sha256"]
    for path in (source / "phonefarm/src").rglob("*.rs"):
        for relative in re.findall(r'include_(?:str|bytes)!\(\s*"([^"]+)"', path.read_text()):
            assert (path.parent / relative).is_file(), (path, relative)
    assert (source / "phonefarm/src/Cargo.lock").is_file()
    assert (source / "phonefarm/knobs/gray/build/build_layer.sh").is_file()
    assert (source / "refbench/build/build.sh").is_file()


def test_native_builder_help_is_available_without_installed_android_tools(tmp_path):
    """Source users can inspect the standalone builder before downloading tools."""
    result = subprocess.run(
        [sys.executable, str(NATIVE / "build.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--source" in result.stdout and "--signing-key" in result.stdout
    assert not list(tmp_path.iterdir())
