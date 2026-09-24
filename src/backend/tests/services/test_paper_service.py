"""Paper workspace service contracts."""

import io
import json
import uuid
import zipfile

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from app import models
from app.core.db import engine
from app.schemas import paper as paper_schemas
from app.services import paper_service, paper_workflow, project_service
from tests.utils.user import create_random_user


@pytest.fixture(scope="module")
def db():
    """Provide a database session for paper source mutation tests."""
    with Session(engine) as session:
        yield session


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class _MemoryStorage:
    """Minimal object store used by mutable paper source tests."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def upload(self, key: str, data, **_kwargs) -> None:
        self.objects[key] = data.read() if hasattr(data, "read") else data

    def download(self, key: str, **_kwargs) -> bytes:
        return self.objects[key]

    def delete(self, key: str, **_kwargs) -> None:
        self.objects.pop(key, None)

    def delete_many(self, keys: list[str], **_kwargs) -> None:
        for key in keys:
            self.objects.pop(key, None)

    def list_objects(self, prefix: str, **_kwargs) -> list[str]:
        return [key for key in self.objects if key.startswith(prefix)]


def _paper_source(
    db: Session,
) -> tuple[models.User, models.PaperWorkspace, models.PaperSourceVersion]:
    user = create_random_user(db)
    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(title="Mutable paper", mode="manuscript"),
    )
    source = models.PaperSourceVersion(
        workspace_id=workspace.id,
        version=1,
        source_kind="markdown",
        filename="paper.md",
        object_key=f"paper/{user.id}/{workspace.id}/sources/current/paper.md",
        content_hash="old",
        content_size=5,
        manifest=["paper.md"],
    )
    db.add(source)
    db.flush()
    workspace.active_source_version_id = source.id
    db.add(workspace)
    db.commit()
    db.refresh(source)
    db.refresh(workspace)
    return user, workspace, source


def test_proposal_foundation_rejects_pre_cloudcli_context_shape() -> None:
    """Require native CloudCLI publications to use the current block contract."""
    with pytest.raises(ValidationError):
        paper_schemas.ProposalFoundation.model_validate(
            {"research_goal": "A field-based context from the removed runtime"}
        )


def test_research_workspace_mode_is_persisted_at_creation(db: Session) -> None:
    """Keep the selected research mode stable for the workspace lifetime."""
    user = create_random_user(db)

    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Algorithm research",
            mode="algorithm",
        ),
    )

    assert workspace.mode == "algorithm"


def test_conversation_preferences_are_saved_without_changing_proposal_brief(db: Session) -> None:
    """Persist explanatory preferences independently of source context."""
    user = create_random_user(db)
    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Proposal preferences",
            mode="proposal",
            description="Existing research brief",
        ),
    )

    updated = paper_service.update_workspace(
        db,
        user,
        workspace.id,
        paper_schemas.PaperWorkspaceUpdate(
            conversation_preferences=paper_schemas.PaperConversationPreferences(
                reply_language="zh",
                additional_guidance="  请简要解释下一步。  ",
            )
        ),
    )

    assert updated.description == "Existing research brief"
    assert updated.conversation_preferences == {
        "reply_language": "zh",
        "additional_guidance": "请简要解释下一步。",
    }
    detail = paper_service.get_workspace_detail(db, user, workspace.id)
    assert detail.conversation_preferences.reply_language == "zh"


def test_research_workspace_is_not_a_normal_project(db: Session) -> None:
    """Keep research workspaces out of the independent project manager."""
    user = create_random_user(db)

    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Independent research workspace",
            mode="manuscript",
        ),
    )
    projects, total = project_service.list_projects(db, user.id, 0, 100)

    assert not hasattr(workspace, "project_id")
    assert projects == []
    assert total == 0
    assert "project_id" not in paper_schemas.PaperWorkspaceCreate.model_fields
    assert "project_id" not in paper_schemas.PaperWorkspaceSummary.model_fields


def test_reviewer_reports_are_mirrored_as_read_only_runtime_context(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose owned active-source reviews with stable IDs and paths."""
    user, workspace, source = _paper_source(db)
    review_content = b"The runtime comparison needs clarification."
    review = models.PaperReview(
        workspace_id=workspace.id,
        source_version_id=source.id,
        source_system="manual",
        reviewer_label="R1",
        title="Main review",
        object_key=f"paper/{user.id}/{workspace.id}/reviews/r1/review.md",
        content_hash="review",
    )
    second_review = models.PaperReview(
        workspace_id=workspace.id,
        source_version_id=source.id,
        source_system="manual",
        reviewer_label="R1",
        title="Second review with the same label",
        object_key=f"paper/{user.id}/{workspace.id}/reviews/r1-duplicate/review.md",
        content_hash="second-review",
    )
    db.add(review)
    db.add(second_review)
    db.commit()
    storage = _MemoryStorage(
        {
            review.object_key: review_content,
            second_review.object_key: b"The notation needs clarification.",
        }
    )
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    reviews_root = paper_service.sync_paper_workspace_reviews(db, workspace, source)

    index = json.loads((reviews_root / "index.json").read_text(encoding="utf-8"))
    assert index["reviews"] == [
        {
            "review_id": str(review.id),
            "reviewer_id": str(review.id),
            "display_label": "R1",
            "title": "Main review",
            "source_system": "manual",
            "path": f"/workspace/.research/reviews/{review.id}.md",
        },
        {
            "review_id": str(second_review.id),
            "reviewer_id": str(second_review.id),
            "display_label": "R1",
            "title": "Second review with the same label",
            "source_system": "manual",
            "path": f"/workspace/.research/reviews/{second_review.id}.md",
        },
    ]
    assert (reviews_root / f"{review.id}.md").read_bytes() == review_content
    assert index["reviews"][0]["reviewer_id"] != index["reviews"][1]["reviewer_id"]


def test_each_explicit_algorithm_task_has_an_independent_project(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a task project without converting or binding the research workspace."""
    from app.services import task_service

    user, workspace, source = _paper_source(db)
    workspace.mode = models.ResearchWorkspaceMode.ALGORITHM.value
    db.add(workspace)
    package_prefix = f"paper/{user.id}/{workspace.id}/proposals/package"
    package_files = {
        "config.yaml": b"""project_name: exported-algorithm
evaluator:
  type: custom
  module: evaluator.py:Evaluator
evolution:
  type: island_ga
planner:
  type: llm_evolution
coder:
  type: llm
providers: []
""",
        "debug_run.py": b"print('ok')\n",
        "test_evaluator.py": b"print('ok')\n",
        "evaluator.py": b"class Evaluator:\n    pass\n",
        "algorithm/solve.py": b"# EVOLVE_START\npass\n# EVOLVE_END\n",
        "blueprint_meta.json": b'{"validation_status": "passed"}',
    }
    storage = _MemoryStorage(
        {f"{package_prefix}/{relative}": content for relative, content in package_files.items()}
    )
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    proposal = models.PaperAlgorithmProposal(
        workspace_id=workspace.id,
        source_version_id=source.id,
        title="Exported algorithm",
        problem_statement="Find a better construction.",
        algorithm_design="Build and evaluate candidate constructions.",
        package_object_prefix=package_prefix,
        package_manifest=list(package_files),
        validation_report={"status": "passed", "project_name": "exported-algorithm"},
    )
    db.add(proposal)
    db.commit()

    def create_task(_db, task_in, _current_user):
        task = models.Task(
            name=task_in.name,
            description=task_in.description,
            project_id=task_in.project_id,
            input_args=task_in.input_args,
            ai_built=task_in.ai_built,
        )
        task.input_data_path = f"tasks/{task.id}/imported"
        _db.add(task)
        _db.commit()
        _db.refresh(task)
        return task

    monkeypatch.setattr(task_service, "create_task", create_task)

    [link] = paper_service.create_proposal_tasks(
        db,
        user,
        workspace.id,
        paper_schemas.PaperProposalTaskCreateRequest(
            proposal_ids=[proposal.id],
            language="en",
        ),
    )

    task = db.get(models.Task, link.task_id)
    assert task is not None
    assert link.project_id == task.project_id
    project = db.get(models.Project, task.project_id)
    assert project is not None
    assert project.name == proposal.title
    assert task.input_args["memory"]["enabled"] is False
    assert task.ai_built is False
    assert (
        storage.objects[f"{task.input_data_path}/exported-algorithm/config.yaml"]
        == package_files["config.yaml"]
    )
    assert not hasattr(workspace, "project_id")


def test_proposal_workspace_starts_with_an_editable_typst_document(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start description-driven proposals without requiring a source upload."""
    user = create_random_user(db)
    storage = _MemoryStorage({})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Description-first proposal",
            description="Study robust optimization under uncertain constraints.",
            mode="proposal",
        ),
    )

    assert workspace.active_source_version_id is not None
    assert workspace.proposal_entry_path == "proposal.typ"
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert source is not None
    source_entries = dict(paper_service._source_entries(source))
    assert source.manifest == sorted(
        [
            "proposal.typ",
            paper_service._PROPOSAL_STYLE_PATH,
            *paper_service._PROPOSAL_SECTION_SOURCES,
        ]
    )
    proposal_source = source_entries["proposal.typ"].decode()
    style_source = source_entries[paper_service._PROPOSAL_STYLE_PATH].decode()
    assert '#import "styles/nsfc-proposal.typ": nsfc-proposal' in proposal_source
    assert "#show: nsfc-proposal.with(title: project_title)" in proposal_source
    assert '#let project_title = "Description-first proposal"' in proposal_source
    assert "Study robust optimization under uncertain constraints." in proposal_source
    assert 'paper: "a4"' in style_source
    assert "top: 2.78cm" in style_source
    assert "Readon/NSFC-application-template-typst" in style_source
    assert "@preview" not in style_source
    assert "报告正文" in style_source
    assert "（一）立项依据：" in proposal_source
    assert "（二）研究内容：" in proposal_source
    assert "（三）研究基础：" in proposal_source
    assert '#include "sections/04-objectives.typ"' in proposal_source
    assert proposal_source.index('#include "sections/07-plan.typ"') < proposal_source.index("（三）研究基础：")
    assert proposal_source.index("（三）研究基础：") < proposal_source.index('#include "sections/06-feasibility.typ"')
    assert "== 研究目标与总体思路" in source_entries["sections/04-objectives.typ"].decode()


def test_proposal_assembly_preserves_existing_includes_without_duplicates() -> None:
    """Add only missing staged sections when an entry already owns an include."""
    entries = paper_service.ensure_proposal_assembly(
        [
            (
                "application/main.typ",
                b'#include "../sections/01-rationale.typ"\n',
            )
        ],
        "application/main.typ",
    )

    source_entries = dict(entries)
    entry = source_entries["application/main.typ"].decode()
    assert entry.count('#include "../sections/01-rationale.typ"') == 1
    assert '#include "../sections/07-plan.typ"' in entry
    assert '#include "../sections/06-feasibility.typ"' in entry
    assert set(paper_service._PROPOSAL_SECTION_SOURCES).issubset(source_entries)


def test_proposal_assembly_connects_generated_bibliography() -> None:
    """Render citations once the literature stage has produced a bibliography."""
    entries = paper_service.ensure_proposal_assembly(
        [
            ("application/main.typ", b"= Proposal\n"),
            (
                "references.bib",
                b"@misc{verified2026, title={Verified source}, year={2026}}\n",
            ),
        ],
        "application/main.typ",
    )

    entry = dict(entries)["application/main.typ"].decode()
    assert '#bibliography("../references.bib")' in entry

    repeated = paper_service.ensure_proposal_assembly(entries, "application/main.typ")
    assert dict(repeated)["application/main.typ"].decode().count("#bibliography(") == 1


def test_user_can_edit_proposal_source_independently_of_the_selected_stage(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep agent stage write boundaries from restricting direct user edits."""
    user = create_random_user(db)
    storage = _MemoryStorage({})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="User-managed proposal",
            description="Draft a proposal from this description.",
            mode="proposal",
        ),
    )

    updated = paper_service.update_source_file(
        db,
        user,
        workspace.active_source_version_id,
        paper_schemas.PaperSourceFileUpdateRequest(
            path="proposal.typ",
            content="= User edit\n",
            workflow_stage="literature",
        ),
    )

    assert dict(paper_service._source_entries(updated))["proposal.typ"] == b"= User edit\n"


def test_final_review_entry_edit_only_stales_final_review(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the selected owner to disambiguate an entry shared with formatting."""
    user = create_random_user(db)
    storage = _MemoryStorage({})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Final assembly edit",
            description="Keep content stages valid after an entry-only correction.",
            mode="proposal",
        ),
    )
    workflow = paper_workflow.get_research_workflow("proposal")
    workspace.proposal_stage_states = {stage: {"status": "ready"} for stage in workflow.stages}
    db.add(workspace)
    db.commit()

    paper_service.update_source_file(
        db,
        user,
        workspace.active_source_version_id,
        paper_schemas.PaperSourceFileUpdateRequest(
            path="proposal.typ",
            content="= Final assembly edit\n",
            workflow_stage="final_review",
        ),
    )

    db.refresh(workspace)
    assert workspace.proposal_stage_states["formatting"]["status"] == "ready"
    assert workspace.proposal_stage_states["innovation_plan"]["status"] == "ready"
    assert workspace.proposal_stage_states["foundation_feasibility"]["status"] == "ready"
    assert workspace.proposal_stage_states["final_review"]["status"] == "stale"


def test_system_generated_proposal_files_cannot_be_deleted(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect the generated proposal assembly while retaining direct edits."""
    user = create_random_user(db)
    storage = _MemoryStorage({})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Protected proposal",
            description="Keep generated sections connected.",
            mode="proposal",
        ),
    )

    with pytest.raises(HTTPException, match="cannot be deleted"):
        paper_service.delete_source_path(
            db,
            user,
            workspace.active_source_version_id,
            paper_schemas.PaperSourcePathDeleteRequest(path="sections/01-rationale.typ"),
        )


def test_user_proposal_reference_can_be_deleted(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow users to remove reference files outside generated workflow paths."""
    user = create_random_user(db)
    storage = _MemoryStorage({})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    workspace = paper_service.create_workspace(
        db,
        user,
        paper_schemas.PaperWorkspaceCreate(
            title="Proposal references",
            description="Use removable user material.",
            mode="proposal",
        ),
    )
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert source is not None
    source = paper_service._persist_source_entries(
        db,
        workspace,
        source,
        [*paper_service._source_entries(source), ("materials/notes.md", b"# Notes\n")],
        change_summary="Added user reference",
    )

    updated = paper_service.delete_source_path(
        db,
        user,
        source.id,
        paper_schemas.PaperSourcePathDeleteRequest(path="materials/notes.md"),
    )

    assert updated is not None
    assert "materials/notes.md" not in updated.manifest


def test_typst_source_is_packaged_as_an_editable_bundle() -> None:
    """Accept a standalone Typst proposal without flattening its source path."""
    validated = paper_service.validate_paper_source_batch([("proposal.typ", b'#set page(paper: "a4")\n= Proposal\n')])

    assert validated.kind == "source_bundle"
    assert validated.manifest == ["proposal.typ"]


def test_proposal_source_bundle_accepts_optional_project_context() -> None:
    """Keep reference materials beside, but separate from, proposal source files."""
    validated = paper_service.validate_paper_source_batch(
        [
            ("proposal.typ", b"= Proposal\n"),
            ("project_context/funder-rules.pdf", b"%PDF-1.7"),
            ("project_context/writing-notes.md", b"# Notes\n"),
        ]
    )

    assert validated.manifest == [
        "project_context/funder-rules.pdf",
        "project_context/writing-notes.md",
        "proposal.typ",
    ]
    with zipfile.ZipFile(io.BytesIO(validated.data)) as archive:
        assert archive.read("project_context/writing-notes.md") == b"# Notes\n"


def test_source_bundle_preserves_a_managed_empty_directory() -> None:
    """Represent a user-created empty folder without exposing a fake document."""
    validated = paper_service.validate_paper_source_batch(
        [
            ("proposal.typ", b"= Proposal\n"),
            ("figures/.llm4ad-directory", b"managed directory\n"),
        ]
    )

    assert "figures/.llm4ad-directory" in validated.manifest


def test_proposal_formatting_can_create_a_typst_entry_from_source_material() -> None:
    """Start proposal drafting even when the uploaded material is not Typst yet."""
    assert paper_service._proposal_formatting_entry(["notes.md"], None) == "proposal.typ"
    assert (
        paper_service._proposal_formatting_entry(["notes.md", "application/main.typ"], None) == "application/main.typ"
    )
    assert (
        paper_service._proposal_formatting_entry(["notes.md"], "application/proposal.typ") == "application/proposal.typ"
    )

    with pytest.raises(HTTPException, match="Typst"):
        paper_service._proposal_formatting_entry(["notes.md"], "notes.md")


def test_paper_source_is_mirrored_into_the_project_workspace(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose RustFS-backed source files to the reusable project container."""
    user, workspace, source = _paper_source(db)
    storage = _MemoryStorage({source.object_key: b"# Persistent source\n"})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    source_root = paper_service.sync_paper_workspace_source(workspace, source)

    assert source_root == paper_service.paper_workspace_source_dir(user.id, workspace.id)
    assert (source_root / "paper.md").read_bytes() == b"# Persistent source\n"


def test_paper_source_export_uses_an_authenticated_backend_download(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep private object-store addresses out of browser-facing exports."""
    user, _workspace, source = _paper_source(db)
    content = b"# Private source\n"
    storage = _MemoryStorage({source.object_key: content})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)

    exported = paper_service.export_source_version(db, user, source.id)
    downloaded, filename = paper_service.download_source_version(
        db,
        user,
        source.id,
    )

    assert exported.url == (f"/api/v1/llm4ad/papers/source-versions/{source.id}/download")
    assert downloaded == content
    assert filename == "paper.md"


def test_research_workflow_definition_is_owned_by_the_backend() -> None:
    """Expose the backend-owned AutoRebuttal workflow and shared boundaries."""
    definition = paper_workflow.get_research_workflow("manuscript")

    assert definition.mode == "manuscript"
    assert definition.available is True
    assert definition.stages == ("rebuttal_baseline", "autorebuttal", "ac_summary")
    assert definition.skills_by_run_kind["rebuttal_baseline"] == (
        "rebuttal-baseline",
        "research-stage-publication",
    )
    assert definition.skills_by_run_kind["autorebuttal"] == (
        "autorebuttal",
        "research-stage-publication",
    )
    assert definition.skills_by_run_kind["ac_summary"] == (
        "ac-summary",
        "research-stage-publication",
    )
    assert definition.prerequisites_by_run_kind["autorebuttal"] == ("rebuttal_baseline",)
    assert definition.prerequisites_by_run_kind["ac_summary"] == ("autorebuttal",)
    assert all(not paths for paths in definition.writable_paths_by_run_kind.values())
    assert "read-only evidence" in definition.prompt_preamble


def test_proposal_workflow_defines_eight_isolated_stages() -> None:
    """Expose the complete backend-controlled proposal workflow."""
    definition = paper_workflow.get_research_workflow("proposal")

    assert definition.available is True
    assert definition.stages == (
        "formatting",
        "literature",
        "rationale",
        "objectives",
        "methods",
        "innovation_plan",
        "foundation_feasibility",
        "final_review",
    )
    assert definition.skills_by_run_kind["proposal_formatting"] == (
        "proposal-foundation-layout",
        "typst-author",
        "research-stage-publication",
    )
    assert definition.skills_by_run_kind["proposal_literature"] == (
        "proposal-literature-evidence",
        "research-stage-publication",
    )
    assert definition.skills_by_run_kind["proposal_innovation_plan"] == (
        "proposal-innovation-plan",
        "research-stage-publication",
    )
    assert definition.skills_by_run_kind["proposal_foundation_feasibility"] == (
        "proposal-foundation-feasibility",
        "research-stage-publication",
    )
    assert definition.prerequisites_by_run_kind["proposal_final_review"] == (
        "innovation_plan",
        "foundation_feasibility",
    )
    assert definition.writable_paths_by_run_kind["proposal_methods"] == ("sections/05-methods.typ",)
    assert definition.dependent_stages("innovation_plan") == ("final_review",)
    assert definition.dependent_stages("methods") == (
        "innovation_plan",
        "foundation_feasibility",
        "final_review",
    )
    assert "edit only" in definition.prompt_preamble.lower()
    assert "Project documents are optional context" in definition.prompt_preamble
    assert "Read other sections" not in definition.prompt_preamble


def test_algorithm_workflow_is_one_conversational_discovery_stage() -> None:
    """Inject discovery and project-building guidance without a visible pipeline."""
    definition = paper_workflow.get_research_workflow("algorithm")

    assert definition.available is True
    assert definition.stages == ("discovery",)
    assert definition.skills_by_run_kind["algorithm_discovery"] == (
        "algorithm-discovery",
        "llm4ad-task-builder",
        "research-stage-publication",
    )
    assert definition.prerequisites_by_run_kind["algorithm_discovery"] == ()
    assert definition.writable_paths_by_run_kind["algorithm_discovery"] == ()
    assert "single conversational AutoDiscovery workspace" in definition.prompt_preamble


def test_editing_a_paper_file_updates_the_current_source_in_place(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, workspace, source = _paper_source(db)
    storage = _MemoryStorage({source.object_key: b"draft"})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)

    updated = paper_service.update_source_file(
        db,
        user,
        source.id,
        paper_schemas.PaperSourceFileUpdateRequest(
            path="paper.md",
            content="# Updated working paper\n",
        ),
    )

    sources = list(
        db.exec(select(models.PaperSourceVersion).where(models.PaperSourceVersion.workspace_id == workspace.id)).all()
    )
    assert updated.id == source.id
    assert [item.id for item in sources] == [source.id]
    assert storage.objects[source.object_key] == b"# Updated working paper\n"


def test_deleting_the_last_paper_file_clears_the_current_source(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, workspace, source = _paper_source(db)
    storage = _MemoryStorage({source.object_key: b"draft"})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)

    updated = paper_service.delete_source_path(
        db,
        user,
        source.id,
        paper_schemas.PaperSourcePathDeleteRequest(path="paper.md"),
    )

    db.refresh(workspace)
    assert updated is None
    assert workspace.active_source_version_id is None
    assert db.get(models.PaperSourceVersion, source.id) is None
    assert source.object_key not in storage.objects


def test_validate_paper_source_accepts_markdown_without_rewriting_content() -> None:
    content = b"# Method\n\nPreserve the exact constraints.\n"

    result = paper_service.validate_paper_source("paper.md", content)

    assert result.kind == "markdown"
    assert result.data == content
    assert result.manifest == ["paper.md"]


def test_validate_paper_source_rejects_zip_path_traversal() -> None:
    payload = _zip_bytes({"../secret.tex": b"secret", "main.tex": b"\\documentclass{article}"})

    with pytest.raises(HTTPException, match="unsafe path") as exc_info:
        paper_service.validate_paper_source("paper.zip", payload)

    assert exc_info.value.status_code == 400


def test_validate_paper_source_rejects_pdf_with_mineru_guidance() -> None:
    with pytest.raises(HTTPException, match="MinerU") as exc_info:
        paper_service.validate_paper_source("paper.pdf", b"%PDF-1.7")

    assert exc_info.value.status_code == 400


def test_validate_paper_source_batch_preserves_nested_paths() -> None:
    result = paper_service.validate_paper_source_batch(
        [
            ("paper/main.tex", b"\\documentclass{article}"),
            ("paper/sections/method.tex", b"Method"),
            ("paper/figures/result.pdf", b"%PDF-1.7"),
        ]
    )

    assert result.kind == "source_bundle"
    assert result.manifest == [
        "paper/figures/result.pdf",
        "paper/main.tex",
        "paper/sections/method.tex",
    ]
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("paper/sections/method.tex") == b"Method"


def test_validate_paper_source_batch_rejects_unsafe_relative_path() -> None:
    with pytest.raises(HTTPException, match="unsafe path") as exc_info:
        paper_service.validate_paper_source_batch([("../main.tex", b"\\documentclass{article}")])

    assert exc_info.value.status_code == 400


def test_expand_upload_entries_unpacks_latex_zip_for_tree_merge() -> None:
    payload = _zip_bytes(
        {
            "main.tex": b"\\documentclass{article}",
            "sections/method.tex": b"Method",
        }
    )

    entries = paper_service._expand_upload_entries(
        [("paper.zip", payload)],
        require_primary_source=False,
    )

    assert dict(entries)["sections/method.tex"] == b"Method"


def test_expand_upload_entries_allows_assets_when_source_already_exists() -> None:
    entries = paper_service._expand_upload_entries(
        [("figures/result.png", b"image")],
        require_primary_source=False,
    )

    assert entries == [("figures/result.png", b"image")]


def test_local_paper_evolution_tasks_disable_memory() -> None:
    """Keep paper-derived task packages local to their selected paper target."""
    input_args = {
        "memory": {
            "enabled": True,
            "include_user_memory": True,
            "include_project_memory": True,
            "include_task_memory": True,
        }
    }

    updated = paper_service._disable_memory_for_local_paper_task(input_args)

    assert updated["memory"] == {
        "enabled": False,
        "include_user_memory": False,
        "include_project_memory": False,
        "include_task_memory": False,
    }


def test_boundary_contract_contains_only_one_language_view() -> None:
    """Keep one boundary representation in the requested interface language."""
    fields = paper_schemas.PaperBoundaryDraft.model_fields

    assert "sections" in fields
    assert "paper_goal" not in fields
    assert "contributions" not in fields
    assert "preferred_language" not in fields
    assert "localized_views" not in fields


def test_deleting_workspace_removes_its_native_runtime_directory(
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean user-scoped local runtimes together with workspace metadata."""
    user, workspace, source = _paper_source(db)
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind="boundary_analysis",
        status="ready",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    project_workspace = paper_service.paper_workspace_root(user.id, workspace.id)
    project_workspace.mkdir(parents=True)
    (project_workspace / ".cloudcli").mkdir()
    storage = _MemoryStorage({source.object_key: b"draft"})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    removed_containers: list[uuid.UUID] = []
    revoked_runs: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.services.paper_workspace_runtime.stop_paper_workspace_container",
        lambda workspace_id, *, remove=False: removed_containers.append(workspace_id) or remove,
    )
    monkeypatch.setattr(
        "app.services.credential_broker.revoke_task_tokens",
        revoked_runs.append,
    )

    paper_service.delete_workspace(db, user, workspace.id)

    assert not project_workspace.exists()
    assert removed_containers == [workspace.id]
    assert revoked_runs == [run_id]


def test_metric_selection_keeps_reviewer_baseline_locked() -> None:
    baseline = paper_schemas.PaperMetricDraft(
        key="reviewer_soundness",
        title="Soundness",
        description="Address the reviewer soundness concern.",
        source="reviewer_baseline",
        selected=True,
        locked=True,
        weight=1,
    )
    novelty = paper_schemas.PaperMetricDraft(
        key="novelty",
        title="Novelty",
        description="Evaluate novelty beyond the baseline.",
        source="model_suggested",
        selected=False,
        locked=False,
        weight=0.5,
    )

    selected = paper_service.apply_metric_selection(
        [baseline, novelty],
        [paper_schemas.PaperMetricSelection(key="novelty", selected=True, weight=0.8)],
    )

    assert selected[0].selected is True
    assert selected[0].locked is True
    assert selected[1].selected is True
    assert selected[1].weight == 0.8


def test_metric_selection_cannot_disable_reviewer_baseline() -> None:
    baseline = paper_schemas.PaperMetricDraft(
        key="reviewer_clarity",
        title="Clarity",
        description="Address the reviewer clarity concern.",
        source="reviewer_baseline",
        selected=True,
        locked=True,
        weight=1,
    )

    with pytest.raises(HTTPException, match="Reviewer baseline") as exc_info:
        paper_service.apply_metric_selection(
            [baseline],
            [paper_schemas.PaperMetricSelection(key="reviewer_clarity", selected=False, weight=1)],
        )

    assert exc_info.value.status_code == 400


def test_judge_aggregation_preserves_baseline_and_optional_dimensions() -> None:
    result = paper_service.aggregate_judge_scores(
        [
            {
                "baseline": {"review_soundness": 0.8, "review_clarity": 0.6},
                "optional": {"novelty": 0.9},
            },
            {
                "baseline": {"review_soundness": 0.6, "review_clarity": 0.8},
                "optional": {"novelty": 0.7},
            },
        ]
    )

    assert result.baseline == {"review_soundness": 0.7, "review_clarity": 0.7}
    assert result.optional == {"novelty": 0.8}
    assert result.overall == pytest.approx(0.7333333333)
    assert result.disagreement > 0


def test_judge_aggregation_applies_user_selected_metric_weights() -> None:
    result = paper_service.aggregate_judge_scores(
        [
            {
                "baseline": {"review_soundness": 0.5},
                "optional": {"novelty": 1.0},
            }
        ],
        weights={"review_soundness": 3.0, "novelty": 1.0},
    )

    assert result.overall == pytest.approx(0.625)


def test_json_dimensions_become_locked_reviewer_metrics() -> None:
    metrics = paper_service.derive_reviewer_baseline_metrics(
        '{"dimensions":[{"name":"Soundness","feedback":"The proof needs a missing case."}]}'
    )

    assert len(metrics) == 1
    assert metrics[0].source == "reviewer_baseline"
    assert metrics[0].locked is True
    assert metrics[0].selected is True
    assert "missing case" in metrics[0].description


def test_plain_reviewer_feedback_still_creates_a_mandatory_baseline() -> None:
    metrics = paper_service.derive_reviewer_baseline_metrics(
        "The method description is incomplete.", reviewer_label="Reviewer A"
    )

    assert metrics[0].key.startswith("review_")
    assert metrics[0].source == "reviewer_baseline"
    assert metrics[0].locked is True


def test_editing_reviewer_feedback_replaces_its_locked_baseline(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, workspace, source = _paper_source(db)
    storage = _MemoryStorage({source.object_key: b"draft"})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    review = paper_service.attach_reviewer_feedback(
        db,
        user,
        source.id,
        paper_schemas.PaperReviewCreate(
            content="The proof is incomplete.",
            reviewer_label="Reviewer A",
            title="Initial review",
        ),
    )

    updated = paper_service.update_reviewer_feedback(
        db,
        user,
        review.id,
        paper_schemas.PaperReviewUpdate(
            content="The experiments need an additional ablation.",
            reviewer_label="Reviewer B",
            title="Revised review",
        ),
    )

    metrics = list(
        db.exec(select(models.PaperEvaluationMetric).where(models.PaperEvaluationMetric.review_id == review.id)).all()
    )
    assert updated.title == "Revised review"
    assert updated.reviewer_label == "Reviewer B"
    assert storage.objects[review.object_key] == b"The experiments need an additional ablation."
    assert len(metrics) == 1
    assert metrics[0].description == "The experiments need an additional ablation."
    assert metrics[0].provenance == ["Reviewer B"]


def test_deleting_reviewer_feedback_removes_derived_state(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete one review, its metrics, and invalidate generated rebuttal state."""
    user, workspace, source = _paper_source(db)
    storage = _MemoryStorage({source.object_key: b"draft"})
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    review = paper_service.attach_reviewer_feedback(
        db,
        user,
        source.id,
        paper_schemas.PaperReviewCreate(
            content="The proof is incomplete.",
            reviewer_label="Reviewer A",
            title="Initial review",
        ),
    )
    workspace.proposal_stage_states = {
        "rebuttal_baseline": {"status": "ready"},
        "autorebuttal": {"status": "ready"},
    }
    db.add(workspace)
    db.commit()
    object_key = review.object_key

    paper_service.delete_reviewer_feedback(db, user, review.id)

    db.expire_all()
    refreshed_workspace = db.get(models.PaperWorkspace, workspace.id)
    assert db.get(models.PaperReview, review.id) is None
    assert (
        db.exec(select(models.PaperEvaluationMetric).where(models.PaperEvaluationMetric.review_id == review.id)).first()
        is None
    )
    assert object_key not in storage.objects
    assert refreshed_workspace is not None
    assert refreshed_workspace.proposal_stage_states["rebuttal_baseline"]["status"] == "stale"
    assert refreshed_workspace.proposal_stage_states["autorebuttal"]["status"] == "stale"


def test_judge_payload_rejects_scores_outside_zero_to_one() -> None:
    with pytest.raises(ValidationError):
        paper_schemas.PaperJudgeScorePayload(
            baseline={"review_soundness": 1.2},
            rationale="Too high.",
        )
