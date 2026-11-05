"""Credential-free candidate archives and safe gateway extraction."""

import io
import stat
import zipfile
from pathlib import Path, PurePosixPath

_EXCLUDED = {
    ".git",
    ".llm4ad",
    ".remote-evaluation",
    "__pycache__",
    "node_modules",
    "data",
    "datasets",
    "logs",
    "checkpoints",
    "config.yaml",
    "config.yml",
    "settings.yaml",
    ".app_config.json",
}
_SOURCE_EXTENSIONS = {
    ".py",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cu",
    ".rs",
    ".go",
    ".java",
    ".jl",
    ".m",
    ".f",
    ".f90",
    ".f95",
    ".js",
    ".ts",
    ".r",
    ".sh",
    ".cs",
}
_BUILD_FILES = {"Makefile", "CMakeLists.txt", "Cargo.toml", "Cargo.lock", "requirements.txt", "pyproject.toml"}


def pack_candidate(root: str, paths: list[str] | None = None) -> bytes:
    """Archive candidate source without runtime config, data or credentials."""
    directory = Path(root).resolve()
    if not directory.is_dir():
        raise ValueError("A candidate worktree is required for remote evaluation")
    if paths and any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in paths):
        raise ValueError("Candidate paths must be relative to the worktree")
    candidates = {path for pattern in paths for path in directory.glob(pattern)} if paths else directory.rglob("*")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(candidates):
            relative = path.relative_to(directory)
            if any(part in _EXCLUDED or part.startswith(".env") for part in relative.parts):
                continue
            if not path.resolve().is_relative_to(directory):
                continue
            if not paths and path.suffix.lower() not in _SOURCE_EXTENSIONS and path.name not in _BUILD_FILES:
                continue
            if path.is_file() and not path.is_symlink():
                entry = zipfile.ZipInfo(relative.as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, path.read_bytes())
        if not archive.namelist():
            raise ValueError("No candidate sources found; configure evaluator.candidate_paths if needed")
    return output.getvalue()


def extract_candidate(payload: bytes, destination: Path) -> None:
    """Extract regular files while refusing traversal and symbolic links."""
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in entry.filename
                or ":" in entry.filename
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError("Unsafe candidate archive path")
            target = (destination / entry.filename).resolve()
            target.relative_to(destination.resolve())
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
