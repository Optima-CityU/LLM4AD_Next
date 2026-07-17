"""Synchronize and validate the repository release version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

VERSION_FILE = "VERSION"
DEVELOPMENT_VERSION = "develop"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TARGETS = {
    "pyproject.toml": re.compile(r'(?m)^version = "([^"]+)"$'),
    "src/llm4ad/__init__.py": re.compile(r'(?m)^__version__ = "([^"]+)"$'),
    "src/frontend/package.json": re.compile(r'(?m)^(\s*"version": )"([^"]+)"(,?)$'),
}


def read_version(root: Path) -> str:
    """Read and validate the authoritative release version."""
    version = (root / VERSION_FILE).read_text(encoding="utf-8").strip()
    if version != DEVELOPMENT_VERSION and not VERSION_RE.fullmatch(version):
        raise ValueError(f"VERSION must match develop or X.Y.Z, got {version!r}")
    return version


def _read_target_version(path: Path, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(path.read_text(encoding="utf-8"))
    if not match:
        return None
    return match.group(2 if path.name == "package.json" else 1)


def check_versions(root: Path) -> list[str]:
    """Return derived manifest versions that differ from VERSION."""
    version = read_version(root)
    if version == DEVELOPMENT_VERSION:
        return []
    errors: list[str] = []
    for relative_path, pattern in TARGETS.items():
        target_path = root / relative_path
        target_version = _read_target_version(target_path, pattern)
        if target_version is None:
            errors.append(f"{relative_path} does not contain a managed version")
        elif target_version != version:
            errors.append(
                f'{relative_path} version "{target_version}" does not match VERSION "{version}"'
            )
    return errors


def sync_versions(root: Path) -> None:
    """Write VERSION to every derived manifest."""
    version = read_version(root)
    if version == DEVELOPMENT_VERSION:
        raise ValueError("develop cannot synchronize release manifests")
    for relative_path, pattern in TARGETS.items():
        target_path = root / relative_path
        content = target_path.read_text(encoding="utf-8")
        if target_path.name == "package.json":
            updated, replacements = pattern.subn(
                lambda match: f'{match.group(1)}"{version}"{match.group(3)}', content, count=1
            )
        else:
            prefix = "version" if target_path.name == "pyproject.toml" else "__version__"
            updated, replacements = pattern.subn(
                f'{prefix} = "{version}"', content, count=1
            )
        if replacements != 1:
            raise ValueError(f"{relative_path} does not contain a managed version")
        target_path.write_text(updated, encoding="utf-8")


def main() -> int:
    """Run the version synchronization or validation command."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="verify derived versions")
    action.add_argument("--sync", action="store_true", help="update derived versions")
    parser.add_argument(
        "--expected", help="also require VERSION to equal this release version"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        version = read_version(root)
        if args.expected and version != args.expected:
            raise ValueError(
                f'VERSION "{version}" does not match release "{args.expected}"'
            )
        if args.sync:
            sync_versions(root)
        errors = check_versions(root)
    except (OSError, ValueError) as exc:
        print(f"Version check failed: {exc}")
        return 1

    if errors:
        print("Version check failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"Version check passed: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
