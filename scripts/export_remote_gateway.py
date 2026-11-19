"""Export the lightweight gateway SDK and HGamey adapter without installing LLM4AD."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DEPENDENCIES = ("pydantic", "aiohttp", "loguru", "typer")
ADAPTER_ROOT = Path("examples/remote_evaluation/hgamey/compat/legacy")
ADAPTER_FILES = (
    "evaluator.py",
    "hgamey_benchmark.py",
    "game_ab.py",
    "game-ab.json",
    "game-check.json",
    "upstream/selfsr/selfsr_ab.py",
    "upstream/selfsr/evidence_transfer.py",
    "upstream/selfsr/mali_capture.py",
    "upstream/selfsr/mali_telemetry.py",
    "upstream/selfsr/frame_telemetry.py",
    "upstream/selfsr/power_telemetry.py",
    "upstream/selfsr/perfetto_capture.py",
    "upstream/selfsr/perfetto_analysis.py",
    "upstream/gfrc/gfrc_ab.py",
    "upstream/provenance.json",
    "upstream/requirements.txt",
    "assets/baseline.comp",
    "settings.json",
)
GENERATED_FILES = {
    "sdk/llm4ad/__init__.py": '"""Lightweight LLM4AD remote evaluation SDK."""\n',
    "sdk/llm4ad/evaluator/__init__.py": '"""Evaluation data models for the remote SDK."""\n',
    # Existing adapters retain the original import API without loading evaluator code.
    "sdk/llm4ad/evaluator/base.py": (
        '"""Compatibility aliases for the canonical evaluation models."""\n\n'
        "from llm4ad.evaluator.models import EvaluationResult, Metric, MetricType\n\n"
        '__all__ = ["EvaluationResult", "Metric", "MetricType"]\n'
    ),
}


def _digest(content: bytes) -> str:
    """Return the SHA-256 digest of exact source or exported bytes."""
    return hashlib.sha256(content).hexdigest()


def _requirements(root: Path) -> bytes:
    """Select the runtime requirements using the source package constraints."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = {}
    for requirement in project["project"]["dependencies"]:
        match = re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        if match and match.group().lower() in RUNTIME_DEPENDENCIES:
            constraints[match.group().lower()] = requirement
    missing = set(RUNTIME_DEPENDENCIES) - constraints.keys()
    if missing:
        raise ValueError(f"Missing source runtime constraints: {', '.join(sorted(missing))}")
    return ("\n".join(constraints[name] for name in RUNTIME_DEPENDENCIES) + "\n").encode()


def _ui_requirements(root: Path) -> bytes:
    """Read optional dashboard dependencies from the canonical package extra.

    Args:
        root: Checkout containing the source package metadata.

    Returns:
        Requirements including the lean runtime and the optional UI extra.
    """
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = project["project"].get("optional-dependencies", {}).get("gateway-ui")
    if not requirements:
        raise ValueError("Missing source optional dependencies: gateway-ui")
    return ("-r requirements.txt\n" + "\n".join(requirements) + "\n").encode()


def _git_state(root: Path) -> tuple[str | None, bool | None]:
    """Read revision and dirty status without recording Git configuration or diffs."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return revision, bool(status)


def export_gateway(output: Path, *, source_root: Path = SOURCE_ROOT) -> dict:
    """Write only known SDK files, preserving unrelated destination contents.

    Args:
        output: Gateway directory containing the SDK, adapter, and container scripts.
        source_root: LLM4AD checkout providing the canonical sources and constraints.

    Returns:
        Manifest with relative source paths, content hashes, revision, and dirty flag.
    """
    root = source_root.resolve()
    output = output.expanduser().absolute()
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    files: dict[str, tuple[bytes, str, bytes]] = {}
    exporter_source = "scripts/export_remote_gateway.py"
    exporter_bytes = (root / exporter_source).read_bytes()
    for name, content in GENERATED_FILES.items():
        if name == "sdk/llm4ad/__init__.py":
            content += f"\n__version__ = {version!r}\n"
        files[name] = (content.encode(), exporter_source, exporter_bytes)

    sources = {f"sdk/llm4ad/remote/{path.name}": path for path in sorted((root / "src/llm4ad/remote").glob("*.py"))}
    for path in sorted((root / "src/llm4ad/gateway").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            sources[f"sdk/llm4ad/gateway/{path.relative_to(root / 'src/llm4ad/gateway')}"] = path
    for name in ("models.py", "behavior.py", "records.py"):
        sources[f"sdk/llm4ad/evaluator/{name}"] = root / "src/llm4ad/evaluator" / name
    if (root / "LICENSE").is_file():
        sources["LICENSE"] = root / "LICENSE"
    for name in ADAPTER_FILES:
        sources[f"adapter/{name}"] = root / ADAPTER_ROOT / name
    for path in sorted((root / ADAPTER_ROOT / "container").glob("*.py")):
        sources[path.name] = path
    for name in ("Dockerfile", "compose.yaml", "entrypoint.sh", "env.example"):
        path = root / ADAPTER_ROOT / "container" / name
        if path.is_file():
            sources[f"deployment/{name}"] = path
    readme = root / ADAPTER_ROOT / "container/README.md"
    if readme.is_file():
        sources["README.md"] = readme
    for destination, path in sources.items():
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Export source must be a regular file inside the checkout: {path}")
        content = path.read_bytes()
        files[destination] = (content, path.relative_to(root).as_posix(), content)
    project_bytes = (root / "pyproject.toml").read_bytes()
    files["requirements.txt"] = (_requirements(root), "pyproject.toml", project_bytes)
    files["requirements-ui.txt"] = (_ui_requirements(root), "pyproject.toml", project_bytes)

    # Validate every destination before writing, including existing directory symlinks.
    destinations = [output / name for name in (*files, "source-manifest.json")]
    for path in destinations:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"Export destination must not contain symbolic links: {path}")
        if path.resolve().is_relative_to(root / "src") or path.resolve().is_relative_to(root / "examples"):
            raise ValueError("Export destination must not overwrite source packages or examples")

    manifest_files = {}
    for name, (content, source, source_content) in sorted(files.items()):
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != content:
            destination.write_bytes(content)
        manifest_files[name] = {
            "source": source,
            "sha256": _digest(content),
            "source_sha256": _digest(source_content),
        }

    revision, dirty = _git_state(root)
    manifest = {
        "format_version": 1,
        "git_revision": revision,
        "dirty": dirty,
        "files": manifest_files,
    }
    destination = output / "source-manifest.json"
    content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    if not destination.exists() or destination.read_bytes() != content:
        destination.write_bytes(content)
    return manifest


def install_container(output: Path) -> None:
    """Install exported deployment files into a matching extracted executor.

    Args:
        output: The executor's exported gateway directory.
    """
    root = output.resolve().parent
    if not (root / "executor.py").is_file() or not (root / "vendor/hgamey").is_dir():
        raise ValueError("Container installation requires the extracted executor repository as the output parent")
    mapping = {
        "Dockerfile": "Dockerfile",
        "compose.yaml": "compose.yaml",
        "entrypoint.sh": "entrypoint.sh",
        "env.example": ".env.example",
    }
    for name, target in mapping.items():
        destination = root / target
        if destination.is_symlink():
            raise ValueError(f"Container destination must not be a symlink: {target}")
        if not (output / "deployment" / name).is_file():
            raise ValueError(f"Missing exported container deployment file: {name}")
    for name, target in mapping.items():
        destination = root / target
        destination.write_bytes((output / "deployment" / name).read_bytes())


def main() -> int:
    """Export the gateway from this checkout using only Python's standard library."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="gateway output directory")
    parser.add_argument(
        "--install-container",
        action="store_true",
        help="Also install Dockerfile, Compose, entrypoint and .env.example into the extracted executor (never changes .env)",
    )
    args = parser.parse_args()
    try:
        manifest = export_gateway(args.output)
        if args.install_container:
            install_container(args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Gateway export failed: {error}\n")
    print(f"Exported {len(manifest['files'])} files to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
