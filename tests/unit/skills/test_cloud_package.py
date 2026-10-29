"""Exercise the standalone cloud packaging Skill against real evaluator runs."""

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[3] / "skills" / "llm4ad-cloud-package"


@pytest.fixture(scope="module")
def package_helper():
    """Load the shipped helper for platform-selection contract tests."""
    spec = importlib.util.spec_from_file_location("cloud_package_helper", SKILL / "scripts/validate_and_pack.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


@pytest.mark.parametrize("host,key", [("Linux", "linux"), ("Darwin", "darwin"), ("Windows", "windows")])
def test_build_selects_the_actual_local_platform(package_helper, host: str, key: str):
    """Choose explicit commands without confusing macOS with Linux."""
    commands = {"linux": "linux-build", "darwin": "mac-build", "windows": "win-build"}
    assert package_helper.select_build_script(SimpleNamespace(script=commands), host) == commands[key]


def test_missing_platform_build_is_not_silently_skipped(package_helper):
    """Do not report a usable build when only another OS was configured."""
    with pytest.raises(ValueError, match="no build for windows"):
        package_helper.select_build_script(SimpleNamespace(script={"linux": "make"}), "Windows")


def test_native_host_paths_are_separate_from_portable_config_paths(package_helper, tmp_path: Path):
    """Windows-native config separators must not pass local checks then fail import."""
    for value in ("C:\\Work\\algorithm", "algorithm\\solver.py", "../outside"):
        with pytest.raises(ValueError):
            package_helper.contained_path(tmp_path, value)
    assert package_helper.contained_path(tmp_path, "algorithm/solver.py") == tmp_path / "algorithm/solver.py"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create an isolated copy of the complete portable example."""
    destination = tmp_path / "knapsack"
    shutil.copytree(
        SKILL / "assets" / "knapsack",
        destination,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return destination


def run_pack(project: Path) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the delivered CLI against an isolated task.

    Args:
        project: Source task directory.

    Returns:
        Completed validation command and requested archive path.
    """
    archive = project.parent / "delivery.zip"
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "validate_and_pack.py"), str(project), "--output", str(archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, archive


def test_valid_package_contains_verified_snapshot_and_matching_report(project: Path):
    """Verify actual sample results, archive bytes, and environment exclusions."""
    (project / ".env").write_text("LLM_API_KEY=not-for-packaging", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "local.txt").write_text("local environment", encoding="utf-8")
    (project / "runs").mkdir()
    (project / "runs" / "old.json").write_text("{}", encoding="utf-8")
    result, archive = run_pack(project)
    assert result.returncode == 0, result.stderr
    report = json.loads(archive.with_suffix(".validation.json").read_text())
    assert report["status"] == "passed"
    assert [sample["score"] for sample in report["samples"]] == [44, 79]
    assert report["empty_candidate_rejected"] is True
    assert report["project_tests"] == {"script": "test_evaluator.py", "exit_code": 0}
    assert report["full_evolution_run"] is False
    assert report["environment"]["llm4ad_next"]
    with zipfile.ZipFile(archive) as package:
        assert json.loads(package.read("knapsack/validation.json")) == report
        for relative, digest in report["files_sha256"].items():
            assert hashlib.sha256(package.read(f"knapsack/{relative}")).hexdigest() == digest
        assert all(name.startswith("knapsack/") for name in package.namelist())
        assert not any(".env" in name or ".venv" in name or "/runs/" in name for name in package.namelist())
    assert not (project / "validation.json").exists()


def test_fixed_seed_evaluator_cannot_pass_candidate_validation(project: Path):
    """Reject a real evaluation that ignores the candidate passed by the platform."""
    evaluator = project / "knapsack_evaluator.py"
    source = evaluator.read_text(encoding="utf-8")
    evaluator.write_text(
        source.replace(
            'Path(cfg.project_root) / "solver.py"',
            'Path(__file__).parent / "algorithm" / "solver.py"',
        ),
        encoding="utf-8",
    )
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "accepted an empty candidate" in result.stderr
    assert not archive.exists()


@pytest.mark.parametrize("section,field", [("coder", "context_max_tokens"), ("repo_analyzer", "context_lines_before")])
def test_context_size_must_be_chosen_explicitly(project: Path, section: str, field: str):
    """Do not silently fall back to small context defaults in delivered projects."""
    config_path = project / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config[section][field]
    config_path.write_text(yaml.safe_dump(config))
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert f"Explicitly size {section}.{field}" in result.stderr
    assert not archive.exists()


def test_unicode_spaces_crlf_and_bom_source_can_be_packaged(project: Path):
    """Exercise native-path arguments and Windows text conventions on real code."""
    renamed = project.with_name("算法 task with spaces")
    project.rename(renamed)
    candidate = renamed / "algorithm/solver.py"
    candidate.write_bytes(b"\xef\xbb\xbf" + candidate.read_bytes().replace(b"\n", b"\r\n"))
    (renamed / ".DS_Store").write_bytes(b"finder metadata")
    (renamed / "Thumbs.db").write_bytes(b"explorer metadata")
    result, archive = run_pack(renamed)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(archive) as package:
        assert all(name.startswith(renamed.name + "/") and "\\" not in name for name in package.namelist())
        assert not any(".DS_Store" in name or "Thumbs.db" in name for name in package.namelist())


def test_one_failed_instance_prevents_delivery(project: Path):
    """A good first instance must not hide a failed second instance in aggregation."""
    sample = project / "data" / "sample" / "instance_002.json"
    sample.write_text('{"capacity": -1, "items": []}', encoding="utf-8")
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "instance_002.json" in result.stderr
    assert not archive.exists()


@pytest.mark.parametrize(
    "dataset,expected",
    [
        ({"mode": "glob", "path": "data/sample", "pattern": "*.json"}, [44, 79]),
        ({"mode": "glob", "path": ".", "pattern": "data/sample/*.json"}, [44, 79]),
        ({"mode": "directory", "path": "data/sample", "recursive": False}, [44, 79]),
        (
            {
                "mode": "files",
                "files": ["data/sample/instance_002.json", "data/sample/instance_001.json", "data/sample/instance_002.json"],
            },
            [79, 44, 79],
        ),
    ],
)
def test_dataset_discovery_preserves_the_runtime_inputs(project: Path, dataset: dict, expected: list[int]):
    """Actually score all selected inputs, respecting glob bases and repeated entries."""
    config_path = project / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["evaluator"]["dataset"] = dataset
    config_path.write_text(yaml.safe_dump(config))
    result, archive = run_pack(project)
    assert result.returncode == 0, result.stderr
    report = json.loads(archive.with_suffix(".validation.json").read_text())
    assert [sample["score"] for sample in report["samples"]] == expected


def test_glob_cannot_silently_drop_directory_matches(project: Path):
    """Do not verify only the file subset of a different runtime dataset."""
    config_path = project / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["evaluator"]["dataset"] = {"mode": "glob", "pattern": "data/*"}
    config_path.write_text(yaml.safe_dump(config))
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "Dataset file is unavailable" in result.stderr
    assert not archive.exists()


def test_invalid_initial_python_syntax_prevents_delivery(project: Path):
    """A marked candidate still needs to compile before it can be delivered."""
    (project / "algorithm/solver.py").write_text("# EVOLVE_START\ndef solve(:\n# EVOLVE_END\n")
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "invalid syntax" in result.stderr
    assert not archive.exists()


def test_shipped_domain_checks_run_actual_candidate_variants(project: Path):
    """Run feasible, invalid, crashing and hanging candidates through the example evaluator."""
    result = subprocess.run([sys.executable, str(project / "test_evaluator.py")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "timeout checks" in result.stdout


@pytest.mark.parametrize("missing", [True, False])
def test_domain_checks_are_required_for_delivery(project: Path, missing: bool):
    """Passing the generic baseline cannot bypass missing or failing domain tests."""
    path = project / "test_evaluator.py"
    if missing:
        path.unlink()
    else:
        path.write_text("raise SystemExit('Known scoring case failed')\n")
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert ("Include test_evaluator.py" if missing else "Task-specific evaluator tests failed") in result.stderr
    assert not archive.exists()


@pytest.mark.parametrize("distribution", ["llm4ad", "llm4ad-next==1.1.0"])
def test_task_requirements_cannot_replace_the_platform(project: Path, distribution: str):
    """Keep the known platform-package collision out of deployable task inputs."""
    (project / "requirements.txt").write_text(distribution + "\n", encoding="utf-8")
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "task requirements must not reinstall" in result.stderr
    assert not archive.exists()


def test_unregistered_method_cannot_be_delivered(project: Path):
    """A methodology Skill alone does not make a method cloud-runnable."""
    path = project / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["evolution"]["type"] = "funsearch"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "funsearch" in result.stderr
    assert not archive.exists()


def test_missing_evolve_region_prevents_delivery(project: Path):
    """Ordinary runnable code must still expose the platform's evolution region."""
    path = project / "algorithm" / "solver.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace("EVOLVE_START", "START").replace("EVOLVE_END", "END"),
        encoding="utf-8",
    )
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "No EVOLVE block" in result.stderr
    assert not archive.exists()


def test_missing_initial_sampler_prevents_delivery(project: Path):
    """A working evaluator cannot compensate for an unusable search configuration."""
    path = project / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["planner"]["samplers"] = []
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    result, archive = run_pack(project)
    assert result.returncode != 0
    assert "must include init_sampler" in result.stderr
    assert not archive.exists()


def test_documented_methods_match_the_installed_platform():
    """Validate every published method snippet against real config and registries."""
    from llm4ad.config import AppConfig
    from llm4ad.orchestrator import BaseOrchestrator
    from llm4ad.planner import BasePlanner

    text = (SKILL / "references" / "algorithms.md").read_text(encoding="utf-8")
    methods = set()
    for snippet in re.findall(r"```yaml\n(.*?)\n```", text, re.DOTALL):
        config = yaml.safe_load(snippet)
        parsed = AppConfig.from_dict(config)
        BaseOrchestrator.get(parsed.evolution.type)
        BasePlanner.get(parsed.planner.type)
        assert parsed.planner.type == getattr(parsed.evolution, "planner_type", "llm_evolution")
        methods.add(parsed.evolution.type)
    mapping = AppConfig.model_json_schema()["properties"]["evolution"]["discriminator"]["mapping"]
    assert methods == set(mapping)
