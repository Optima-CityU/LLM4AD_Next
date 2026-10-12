import {
  ArrowUpRight,
  BookOpen,
  CalendarClock,
  FileCheck2,
  FileText,
  GitBranch,
  Lightbulb,
  Microscope,
  Search,
  Sparkles,
  Target,
  Upload,
} from "lucide-react"
import { type ReactNode, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import type { ProposalContextBlock } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Pagination,
  PaginationContent,
  PaginationItem,
} from "@/components/ui/pagination"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const STAGE_PRESENTATION = {
  formatting: { icon: Sparkles, order: "01", layout: "cards" },
  literature: { icon: Search, order: "02", layout: "cards" },
  rationale: { icon: Lightbulb, order: "03", layout: "cards" },
  objectives: { icon: Target, order: "04", layout: "numbered" },
  methods: { icon: GitBranch, order: "05", layout: "timeline" },
  innovation_plan: { icon: CalendarClock, order: "06", layout: "timeline" },
  foundation_feasibility: { icon: Microscope, order: "07", layout: "cards" },
  final_review: { icon: FileCheck2, order: "08", layout: "review" },
} as const

type ProposalStageKey = keyof typeof STAGE_PRESENTATION
const FOUNDATION_PAGE_SIZE = 8
const FINDING_PAGE_SIZE = 20

/** Keep a changing result set on a valid page without mounting every entry. */
export function getStagePageWindow(
  total: number,
  requestedPage: number,
  pageSize: number,
) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const page = Math.min(Math.max(0, requestedPage), pageCount - 1)
  const start = page * pageSize
  return { page, pageCount, start, end: Math.min(total, start + pageSize) }
}

export type ProposalResultStage = {
  key: ProposalStageKey
  title: string
  hint: string
  status: string
  summary: string | null
  findings: string[]
  files: string[]
  foundationBlocks: ProposalContextBlock[]
}

type ProposalResultsDockProps = {
  stage: ProposalResultStage
  editorOpen: boolean
  onEditorOpenChange: (open: boolean) => void
  onOpenFile: (path: string) => void
  onUploadProjectContext: () => void
  children: ReactNode
}

const markdownClass =
  "prose prose-neutral min-w-0 max-w-none break-words text-[15px] leading-7 text-foreground/90 dark:prose-invert [&_a]:break-all [&_li]:leading-7 [&_p]:my-2 [&_p]:leading-7 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto"

/** Show free-form stage text as readable Markdown without inventing fixed fields. */
function StageMarkdown({ children }: { children: string }) {
  return (
    <div className={markdownClass}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

/** Show a compact page range and keyboard-accessible controls for long results. */
function StagePageControls({
  section,
  total,
  window,
  onPageChange,
}: {
  section: string
  total: number
  window: ReturnType<typeof getStagePageWindow>
  onPageChange: (page: number) => void
}) {
  const { t } = useTranslation()
  if (window.pageCount <= 1) return null

  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
      <span
        aria-live="polite"
        className="text-sm tabular-nums text-muted-foreground"
      >
        {t("paper.proposalResults.pageRange", {
          from: window.start + 1,
          to: window.end,
          total,
        })}
      </span>
      <Pagination
        className="mx-0 w-auto justify-end"
        aria-label={t("paper.proposalResults.paginationLabel", { section })}
      >
        <PaginationContent>
          <PaginationItem>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={window.page === 0}
              onClick={() => onPageChange(window.page - 1)}
            >
              {t("paper.proposalResults.previousPage")}
            </Button>
          </PaginationItem>
          <PaginationItem>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={window.page === window.pageCount - 1}
              onClick={() => onPageChange(window.page + 1)}
            >
              {t("paper.proposalResults.nextPage")}
            </Button>
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  )
}

/** Render each proposal stage with its own scholarly reading pattern. */
function StageResultContent({
  stage,
  onOpenFile,
  onEditorOpenChange,
}: Pick<
  ProposalResultsDockProps,
  "stage" | "onOpenFile" | "onEditorOpenChange"
>) {
  const { t } = useTranslation()
  const presentation = STAGE_PRESENTATION[stage.key]
  const StageIcon = presentation.icon
  const [foundationPage, setFoundationPage] = useState(0)
  const [findingPage, setFindingPage] = useState(0)
  const foundationSectionRef = useRef<HTMLElement>(null)
  const findingSectionRef = useRef<HTMLElement>(null)
  const foundationWindow = getStagePageWindow(
    stage.foundationBlocks.length,
    foundationPage,
    FOUNDATION_PAGE_SIZE,
  )
  const findingWindow = getStagePageWindow(
    stage.findings.length,
    findingPage,
    FINDING_PAGE_SIZE,
  )
  const hasResult = Boolean(
    stage.summary ||
      stage.findings.length ||
      stage.files.length ||
      (stage.key === "formatting" && stage.foundationBlocks.length),
  )

  return (
    <section
      className="flex h-full min-h-0 flex-col overflow-hidden"
      data-stage={stage.key}
    >
      <header className="shrink-0 border-b bg-gradient-to-br from-primary/[0.07] via-background to-background px-5 py-5 sm:px-7 sm:py-6">
        <div className="flex items-start gap-3.5">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl border border-primary/15 bg-background text-primary shadow-sm">
            <StageIcon className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold tracking-[0.14em] text-primary uppercase">
              {t("paper.proposalResults.stageIndex", {
                current: presentation.order,
                total: "08",
              })}
            </p>
            <h2 className="mt-1 font-serif text-xl font-semibold leading-snug text-balance text-foreground sm:text-2xl">
              {stage.title}
            </h2>
          </div>
          <Badge variant="outline" className="shrink-0 bg-background/80">
            {stage.status}
          </Badge>
        </div>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
          {stage.hint}
        </p>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-5 py-7 sm:px-7 sm:py-8">
          {!hasResult ? (
            <div className="rounded-xl border border-dashed bg-card/50 px-5 py-7">
              <BookOpen className="size-5 text-primary/70" aria-hidden="true" />
              <p className="mt-3 text-base font-medium text-foreground">
                {t("paper.proposalResults.pendingTitle")}
              </p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {t("paper.proposalResults.pending")}
              </p>
            </div>
          ) : (
            <>
              {stage.summary && (
                <section aria-label={t("paper.proposalResults.summary")}>
                  <h3 className="mb-3 text-xs font-semibold tracking-[0.13em] text-muted-foreground uppercase">
                    {t("paper.proposalResults.summary")}
                  </h3>
                  <div className="border-l-[3px] border-primary/55 bg-card/55 py-4 pr-5 pl-5">
                    <StageMarkdown>{stage.summary}</StageMarkdown>
                  </div>
                </section>
              )}

              {stage.key === "formatting" &&
                stage.foundationBlocks.length > 0 && (
                  <section
                    ref={foundationSectionRef}
                    aria-label={t("paper.proposalResults.foundation")}
                  >
                    <div className="mb-3 flex items-baseline justify-between gap-3">
                      <h3 className="font-serif text-lg font-semibold text-foreground">
                        {t("paper.proposalResults.foundation")}
                      </h3>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {stage.foundationBlocks.length}
                      </span>
                    </div>
                    <p className="mb-4 text-sm leading-6 text-muted-foreground">
                      {t("paper.proposalResults.foundationHint")}
                    </p>
                    <div className="space-y-3">
                      {stage.foundationBlocks
                        .slice(foundationWindow.start, foundationWindow.end)
                        .map((block, pageIndex) => (
                          <article
                            key={block.key}
                            className="rounded-xl border bg-card px-5 py-5 shadow-sm"
                          >
                            <div className="mb-3 flex items-start gap-3">
                              <span className="pt-0.5 font-mono text-xs font-semibold text-primary/70 tabular-nums">
                                {String(
                                  foundationWindow.start + pageIndex + 1,
                                ).padStart(2, "0")}
                              </span>
                              <h4 className="min-w-0 font-serif text-base font-semibold leading-6 text-foreground">
                                {block.title}
                              </h4>
                            </div>
                            <div className="pl-7">
                              <StageMarkdown>{block.content}</StageMarkdown>
                            </div>
                            {Boolean(block.source_refs?.length) && (
                              <div className="mt-4 break-all border-t pt-3 pl-7 text-xs leading-5 text-muted-foreground">
                                <span className="font-medium">
                                  {t("paper.proposalResults.sourceRefs")}:{" "}
                                </span>
                                {block.source_refs?.join(" · ")}
                              </div>
                            )}
                          </article>
                        ))}
                    </div>
                    <StagePageControls
                      section={t("paper.proposalResults.foundation")}
                      total={stage.foundationBlocks.length}
                      window={foundationWindow}
                      onPageChange={(page) => {
                        setFoundationPage(page)
                        foundationSectionRef.current?.scrollIntoView({
                          block: "start",
                        })
                      }}
                    />
                  </section>
                )}

              {stage.findings.length > 0 && (
                <section
                  ref={findingSectionRef}
                  aria-label={t(
                    `paper.proposalResults.findingTitles.${stage.key}`,
                  )}
                >
                  <div className="mb-4 flex items-baseline justify-between gap-3">
                    <h3 className="font-serif text-lg font-semibold text-foreground">
                      {t(`paper.proposalResults.findingTitles.${stage.key}`)}
                    </h3>
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {stage.findings.length}
                    </span>
                  </div>
                  <ol
                    className={
                      presentation.layout === "timeline"
                        ? "ml-3 space-y-0 border-l border-primary/25"
                        : "space-y-3"
                    }
                  >
                    {stage.findings
                      .slice(findingWindow.start, findingWindow.end)
                      .map((finding, pageIndex) => (
                        <li
                          key={`${stage.key}-${findingWindow.start + pageIndex}`}
                          className={
                            presentation.layout === "timeline"
                              ? "relative py-2 pr-1 pl-7 before:absolute before:top-5 before:-left-[5px] before:size-[9px] before:rounded-full before:border-2 before:border-primary before:bg-background"
                              : presentation.layout === "review"
                                ? "flex min-w-0 gap-4 rounded-r-xl border border-l-[3px] border-l-amber-500/60 bg-card px-4 py-4 shadow-sm"
                                : "flex min-w-0 gap-4 rounded-xl border bg-card px-4 py-4 shadow-sm"
                          }
                        >
                          {presentation.layout === "review" ? (
                            <FileCheck2
                              className="mt-1 size-5 shrink-0 text-amber-600 dark:text-amber-400"
                              aria-hidden="true"
                            />
                          ) : presentation.layout === "cards" ? (
                            <span
                              className="mt-2 size-2 shrink-0 rounded-full bg-primary/65"
                              aria-hidden="true"
                            />
                          ) : presentation.layout === "numbered" ? (
                            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/[0.09] font-mono text-xs font-semibold text-primary tabular-nums">
                              {String(
                                findingWindow.start + pageIndex + 1,
                              ).padStart(2, "0")}
                            </span>
                          ) : null}
                          <StageMarkdown>{finding}</StageMarkdown>
                        </li>
                      ))}
                  </ol>
                  <StagePageControls
                    section={t(
                      `paper.proposalResults.findingTitles.${stage.key}`,
                    )}
                    total={stage.findings.length}
                    window={findingWindow}
                    onPageChange={(page) => {
                      setFindingPage(page)
                      findingSectionRef.current?.scrollIntoView({
                        block: "start",
                      })
                    }}
                  />
                </section>
              )}

              {stage.files.length > 0 && (
                <section aria-label={t("paper.proposalResults.files")}>
                  <h3 className="mb-3 font-serif text-lg font-semibold text-foreground">
                    {t("paper.proposalResults.files")}
                  </h3>
                  <div className="overflow-hidden rounded-xl border bg-card">
                    {stage.files.map((path) => (
                      <button
                        key={path}
                        type="button"
                        onClick={() => {
                          onOpenFile(path)
                          onEditorOpenChange(true)
                        }}
                        className="group flex w-full min-w-0 items-center gap-3 border-b px-4 py-3.5 text-left transition-colors last:border-b-0 hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
                        aria-label={t("paper.proposalResults.openFile", {
                          path,
                        })}
                      >
                        <FileText
                          className="size-4 shrink-0 text-primary"
                          aria-hidden="true"
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-sm text-foreground">
                          {path}
                        </span>
                        <ArrowUpRight
                          className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-primary"
                          aria-hidden="true"
                        />
                      </button>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </ScrollArea>
    </section>
  )
}

/** Switch the proposal workbench between the active stage result and editor. */
export default function ProposalResultsDock({
  stage,
  editorOpen,
  onEditorOpenChange,
  onOpenFile,
  onUploadProjectContext,
  children,
}: ProposalResultsDockProps) {
  const { t } = useTranslation()

  return (
    <aside
      data-testid="proposal-results-dock"
      className="hidden h-full min-h-0 min-w-0 flex-col overflow-hidden border-l bg-background lg:flex"
    >
      <Tabs
        value={editorOpen ? "editor" : "result"}
        onValueChange={(value) => onEditorOpenChange(value === "editor")}
        className="h-full min-h-0 gap-0"
      >
        <div className="shrink-0 border-b bg-card/55 p-3">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="result">
              <BookOpen data-icon="inline-start" />
              {t("paper.proposalResults.title")}
            </TabsTrigger>
            <TabsTrigger value="editor">
              <FileText data-icon="inline-start" />
              {t("paper.proposalResults.editor")}
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent
          value="result"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <StageResultContent
            key={stage.key}
            stage={stage}
            onOpenFile={onOpenFile}
            onEditorOpenChange={onEditorOpenChange}
          />
        </TabsContent>

        <TabsContent
          value="editor"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          {editorOpen && (
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex shrink-0 justify-end border-b bg-card/30 px-3 py-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onUploadProjectContext}
                >
                  <Upload data-icon="inline-start" />
                  {t("paper.source.projectContext.upload")}
                </Button>
              </div>
              <div className="min-h-0 flex-1">{children}</div>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </aside>
  )
}
