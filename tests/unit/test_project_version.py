import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.project_version import check_versions, sync_versions


def _write_versioned_files(root: Path, version: str) -> None:
    (root / "src" / "llm4ad").mkdir(parents=True)
    (root / "src" / "frontend").mkdir(parents=True)
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (root / "src" / "llm4ad" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n', encoding="utf-8"
    )
    (root / "src" / "frontend" / "package.json").write_text(
        '{\n  "name": "frontend",\n  "version": "0.0.0"\n}\n',
        encoding="utf-8",
    )


def test_sync_versions_updates_all_derived_manifests(tmp_path: Path) -> None:
    """The sync command copies VERSION into every managed manifest."""
    _write_versioned_files(tmp_path, "1.2.3")

    sync_versions(tmp_path)

    assert check_versions(tmp_path) == []
    assert 'version = "1.2.3"' in (tmp_path / "pyproject.toml").read_text()
    assert '__version__ = "1.2.3"' in (
        tmp_path / "src" / "llm4ad" / "__init__.py"
    ).read_text()
    assert '"version": "1.2.3"' in (
        tmp_path / "src" / "frontend" / "package.json"
    ).read_text()


def test_check_versions_reports_a_derived_version_mismatch(tmp_path: Path) -> None:
    """The check command identifies a manifest that drifted from VERSION."""
    _write_versioned_files(tmp_path, "1.2.3")
    sync_versions(tmp_path)
    (tmp_path / "src" / "frontend" / "package.json").write_text(
        '{\n  "name": "frontend",\n  "version": "1.2.4"\n}\n',
        encoding="utf-8",
    )

    errors = check_versions(tmp_path)

    assert errors == [
        'src/frontend/package.json version "1.2.4" does not match VERSION "1.2.3"'
    ]


def test_develop_version_skips_release_manifest_synchronization(tmp_path: Path) -> None:
    """A development checkout displays develop without claiming a release version."""
    _write_versioned_files(tmp_path, "develop")

    assert check_versions(tmp_path) == []
    with pytest.raises(ValueError, match="cannot synchronize release manifests"):
        sync_versions(tmp_path)
