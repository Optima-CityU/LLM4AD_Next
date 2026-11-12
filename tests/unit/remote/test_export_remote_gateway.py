"""Regression coverage for the bounded remote SDK export."""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("export_remote_gateway", ROOT / "scripts/export_remote_gateway.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


@pytest.fixture
def source_tree(tmp_path):
    """Copy only exporter inputs into a checkout-shaped fixture without Git."""
    root = tmp_path / "source"
    paths = [
        "pyproject.toml",
        "scripts/export_remote_gateway.py",
        "src/llm4ad/evaluator/models.py",
        "src/llm4ad/evaluator/behavior.py",
        "src/llm4ad/evaluator/records.py",
        *(path.relative_to(ROOT).as_posix() for path in (ROOT / "src/llm4ad/remote").glob("*.py")),
        *(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src/llm4ad/gateway").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        ),
        *(f"examples/remote_evaluation/hgamey/compat/legacy/{name}" for name in exporter.ADAPTER_FILES),
    ]
    for name in paths:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    return root


@pytest.fixture
def exported(source_tree, tmp_path):
    """Export a clean lightweight runtime without container deployment files."""
    output = tmp_path / "gateway"
    manifest = exporter.export_gateway(output, source_root=source_tree)
    return output, manifest


def test_schema_and_legacy_import_equivalence():
    """Preserve the original JSON schemas, frozen metrics, defaults, and aliases."""
    from pydantic import ValidationError

    from llm4ad.evaluator import base, models
    from llm4ad.evaluator.behavior import BehaviorData, BehaviorVisualization

    expected = json.loads((Path(__file__).parent / "fixtures/evaluation_models.schema.json").read_text())
    for name in ("Metric", "EvaluationResult"):
        assert getattr(models, name).model_json_schema() == expected[name]
    for name in ("MetricType", "Metric", "EvaluationResult"):
        assert getattr(base, name) is getattr(models, name)
    assert [(metric.name, metric.value) for metric in models.MetricType] == [
        ("MINIMIZE", "minimize"),
        ("MAXIMIZE", "maximize"),
    ]
    metric = models.Metric(name="latency", type="minimize")
    with pytest.raises(ValidationError, match="frozen"):
        metric.weight = 2
    with pytest.raises(ValidationError):
        models.EvaluationResult()
    result = models.EvaluationResult(
        metrics={"latency": 3},
        behavior=BehaviorData(visualizations=[BehaviorVisualization(label="frame", data_base64="fixture")]),
    )
    assert models.EvaluationResult.model_validate_json(result.model_dump_json()) == result
    result.metadata["private"] = True
    result.evolution_feedback["hint"] = "fixture"
    fresh = models.EvaluationResult(metrics={})
    assert fresh.metadata == fresh.evolution_feedback == fresh.monitor_metrics == {}


def test_export_is_bounded_and_copies_sources_verbatim(source_tree, exported):
    """Export only known sources and minimal generated package namespaces."""
    output, manifest = exported
    remote_names = {path.name for path in (source_tree / "src/llm4ad/remote").glob("*.py")}
    expected = {
        *(f"sdk/llm4ad/remote/{name}" for name in remote_names),
        *(
            f"sdk/llm4ad/gateway/{path.relative_to(source_tree / 'src/llm4ad/gateway')}"
            for path in (source_tree / "src/llm4ad/gateway").rglob("*")
            if path.is_file()
        ),
        "sdk/llm4ad/evaluator/models.py",
        "sdk/llm4ad/evaluator/behavior.py",
        "sdk/llm4ad/evaluator/records.py",
        *exporter.GENERATED_FILES,
        *(f"adapter/{name}" for name in exporter.ADAPTER_FILES),
        "requirements.txt",
        "requirements-ui.txt",
    }
    assert set(manifest["files"]) == expected
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == {
        *expected,
        "source-manifest.json",
    }
    for name, entry in manifest["files"].items():
        if name not in exporter.GENERATED_FILES and name not in {"requirements.txt", "requirements-ui.txt"}:
            assert (output / name).read_bytes() == (source_tree / entry["source"]).read_bytes()
    assert "llm4ad.llm4ad" not in (output / "sdk/llm4ad/__init__.py").read_text()
    assert "dispatcher" not in (output / "sdk/llm4ad/evaluator/__init__.py").read_text()


def test_source_manifest_hashes_and_existing_dependency_constraints(source_tree, exported):
    """Hash every copied/generated artifact and reuse exactly four source constraints."""
    output, manifest = exported
    assert json.loads((output / "source-manifest.json").read_text()) == manifest
    assert manifest["format_version"] == 1
    assert manifest["git_revision"] is None
    assert manifest["dirty"] is None
    for name, entry in manifest["files"].items():
        assert entry["sha256"] == hashlib.sha256((output / name).read_bytes()).hexdigest()
        source = Path(entry["source"])
        assert not source.is_absolute() and ".." not in source.parts
        assert entry["source_sha256"] == hashlib.sha256((source_tree / source).read_bytes()).hexdigest()
    dependencies = tomllib.loads((source_tree / "pyproject.toml").read_text())["project"]["dependencies"]
    selected = (output / "requirements.txt").read_text().splitlines()
    assert selected == [
        next(item for item in dependencies if item.startswith(f"{name}>=")) for name in ("pydantic", "aiohttp", "loguru", "typer")
    ]
    project = tomllib.loads((source_tree / "pyproject.toml").read_text())["project"]
    assert (output / "requirements-ui.txt").read_text().splitlines() == [
        "-r requirements.txt", *project["optional-dependencies"]["gateway-ui"]
    ]


def test_repeated_export_updates_known_files_and_preserves_unrelated_files(source_tree, exported):
    """Keep identical bytes and mtimes stable without deleting unrelated operator data."""
    output, manifest = exported
    private = output / "settings.json"
    private.write_text('{"token": "PRIVATE_OPERATOR_TOKEN"}')
    reference = output / "inputs/frame.png"
    reference.parent.mkdir()
    reference.write_bytes(b"PRIVATE_REFERENCE_FRAME")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.rglob("*") if path.is_file()}
    assert exporter.export_gateway(output, source_root=source_tree) == manifest
    for path, value in before.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == value
    source = source_tree / "src/llm4ad/remote/client.py"
    source.write_text(source.read_text() + "\n# Fixture source update.\n")
    changed = exporter.export_gateway(output, source_root=source_tree)
    entry = changed["files"]["sdk/llm4ad/remote/client.py"]
    assert entry["sha256"] != manifest["files"]["sdk/llm4ad/remote/client.py"]["sha256"]
    assert (output / "sdk/llm4ad/remote/client.py").read_bytes() == source.read_bytes()
    assert private.read_bytes() == before[private][0]
    assert reference.read_bytes() == before[reference][0]
    assert "PRIVATE" not in (output / "source-manifest.json").read_text()


def test_optional_container_python_files_are_exported_without_runtime_data(source_tree, tmp_path):
    """Include supervisor, configuration, and unittest files while excluding private inputs."""
    container = source_tree / "examples/remote_evaluation/hgamey/compat/legacy/container"
    container.mkdir()
    for name in ("service.py", "configuration.py", "test_service.py"):
        (container / name).write_text('"""Fixture container module."""\n')
    for name in (
        ".env",
        "settings.json",
        "identity.json",
        "jobs.sqlite3",
        "reference.png",
        "Dockerfile",
    ):
        (container / name).write_text("PRIVATE")
    nested = container / "__pycache__"
    nested.mkdir()
    (nested / "private.py").write_text("PRIVATE")
    output = tmp_path / "gateway"
    manifest = exporter.export_gateway(output, source_root=source_tree)
    for name in ("service.py", "configuration.py", "test_service.py"):
        assert (output / name).read_bytes() == (container / name).read_bytes()
        assert manifest["files"][name]["source"] == f"examples/remote_evaluation/hgamey/compat/legacy/container/{name}"
    assert not any((output / name).exists() for name in (".env", "settings.json", "reference.png", "Dockerfile"))
    assert "PRIVATE" not in (output / "source-manifest.json").read_text()


def test_export_cli_uses_only_standard_library(source_tree, tmp_path):
    """Run the documented script API from another directory under isolated Python."""
    output = tmp_path / "gateway"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(source_tree / "scripts/export_remote_gateway.py"),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert "Exported" in result.stdout
    assert (output / "source-manifest.json").is_file()


def test_isolated_sdk_and_adapter_imports_without_heavy_dependencies(exported, tmp_path):
    """Import the exported gateway, client, CLI, and adapter while blocking the full core."""
    output, _ = exported
    code = """
import importlib
import importlib.abc
import json
import pathlib
import runpy
import sys

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "sdk"))
sys.path.insert(0, str(root / "adapter"))
blocked = {
    "nicegui", "fastapi", "uvicorn",
    "numpy", "scipy", "pandas", "sklearn", "xgboost", "torch", "tensorflow",
    "openai", "anthropic", "agentscope", "ray", "matplotlib", "PIL",
    "llm4ad.llm4ad", "llm4ad.config", "llm4ad.utils", "llm4ad.infra",
    "llm4ad.coder", "llm4ad.planner", "llm4ad.orchestrator",
    "llm4ad.evaluator.dispatcher", "llm4ad.evaluator.renderer",
}
class BlockHeavyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Forbidden import: " + fullname)
sys.meta_path.insert(0, BlockHeavyImports())

import llm4ad
assert pathlib.Path(llm4ad.__file__).is_relative_to(root / "sdk")
for name in ("contracts", "bundles", "journal", "gateway", "client", "cli"):
    importlib.import_module("llm4ad.remote." + name)
import evaluator
import hgamey_benchmark
from llm4ad.evaluator import base, models
from llm4ad.remote.contracts import EvaluationContract
assert base.EvaluationResult is models.EvaluationResult
assert evaluator.EvaluationResult is models.EvaluationResult
assert hgamey_benchmark.Metric is models.Metric
contract = EvaluationContract(evaluator_id="fixture", version="1", data_version="1", metrics=[models.Metric(name="latency")])
assert EvaluationContract.model_validate_json(contract.model_dump_json()) == contract
from llm4ad.gateway.cli import ADAPTERS
assert "hgamey" in ADAPTERS
assert (root / "sdk/llm4ad/gateway/examples/hgamey/deployment/compose.yaml").is_file()
expected = json.loads(pathlib.Path(sys.argv[2]).read_text())
assert {name: getattr(models, name).model_json_schema() for name in expected} == expected
sys.argv = ["llm4ad.remote", "--help"]
runpy.run_module("llm4ad.remote", run_name="__main__")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            code,
            str(output),
            str(Path(__file__).parent / "fixtures/evaluation_models.schema.json"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert "start" in result.stdout and "resolve" in result.stdout and "status" in result.stdout


def test_git_manifest_records_revision_and_dirty_flag(source_tree, tmp_path):
    """Record clean/dirty source provenance without embedding configuration or diffs."""

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=source_tree, text=True, capture_output=True, check=True, timeout=30
        ).stdout.strip()

    git("init")
    git("add", ".")
    git(
        "-c",
        "user.name=SDK fixture",
        "-c",
        "user.email=sdk@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    output = tmp_path / "gateway"
    clean = exporter.export_gateway(output, source_root=source_tree)
    assert clean["git_revision"] == git("rev-parse", "HEAD")
    assert clean["dirty"] is False
    (source_tree / ".env").write_text("PRIVATE_CREDENTIAL=fixture\n")
    dirty = exporter.export_gateway(output, source_root=source_tree)
    assert dirty["git_revision"] == clean["git_revision"]
    assert dirty["dirty"] is True
    assert "PRIVATE" not in (output / "source-manifest.json").read_text()


@pytest.mark.parametrize("source_link", [False, True])
def test_export_refuses_symlinks_without_writing_outside_known_tree(source_tree, tmp_path, source_link):
    """Reject linked inputs and outputs before overwriting unrelated files."""
    output = tmp_path / "gateway"
    private = tmp_path / "private"
    private.mkdir()
    (private / "client.py").write_text("PRIVATE")
    if source_link:
        source = source_tree / "src/llm4ad/remote/client.py"
        source.unlink()
        source.symlink_to(private / "client.py")
    else:
        output.mkdir()
        (output / "sdk").symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links|regular file"):
        exporter.export_gateway(output, source_root=source_tree)
    assert (private / "client.py").read_text() == "PRIVATE"
    assert list(private.iterdir()) == [private / "client.py"]


def test_missing_source_constraint_fails_before_writing(source_tree, tmp_path):
    """Refuse an unconstrained runtime dependency instead of inventing a version."""
    project = source_tree / "pyproject.toml"
    project.write_text(project.read_text().replace('"typer>=0.24.1",', ""))
    output = tmp_path / "gateway"
    with pytest.raises(ValueError, match="Missing source runtime constraints: typer"):
        exporter.export_gateway(output, source_root=source_tree)
    assert not output.exists()


def test_ui_sources_are_exported_without_importing_optional_dependencies(source_tree, tmp_path):
    """Copy dashboard modules verbatim even when their extra is not installed."""
    source = source_tree / "src/llm4ad/gateway/ui/fixture.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("import nicegui\n")
    output = tmp_path / "gateway"
    manifest = exporter.export_gateway(output, source_root=source_tree)
    name = "sdk/llm4ad/gateway/ui/fixture.py"
    assert (output / name).read_bytes() == source.read_bytes()
    assert manifest["files"][name]["source"] == source.relative_to(source_tree).as_posix()


def test_container_installation_preserves_environment_and_unrelated_files(source_tree, tmp_path):
    """Install only deployment templates explicitly requested by the operator."""
    container = source_tree / exporter.ADAPTER_ROOT / "container"
    container.mkdir(parents=True)
    for name in ("Dockerfile", "compose.yaml", "entrypoint.sh", "env.example"):
        (container / name).write_text(f"fixture-{name}\n")
    executor_root = tmp_path / "extracted"
    (executor_root / "vendor/hgamey").mkdir(parents=True)
    (executor_root / "executor.py").write_text("fixture executor")
    (executor_root / ".env").write_text("PRIVATE_OPERATOR_CONFIGURATION")
    output = executor_root / "gateway"
    exporter.export_gateway(output, source_root=source_tree)
    exporter.install_container(output)
    assert (executor_root / ".env").read_text() == "PRIVATE_OPERATOR_CONFIGURATION"
    assert (executor_root / ".env.example").read_text() == "fixture-env.example\n"
    assert (executor_root / "Dockerfile").read_text() == "fixture-Dockerfile\n"
    assert (executor_root / "executor.py").read_text() == "fixture executor"


def test_container_installation_validates_all_files_before_overwriting(tmp_path):
    """An incomplete bundle cannot leave a half-updated executor deployment."""
    (tmp_path / "vendor/hgamey").mkdir(parents=True)
    (tmp_path / "executor.py").write_text("executor")
    (tmp_path / "Dockerfile").write_text("ORIGINAL")
    output = tmp_path / "gateway"
    (output / "deployment").mkdir(parents=True)
    (output / "deployment/Dockerfile").write_text("REPLACEMENT")
    with pytest.raises(ValueError, match="Missing exported container deployment file"):
        exporter.install_container(output)
    assert (tmp_path / "Dockerfile").read_text() == "ORIGINAL"
