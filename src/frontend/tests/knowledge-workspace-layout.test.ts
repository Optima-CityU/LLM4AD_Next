import { expect, test } from "bun:test"

const workspaceSource = await Bun.file(
  new URL(
    "../src/components/Knowledge/KnowledgeMemoryImportWorkspace.tsx",
    import.meta.url,
  ),
).text()
const draftReviewSource = await Bun.file(
  new URL(
    "../src/components/Knowledge/KnowledgeDocumentBlockReview.tsx",
    import.meta.url,
  ),
).text()
const memoryCardManagerSource = await Bun.file(
  new URL(
    "../src/components/Memory/MemoryCardManager.tsx",
    import.meta.url,
  ),
).text()
const memoryCardPresentationSource = await Bun.file(
  new URL(
    "../src/components/Memory/MemoryCardPresentation.tsx",
    import.meta.url,
  ),
).text()

test("the Markdown editor fills the remaining document panel height", () => {
  expect(workspaceSource).toContain('data-testid="knowledge-source-editor"')
  expect(workspaceSource).toContain(
    'className="h-full min-h-0 w-full resize-none',
  )
})

test("the source preview shrinks with its panel without clipping Markdown", () => {
  expect(workspaceSource).toContain('data-testid="knowledge-source-preview"')
  expect(workspaceSource).toContain(
    'className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"',
  )
  expect(workspaceSource).toContain(
    'className="prose prose-sm w-full min-w-0 max-w-full break-words p-5 dark:prose-invert [&_pre]:max-w-full [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto"',
  )
})

test("source documents use a compact toolbar switcher instead of a fixed sidebar", () => {
  expect(workspaceSource).toContain('data-testid="knowledge-source-switcher"')
  expect(workspaceSource).not.toContain(
    'className="w-44 shrink-0 border-r bg-muted/10 overscroll-contain"',
  )
})

test("topic and document edits can be cancelled without losing the original content", () => {
  expect(workspaceSource).toContain('data-testid="knowledge-topic-title-dialog"')
  expect(workspaceSource).toContain("const cancelFileEdit = () =>")
  expect(workspaceSource).toContain("onClick={cancelFileEdit}")
})

test("stopping parsing refreshes both the terminal run and restored source status", () => {
  const stopExtraction = workspaceSource.slice(
    workspaceSource.indexOf("const stopExtraction = async () =>"),
    workspaceSource.indexOf("return (", workspaceSource.indexOf("const stopExtraction = async () =>")),
  )

  expect(stopExtraction).toContain("loadRun(detail.id)")
  expect(stopExtraction).toContain("loadDetail(detail.id)")
  expect(stopExtraction).toContain("loadSources(detail.id)")
})

test("truncated topic and document titles expose their full value on hover", () => {
  expect(workspaceSource).toContain("title={detail.title}")
  expect(workspaceSource).toContain(
    'title={selectedFile?.original_filename || ""}',
  )
})

test("document block review offers an explicit from-scratch organization action", () => {
  expect(draftReviewSource).toContain(
    'data-testid="knowledge-restart-extraction"',
  )
  expect(workspaceSource).toContain(
    'data-testid="knowledge-restart-confirm-dialog"',
  )
  expect(workspaceSource).toContain("onRestart={() => setRestartOpen(true)}")
})

test("selected document blocks can be inserted through the structured batch endpoint", () => {
  expect(workspaceSource).toContain("KnowledgeDocumentBlockReview")
  expect(workspaceSource).toContain("Llm4AdKnowledgeService.getDocumentContent")
  expect(workspaceSource).toContain("Llm4AdKnowledgeService.updateDocument")
  expect(workspaceSource).toContain("/documents/insert")
  expect(draftReviewSource).toContain("onInsert")
  expect(draftReviewSource).toContain('data-testid="knowledge-insert-selected"')
})

test("knowledge CRUD uses the openapi-ts generated client", () => {
  expect(workspaceSource).toContain("Llm4AdKnowledgeService.listSources")
  expect(workspaceSource).toContain("Llm4AdKnowledgeService.addSourceFiles")
  expect(workspaceSource).toContain("Llm4AdKnowledgeService.startParse")
  expect(workspaceSource).toContain("Llm4AdMemoryService.updateMemoryCard")
  expect(workspaceSource.match(/authFetch\(/g)?.length).toBe(1)
})

test("structured document insertion streams visible stage progress", () => {
  expect(workspaceSource).toContain("/documents/insert/stream")
  expect(workspaceSource).toContain("response.body?.getReader()")
  expect(workspaceSource).toContain("insertProgress")
  expect(draftReviewSource).toContain('data-testid="knowledge-insert-progress"')
})

test("structured insertion progress never moves backwards on heartbeats or stale stages", () => {
  expect(workspaceSource).toContain("setInsertProgress((current) =>")
  expect(workspaceSource).toContain("Math.max(previousPercent, reportedPercent)")
  expect(workspaceSource).not.toContain("event.percent ?? 1")
})

test("structured insertion retries interrupted browser streams with the same request", () => {
  expect(workspaceSource).toContain("MAX_KNOWLEDGE_INSERT_STREAM_ATTEMPTS")
  expect(workspaceSource).toMatch(/for\s*\(\s*let attempt = 1;/)
  expect(workspaceSource).toContain("knowledge.documentBlocks.insertStages.reconnecting")
})

test("generated memories are shown below document blocks with edit and delete actions", () => {
  expect(workspaceSource).toContain("generatedMemories")
  expect(draftReviewSource).toContain('data-testid="knowledge-generated-memory-results"')
  expect(draftReviewSource).toContain("onEditMemory")
  expect(draftReviewSource).toContain("onDeleteMemory")
  expect(draftReviewSource).toContain('data-testid="knowledge-generated-memory-edit"')
  expect(draftReviewSource).toContain('data-testid="knowledge-generated-memory-delete"')
})

test("generated structured memories render exact source artifacts and preserve them while editing facts", () => {
  expect(memoryCardPresentationSource).toContain("structured_content.artifacts")
  expect(memoryCardPresentationSource).toContain("artifact.content")
  expect(memoryCardPresentationSource).toContain("whitespace-pre")
  expect(memoryCardPresentationSource).toContain("sourceArtifact")
})

test("source artifacts follow card facts and precede editor system details", () => {
  expect(memoryCardPresentationSource).toContain('data-testid="memory-card-source-artifacts"')
  expect(memoryCardPresentationSource.indexOf("card.tags.length")).toBeLessThan(
    memoryCardPresentationSource.indexOf('data-testid="memory-card-source-artifacts"'),
  )
  expect(
    memoryCardPresentationSource.indexOf('data-testid="memory-card-editor-source-artifacts"'),
  ).toBeLessThan(
    memoryCardPresentationSource.indexOf("systemInfoDescription"),
  )
})

test("source artifacts stay collapsed inside list and grid cards", () => {
  expect(memoryCardPresentationSource).toContain("<details")
  expect(memoryCardPresentationSource).toContain(
    'data-testid="memory-card-source-artifacts"',
  )
  expect(memoryCardPresentationSource).toContain("sourceArtifactsCollapsed")
  expect(memoryCardPresentationSource).toContain(
    'className="min-w-0 max-w-full overflow-hidden"',
  )
})

test("generated memories reuse the global memory card and editor components", () => {
  expect(draftReviewSource).toContain("MemoryCardTile")
  expect(draftReviewSource).toContain("MemoryCardEditorDialog")
  expect(draftReviewSource).toContain("MemoryCardDeleteDialog")
  expect(memoryCardManagerSource).toContain("MemoryCardTile")
  expect(memoryCardManagerSource).toContain("MemoryCardEditorDialog")
  expect(memoryCardManagerSource).toContain("MemoryCardDeleteDialog")
  expect(draftReviewSource).not.toContain("knowledge-generated-memory-tags")
})

test("shared memory cards edit typed descriptions and facts without exposing JSON", async () => {
  const sharedCardSource = await Bun.file(
    new URL(
      "../src/components/Memory/MemoryCardPresentation.tsx",
      import.meta.url,
    ),
  ).text()
  const memoryTypesSource = await Bun.file(
    new URL("../src/components/Memory/types.ts", import.meta.url),
  ).text()

  expect(memoryTypesSource).toContain("structured_content")
  expect(memoryTypesSource).toContain("description: string")
  expect(memoryTypesSource).toContain("content: string[]")
  expect(sharedCardSource).toContain("draft.structured_content.description")
  expect(sharedCardSource).toContain("draft.structured_content.content")
  expect(sharedCardSource).not.toContain("JSON.stringify(card.structured_content")
  expect(memoryCardManagerSource).toContain("structured_content: draft.structured_content")
})

test("shared memory cards keep actions visible in narrow panels", async () => {
  const sharedCardSource = await Bun.file(
    new URL(
      "../src/components/Memory/MemoryCardPresentation.tsx",
      import.meta.url,
    ),
  ).text()

  expect(sharedCardSource).toContain(
    'className="flex min-w-0 flex-wrap items-start gap-2"',
  )
  expect(sharedCardSource).toContain(
    'className="ml-auto max-w-full shrink-0"',
  )
  expect(sharedCardSource).toContain(
    '"min-w-0 overflow-hidden flex flex-col rounded-md',
  )
})

test("generated memory cards stay inside the knowledge review panel", () => {
  expect(draftReviewSource).toContain(
    'className="w-full min-w-0 max-w-full space-y-3"',
  )
  expect(draftReviewSource).toContain(
    'className="min-w-0 space-y-2 border-t pt-3"',
  )
  expect(draftReviewSource).toContain(
    'className="w-full min-w-0 max-w-full"',
  )
  expect(draftReviewSource).toContain(
    'className="flex max-w-full shrink-0 flex-wrap justify-end gap-1"',
  )
})

test("the knowledge review scroller cannot expand wider than its panel", () => {
  expect(workspaceSource).toContain(
    'data-testid="knowledge-review-scroller"',
  )
  expect(workspaceSource).toContain(
    'className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"',
  )
})

test("the document block panel has additional review width", () => {
  expect(workspaceSource).toContain(
    'className="flex min-w-[360px] basis-[42%] flex-col bg-muted/10 2xl:basis-[46%]"',
  )
})

test("the topic heading is prominent and keeps its edit affordance visible", () => {
  expect(workspaceSource).toContain('data-testid="knowledge-topic-heading"')
  expect(workspaceSource).toContain('data-testid="knowledge-topic-edit-icon"')
  expect(workspaceSource).not.toContain(
    'opacity-0 transition-opacity group-hover:opacity-100',
  )
})

test("document switching stays compact and document upload belongs to document actions", () => {
  expect(workspaceSource).toContain(
    'className="min-w-0 max-w-[240px] flex-1 overflow-hidden bg-muted/20 text-xs"',
  )
  const documentActions = workspaceSource.indexOf(
    'data-testid="knowledge-document-actions"',
  )
  const addDocument = workspaceSource.indexOf(
    'data-testid="knowledge-add-document"',
  )
  const backgroundAction = workspaceSource.indexOf(
    'data-testid="knowledge-background-action"',
  )
  expect(documentActions).toBeGreaterThan(-1)
  expect(addDocument).toBeGreaterThan(documentActions)
  expect(backgroundAction).toBeGreaterThan(addDocument)
})

test("document actions stay clipped before the neighboring toolbar divider", () => {
  expect(workspaceSource).toContain(
    'className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden pr-1"',
  )
})

test("document and background labels respond to the source pane width", () => {
  expect(workspaceSource).toContain("@container/source-pane")
  expect(
    workspaceSource.match(/hidden @3xl\/source-pane:inline/g)?.length,
  ).toBe(2)
  expect(workspaceSource).not.toContain("hidden 2xl:inline")
})

test("parser binding offers common token-limit presets with a custom fallback", () => {
  expect(workspaceSource).toContain("const modelCapacityPresets = [")
  expect(workspaceSource).toContain('id: "128k-16k"')
  expect(workspaceSource).toContain('id: "200k-32k"')
  expect(workspaceSource).toContain('id: "1m-64k"')
  expect(workspaceSource).toContain('data-testid="knowledge-limit-preset"')
  expect(workspaceSource).toContain('value="custom"')
  expect(workspaceSource).toContain('selectedLimitPreset === "custom"')
})
