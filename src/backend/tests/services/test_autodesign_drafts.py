"""Exercise canonical draft reconciliation without a model or object-store service."""

import yaml

from app.services.autodesign_drafts import draft_state, synchronize_draft


def test_inputs_merge_changes_without_replacing_agent_files(tmp_path):
    """Manual additions, removals and modifications retain independent Agent work."""
    draft = tmp_path / "draft"
    draft.mkdir()
    config = {"random_seed": 1, "evolution": {"max_generations": 3}}
    (draft / "config.yaml").write_text(yaml.safe_dump(config))
    (draft / "solver.py").write_bytes(b"old")
    (draft / "obsolete.txt").write_bytes(b"remove")
    state = tmp_path / "private" / "state.json"
    source = {"solver.py": b"old", "obsolete.txt": b"remove"}
    synchronize_draft(draft, state, config, "v1", lambda: source, initialize=True)
    (draft / "agent-notes.txt").write_bytes(b"keep")
    updated = {"solver.py": b"new", "data.txt": b"new-data"}
    assert synchronize_draft(draft, state, config, "v2", lambda: updated) == []
    assert (draft / "solver.py").read_bytes() == b"new"
    assert (draft / "data.txt").read_bytes() == b"new-data"
    assert not (draft / "obsolete.txt").exists()
    assert (draft / "agent-notes.txt").read_bytes() == b"keep"


def test_conflicting_file_changes_remain_until_explicit_resolution(tmp_path):
    """A second context refresh must not silently forget an unresolved conflict."""
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "config.yaml").write_text("random_seed: 1\n")
    (draft / "solver.py").write_bytes(b"original")
    state = tmp_path / "private" / "state.json"
    synchronize_draft(draft, state, {"random_seed": 1}, "v1", lambda: {"solver.py": b"original"}, initialize=True)
    (draft / "solver.py").write_bytes(b"agent-edit")
    for _ in range(2):
        assert synchronize_draft(draft, state, {"random_seed": 1}, "v2", lambda: {"solver.py": b"manual-edit"}) == [
            "file:solver.py"
        ]
        assert (draft / "solver.py").read_bytes() == b"agent-edit"
    assert (
        synchronize_draft(
            draft, state, {"random_seed": 1}, "v2", lambda: {"solver.py": b"manual-edit"}, resolution="use_task"
        )
        == []
    )
    assert (draft / "solver.py").read_bytes() == b"manual-edit"


def test_legacy_draft_does_not_silently_override_current_parameters(tmp_path):
    """An unknown historical baseline requires resolving different existing values."""
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "config.yaml").write_text("random_seed: 17\n")
    (draft / "solver.py").write_bytes(b"agent-edit")
    state = tmp_path / "private" / "state.json"
    for _ in range(2):
        conflicts = synchronize_draft(draft, state, {"random_seed": 29}, "v1", lambda: {"solver.py": b"manual-edit"})
        assert conflicts == ["config.random_seed", "file:solver.py"]
    assert (
        synchronize_draft(
            draft, state, {"random_seed": 29}, "v1", lambda: {"solver.py": b"manual-edit"}, resolution="keep_draft"
        )
        == []
    )
    assert yaml.safe_load((draft / "config.yaml").read_text())["random_seed"] == 17
    assert (draft / "solver.py").read_bytes() == b"agent-edit"
    assert draft_state(state)["config"]["random_seed"] == 29


def test_unchanged_canonical_revision_does_not_redownload_inputs(tmp_path):
    """Frequent context refreshes reuse fingerprints instead of reading all objects."""
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "config.yaml").write_text("random_seed: 1\n")
    state = tmp_path / "private" / "state.json"
    synchronize_draft(draft, state, {"random_seed": 1}, "v1", dict, initialize=True)

    def unexpected_download():
        raise AssertionError("Unchanged task triggered an object download")

    assert synchronize_draft(draft, state, {"random_seed": 1}, "v1", unexpected_download) == []


def test_racing_editor_write_is_retained_before_sync_replaces_a_file(tmp_path, monkeypatch):
    """An edit arriving during synchronization must become a conflict, not be lost."""
    import pytest
    from fastapi import HTTPException

    from app.services import autodesign_drafts as drafts

    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "config.yaml").write_text("random_seed: 1\n")
    state = tmp_path / "private" / "state.json"
    synchronize_draft(draft, state, {"random_seed": 1}, "v1", dict, initialize=True)
    original_write = drafts._write

    def competing_write(path, content, owner, expected=drafts._MISSING):
        if path == draft / "config.yaml":
            path.write_text("random_seed: 99\n")
        return original_write(path, content, owner, expected)

    monkeypatch.setattr(drafts, "_write", competing_write)
    with pytest.raises(HTTPException) as error:
        synchronize_draft(draft, state, {"random_seed": 2}, "v2", dict)
    assert error.value.status_code == 409
    assert yaml.safe_load((draft / "config.yaml").read_text())["random_seed"] == 99
    assert draft_state(state)["revision"] == "v1"
