"""Isolated contracts for mixed paper archives and atomic versioned uploads."""

from __future__ import annotations

import hashlib
import io
import stat
import struct
import tarfile
import zipfile

import pytest
from fastapi import HTTPException, UploadFile
from sqlmodel import Session, create_engine, select

from app import models
from app.services import paper_service

_FORMATS = ("zip", "tar", "tar.gz", "tgz")
_UNSAFE_PATHS = (
    "../outside.md",
    "nested/../../outside.md",
    "/absolute.md",
    "\\absolute.md",
    "C:\\outside.md",
    "C:relative.md",
    "\\\\server\\share\\outside.md",
    "main.md\x00ignored.md",
)


def _archive(suffix: str, members: list[tuple[str, bytes | None]]) -> bytes:
    """Build tiny archives, retaining malicious names instead of sanitizing them."""
    buffer = io.BytesIO()
    if suffix == "zip":
        substitutions = []
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                if content is None:
                    archive.writestr(name.rstrip("/") + "/", b"")
                    continue
                if "\x00" in name:
                    encoded = name.replace("\x00", "X")
                    substitutions.append((encoded.encode(), name.encode()))
                    name = encoded
                archive.writestr(name, content)
        payload = buffer.getvalue()
        for original, replacement in substitutions:
            # ZipInfo truncates NUL names on write, so patch both name headers.
            payload = payload.replace(original, replacement)
        return payload
    with tarfile.open(
        fileobj=buffer, mode="w:gz" if suffix in {"tar.gz", "tgz"} else "w", format=tarfile.PAX_FORMAT
    ) as archive:
        for name, content in members:
            entry = tarfile.TarInfo(name if "\x00" not in name else "placeholder.md")
            if "\x00" in name:
                entry.pax_headers = {"path": name}
            if content is None:
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            else:
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def _expand(files, *, require_primary_source=True):
    """Exercise the public upload expansion boundary without touching storage."""
    return paper_service._expand_upload_entries(files, require_primary_source=require_primary_source)


def _reject(files, *, status=400, require_primary_source=True):
    """Require a handled HTTP rejection instead of an archive-library exception."""
    with pytest.raises(HTTPException) as rejected:
        _expand(files, require_primary_source=require_primary_source)
    assert rejected.value.status_code == status


@pytest.mark.parametrize("suffix", _FORMATS)
def test_archive_expansion_keeps_outer_parent_nested_paths_and_unicode(suffix):
    """Keep archive members below the browser-selected archive's parent folder."""
    members = [
        ("main.tex", b"Paper source"),
        ("sections/方法.tex", "研究方法\r\n".encode()),
        ("figures/结果.png", b"image bytes"),
    ]
    expanded = _expand([(f"项目/paper.{suffix}", _archive(suffix, members))])
    assert dict(expanded) == {f"项目/{path}": content for path, content in members}


def test_mixed_archive_formats_and_loose_files_expand_as_one_batch():
    """Combine all supported containers without flattening or retaining archives."""
    files = [("paper/main.md", b"# Main paper")]
    expected = dict(files)
    for index, suffix in enumerate(_FORMATS):
        members = [(f"section-{index}.tex", f"Section {index}".encode())]
        files.append((f"project_context/group-{index}/upload.{suffix}", _archive(suffix, members)))
        expected[f"project_context/group-{index}/section-{index}.tex"] = members[0][1]
    assert dict(_expand(files)) == expected


def test_reference_archive_uses_outer_rules_parent_without_archive_basename():
    """Resolve rules.tar.gz members directly below project_context/rules."""
    payload = _archive(
        "tar.gz", [("requirements.txt", b"Follow the venue rules"), ("nested/limits.json", b'{"words": 500}')]
    )
    assert dict(_expand([("project_context/rules/rules.tar.gz", payload)], require_primary_source=False)) == {
        "project_context/rules/requirements.txt": b"Follow the venue rules",
        "project_context/rules/nested/limits.json": b'{"words": 500}',
    }


@pytest.mark.parametrize("suffix", ("tar", "tar.gz", "tgz"))
def test_tar_dot_root_and_directory_members_are_accepted(suffix):
    """Accept tar tools' conventional ./ root and explicit directory entries."""
    payload = _archive(
        suffix,
        [("./", None), ("./sections/", None), ("./main.typ", b"= Proposal"), ("./sections/方法.typ", b"Methods")],
    )
    assert dict(_expand([(f"project/paper.{suffix}", payload)])) == {
        "project/main.typ": b"= Proposal",
        "project/sections/方法.typ": b"Methods",
    }


@pytest.mark.parametrize("suffix", _FORMATS)
def test_archive_batch_repacks_a_deterministic_normalized_zip(suffix):
    """Normalize container formats, member order, manifest, and stored hash."""
    members = [("z-notes.md", b"Notes\r\n"), ("main.tex", b"Main source")]
    files = [(f"project/source.{suffix}", _archive(suffix, members)), ("a.txt", b"Loose context")]
    validated = paper_service.validate_paper_source_batch(files)
    repeated = paper_service.validate_paper_source_batch(list(reversed(files)))
    expected = {"project/z-notes.md": b"Notes\r\n", "project/main.tex": b"Main source", "a.txt": b"Loose context"}
    assert validated.kind == "source_bundle"
    assert validated.filename.endswith(".zip")
    assert validated.manifest == sorted(expected)
    assert validated.content_hash == hashlib.sha256(validated.data).hexdigest()
    assert repeated.data == validated.data
    with zipfile.ZipFile(io.BytesIO(validated.data)) as archive:
        assert archive.namelist() == validated.manifest
        assert {name: archive.read(name) for name in archive.namelist()} == expected
        assert archive.testzip() is None


@pytest.mark.parametrize("suffix", _FORMATS)
def test_markdown_only_archives_are_valid_primary_sources(suffix):
    """Accept Markdown papers without requiring a .tex or .typ member."""
    payload = _archive(suffix, [("README.md", b"# Paper\r\n"), ("notes/context.markdown", b"Context")])
    validated = paper_service.validate_paper_source_batch([(f"paper.{suffix}", payload)])
    assert validated.kind == "source_bundle"
    assert validated.manifest == ["README.md", "notes/context.markdown"]
    with zipfile.ZipFile(io.BytesIO(validated.data)) as archive:
        assert archive.read("README.md") == b"# Paper\r\n"


def test_single_root_markdown_preserves_original_bytes_and_kind():
    """Keep the existing direct Markdown upload representation unchanged."""
    raw = "# 论文\r\n\r\nOriginal whitespace.  \r\n".encode()
    validated = paper_service.validate_paper_source_batch([("paper.md", raw)])
    assert validated.kind == "markdown"
    assert validated.data == raw
    assert validated.manifest == ["paper.md"]
    assert validated.content_hash == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("suffix", _FORMATS)
def test_asset_archives_require_a_primary_source_only_for_initial_upload(suffix):
    """Allow context-only archives to merge into an existing workspace."""
    files = [(f"context/assets.{suffix}", _archive(suffix, [("rules.txt", b"Rules"), ("figure.png", b"image")]))]
    _reject(files)
    assert dict(_expand(files, require_primary_source=False)) == {
        "context/rules.txt": b"Rules",
        "context/figure.png": b"image",
    }


@pytest.mark.parametrize("suffix", _FORMATS)
def test_archive_desktop_metadata_is_skipped_before_file_validation(suffix):
    """Ignore desktop metadata anywhere inside a container, including nested archives."""
    ignored = [
        ".DS_Store",
        "nested/.DS_Store",
        "__MACOSX/._main.tex",
        "__MACOSX/nested/extra.exe",
        "._main.tex",
        "nested/._attachment",
        "Thumbs.db",
        "nested/desktop.ini",
        "__MACOSX/hidden.zip",
    ]
    members = [("main.tex", b"Paper")] + [(path, b"ignored bytes") for path in ignored]
    assert _expand([(f"paper.{suffix}", _archive(suffix, members))]) == [("main.tex", b"Paper")]


def test_desktop_metadata_containers_are_skipped_without_opening_them():
    """Ignore metadata in the outer batch even when a junk archive is corrupt."""
    files = [
        ("main.md", b"# Paper"),
        (".DS_Store", b"ignored"),
        ("nested/Thumbs.db", b"ignored"),
        ("nested/desktop.ini", b"ignored"),
        ("__MACOSX/invalid.tar.gz", b"not an archive"),
        ("nested/._invalid.zip", b"not an archive"),
    ]
    assert _expand(files) == [("main.md", b"# Paper")]


@pytest.mark.parametrize("suffix", _FORMATS)
@pytest.mark.parametrize("unsafe", _UNSAFE_PATHS)
def test_archive_members_reject_traversal_absolute_drive_unc_and_nul(suffix, unsafe):
    """Reject unsafe paths before joining them to an otherwise safe outer parent."""
    payload = _archive(suffix, [("main.tex", b"Valid primary source"), (unsafe, b"Unsafe member")])
    _reject([(f"context/source.{suffix}", payload)])


@pytest.mark.parametrize("unsafe", _UNSAFE_PATHS)
def test_outer_upload_paths_reject_traversal_absolute_drive_unc_and_nul(unsafe):
    """Apply the same ownership-safe path checks to browser-relative uploads."""
    _reject([("main.tex", b"Valid primary source"), (unsafe, b"Unsafe loose file")])
    _reject([(unsafe + "/paper.zip", _archive("zip", [("main.tex", b"Paper")]))])


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_zip_links_and_special_files_are_rejected(file_type):
    """Reject non-regular ZIP entries even when a valid paper is also present."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("main.tex", b"Paper")
        entry = zipfile.ZipInfo("unsafe.md")
        entry.create_system = 3
        entry.external_attr = (file_type | 0o777) << 16
        archive.writestr(entry, b"../outside")
    _reject([("paper.zip", buffer.getvalue())])


@pytest.mark.parametrize("suffix", ["tar", "tar.gz", "tgz"])
@pytest.mark.parametrize(
    "file_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE]
)
def test_tar_links_and_special_files_are_rejected(suffix, file_type):
    """Reject hard links, symbolic links, devices, and FIFOs in TAR inputs."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if suffix != "tar" else "w") as archive:
        primary = tarfile.TarInfo("main.tex")
        primary.size = 5
        archive.addfile(primary, io.BytesIO(b"Paper"))
        entry = tarfile.TarInfo("unsafe.md")
        entry.type = file_type
        entry.linkname = "../outside.md"
        archive.addfile(entry)
    _reject([(f"paper.{suffix}", buffer.getvalue())])


def test_encrypted_zip_is_rejected_as_an_http_validation_error():
    """Set real ZIP encryption flags without depending on an encryption executable."""
    payload = bytearray(_archive("zip", [("main.tex", b"Paper")]))
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = payload.index(signature) + offset
        flags = struct.unpack_from("<H", payload, position)[0]
        struct.pack_into("<H", payload, position, flags | 1)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.infolist()[0].flag_bits & 1
    _reject([("paper.zip", bytes(payload))])


@pytest.mark.parametrize("suffix", _FORMATS)
def test_corrupt_containers_are_rejected_as_http_errors(suffix):
    """Reject invalid container bytes without exposing archive parser exceptions."""
    _reject([(f"paper.{suffix}", b"This is not an archive")])


def test_zip_member_crc_corruption_is_detected_during_expansion():
    """Validate actual member contents rather than trusting a readable central directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("main.tex", b"Original paper content.")
    payload = buffer.getvalue().replace(b"Original paper content.", b"Tampered paper content.")
    _reject([("paper.zip", payload)])


@pytest.mark.parametrize("suffix", ["zip", "tar.gz", "tgz"])
def test_corrupt_deflate_streams_are_rejected_as_http_errors(suffix):
    """Translate compression-library failures into the public archive validation error."""
    payload = bytearray(_archive(suffix, [("main.tex", b"Paper source" * 20)]))
    if suffix == "zip":
        name_size, extra_size = struct.unpack_from("<HH", payload, 26)
        position = 30 + name_size + extra_size
    else:
        assert payload[3] == 0
        position = 10
    payload[position] = 0x07
    _reject([(f"paper.{suffix}", bytes(payload))])


@pytest.mark.parametrize("outer_suffix", _FORMATS)
@pytest.mark.parametrize("inner_suffix", _FORMATS)
def test_nested_archives_are_rejected_instead_of_recursively_expanded(outer_suffix, inner_suffix):
    """Require archive members to be ordinary project files."""
    inner = _archive(inner_suffix, [("note.md", b"Nested paper")])
    outer = _archive(outer_suffix, [("main.tex", b"Paper"), (f"nested/context.{inner_suffix}", inner)])
    _reject([(f"paper.{outer_suffix}", outer)])


@pytest.mark.parametrize("suffix", _FORMATS)
@pytest.mark.parametrize("names", [("main.tex", "MAIN.TEX"), ("Straße.md", "STRASSE.MD")])
def test_duplicate_archive_members_are_rejected_using_unicode_casefold(suffix, names):
    """Reject ambiguous member identities inside each supported container."""
    _reject([(f"paper.{suffix}", _archive(suffix, [(names[0], b"First"), (names[1], b"Second")]))])


def test_duplicate_paths_are_rejected_across_loose_files_and_multiple_archives():
    """Share duplicate detection across the complete expanded upload batch."""
    cases = [
        [("main.tex", b"Loose source"), ("paper.zip", _archive("zip", [("MAIN.TEX", b"Archived source")]))],
        [
            ("first.zip", _archive("zip", [("main.md", b"First")])),
            ("second.tgz", _archive("tgz", [("MAIN.MD", b"Second")])),
        ],
        [("project/context.tar", _archive("tar", [("rules.md", b"Rules")])), ("PROJECT/RULES.MD", b"Duplicate")],
    ]
    for files in cases:
        _reject(files)


@pytest.mark.parametrize("suffix", _FORMATS)
def test_casefolded_file_and_directory_ancestor_conflicts_are_rejected(suffix):
    """Prevent a retained file from also becoming another retained file's parent."""
    payload = _archive(suffix, [("A.md", b"File"), ("a.MD/note.txt", b"Child")])
    _reject([(f"paper.{suffix}", payload)])
    _reject(list(reversed([(f"paper.{suffix}", _archive(suffix, [("a.MD/note.txt", b"Child")])), ("A.md", b"File")])))


def test_file_and_directory_conflicts_are_rejected_across_archive_boundaries():
    """Detect conflicting ancestors after adding the outer archive directory prefix."""
    _reject([("A.md", b"File"), ("a.MD/context.tar.gz", _archive("tar.gz", [("note.txt", b"Child")]))])


@pytest.mark.parametrize("entrypoint", ["expand", "batch"])
def test_compressed_upload_limit_is_cumulative_across_the_batch(monkeypatch, entrypoint):
    """Enforce the raw upload budget across containers, without huge fixtures."""
    files = [
        ("first.zip", _archive("zip", [("main.tex", b"Paper")])),
        ("second.tgz", _archive("tgz", [("notes.md", b"Notes")])),
    ]
    monkeypatch.setattr(paper_service, "MAX_PAPER_SOURCE_BYTES", sum(len(data) for _, data in files) - 1)
    with pytest.raises(HTTPException) as rejected:
        _expand(files) if entrypoint == "expand" else paper_service.validate_paper_source_batch(files)
    assert rejected.value.status_code == 413


@pytest.mark.parametrize("entrypoint", ["expand", "batch"])
def test_expanded_limit_counts_all_archives_and_loose_files(monkeypatch, entrypoint):
    """Accept the exact expanded limit and reject one excess byte across formats."""
    files = [
        ("first.zip", _archive("zip", [("main.tex", b"a" * 17)])),
        ("refs/second.tgz", _archive("tgz", [("context.md", b"b" * 19)])),
        ("notes.txt", b"ccc"),
    ]
    monkeypatch.setattr(paper_service, "MAX_PAPER_EXPANDED_BYTES", 39)
    if entrypoint == "expand":
        assert len(_expand(files)) == 3
    else:
        assert len(paper_service.validate_paper_source_batch(files).manifest) == 3
    monkeypatch.setattr(paper_service, "MAX_PAPER_EXPANDED_BYTES", 38)
    with pytest.raises(HTTPException) as rejected:
        _expand(files) if entrypoint == "expand" else paper_service.validate_paper_source_batch(files)
    assert rejected.value.status_code == 413


@pytest.mark.parametrize("entrypoint", ["expand", "batch"])
def test_file_count_limit_is_cumulative_across_archives_and_loose_files(monkeypatch, entrypoint):
    """Apply the retained-file count to the whole batch rather than each archive."""
    files = [
        ("main.md", b"# Paper"),
        ("first.zip", _archive("zip", [("one.txt", b"One"), ("two.txt", b"Two")])),
        ("second.tar", _archive("tar", [("three.txt", b"Three")])),
    ]
    monkeypatch.setattr(paper_service, "MAX_PAPER_ARCHIVE_FILES", 4)
    if entrypoint == "expand":
        assert len(_expand(files)) == 4
    else:
        assert len(paper_service.validate_paper_source_batch(files).manifest) == 4
    monkeypatch.setattr(paper_service, "MAX_PAPER_ARCHIVE_FILES", 3)
    with pytest.raises(HTTPException) as rejected:
        _expand(files) if entrypoint == "expand" else paper_service.validate_paper_source_batch(files)
    assert rejected.value.status_code == 413


def test_expanded_tree_can_exceed_the_compressed_upload_budget(monkeypatch):
    """Keep the raw and expanded budgets distinct during validation and repacking."""
    payload = _archive("tar.gz", [("main.tex", b"a" * 4096), ("notes.md", b"Notes")])
    assert len(payload) < 512
    monkeypatch.setattr(paper_service, "MAX_PAPER_SOURCE_BYTES", 512)
    monkeypatch.setattr(paper_service, "MAX_PAPER_EXPANDED_BYTES", 8192)
    validated = paper_service.validate_paper_source_batch([("paper.tar.gz", payload)])
    with zipfile.ZipFile(io.BytesIO(validated.data)) as archive:
        assert archive.read("main.tex") == b"a" * 4096


class MemoryStorage:
    """Keep source versions isolated from RustFS and all external services."""

    def __init__(self):
        self.objects = {}

    def upload(self, key, data, **_kwargs):
        """Retain exact object bytes in memory."""
        self.objects[key] = data.read() if hasattr(data, "read") else data

    def download(self, key, **_kwargs):
        """Read one immutable stored version."""
        return self.objects[key]

    def delete(self, key, **_kwargs):
        """Remove an object whose database publication failed."""
        self.objects.pop(key, None)


@pytest.fixture
def archive_workspace(tmp_path, monkeypatch):
    """Provide real SQLite persistence and a disposable source-tree mirror."""
    engine = create_engine(f"sqlite:///{tmp_path / 'archives.sqlite'}")
    tables = [
        models.PaperWorkspace,
        models.PaperSourceVersion,
        models.PaperReview,
        models.PaperEvaluationMetric,
        models.PaperBoundarySnapshot,
        models.PaperOptimizationTarget,
        models.PaperAgentRun,
    ]
    models.PaperWorkspace.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    storage = MemoryStorage()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    user = models.User(email="archives@example.com", hashed_password="unused")
    with Session(engine) as db:
        workspace = models.PaperWorkspace(user_id=user.id, title="Archive uploads", mode="manuscript")
        db.add(workspace)
        db.commit()
        yield db, user, workspace, storage, engine
    engine.dispose()


def _uploads(files):
    """Use real asynchronous UploadFile readers for the service integration tests."""
    return [UploadFile(filename=name, file=io.BytesIO(data)) for name, data in files]


@pytest.mark.asyncio
async def test_initial_async_mixed_upload_persists_one_normalized_source(archive_workspace):
    """Expand initial uploads once and persist their complete normalized tree."""
    db, user, workspace, _storage, _engine = archive_workspace
    files = [
        ("paper.zip", _archive("zip", [("main.tex", b"Paper")])),
        ("project_context/rules/rules.tar.gz", _archive("tar.gz", [("rules.txt", b"Venue rules")])),
        ("参考/notes.txt", "补充材料".encode()),
    ]
    uploads = _uploads(files)
    source = await paper_service.upload_source_version(db, user, workspace.id, uploads)
    assert source.source_kind == "source_bundle"
    assert dict(paper_service._source_entries(source)) == {
        "main.tex": b"Paper",
        "project_context/rules/rules.txt": b"Venue rules",
        "参考/notes.txt": "补充材料".encode(),
    }
    assert workspace.active_source_version_id == source.id
    assert source.version == 1
    assert source.parent_version_id is None
    assert len(db.exec(select(models.PaperSourceVersion)).all()) == 1
    assert all(upload.file.closed for upload in uploads)


@pytest.mark.asyncio
async def test_async_archive_merge_preserves_history_and_existing_files(archive_workspace):
    """Merge context-only archives into a new linked version without losing old bytes."""
    db, user, workspace, storage, _engine = archive_workspace
    original = paper_service._create_source_version(
        db,
        workspace,
        [("main.md", b"# Original paper"), ("figures/existing.png", b"Existing figure")],
        change_summary="Initial",
    )
    original_id, original_key, original_hash = original.id, original.object_key, original.content_hash
    original_bytes = storage.download(original_key)
    files = [
        ("update.zip", _archive("zip", [("main.md", b"# Revised paper")])),
        ("project_context/rules/rules.tgz", _archive("tgz", [("rules.txt", b"Rules only")])),
        ("参考.txt", "中文补充".encode()),
    ]
    uploads = _uploads(files)
    updated = await paper_service.upload_source_version(db, user, workspace.id, uploads)
    assert updated.id != original_id
    assert updated.parent_version_id == original_id
    assert updated.version == 2
    assert workspace.active_source_version_id == updated.id
    assert dict(paper_service._source_entries(updated)) == {
        "main.md": b"# Revised paper",
        "figures/existing.png": b"Existing figure",
        "project_context/rules/rules.txt": b"Rules only",
        "参考.txt": "中文补充".encode(),
    }
    assert db.get(models.PaperSourceVersion, original_id).content_hash == original_hash
    assert storage.download(original_key) == original_bytes
    assert paper_service.download_source_version(db, user, original_id)[0] == original_bytes
    assert len(db.exec(select(models.PaperSourceVersion)).all()) == 2
    assert all(upload.file.closed for upload in uploads)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["corrupt", "duplicate", "expanded", "count", "ancestor"])
async def test_failed_async_archive_batch_does_not_publish_partial_history(archive_workspace, monkeypatch, failure):
    """Reject a complete batch before advancing pointers or writing source objects."""
    db, user, workspace, storage, engine = archive_workspace
    original = paper_service._create_source_version(
        db, workspace, [("main.md", b"# Original")], change_summary="Initial"
    )
    workspace_id, original_id = workspace.id, original.id
    before = dict(storage.objects)
    files = [("accepted.txt", b"Good file"), ("bad.tgz", b"Corrupt container")]
    status = 400
    if failure == "duplicate":
        files = [
            ("first.zip", _archive("zip", [("rules.txt", b"First")])),
            ("second.tar", _archive("tar", [("RULES.TXT", b"Second")])),
        ]
    elif failure == "expanded":
        files = [
            ("first.zip", _archive("zip", [("one.txt", b"a" * 9)])),
            ("second.tar", _archive("tar", [("two.txt", b"b" * 9)])),
        ]
        monkeypatch.setattr(paper_service, "MAX_PAPER_EXPANDED_BYTES", 17)
        status = 413
    elif failure == "count":
        files = [("first.zip", _archive("zip", [("one.txt", b"One"), ("two.txt", b"Two")]))]
        monkeypatch.setattr(paper_service, "MAX_PAPER_ARCHIVE_FILES", 2)
        status = 413
    elif failure == "ancestor":
        files = [("A.md", b"File"), ("a.MD/context.tar", _archive("tar", [("note.txt", b"Child")]))]
    uploads = _uploads(files)
    with pytest.raises(HTTPException) as rejected:
        await paper_service.upload_source_version(db, user, workspace_id, uploads)
    assert rejected.value.status_code == status
    assert storage.objects == before
    with Session(engine) as fresh:
        assert fresh.get(models.PaperWorkspace, workspace_id).active_source_version_id == original_id
        assert len(fresh.exec(select(models.PaperSourceVersion)).all()) == 1
    assert all(upload.file.closed for upload in uploads)


@pytest.mark.asyncio
async def test_async_archive_database_failure_preserves_original_and_cleans_object(archive_workspace, monkeypatch):
    """Roll back the new version and remove its object if the final commit fails."""
    db, user, workspace, storage, engine = archive_workspace
    original = paper_service._create_source_version(
        db, workspace, [("main.md", b"# Original")], change_summary="Initial"
    )
    workspace_id, original_id = workspace.id, original.id
    before = dict(storage.objects)
    uploads = _uploads([("context.tar.gz", _archive("tar.gz", [("notes.txt", b"New context")]))])

    def fail_commit():
        raise RuntimeError("Injected archive publication failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Injected archive publication failure"):
        await paper_service.upload_source_version(db, user, workspace_id, uploads)
    assert storage.objects == before
    with Session(engine) as fresh:
        assert fresh.get(models.PaperWorkspace, workspace_id).active_source_version_id == original_id
        assert len(fresh.exec(select(models.PaperSourceVersion)).all()) == 1
    assert all(upload.file.closed for upload in uploads)


@pytest.mark.parametrize("metadata", ["pax", "ignored_file"])
def test_tar_physical_budget_includes_pax_headers_and_ignored_metadata(monkeypatch, metadata):
    """Bound gzip expansion before TAR can consume hidden metadata or PAX strings."""
    monkeypatch.setattr(paper_service, "MAX_PAPER_EXPANDED_BYTES", 64)
    monkeypatch.setattr(paper_service, "MAX_PAPER_ARCHIVE_FILES", 1)
    assert _expand([("normal.tgz", _archive("tgz", [("main.tex", b"Paper")]))]) == [("main.tex", b"Paper")]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        if metadata == "ignored_file":
            hidden = tarfile.TarInfo("__MACOSX/ignored.bin")
            hidden.size = 20_000
            archive.addfile(hidden, io.BytesIO(b"x" * hidden.size))
        primary = tarfile.TarInfo("main.tex")
        primary.size = 5
        if metadata == "pax":
            primary.pax_headers = {"comment": "x" * 20_000}
        archive.addfile(primary, io.BytesIO(b"Paper"))
    assert len(buffer.getvalue()) < 1_024
    _reject([("hidden.tgz", buffer.getvalue())], status=413)


def test_tar_physical_decompression_budget_is_shared_across_the_batch(monkeypatch):
    """Share the cost of consumed metadata while allowing ordinary tiny archives."""
    monkeypatch.setattr(paper_service, "MAX_PAPER_EXPANDED_BYTES", 64)
    monkeypatch.setattr(paper_service, "MAX_PAPER_ARCHIVE_FILES", 4)
    tiny = [("one.tgz", _archive("tgz", [("one.tex", b"One")])), ("two.tgz", _archive("tgz", [("two.tex", b"Two")]))]
    assert dict(_expand(tiny)) == {"one.tex": b"One", "two.tex": b"Two"}
    files = []
    for name, content in (("one", b"One"), ("two", b"Two")):
        payload = _archive("tgz", [("__MACOSX/ignored.bin", b"x" * 10_000), (f"{name}.tex", content)])
        upload = (f"{name}.tgz", payload)
        assert _expand([upload]) == [(f"{name}.tex", content)]
        files.append(upload)
    _reject(files, status=413)


@pytest.mark.parametrize("filename", ["bad\ud800.md", "bad\udcff.md"])
def test_unencodable_outer_filenames_are_handled_validation_errors(filename):
    """Reject surrogate filenames before JSON, hashing, or object publication."""
    _reject([("main.tex", b"Paper"), (filename, b"Invalid filename")])


def test_tar_surrogate_escaped_member_filename_is_rejected():
    """Reject invalid UTF-8 TAR names that Python decodes using surrogateescape."""
    payload = bytearray(_archive("tar", [("badX.md", b"Paper")]))
    payload[:100] = payload[:100].replace(b"badX.md", b"bad\xff.md")
    payload[148:156] = b" " * 8
    checksum = sum(payload[:512])
    payload[148:156] = f"{checksum:06o}\x00 ".encode("ascii")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        assert "\udcff" in archive.getmembers()[0].name
    _reject([("source.tar", bytes(payload))])


@pytest.mark.parametrize("suffix", ["tar.gz", "tgz"])
@pytest.mark.parametrize("corruption", ["truncate", "crc"])
def test_gzip_footer_is_validated_after_tar_end_marker(suffix, corruption):
    """Reject corrupt gzip trailers even when all TAR members remain readable."""
    payload = _archive(suffix, [("main.tex", b"Paper")])
    if corruption == "truncate":
        payload = payload[:-8]
    else:
        payload = payload[:-8] + bytes([payload[-8] ^ 1]) + payload[-7:]
    _reject([(f"source.{suffix}", payload)])
