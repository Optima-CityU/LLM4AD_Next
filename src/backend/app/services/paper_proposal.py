"""Upstream Markdown chapter organization and read-only authoring projections."""

from __future__ import annotations

import io
import json
import re
import zipfile
from functools import lru_cache
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.schemas.paper import PaperProposalChapter
from app.services.paper_archives import safe_archive_path

if TYPE_CHECKING:
    from app.models import PaperSourceVersion

# Preserve the upstream section identities and filenames independently of the
# platform's historical stage adapters. These are document chapters, not gates.
PROPOSAL_CHAPTERS = (
    ("S1", "研究目的与意义", "1-研究目的与意义.md"),
    ("S2", "国内外研究现状", "2-国内外研究现状.md"),
    ("S3", "应用前景与学术价值", "3-应用前景与学术价值.md"),
    ("S4", "参考文献", "4-参考文献.md"),
    ("S5", "研究基础与条件", "5-研究基础与条件.md"),
    ("S6", "研究目标与内容", "6-研究目标与内容.md"),
    ("S7", "研究方法", "7-研究方法.md"),
    ("S8", "可行性分析", "8-可行性分析.md"),
    ("S9", "创新点与计划", "9-创新点与计划.md"),
)
DEFAULT_PROPOSAL_ENTRY = PROPOSAL_CHAPTERS[0][2]


def default_sectioned_proposal(title: str, description: str | None) -> list[tuple[str, bytes]]:
    """Initialize upstream chapter sources without inventing any proposal claims.

    Args:
        title: Author-supplied project title, retained in project information.
        description: Author brief retained verbatim for the assistant's intake.

    Returns:
        Nine Markdown chapters plus the upstream state and writing log files.
        The renderer's existing factory supplies the editable presentation style.
    """
    entries = [
        (path, f"# {heading}\n\n<!-- 待作者与助手共同撰写。 -->\n".encode())
        for _identifier, heading, path in PROPOSAL_CHAPTERS
    ]
    state = {
        "output_path": ".",
        "created_at": None,
        "last_updated": None,
        "project_info": {"topic": title, "description": description},
        "current_phase": 0,
        "sections": {
            identifier: {
                "status": "not_started",
                "file": path,
                "iteration": 0,
                "last_updated": None,
                "notes": "",
            }
            for identifier, _heading, path in PROPOSAL_CHAPTERS
        },
        "ref_counter": 0,
    }
    entries.extend(
        [
            ("proposal-state.json", (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()),
            ("proposal-log.md", "# 申报书撰写日志\n".encode()),
        ]
    )
    return entries


def _section_records(entries: dict[str, bytes]) -> dict:
    """Read upstream section records as guidance, tolerating older source trees."""
    try:
        state = json.loads(entries.get("proposal-state.json", b"{}"))
        sections = state.get("sections") if isinstance(state, dict) else None
        return sections if isinstance(sections, dict) else {}
    except (UnicodeDecodeError, ValueError):
        return {}


def _chapter_title(data: bytes, fallback: str) -> str:
    """Use the chapter's first heading as a display label when one is present."""
    for line in data.decode("utf-8", errors="replace").splitlines()[:100]:
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+\s*)?$", line)
        if heading:
            return heading[1].strip() or fallback
    return fallback


def _chapter_has_content(data: bytes) -> bool:
    """Detect chapter content after removing comments and its leading heading."""
    body = re.sub(r"<!--.*?-->", "", data.decode("utf-8-sig", errors="replace"), flags=re.DOTALL).lstrip()
    body = re.sub(
        r"\A(?:#{1,6}(?:[ \t]+[^\r\n]*)?(?:\r?\n|\Z)" r"|[^\r\n]+\r?\n[ \t]*(?:=+|-+)[ \t]*(?:\r?\n|\Z))",
        "",
        body,
        count=1,
    )
    return bool(body.strip())


def chapter_index(entries: list[tuple[str, bytes]], entry_path: str | None = None) -> list[PaperProposalChapter]:
    """Project saved source into ordered chapters while retaining legacy entries.

    Args:
        entries: Already-owned source files from the selected source revision.
        entry_path: Existing single-document entry for historical workspaces.

    Returns:
        Existing Markdown chapter paths in upstream S1–S9 order. State values
        are shown verbatim and never used to prohibit edits or exports. Older
        single Markdown documents become one chapter; Typst stays in its editor.
    """
    resources = dict(entries)
    sections = _section_records(resources)
    result = []
    used = set()
    for identifier, heading, default_path in PROPOSAL_CHAPTERS:
        section = sections.get(identifier)
        section = section if isinstance(section, dict) else {}
        candidate = section.get("file", default_path)
        if not isinstance(candidate, str):
            continue
        try:
            path = safe_archive_path(candidate).as_posix()
        except HTTPException:
            continue
        if (
            path not in resources
            or path in used
            or PurePosixPath(path).suffix.lower() not in {".md", ".markdown"}
            or PurePosixPath(path).parts[0] == "project_context"
        ):
            continue
        used.add(path)
        result.append(
            PaperProposalChapter(
                id=identifier,
                title=_chapter_title(resources[path], heading),
                path=path,
                status=section.get("status") if isinstance(section.get("status"), str) else None,
                has_content=_chapter_has_content(resources[path]),
            )
        )
    if result:
        return result
    fallback = entry_path if entry_path in resources else "proposal.md" if "proposal.md" in resources else None
    if fallback and PurePosixPath(fallback).suffix.lower() in {".md", ".markdown"}:
        return [
            PaperProposalChapter(
                id="document",
                path=fallback,
                title=_chapter_title(resources[fallback], "申请书正文"),
                has_content=_chapter_has_content(resources[fallback]),
            )
        ]
    return []


def proposal_entry_paths(entries: list[tuple[str, bytes]]) -> list[str]:
    """Resolve existing upstream chapter inputs for one ordered platform export."""
    return [chapter.path for chapter in chapter_index(entries) if chapter.id != "document"]


@lru_cache(maxsize=128)
def _stored_chapter_index(
    object_key: str,
    _source_hash: str,
    source_kind: str,
    filename: str,
    manifest: tuple[str, ...],
    entry_path: str | None,
) -> tuple[tuple[str, str, str, str | None, bool], ...]:
    """Cache only small chapter labels and content flags, never reference bodies.

    Immutable object identity and content hash invalidate this projection on
    every saved change. Polling a workspace must not repeatedly download its
    potentially large reference bundle merely to show nine navigation labels.
    """
    from app.services import paper_service

    data = paper_service._storage().download(object_key)
    if source_kind == "markdown":
        entries = [(filename, data)]
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = []
            if "proposal-state.json" in manifest:
                entries.append(("proposal-state.json", archive.read("proposal-state.json")))
            records = _section_records(dict(entries))
            candidates = {entry_path, "proposal.md", *(item[2] for item in PROPOSAL_CHAPTERS)}
            for value in records.values():
                if isinstance(value, dict) and isinstance(value.get("file"), str):
                    candidates.add(value["file"])
            for candidate in candidates:
                if candidate and candidate in manifest:
                    entries.append((candidate, archive.read(candidate)))
    return tuple(
        (item.id, item.title, item.path, item.status, item.has_content) for item in chapter_index(entries, entry_path)
    )


def source_chapter_index(source: PaperSourceVersion, entry_path: str | None) -> list[PaperProposalChapter]:
    """Read an owned revision's chapter labels without caching source documents."""
    if entry_path and entry_path.lower().endswith(".typ"):
        return []
    if not any(PurePosixPath(path).suffix.lower() in {".md", ".markdown"} for path in source.manifest):
        return []
    values = _stored_chapter_index(
        source.object_key,
        source.content_hash,
        source.source_kind,
        source.filename,
        tuple(source.manifest),
        entry_path,
    )
    return [
        PaperProposalChapter(id=identifier, title=title, path=path, status=status, has_content=has_content)
        for identifier, title, path, status, has_content in values
    ]
