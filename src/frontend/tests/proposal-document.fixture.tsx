// The test server extracts the production route's save queue and render contract.
// @ts-expect-error Vite provides this test-only virtual module.
import { useRouteSourceSave } from "virtual:proposal-route-save"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import { useTranslation } from "react-i18next"
import { OpenAPI } from "../src/client"
import ProposalDocumentPreview, {
  ProposalLayoutPreview,
  proposalAssetPath,
  proposalDocumentEntryPath,
} from "../src/components/Paper/ProposalDocumentPreview"
import ProposalResultsDock, {
  ProposalToolbar,
  type ProposalView,
} from "../src/components/Paper/ProposalResultsDock"
import {
  usePaperSourceFile,
  usePaperWorkspace,
  useUpdatePaperSourceFile,
} from "../src/hooks/usePapers"
import { proposalChapters } from "../src/lib/proposalDocument"
import "../src/i18n"
import "../src/index.css"

OpenAPI.BASE = location.origin
OpenAPI.TOKEN = "proposal-test-token"

function Fixture() {
  const { t } = useTranslation()
  const workspaceId = "workspace-a"
  const workspaceQuery = usePaperWorkspace(workspaceId)
  const workspace = workspaceQuery.data
  const activeSource = workspace?.source_versions?.find(
    (source) => source.id === workspace.active_source_version_id,
  )
  const entry = proposalDocumentEntryPath(
    activeSource?.manifest ?? [],
    workspace?.proposal_entry_path,
  )
  const chapters = proposalChapters(
    activeSource?.manifest ?? [],
    entry,
    workspace?.proposal_chapters,
  )
  const isMarkdownProposal = /\.(md|markdown)$/i.test(entry)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  useEffect(() => {
    if (activeSource && !selectedPath) setSelectedPath(entry)
  }, [activeSource, selectedPath, entry])
  const sourceFile = usePaperSourceFile(
    activeSource?.id ?? null,
    selectedPath,
    activeSource?.content_hash,
  )
  const [sourceDraft, setSourceDraft] = useState("")
  const currentSourceDraft = useRef(sourceDraft)
  currentSourceDraft.current = sourceDraft
  const sourceServerSnapshot = useRef<{ path: string; content: string } | null>(
    null,
  )
  const submittedSourceDraft = useRef<{ path: string; content: string } | null>(
    null,
  )
  const [sourceAutoSaveError, setSourceAutoSaveError] = useState<string | null>(
    null,
  )
  const [view, setView] = useState<ProposalView>("document")
  const [dirty, setDirty] = useState(false)
  useEffect(() => {
    if (!selectedPath || !sourceFile.data) return
    const previous = sourceServerSnapshot.current
    const submitted = submittedSourceDraft.current
    const incoming = sourceFile.data.content
    setSourceDraft((current) =>
      previous?.path !== selectedPath || current === previous.content
        ? incoming
        : current,
    )
    sourceServerSnapshot.current = { path: selectedPath, content: incoming }
    if (submitted?.content === incoming) submittedSourceDraft.current = null
  }, [selectedPath, sourceFile.data])
  const updateSourceFile = useUpdatePaperSourceFile(workspaceId)
  const { flushProposalSource, proposalRender } = useRouteSourceSave({
    t,
    workspaceId,
    workspaceQuery,
    workspace,
    activeSource,
    selectedPath,
    selectedPathEditable: true,
    currentSourceDraft,
    sourceServerSnapshot,
    submittedSourceDraft,
    updateSourceFile,
    setSourceAutoSaveError,
    chapters,
    proposalEntryPath: entry,
  })
  const change = (value: string) => {
    currentSourceDraft.current = value
    setSourceDraft(value)
    setDirty(true)
  }
  const select = async (path: string) => {
    try {
      await flushProposalSource()
      setSelectedPath(path)
      setDirty(false)
      return true
    } catch {
      return false
    }
  }
  const selectView = async (next: ProposalView) => {
    setView(next)
    return true
  }
  return (
    <main className="flex h-screen flex-col">
      <div className="flex gap-3 p-2">
        <button
          type="button"
          onClick={() => {
            void workspaceQuery.refetch()
          }}
        >
          Receive publication
        </button>
        <button
          type="button"
          onClick={() => {
            void flushProposalSource()
          }}
        >
          Start autosave
        </button>
        <output data-testid="asset-paths" hidden>
          {JSON.stringify([
            proposalAssetPath("proposal.md", "assets/figure.png"),
            proposalAssetPath("sections/methods.md", "../assets/figure.png"),
            proposalAssetPath("sections/methods.md", "./plot%20one.svg#figure"),
            proposalAssetPath("proposal.md", "../../outside.png"),
            proposalAssetPath("proposal.md", "%2e%2e/outside.png"),
            proposalAssetPath("proposal.md", "https://example.com/image.png"),
          ])}
        </output>
      </div>
      {activeSource && (
        <div className="min-h-0 flex-1">
          <ProposalResultsDock
            workspaceId={workspaceId}
            view={view}
            onViewChange={selectView}
            draftFormat={isMarkdownProposal ? "Markdown" : "Typst"}
            onOpenFile={(path) => void select(path)}
            toolbar={
              <ProposalToolbar
                referenceCount={0}
                onUploadReferences={() => {}}
                progressSummary="Checking the actual method"
                history={<button type="button">History</button>}
              />
            }
            renderAction={
              <button
                type="button"
                disabled={proposalRender.pending}
                onClick={() => {
                  setView("layout")
                  void proposalRender.confirm()
                }}
              >
                {t("paper.proposalDocument.confirmRender")}
              </button>
            }
            document={
              isMarkdownProposal ? (
                <ProposalDocumentPreview
                  workspaceId={workspaceId}
                  sourceVersionId={activeSource.id}
                  sourceHash={activeSource.content_hash}
                  entryPath={selectedPath ?? entry}
                  content={sourceDraft}
                  loading={sourceFile.isLoading}
                  chapters={chapters}
                  onSelectChapter={select}
                  onEditChapter={() => void selectView("source")}
                  saving={updateSourceFile.isPending}
                  dirty={dirty}
                  saveError={sourceAutoSaveError === sourceDraft}
                />
              ) : (
                <textarea
                  aria-label="Existing Typst editor"
                  value={sourceDraft}
                  onChange={(event) => change(event.target.value)}
                />
              )
            }
            references={<p>Existing contextual reference components</p>}
            progress={
              <p>
                Only actual semantic progress: running / completed / waiting.
              </p>
            }
            layout={
              proposalRender.available ? (
                <ProposalLayoutPreview
                  render={proposalRender}
                  dirty={dirty && activeSource.id === "source-a"}
                />
              ) : undefined
            }
          >
            <textarea
              aria-label="Source editor"
              value={sourceDraft}
              onChange={(event) => change(event.target.value)}
            />
          </ProposalResultsDock>
        </div>
      )}
    </main>
  )
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    <Fixture />
  </QueryClientProvider>,
)
