import {
  BookOpen,
  ChevronDown,
  FileCode2,
  FolderOpen,
  ListChecks,
  Upload,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import { useTranslation } from "react-i18next"
import { PaperRegionProvider } from "@/components/Paper/PaperRegionWorkbench"
import ResearchWorkbenchTabsHeader from "@/components/Paper/ResearchWorkbenchTabsHeader"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { PaperRegionAgentRequest } from "@/lib/paperRegions"
import type { ProposalChapter } from "@/lib/proposalDocument"

const PROPOSAL_VIEWS = {
  document: { icon: BookOpen, label: "document" },
  references: { icon: FolderOpen, label: "references" },
  progress: { icon: ListChecks, label: "writingProgress" },
  source: { icon: FileCode2, label: "sourceFiles" },
} as const

type ProposalSection = keyof typeof PROPOSAL_VIEWS
export type ProposalView = ProposalSection | "layout"

export function ProposalToolbar({
  referenceCount,
  onUploadReferences,
  progressSummary,
  history,
}: {
  referenceCount: number
  onUploadReferences: () => void
  progressSummary?: string
  history?: ReactNode
}) {
  const { t } = useTranslation()
  const views: ProposalSection[] = [
    "progress",
    "document",
    "references",
    "source",
  ]
  return (
    <ResearchWorkbenchTabsHeader
      data-testid="proposal-toolbar"
      label={t("paper.proposalDocument.navigation")}
      listClassName="min-w-[min(100%,25rem)] grid-cols-4"
      actions={
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={onUploadReferences}>
            <Upload data-icon="inline-start" aria-hidden="true" />
            {t("paper.source.projectContext.upload")}
          </Button>
          {history}
        </div>
      }
    >
      {views.map((target) => {
        const { icon: Icon, label } = PROPOSAL_VIEWS[target]
        return (
          <TabsTrigger
            key={target}
            value={target}
            title={target === "progress" ? progressSummary : undefined}
          >
            <Icon className="hidden size-4 sm:block" aria-hidden="true" />
            {t(`paper.proposalDocument.${label}`)}
            {target === "references" && referenceCount > 0 && (
              <span className="text-xs tabular-nums text-muted-foreground">
                {referenceCount}
              </span>
            )}
          </TabsTrigger>
        )
      })}
    </ResearchWorkbenchTabsHeader>
  )
}

export function ProposalChapterPicker({
  chapters,
  chapterPath,
  onSelect,
}: {
  chapters: ProposalChapter[]
  chapterPath: string
  onSelect: (path: string) => Promise<boolean>
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const label = t("paper.proposalDocument.chapters")
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="outline" className="max-w-full">
          <BookOpen data-icon="inline-start" aria-hidden="true" />
          {label}
          <ChevronDown data-icon="inline-end" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-label={label}
        className="max-h-(--radix-popover-content-available-height) w-[min(22rem,calc(100vw-1.5rem))] overflow-y-auto p-1"
      >
        <ul aria-label={label} className="space-y-0.5">
          {chapters.map((chapter) => (
            <li key={chapter.path}>
              <Button
                variant={chapter.path === chapterPath ? "secondary" : "ghost"}
                aria-current={
                  chapter.path === chapterPath ? "location" : undefined
                }
                className="h-auto min-h-8 w-full justify-start whitespace-normal break-words text-left"
                onClick={async () => {
                  if (await onSelect(chapter.path)) setOpen(false)
                }}
              >
                {chapter.title}
              </Button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}

export default function ProposalResultsDock({
  workspaceId,
  onRequestAgent,
  onOpenFile,
  children,
  document,
  onboarding,
  hasDraft = true,
  toolbar,
  references,
  progress,
  layout,
  renderAction,
  draftFormat = "Markdown",
  view,
  onViewChange,
}: {
  workspaceId?: string
  onRequestAgent?: (request: PaperRegionAgentRequest) => void
  onOpenFile: (path: string) => void
  children: ReactNode
  document: ReactNode
  onboarding?: ReactNode
  hasDraft?: boolean
  toolbar: ReactNode
  references: ReactNode
  progress: ReactNode
  layout?: ReactNode
  renderAction?: ReactNode
  draftFormat?: string
  view: ProposalView
  onViewChange: (view: ProposalView) => Promise<boolean>
}) {
  const { t } = useTranslation()
  const section = view === "layout" ? "document" : view
  return (
    <PaperRegionProvider
      workspaceId={workspaceId}
      onRequestAgent={onRequestAgent}
      onOpenFile={onOpenFile}
    >
      <Tabs
        value={section}
        onValueChange={(value) => void onViewChange(value as ProposalSection)}
        data-testid="proposal-results-dock"
        data-view={view}
        className="@container h-full min-h-0 min-w-0 gap-0 overflow-hidden bg-card/30 lg:border-l"
      >
        {toolbar}
        <TabsContent value="document" className="min-h-0 overflow-hidden">
          {hasDraft || layout ? (
            <Tabs
              value={view}
              onValueChange={(value) =>
                void onViewChange(value === "layout" ? "layout" : "document")
              }
              className="h-full min-h-0 gap-0"
            >
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
                <TabsList aria-label={t("paper.proposalDocument.format")}>
                  <TabsTrigger value="document">{draftFormat}</TabsTrigger>
                  <TabsTrigger value="layout" disabled={!layout}>
                    PDF
                  </TabsTrigger>
                </TabsList>
                {renderAction}
              </div>
              <TabsContent value="document" className="min-h-0 overflow-hidden">
                {hasDraft ? document : onboarding}
              </TabsContent>
              <TabsContent value="layout" className="min-h-0 overflow-hidden">
                {layout}
              </TabsContent>
            </Tabs>
          ) : (
            onboarding
          )}
        </TabsContent>
        <TabsContent value="progress" className="min-h-0 overflow-hidden">
          {progress}
        </TabsContent>
        <TabsContent value="references" className="min-h-0 overflow-hidden">
          {references}
        </TabsContent>
        <TabsContent value="source" className="min-h-0 overflow-hidden">
          {children}
        </TabsContent>
      </Tabs>
    </PaperRegionProvider>
  )
}
