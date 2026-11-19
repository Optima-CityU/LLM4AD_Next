"""Real offline conversion and immutable render-artifact regression tests."""

from __future__ import annotations

import copy
import importlib
import io
import json
import shutil
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine

from app import models
from app.schemas.paper_rendering import PaperRenderDiagnostic, PaperRenderResponse
from app.services import paper_rendering as rendering


@pytest.fixture
def compiler():
    """Use installed production converters instead of simulating their output."""
    if sys.platform != "linux" or shutil.which("pandoc") is None:
        pytest.skip("Real offline rendering requires Linux and the Pandoc binary")
    pytest.importorskip("typst")
    if not rendering._font_paths():
        pytest.skip("Verified frontend static fonts have not been fetched")
    return rendering.render_proposal


def test_default_delegates_sectioned_source_and_adds_style_without_processes():
    """Import and source creation do not execute or install rendering programs."""
    title = "Gaussian-process proposal 高斯过程"
    description = "  The author's exact brief.\n\n保留空行、引文与数学 $x^2$。\n"
    from app.services.paper_proposal import PROPOSAL_CHAPTERS, default_sectioned_proposal

    with (
        patch("subprocess.run", side_effect=AssertionError("Unexpected converter execution")),
        patch("subprocess.Popen", side_effect=AssertionError("Unexpected child process")),
    ):
        importlib.reload(rendering)
        entries = dict(rendering.default_markdown_proposal(title, description))
    assert entries == dict(
        [*default_sectioned_proposal(title, description), (rendering._STYLE_ENTRY, rendering._STYLE_PATH.read_bytes())]
    )
    assert len([path for path in entries if path.endswith(".md") and path != "proposal-log.md"]) == 9
    state = json.loads(entries["proposal-state.json"])
    assert state["project_info"]["topic"] == title
    assert state["project_info"]["description"] == description
    for _identifier, heading, path in PROPOSAL_CHAPTERS:
        assert heading in entries[path].decode()
        assert b"#include" not in entries[path]
    assert b"show par: set block" not in entries["styles/nsfc-proposal.typ"]


def test_default_chinese_draft_renders_without_cjk_or_deprecation_warnings(compiler):
    """All bundled styles and CJK fonts work with the actual Typst compiler."""
    entries = rendering.default_markdown_proposal("研究计划", "作者的初始说明。")
    before = copy.deepcopy(entries)
    result = compiler(entries)
    assert result.status == "ready", result.diagnostics
    assert result.pdf.startswith(b"%PDF-")
    assert b"nsfc-proposal" in result.typst
    assert not result.diagnostics
    assert entries == before


def test_math_tables_citations_and_local_images_use_real_pandoc(compiler):
    """Preserve GP notation, length scales, bold math, tables and references."""
    markdown = r"""# Gaussian process 高斯过程

A prior $f \sim \mathcal{GP}(\mu,k)$, length scale $\ell$ and $\mathbf{BF}$.

$$
k(x,x')=\exp\left(-\frac{\|x-x'\|^2}{2\ell^2}\right)
$$

| Method | Cost |
|---|---:|
| GP | $n^3$ |

Verified citation [@checked].

![A local shape](images/shape.svg)
"""
    entries = [
        ("draft/main.md", markdown.encode()),
        (
            "references.bib",
            b"@article{checked, author={A. Author}, title={Checked source}, journal={Journal}, year={2024}}",
        ),
        (
            "draft/images/shape.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>',
        ),
    ]
    result = compiler(entries, "draft/main.md")
    assert result.status == "ready", result.diagnostics
    assert result.pdf.startswith(b"%PDF-")
    assert b"table(" in result.typst
    assert b"references.bib" in result.typst
    assert not any("Could not convert TeX math" in item.message for item in result.diagnostics)
    assert all("unknown font family" not in item.message for item in result.diagnostics)


def test_unsupported_math_is_not_silently_exported_as_literal_tex(compiler):
    """Pandoc's successful exit must not hide a failed mathematical conversion."""
    entries = [("proposal.md", rb"# Methods" + b"\n\n" + rb"$\unsupportedmacro{x}$")]
    original = copy.deepcopy(entries)
    result = compiler(entries)
    assert result.status == "failed"
    assert result.pdf is None
    assert any("Math conversion failed" in item.message for item in result.diagnostics)
    assert entries == original


def test_legacy_typst_entry_returns_pdf_and_unchanged_source(compiler):
    """Existing Typst projects remain renderable with relative includes."""
    entries = [
        ("old/main.typ", b'#include "section.typ"'),
        ("old/section.typ", b"= Existing source\nOriginal author words."),
    ]
    result = compiler(entries, "old/main.typ")
    assert result.status == "ready", result.diagnostics
    assert result.typst == entries[0][1]
    assert result.pdf.startswith(b"%PDF-")


@pytest.mark.parametrize("unsafe", ["../outside.md", "/etc/passwd", "C:\\secrets.md", "draft/../../outside.md"])
def test_unsafe_resource_paths_fail_before_starting_a_converter(unsafe):
    """A render cannot create or read a resource outside its isolated tree."""
    with patch("subprocess.Popen", side_effect=AssertionError("Unexpected worker")):
        result = rendering.render_proposal([(unsafe, b"# Draft")], unsafe)
    assert result.status == "failed"
    assert result.pdf is None
    assert result.diagnostics[0].severity == "error"


def test_raw_markdown_typst_is_literal_and_cannot_read_host_files(compiler):
    """Untrusted raw blocks are never enabled as executable Typst markup."""
    markdown = b'# Author text\n\n```{=typst}\n#read("/etc/passwd")\n```\n'
    result = compiler([("proposal.md", markdown)])
    assert result.status == "ready", result.diagnostics
    assert result.pdf.startswith(b"%PDF-")


def test_legacy_typst_is_resource_root_bounded_and_preserves_failed_draft(compiler):
    """Compilation errors do not modify author input or expose host files."""
    entries = [("proposal.typ", b'#read("../../etc/passwd")')]
    before = copy.deepcopy(entries)
    result = compiler(entries, "proposal.typ")
    assert result.status == "failed"
    assert result.pdf is None
    assert result.typst == entries[0][1]
    assert result.diagnostics
    assert entries == before


def test_remote_resources_cannot_make_runtime_network_requests(compiler):
    """Both converter and native compiler children run with sockets denied."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        """Count any unexpected request made by a rendering process."""

        def do_GET(self):
            """Record requests without serving external data."""
            requests.append(self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_args):
            """Keep test server output quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        markdown = f"# Offline\n\n![remote](http://127.0.0.1:{server.server_port}/secret.png)".encode()
        result = compiler([("proposal.md", markdown)])
        assert result.status == "failed"
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_timeout_returns_diagnostics_and_does_not_mutate_source(compiler):
    """Kill the full converter process group at the configured wall-clock bound."""
    entries = [("proposal.md", b"# Source preserved")]
    result = compiler(entries, timeout_seconds=0.001)
    assert result.status == "failed"
    assert any("time limit" in item.message for item in result.diagnostics)
    assert entries == [("proposal.md", b"# Source preserved")]


class MemoryStorage:
    """Persist build manifests and artifacts without contacting object storage."""

    def __init__(self):
        """Initialize isolated immutable object bytes."""
        self.objects = {}

    def upload(self, key, data, **_kwargs):
        """Store bytes while retaining the actual object key."""
        self.objects[key] = data if isinstance(data, bytes) else data.read()

    def download(self, key):
        """Return an existing object or a missing-object signal."""
        return self.objects[key]

    def iter_download(self, key):
        """Stream stored bytes through the production download contract."""
        yield self.objects[key]


@pytest.fixture
def render_store(tmp_path, monkeypatch):
    """Use real ownership SQL with immutable source objects and fake build bytes."""
    from app.services import paper_revision_history, paper_service

    captures = []
    original_capture = paper_revision_history.capture_revision
    monkeypatch.setattr(
        paper_revision_history,
        "capture_revision",
        lambda _db, workspace, **kwargs: captures.append((workspace.active_source_version_id, kwargs)),
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'render.sqlite'}")
    models.PaperWorkspace.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                models.User,
                models.LLMProvider,
                models.PaperWorkspace,
                models.PaperSourceVersion,
                models.PaperReview,
                models.PaperAgentRun,
            )
        ],
    )
    storage = MemoryStorage()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(rendering, "_converter_version", lambda: "pandoc-test")
    monkeypatch.setattr(rendering, "_renderer_fingerprint", lambda *_args: "template-1")
    calls = []

    def build(entries, entry_path, *, entry_paths=None):
        """Record source identity while isolating persistence from compiler tests."""
        calls.append((copy.deepcopy(entries), entry_paths or [entry_path]))
        return rendering.RenderedProposal("ready", entry_path, typst=b"= Generated", pdf=b"%PDF-generated")

    monkeypatch.setattr(rendering, "render_proposal", build)
    with Session(engine) as db:
        user = models.User(email="render@example.com", hashed_password="unused")
        workspace = models.PaperWorkspace(
            user_id=user.id, title="Proposal", mode="proposal", proposal_entry_path="proposal.md"
        )
        db.add(user)
        db.add(workspace)
        sources = []
        for version in (1, 2):
            source = models.PaperSourceVersion(
                workspace_id=workspace.id,
                version=version,
                source_kind="markdown",
                filename="proposal.md",
                object_key=f"source-{version}.md",
                content_hash=f"hash-{version}",
                content_size=10,
            )
            storage.objects[source.object_key] = f"# Revision {version}".encode()
            sources.append(source)
            db.add(source)
        workspace.active_source_version_id = sources[1].id
        db.add(workspace)
        db.commit()
        storage.captures = captures
        storage.original_capture = original_capture
        yield db, user, workspace, sources, storage, calls
    engine.dispose()


def test_render_cache_is_immutable_and_tied_to_source_and_converter(render_store, monkeypatch):
    """New source or converter versions produce new URLs without replacing old files."""
    db, user, workspace, sources, storage, calls = render_store
    first = rendering.render_source_version(db, user, sources[0].id)
    repeated = rendering.render_source_version(db, user, sources[0].id)
    assert first == repeated and len(calls) == 1
    second = rendering.render_source_version(db, user, sources[1].id)
    assert second.pdf_url != first.pdf_url and len(calls) == 2
    monkeypatch.setattr(rendering, "_renderer_fingerprint", lambda *_args: "template-2")
    rebuilt = rendering.render_source_version(db, user, sources[0].id)
    assert rebuilt.pdf_url != first.pdf_url and len(calls) == 3
    assert workspace.active_source_version_id == sources[1].id
    assert workspace.proposal_entry_path == "proposal.md"
    exports = workspace.rebuttal_context["_paper_exports"]
    assert len(exports[str(sources[0].id)]) == 2
    assert len(exports[str(sources[1].id)]) == 1
    assert len(storage.captures) == 1
    assert storage.captures[0][0] == sources[1].id
    assert storage.captures[0][1]["origin"] == "export"
    for manifest_key in exports[str(sources[0].id)]:
        manifest = PaperRenderResponse.model_validate_json(storage.objects[manifest_key])
        assert manifest.source_version_id == sources[0].id
    body, mime = rendering.rendered_source_file(db, user, sources[0].id, first.pdf_url.rsplit("/", 1)[1])
    assert b"".join(body) == b"%PDF-generated" and mime == "application/pdf"
    assert all(
        not key.startswith("source-") for key in set(storage.objects) - {source.object_key for source in sources}
    )


def test_failure_retains_source_and_partial_typst_without_a_pdf_url(render_store, monkeypatch):
    """An actual export error leaves author revisions and older PDFs intact."""
    db, user, workspace, sources, storage, _calls = render_store
    original = dict(storage.objects)
    monkeypatch.setattr(
        rendering,
        "render_proposal",
        lambda _entries, entry, **_kwargs: rendering.RenderedProposal(
            "failed",
            entry,
            diagnostics=[PaperRenderDiagnostic(message="actual compile error", path=entry)],
            typst=b"broken source",
        ),
    )
    response = rendering.render_source_version(db, user, sources[0].id)
    assert response.status == "failed" and response.pdf_url is None
    assert response.typst_url and len(response.files) == 1
    assert all(storage.objects[key] == value for key, value in original.items())
    assert workspace.active_source_version_id == sources[1].id
    assert "_paper_exports" not in workspace.rebuttal_context
    assert storage.captures == []


def test_foreign_source_and_invalid_render_names_are_not_downloadable(render_store):
    """Ownership is checked before generated object keys are disclosed or streamed."""
    db, user, _workspace, sources, _storage, _calls = render_store
    response = rendering.render_source_version(db, user, sources[0].id)
    other = models.User(email="other@example.com", hashed_password="unused")
    with pytest.raises(HTTPException) as error:
        rendering.render_source_version(db, other, sources[0].id)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        rendering.rendered_source_file(db, other, sources[0].id, response.pdf_url.rsplit("/", 1)[1])
    for filename in ("../source-1.md", "proposal.pdf", "a" * 64 + ".json"):
        with pytest.raises(HTTPException):
            rendering.rendered_source_file(db, user, sources[0].id, filename)


def test_api_shape_has_optional_entry_query_and_authenticated_streaming(render_store):
    """The router exposes typed results and serves bytes through owner authentication."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_db
    from app.api.llm4ad.paper_rendering import router

    db, user, _workspace, sources, _storage, _calls = render_store
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        response = client.post(f"/api/v1/llm4ad/papers/source-versions/{sources[0].id}/render?entry_path=proposal.md")
        assert response.status_code == 200
        rendered = PaperRenderResponse.model_validate(response.json())
        assert rendered.source_version_id == sources[0].id
        assert rendered.source_hash == "hash-1" and rendered.entry_path == "proposal.md"
        pdf = client.get(rendered.pdf_url)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["cache-control"] == "private, no-store"
        app.dependency_overrides.pop(get_current_user)
        assert client.get(rendered.pdf_url).status_code == 401


def test_real_history_capture_keeps_export_metadata_out_of_author_content_hash(render_store, monkeypatch):
    """Actual history stores export references without duplicating PDF bodies."""
    from app.services import paper_revision_history as history

    db, user, workspace, sources, storage, _calls = render_store
    monkeypatch.setattr(history, "capture_revision", storage.original_capture)
    before = history._state_hash(history._state(db, workspace))
    response = rendering.render_source_version(db, user, sources[1].id)
    after = history._state_hash(history._state(db, workspace))
    assert before == after
    assert workspace.active_source_version_id == sources[1].id
    revisions = history.get_history(db, user, workspace.id)
    assert any(revision.origin == "export" for revision in revisions.revisions)
    manifests = workspace.rebuttal_context["_paper_exports"][str(sources[1].id)]
    assert len(manifests) == 1
    assert str(sources[1].id) in manifests[0]
    assert sum(content == b"%PDF-generated" for content in storage.objects.values()) == 1
    assert response.pdf_url


def test_cached_manifest_must_match_its_exact_source_revision(render_store):
    """Never return another revision's metadata if a stored manifest is misplaced."""
    db, user, workspace, sources, storage, calls = render_store
    response = rendering.render_source_version(db, user, sources[0].id)
    key = workspace.rebuttal_context["_paper_exports"][str(sources[0].id)][0]
    misplaced = response.model_copy(update={"source_version_id": sources[1].id, "source_hash": sources[1].content_hash})
    storage.objects[key] = misplaced.model_dump_json().encode()
    with pytest.raises(HTTPException) as error:
        rendering.rendered_source_file(db, user, sources[0].id, response.pdf_url.rsplit("/", 1)[1])
    assert error.value.status_code == 404
    rebuilt = rendering.render_source_version(db, user, sources[0].id)
    assert rebuilt.source_version_id == sources[0].id
    assert rebuilt.source_hash == sources[0].content_hash and len(calls) == 2


@pytest.fixture
def sectioned_render_store(render_store):
    """Store the real nine-chapter source as an immutable ZIP revision."""
    from app.services.paper_proposal import DEFAULT_PROPOSAL_ENTRY, proposal_entry_paths

    db, user, workspace, sources, storage, calls = render_store
    entries = rendering.default_markdown_proposal("高斯过程研究", "待作者确认的初始内容。")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in reversed(entries):
            archive.writestr(path, content)
    source = sources[1]
    source.source_kind = "latex_zip"
    source.filename = "proposal.zip"
    source.manifest = [path for path, _content in reversed(entries)]
    storage.objects[source.object_key] = payload.getvalue()
    workspace.proposal_entry_path = DEFAULT_PROPOSAL_ENTRY
    db.add(source)
    db.add(workspace)
    db.commit()
    return db, user, workspace, sources, storage, calls, proposal_entry_paths(entries)


def test_real_sectioned_chinese_math_images_and_numbered_references_preserve_order(compiler):
    """Compile actual chapters together without duplicating Markdown references."""
    from app.services.paper_proposal import proposal_entry_paths

    resources = dict(rendering.default_markdown_proposal("高斯过程", "作者确认后排版。"))
    paths = proposal_entry_paths(list(resources.items()))
    for index, path in enumerate(paths, 1):
        heading = resources[path].decode().splitlines()[0]
        resources[path] = f"{heading}\n\n正文标记{index}。\n".encode()
    resources[paths[0]] += r"""

已有方法见 [1]，后文继续使用相同编号 [1]。

行内先验 $f \sim \mathcal{GP}(\mu,k)$ 与尺度 $\ell$。

$$
k(x,x')=\exp\left(-\frac{\|x-x'\|^2}{2\ell^2}\right)
$$

![技术路线](images/shape.svg)
""".encode()
    resources[paths[3]] += "\n[1] 作者甲. 已核实的中文研究. 2024.\n".encode()
    resources[paths[6]] += "\n遵循文献 [1] 的记号，验证方案由作者决定。\n".encode()
    resources["images/shape.svg"] = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
    )
    resources["references.bib"] = (
        b"@article{duplicate,author={Duplicate},title={Must not append},journal={Journal},year={2024}}"
    )
    entries = list(reversed(list(resources.items())))
    original = copy.deepcopy(entries)
    result = compiler(entries)
    assert result.status == "ready", result.diagnostics
    assert result.entry_path == paths[0]
    assert result.pdf.startswith(b"%PDF-")
    typst = result.typst.decode()
    positions = [typst.index(f"正文标记{index}") for index in range(1, 10)]
    assert positions == sorted(positions)
    assert "已核实的中文研究" in typst
    assert typst.count(r"\[1\]") == 4
    assert "references.bib" not in typst and "#bibliography" not in typst
    assert "image(" in typst and "exp" in typst
    assert not result.diagnostics
    assert entries == original
    subset = compiler(entries, entry_paths=[paths[0]])
    assert subset.status == "ready", subset.diagnostics
    assert b"#bibliography" not in subset.typst


def test_ordered_chapter_cache_defaults_and_explicit_order(sectioned_render_store, monkeypatch):
    """Canonical chapters and explicit order have separate immutable builds."""
    db, user, workspace, sources, storage, calls, paths = sectioned_render_store
    source = sources[1]
    first = rendering.render_source_version(db, user, source.id, expected_source_hash=source.content_hash)
    assert first.entry_paths == paths and first.entry_path == paths[0]
    assert calls[0][1] == paths
    assert len(calls[0][0]) == len(source.manifest)
    repeated = rendering.render_source_version(db, user, source.id, entry_paths=paths)
    assert first == repeated and len(calls) == 1
    reversed_paths = list(reversed(paths))
    reordered = rendering.render_source_version(db, user, source.id, entry_paths=reversed_paths)
    assert reordered.pdf_url != first.pdf_url
    assert reordered.entry_paths == reversed_paths and reordered.entry_path == paths[-1]
    assert calls[1][1] == reversed_paths
    snapshot = copy.deepcopy(storage.objects)
    context = copy.deepcopy(workspace.rebuttal_context)
    with (
        patch.object(rendering, "_converter_version", side_effect=AssertionError("GET probed converter")),
        patch.object(rendering, "_renderer_fingerprint", side_effect=AssertionError("GET probed renderer")),
        patch.object(rendering, "_font_paths", side_effect=AssertionError("GET probed fonts")),
        patch.object(rendering, "render_proposal", side_effect=AssertionError("GET rendered")),
        patch.object(rendering, "_register_export", side_effect=AssertionError("GET wrote history")),
    ):
        assert rendering.get_source_version_render(db, user, source.id) == first
        assert rendering.get_source_version_render(db, user, source.id, entry_paths=reversed_paths) == reordered
        assert rendering.get_source_version_render(db, user, source.id, entry_paths=paths[:2]) is None
    assert storage.objects == snapshot and workspace.rebuttal_context == context
    assert len(calls) == 2
    monkeypatch.setattr(rendering, "_renderer_fingerprint", lambda *_args: "new-renderer")
    assert rendering.render_source_version(db, user, source.id).pdf_url != first.pdf_url


def test_hash_mismatch_precedes_source_reads_conversion_and_writes(sectioned_render_store):
    """Stale author confirmation fails before any converter or source work."""
    from app.services import paper_service

    db, user, workspace, sources, storage, calls, paths = sectioned_render_store
    snapshot = copy.deepcopy(storage.objects)
    context = copy.deepcopy(workspace.rebuttal_context)
    with (
        patch.object(paper_service, "_source_entries", side_effect=AssertionError("Stale source read")),
        patch.object(rendering, "_converter_version", side_effect=AssertionError("Stale converter probe")),
        patch.object(rendering, "_renderer_fingerprint", side_effect=AssertionError("Stale renderer probe")),
    ):
        with pytest.raises(HTTPException) as error:
            rendering.render_source_version(
                db, user, sources[1].id, entry_paths=paths, expected_source_hash="stale-author-hash"
            )
    assert error.value.status_code == 409
    assert storage.objects == snapshot and workspace.rebuttal_context == context and calls == []


def test_get_returns_failed_diagnostics_without_building_or_capturing_history(render_store, monkeypatch):
    """Persisted failed output is readable while untouched sources return null."""
    db, user, workspace, sources, storage, calls = render_store
    assert rendering.get_source_version_render(db, user, sources[0].id) is None
    monkeypatch.setattr(
        rendering,
        "render_proposal",
        lambda _entries, entry, **_kwargs: rendering.RenderedProposal(
            "failed", entry, diagnostics=[PaperRenderDiagnostic(message="compile failed", path=entry)]
        ),
    )
    failed = rendering.render_source_version(db, user, sources[0].id)
    snapshot = copy.deepcopy(storage.objects)
    monkeypatch.setattr(rendering, "render_proposal", lambda *_args, **_kwargs: pytest.fail("GET rendered"))
    monkeypatch.setattr(rendering, "_converter_version", lambda: pytest.fail("GET probed converter"))
    monkeypatch.setattr(rendering, "_font_paths", lambda: pytest.fail("GET probed fonts"))
    assert rendering.get_source_version_render(db, user, sources[0].id) == failed
    assert rendering.get_source_version_render(db, user, sources[1].id) is None
    assert storage.objects == snapshot and storage.captures == [] and calls == []
    assert "_paper_exports" not in workspace.rebuttal_context


def test_get_uses_legacy_export_references_and_rejects_wrong_source(render_store):
    """Older indexed manifests remain readable without constructing a new cache key."""
    db, user, workspace, sources, storage, _calls = render_store
    response = rendering.render_source_version(db, user, sources[0].id)
    pointer = f"{rendering._selection_prefix(rendering._render_prefix(user.id, workspace.id, sources[0]), ['proposal.md'])}/manifest.json"
    del storage.objects[pointer]
    key = workspace.rebuttal_context["_paper_exports"][str(sources[0].id)][0]
    legacy = json.loads(storage.objects[key])
    del legacy["entry_paths"]
    storage.objects[key] = json.dumps(legacy).encode()
    stored = rendering.get_source_version_render(db, user, sources[0].id)
    assert stored.source_hash == response.source_hash and stored.entry_paths == []
    assert rendering.get_source_version_render(db, user, sources[1].id) is None
    legacy["source_version_id"] = str(sources[1].id)
    storage.objects[key] = json.dumps(legacy).encode()
    assert rendering.get_source_version_render(db, user, sources[0].id) is None
    other = models.User(email="foreign-read@example.com", hashed_password="unused")
    with pytest.raises(HTTPException) as error:
        rendering.get_source_version_render(db, other, sources[0].id)
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    "paths",
    [[], ["../outside.md"], ["/etc/passwd"], ["missing.md"], ["proposal.md", "proposal.md"], ["source.pdf"]],
)
def test_invalid_input_selections_never_start_conversion(render_store, paths):
    """Validate only mechanical constraints before converter discovery or writes."""
    db, user, _workspace, sources, storage, calls = render_store
    snapshot = copy.deepcopy(storage.objects)
    with patch.object(rendering, "_converter_version", side_effect=AssertionError("Invalid input probed converter")):
        with pytest.raises(HTTPException) as error:
            rendering.render_source_version(db, user, sources[0].id, entry_paths=paths)
    assert error.value.status_code == 422
    assert storage.objects == snapshot and calls == []


def test_api_ordered_body_hash_guard_and_read_only_get(sectioned_render_store, monkeypatch):
    """Author confirmation and read-only inspection share the existing render URL."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_db
    from app.api.llm4ad.paper_rendering import router

    db, user, _workspace, sources, storage, calls, paths = sectioned_render_store
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    route = f"/api/v1/llm4ad/papers/source-versions/{sources[1].id}/render"
    with TestClient(app) as client:
        assert client.get(route).json() is None
        assert calls == []
        stale = client.post(route, json={"entry_paths": paths, "expected_source_hash": "old"})
        assert stale.status_code == 409 and calls == []
        response = client.post(route, json={"entry_paths": paths, "expected_source_hash": sources[1].content_hash})
        assert response.status_code == 200
        assert response.json()["entry_paths"] == paths and response.json()["entry_path"] == paths[0]
        assert client.post(route, json={"entry_paths": []}).status_code == 422
        assert client.post(route, params={"entry_path": paths[-1]}, json={"entry_paths": paths}).status_code == 422
        snapshot = copy.deepcopy(storage.objects)
        monkeypatch.setattr(rendering, "_converter_version", lambda: pytest.fail("GET probed converter"))
        assert client.get(route).json() == response.json()
        assert client.get(route, params=[("entry_paths", path) for path in paths]).json() == response.json()
        assert client.get(route, params=[("entry_paths", path) for path in reversed(paths)]).json() is None
        assert storage.objects == snapshot and len(calls) == 1
        app.dependency_overrides.pop(get_current_user)
        assert client.get(route).status_code == 401
