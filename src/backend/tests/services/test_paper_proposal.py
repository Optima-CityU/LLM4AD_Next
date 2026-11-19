"""Chapter source organization, safe navigation and lightweight projections."""

import io
import json
import uuid
import zipfile
from unittest.mock import Mock

import pytest

from app.models import PaperSourceVersion
from app.schemas.paper import PaperProposalChapter
from app.services import paper_proposal, paper_service


def test_new_proposals_preserve_upstream_sections_and_exact_author_context():
    """Only author facts and empty headings are initialized before research."""
    description = "  An exact brief.\n\n保留原文 $x^2$。\n"
    entries = dict(paper_proposal.default_sectioned_proposal("Research topic", description))
    state = json.loads(entries["proposal-state.json"])
    assert len(state["sections"]) == 9
    assert state["project_info"] == {"topic": "Research topic", "description": description}
    assert state["current_phase"] == 0
    assert all(item["status"] == "not_started" for item in state["sections"].values())
    assert "proposal.md" not in entries and "references.bib" not in entries
    assert entries["proposal-log.md"] == "# 申报书撰写日志\n".encode()
    assert paper_proposal.default_sectioned_proposal("Research topic", description) == list(entries.items())
    assert all(not item.has_content for item in paper_proposal.chapter_index(list(entries.items())))


def test_chapter_content_metadata_defaults_to_false():
    """Older chapter payloads remain valid without the display flag."""
    chapter = PaperProposalChapter(id="S1", title="Purpose", path="purpose.md")
    assert chapter.has_content is False
    assert chapter.model_dump()["has_content"] is False


@pytest.mark.parametrize(
    ("markdown", "has_content"),
    [
        ("", False),
        (" \t\n\r\n", False),
        ("# Chapter\n", False),
        (" \n  ### Chapter ###\r\n\t", False),
        ("#\n", False),
        ("\ufeff# Chapter\n", False),
        ("<!-- Intake facts\nnot chapter prose. -->\n# Chapter\n<!-- Pending. -->", False),
        ("# Chapter <!-- Pending. -->\n\n<!-- More\nplaceholders. -->\n", False),
        ("Chapter\n=======\n<!-- Pending. -->", False),
        ("Chapter\r\n-------\r\n\t", False),
        ("# Chapter\n\nAuthor body text.\n", True),
        ("# Chapter\n\n- First objective\n- Second objective\n", True),
        ("# Chapter\n\n## Planned experiments\n", True),
        ("# Chapter\n\n$$\\min_x f(x)$$\n", True),
        ("# Chapter\n\n<!-- Internal note. -->Actual<!-- Note. --> text.", True),
        ("Body text without a chapter heading.", True),
        ("$x^2$", True),
        ("Chapter\n=======\n\nBody text.", True),
    ],
)
def test_chapter_content_is_computed_from_markdown_bytes(markdown, has_content):
    """Only comments and the leading chapter heading are presentation scaffolding."""
    entries = paper_proposal.default_sectioned_proposal("Topic", None)
    entries[0] = (paper_proposal.DEFAULT_PROPOSAL_ENTRY, markdown.encode())
    original = list(entries)
    chapters = paper_proposal.chapter_index(entries)
    assert chapters[0].has_content is has_content
    assert all(not item.has_content for item in chapters[1:])
    assert paper_proposal.proposal_entry_paths(entries) == [item[2] for item in paper_proposal.PROPOSAL_CHAPTERS]
    assert entries == original


def test_reference_uploads_bibliography_logs_and_state_do_not_populate_chapters():
    """Intake evidence and workflow bookkeeping do not become chapter prose."""
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", "Detailed author brief."))
    state = json.loads(entries["proposal-state.json"])
    state.update(current_phase=9, ref_counter=12, last_updated="2026-11-18")
    for section in state["sections"].values():
        section.update(status="finalized", iteration=4, notes="The assistant reviewed reference material.")
    entries.update(
        {
            "proposal-state.json": json.dumps(state).encode(),
            "proposal-log.md": b"# Log\n\nReference intake and chapter reviews completed.",
            "project_context/reference.md": b"# Uploaded reference\n\nDetailed background.",
            "project_context/reference.pdf": b"%PDF-1.4 reference-only",
            "references.bib": b"@article{ref1, title={Reference evidence}}",
        }
    )
    chapters = paper_proposal.chapter_index(list(entries.items()))
    assert [item.id for item in chapters] == [f"S{number}" for number in range(1, 10)]
    assert all(item.status == "finalized" and not item.has_content for item in chapters)
    assert paper_proposal.proposal_entry_paths(list(entries.items())) == [item.path for item in chapters]


def test_bibliography_chapter_content_only_populates_its_own_chapter():
    """A written references chapter does not imply the proposal body is drafted."""
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", None))
    entries[paper_proposal.PROPOSAL_CHAPTERS[3][2]] += b"\n[1] Author. Research evidence. 2026.\n"
    chapters = paper_proposal.chapter_index(list(entries.items()))
    assert [item.id for item in chapters if item.has_content] == ["S4"]
    assert len(paper_proposal.proposal_entry_paths(list(entries.items()))) == 9


@pytest.mark.parametrize("status", ["not_started", "in_progress", "needs_revision", "finalized", None])
@pytest.mark.parametrize("body", ["", "A real chapter body."])
def test_chapter_status_never_overrides_actual_content(status, body):
    """Both finalized empty chapters and not-started written chapters stay accurate."""
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", None))
    state = json.loads(entries["proposal-state.json"])
    state["sections"]["S1"]["status"] = status
    entries["proposal-state.json"] = json.dumps(state).encode()
    entries[paper_proposal.DEFAULT_PROPOSAL_ENTRY] = f"# Purpose\n\n{body}".encode()
    chapter = paper_proposal.chapter_index(list(entries.items()))[0]
    assert chapter.status == status
    assert chapter.has_content is bool(body)


def test_chapter_navigation_uses_upstream_order_instead_of_archive_or_stage_order():
    """Reference, log, style and state files never become document chapters."""
    entries = paper_proposal.default_sectioned_proposal("Topic", None)
    entries.extend([("project_context/instructions.md", b"# Reference only"), ("styles/style.typ", b"style")])
    chapters = paper_proposal.chapter_index(list(reversed(entries)))
    assert [item.id for item in chapters] == [f"S{number}" for number in range(1, 10)]
    assert [item.path for item in chapters] == [item[2] for item in paper_proposal.PROPOSAL_CHAPTERS]
    assert paper_proposal.proposal_entry_paths(entries) == [item.path for item in chapters]
    assert all(item.status == "not_started" for item in chapters)
    assert all(not item.has_content for item in chapters)


def test_saved_author_titles_and_upstream_statuses_are_projected_without_locks():
    """A chapter can be read, edited or rendered regardless of its draft status."""
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", None))
    state = json.loads(entries["proposal-state.json"])
    state["sections"]["S7"].update(file="chapters/methods.md", status="author_requested_revision")
    entries["proposal-state.json"] = json.dumps(state).encode()
    entries["chapters/methods.md"] = "# 作者选择的方法\n\nCurrent content.".encode()
    chapters = paper_proposal.chapter_index(list(entries.items()))
    method = next(item for item in chapters if item.id == "S7")
    assert method.path == "chapters/methods.md"
    assert method.title == "作者选择的方法"
    assert method.status == "author_requested_revision"
    assert method.has_content is True
    assert method.path in paper_proposal.proposal_entry_paths(list(entries.items()))


def test_unsafe_or_reference_section_aliases_cannot_escape_chapter_sources():
    """Untrusted state files cannot navigate outside source or into references."""
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", None))
    state = json.loads(entries["proposal-state.json"])
    state["sections"]["S1"]["file"] = "../outside.md"
    state["sections"]["S2"]["file"] = "project_context/private.md"
    entries["proposal-state.json"] = json.dumps(state).encode()
    entries["project_context/private.md"] = b"# Private reference"
    chapters = paper_proposal.chapter_index(list(entries.items()))
    assert not {"S1", "S2"}.intersection(item.id for item in chapters)
    assert all(not item.path.startswith(("..", "project_context")) for item in chapters)


def test_legacy_single_markdown_and_typst_projects_are_not_migrated():
    """Old document entry paths and original bytes remain authoritative."""
    entries = [("notes/application.md", b"# Existing proposal\n"), ("project_context/rules.md", b"Rules")]
    chapters = paper_proposal.chapter_index(entries, "notes/application.md")
    assert [(item.id, item.path, item.title) for item in chapters] == [
        ("document", "notes/application.md", "Existing proposal")
    ]
    assert paper_proposal.proposal_entry_paths(entries) == []
    assert chapters[0].has_content is False
    assert paper_proposal.chapter_index([("application.typ", b"= Existing proposal")], "application.typ") == []


def test_invalid_optional_state_does_not_hide_existing_upstream_chapters():
    """Chapter navigation still works when the assistant needs to repair its state."""
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", None))
    entries["proposal-state.json"] = b"{ incomplete"
    assert len(paper_proposal.chapter_index(list(entries.items()))) == 9


def test_typst_only_source_without_a_configured_entry_never_loads_markdown_metadata(monkeypatch):
    """Historical Typst sources need no object-store request for chapter labels."""
    storage = Mock()
    storage.download.side_effect = AssertionError("Legacy Typst must not fetch Markdown state")
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    source = PaperSourceVersion(
        workspace_id=uuid.uuid4(),
        version=1,
        source_kind="latex",
        filename="source.zip",
        object_key="test/legacy-typst.zip",
        content_hash="legacy",
        content_size=1,
        manifest=["main.typ"],
    )
    assert paper_proposal.source_chapter_index(source, "proposal.md") == []
    storage.download.assert_not_called()


def test_repeated_workspace_reads_cache_labels_only_and_invalidate_on_source_change(monkeypatch):
    """Polling a large reference ZIP does not repeatedly download its document."""
    paper_proposal._stored_chapter_index.cache_clear()
    entries = dict(paper_proposal.default_sectioned_proposal("Topic", None))

    def archive():
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w") as bundle:
            for name, content in entries.items():
                bundle.writestr(name, content)
            bundle.writestr("project_context/large-reference.txt", b"reference" * 1000)
        return body.getvalue()

    storage = Mock()
    storage.download.side_effect = lambda _key: archive()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    source = PaperSourceVersion(
        workspace_id=uuid.uuid4(),
        version=1,
        source_kind="latex",
        filename="paper-source.zip",
        object_key=f"test/{uuid.uuid4()}/source.zip",
        content_hash="first",
        content_size=1,
        manifest=[*entries, "project_context/large-reference.txt"],
    )
    first = paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)
    second = paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)
    assert first == second and storage.download.call_count == 1
    assert all(not item.has_content for item in first)
    first[0].title = "A caller must not mutate cached navigation"
    first[0].has_content = True
    first[0].status = "finalized"
    cached = paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)
    assert cached[0].title == "研究目的与意义"
    assert cached[0].has_content is False
    assert cached[0].status == "not_started"
    assert second[0].has_content is False
    assert storage.download.call_count == 1
    entries[paper_proposal.DEFAULT_PROPOSAL_ENTRY] = "# 修改后的研究目的\n\n实际的研究内容。\n".encode()
    source.content_hash = "second"
    changed = paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)
    assert changed[0].title == "修改后的研究目的"
    assert changed[0].has_content is True
    assert changed[0].status == "not_started"
    assert storage.download.call_count == 2
    changed[0].has_content = False
    assert paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)[0].has_content is True
    assert storage.download.call_count == 2
    entries[paper_proposal.DEFAULT_PROPOSAL_ENTRY] = b"# Cleared chapter\n\n<!-- Pending. -->\n"
    source.content_hash = "third"
    cleared = paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)
    assert cleared[0].has_content is False
    assert storage.download.call_count == 3
    entries[paper_proposal.DEFAULT_PROPOSAL_ENTRY] = b"# Chapter\n\nNew object body.\n"
    source.object_key = f"test/{uuid.uuid4()}/source.zip"
    assert paper_proposal.source_chapter_index(source, paper_proposal.DEFAULT_PROPOSAL_ENTRY)[0].has_content is True
    assert storage.download.call_count == 4
