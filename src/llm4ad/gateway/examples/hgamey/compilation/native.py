"""Build the frozen candidate into the native Android Vulkan layer."""

import subprocess
import sys
from pathlib import Path


def build_game_layer(config: dict, source: Path, output: Path, kind: str) -> Path:
    """Build an exact frozen shader through the external resize adapter.

    Args:
        config: Operator-owned NDK, phonefarm and exact-shader builder paths.
        source: Frozen baseline or candidate compute shader.
        output: New directory owned by this accepted attempt.
        kind: External builder identity, either seed or candidate.

    Returns:
        The built ELF library, ready for the executor's read-only mount.
    """
    command = [
        sys.executable,
        config["builder_path"],
        "--source",
        str(source),
        "--ndk",
        config["ndk_path"],
        "--phonefarm",
        config["phonefarm"],
        "--phonefarm-root",
        config["phonefarm_root"],
        "--out",
        str(output),
        "--kind",
        kind,
        "--mode",
        "resize",
    ]
    try:
        with output.with_suffix(".log").open("w", encoding="utf-8") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)
        layer = output / "libVkLayer_refknobs.so"
        with layer.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise ValueError("Builder did not produce an ELF library")
        return layer
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Game {kind} layer build failed; inspect {output.with_suffix('.log')}: {error}") from error
