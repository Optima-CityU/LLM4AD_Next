import assert from "node:assert/strict"
import { test } from "node:test"
import {
  proposalChapters,
  proposalDraftChapters,
  proposalRenderKey,
} from "../src/lib/proposalDocument.ts"

test("new projects preserve server chapter order without requiring proposal.md", () => {
  const chapters = [
    { id: "methods", title: "Methods", path: "05-methods.md", status: null },
    {
      id: "rationale",
      title: "Rationale",
      path: "01-rationale.md",
      status: "ready",
    },
  ]
  assert.deepEqual(
    proposalChapters(
      [
        "01-rationale.md",
        "05-methods.md",
        "proposal-state.json",
        "progress.log",
        "styles/proposal.typ",
      ],
      "01-rationale.md",
      chapters,
    ),
    chapters,
  )
})

test("a legacy Markdown entry is one chapter and Typst is preserved without migration", () => {
  assert.deepEqual(
    proposalChapters(["proposal.md", "materials/reference.md"], "proposal.md"),
    [
      {
        id: "proposal.md",
        title: "proposal.md",
        path: "proposal.md",
        status: null,
        has_content: true,
      },
    ],
  )
  assert.deepEqual(
    proposalChapters(["proposal.typ", "notes.md"], "proposal.typ"),
    [],
  )
})

test("chapter selection excludes deleted and duplicate paths without reordering the document", () => {
  assert.deepEqual(
    proposalChapters(["a.md", "b.md"], "a.md", [
      { id: "missing", title: "Missing", path: "removed.md" },
      { id: "b", title: "B", path: "b.md" },
      { id: "duplicate", title: "Duplicate", path: "b.md" },
      { id: "a", title: "A", path: "a.md" },
    ]).map((chapter) => chapter.id),
    ["b", "a"],
  )
})

test("render cache separates workspaces, versions, hashes and chapter order", () => {
  const revision = {
    sourceVersionId: "source-a",
    sourceHash: "hash-a",
    entryPaths: ["a.md", "b.md"],
  }
  const key = proposalRenderKey("workspace-a", revision)
  for (const next of [
    { ...revision, sourceVersionId: "source-b" },
    { ...revision, sourceHash: "hash-b" },
    { ...revision, entryPaths: ["b.md", "a.md"] },
  ])
    assert.notDeepEqual(proposalRenderKey("workspace-a", next), key)
  assert.notDeepEqual(proposalRenderKey("workspace-b", revision), key)
})

test("empty upstream templates and optimistic statuses do not reveal draft chapters", () => {
  const chapters = Array.from({ length: 9 }, (_, i) => ({
    id: `S${i + 1}`,
    title: "Chapter",
    path: `${i}.md`,
    status: "finalized",
    has_content: false,
  }))
  assert.deepEqual(proposalDraftChapters(chapters), [])
  chapters[6].has_content = true
  assert.deepEqual(
    proposalDraftChapters(chapters).map((c) => c.id),
    ["S7"],
  )
  assert.equal(
    proposalDraftChapters([
      { id: "document", title: "Legacy", path: "old.md", has_content: false },
    ]).length,
    1,
  )
})
