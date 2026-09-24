import { BookOpen, FileText, Upload } from "lucide-react"
import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export type ProposalResultStage = {
  key: string
  title: string
  hint: string
  status: string
  summary: string | null
  findings: string[]
  files: string[]
}

type ProposalResultsDockProps = {
  stage: ProposalResultStage
  editorOpen: boolean
  onEditorOpenChange: (open: boolean) => void
  onOpenFile: (path: string) => void
  onUploadProjectContext: () => void
  children: ReactNode
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
          <section className="flex h-full min-h-0 flex-col overflow-hidden">
            <header className="flex shrink-0 items-start justify-between gap-4 border-b px-6 py-5">
              <h2 className="min-w-0 font-serif text-xl font-semibold leading-7 text-pretty">
                {stage.title}
              </h2>
              <Badge variant="outline">{stage.status}</Badge>
            </header>
            <ScrollArea className="min-h-0 flex-1">
              <div className="flex flex-col gap-5 px-6 py-6">
                {stage.summary ? (
                  <div className="prose max-w-none break-words text-[15px] leading-7 text-foreground/85 dark:prose-invert [&_p]:my-0 [&_p]:leading-7">
                    <ReactMarkdown>{stage.summary}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="flex flex-col gap-2 text-[15px] leading-7 text-muted-foreground">
                    <p>{t("paper.proposalResults.pending")}</p>
                    <p className="text-foreground/80">{stage.hint}</p>
                  </div>
                )}
                {stage.findings.length > 0 && (
                  <ul className="flex flex-col gap-2 border-l-2 border-primary/30 pl-4 text-sm leading-6 text-foreground/80">
                    {stage.findings.map((finding, index) => (
                      <li key={`${stage.key}-${index}`} className="break-words">
                        {finding}
                      </li>
                    ))}
                  </ul>
                )}
                {stage.files.length > 0 && (
                  <div className="flex flex-col gap-2 border-t pt-4">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("paper.proposalResults.files")}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {stage.files.map((path) => (
                        <button
                          key={path}
                          type="button"
                          onClick={() => {
                            onOpenFile(path)
                            onEditorOpenChange(true)
                          }}
                          className="inline-flex max-w-full items-center gap-1.5 border border-border bg-background px-2.5 py-1.5 text-sm text-primary hover:border-primary/40 hover:bg-primary/5 focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <FileText className="size-3.5 shrink-0" />
                          <span className="truncate">{path}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>
          </section>
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
