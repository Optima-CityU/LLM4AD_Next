import { useNavigate } from "@tanstack/react-router"
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  GitBranch,
  Loader2,
  PackageCheck,
  Rocket,
  Send,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import ReactMarkdown from "react-markdown"
import { toast } from "sonner"

import type { PaperAlgorithmProposalResponse } from "@/client"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useCreatePaperProposalTasks } from "@/hooks/usePapers"
import { cn } from "@/lib/utils"

type AlgorithmDiscoveryPanelProps = {
  workspaceId: string
  sourceVersionId: string
  proposals: PaperAlgorithmProposalResponse[]
}

function proposalError(error: unknown, fallback: string) {
  const candidate = error as { body?: { detail?: string }; message?: string }
  return candidate.body?.detail || candidate.message || fallback
}

function ProposalMarkdown({ content, id }: { content: string; id: string }) {
  const components = useMemo(() => makeMarkdownComponents(id), [id])
  return (
    <div className="prose prose-sm min-w-0 max-w-none break-words text-sm leading-6 dark:prose-invert [&_p]:my-1 [&_pre]:overflow-x-auto [&_table]:block [&_table]:overflow-x-auto">
      <ReactMarkdown
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default function AlgorithmDiscoveryPanel({
  workspaceId,
  sourceVersionId,
  proposals,
}: AlgorithmDiscoveryPanelProps) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const createTasks = useCreatePaperProposalTasks(workspaceId)
  const currentProposals = useMemo(
    () =>
      proposals.filter((item) => item.source_version_id === sourceVersionId),
    [proposals, sourceVersionId],
  )
  const [expandedId, setExpandedId] = useState<string | null>(
    currentProposals[0]?.id ?? null,
  )
  const [sendingId, setSendingId] = useState<string | null>(null)

  useEffect(() => {
    if (
      currentProposals.length > 0 &&
      !currentProposals.some((proposal) => proposal.id === expandedId)
    ) {
      setExpandedId(currentProposals[0].id)
    }
  }, [currentProposals, expandedId])

  const sendToProjectManagement = async (
    proposal: PaperAlgorithmProposalResponse,
  ) => {
    setSendingId(proposal.id)
    try {
      await createTasks.mutateAsync({
        proposal_ids: [proposal.id],
        language: i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en",
      })
      toast.success(t("paper.discovery.sent"))
    } catch (error) {
      toast.error(proposalError(error, t("paper.discovery.sendFailed")))
    } finally {
      setSendingId(null)
    }
  }

  const openEvolution = (projectId: string) => {
    void navigate({ to: "/evolution", search: { projectId } })
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <header className="shrink-0 border-b bg-muted/20 px-5 py-4">
        <div className="flex items-start gap-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
            <GitBranch className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold tracking-tight">
                {t("paper.discovery.title")}
              </h2>
              <Badge variant="secondary" className="rounded-md font-normal">
                {t("paper.discovery.count", {
                  count: currentProposals.length,
                })}
              </Badge>
            </div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("paper.discovery.description")}
            </p>
          </div>
        </div>
      </header>

      {currentProposals.length === 0 ? (
        <div className="grid min-h-0 flex-1 place-items-center p-6">
          <div className="max-w-sm text-center">
            <div className="mx-auto grid size-12 place-items-center rounded-2xl border border-dashed bg-muted/30 text-muted-foreground">
              <FlaskConical className="size-5" />
            </div>
            <h3 className="mt-4 text-sm font-medium">
              {t("paper.discovery.emptyTitle")}
            </h3>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              {t("paper.discovery.emptyDescription")}
            </p>
          </div>
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 p-4">
            {currentProposals.map((proposal, index) => {
              const expanded = expandedId === proposal.id
              const linked = Boolean(proposal.task_project_id)
              const sending = sendingId === proposal.id
              const packageValidated =
                proposal.validation_report?.status === "passed" &&
                (proposal.package_manifest?.length ?? 0) > 0
              return (
                <article
                  key={proposal.id}
                  className={cn(
                    "overflow-hidden rounded-xl border bg-card transition-colors",
                    expanded && "border-primary/30 shadow-sm",
                  )}
                >
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-controls={`algorithm-proposal-${proposal.id}`}
                    onClick={() => setExpandedId(expanded ? null : proposal.id)}
                    className="flex w-full items-start gap-3 px-4 py-4 text-left outline-none transition-colors hover:bg-muted/35 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-foreground text-xs font-semibold text-background">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-medium leading-5">
                          {proposal.title}
                        </span>
                        {linked && (
                          <Badge
                            variant="outline"
                            className="border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                          >
                            <CheckCircle2 className="mr-1 size-3" />
                            {t("paper.discovery.inProject")}
                          </Badge>
                        )}
                        {packageValidated && !linked && (
                          <Badge
                            variant="outline"
                            className="border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300"
                          >
                            <PackageCheck className="mr-1 size-3" />
                            {t("paper.discovery.validated")}
                          </Badge>
                        )}
                      </span>
                      <span className="mt-1 line-clamp-2 block text-xs leading-5 text-muted-foreground">
                        {proposal.problem_statement}
                      </span>
                    </span>
                    {expanded ? (
                      <ChevronDown className="mt-1 size-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground" />
                    )}
                  </button>

                  {expanded && (
                    <div
                      id={`algorithm-proposal-${proposal.id}`}
                      className="border-t bg-muted/10"
                    >
                      <div className="space-y-5 px-4 py-5">
                        <section>
                          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                            {t("paper.discovery.problem")}
                          </h3>
                          <ProposalMarkdown
                            id={`${proposal.id}-problem`}
                            content={proposal.problem_statement}
                          />
                        </section>
                        <section>
                          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                            {t("paper.discovery.design")}
                          </h3>
                          <ProposalMarkdown
                            id={`${proposal.id}-design`}
                            content={proposal.algorithm_design}
                          />
                        </section>
                        {(proposal.evaluator_requirements?.length ?? 0) > 0 && (
                          <section>
                            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                              {t("paper.discovery.evaluator")}
                            </h3>
                            <ul className="space-y-2 text-sm leading-6 text-foreground/90">
                              {(proposal.evaluator_requirements ?? []).map(
                                (item) => (
                                  <li key={item} className="flex gap-2">
                                    <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" />
                                    <span className="min-w-0 break-words">
                                      {item}
                                    </span>
                                  </li>
                                ),
                              )}
                            </ul>
                          </section>
                        )}
                        {(proposal.assumptions?.length ?? 0) > 0 && (
                          <section>
                            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                              {t("paper.discovery.assumptions")}
                            </h3>
                            <ul className="space-y-1.5 text-sm leading-6 text-muted-foreground">
                              {(proposal.assumptions ?? []).map((item) => (
                                <li key={item}>— {item}</li>
                              ))}
                            </ul>
                          </section>
                        )}
                        {(proposal.provenance?.length ?? 0) > 0 && (
                          <section>
                            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                              {t("paper.discovery.provenance")}
                            </h3>
                            <div className="flex flex-wrap gap-1.5">
                              {(proposal.provenance ?? []).map((item) => (
                                <Badge
                                  key={item}
                                  variant="outline"
                                  className="max-w-full whitespace-normal break-words text-left font-normal"
                                >
                                  {item}
                                </Badge>
                              ))}
                            </div>
                          </section>
                        )}
                        {packageValidated && (
                          <section className="rounded-lg border bg-background/70 p-3">
                            <div className="flex items-center justify-between gap-3">
                              <h3 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                                {t("paper.discovery.package")}
                              </h3>
                              <span className="text-xs text-muted-foreground">
                                {t("paper.discovery.fileCount", {
                                  count: proposal.package_manifest?.length ?? 0,
                                })}
                              </span>
                            </div>
                            <div className="mt-2 flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
                              {(proposal.package_manifest ?? []).map((path) => (
                                <code
                                  key={path}
                                  className="max-w-full break-all rounded bg-muted px-1.5 py-0.5 text-[11px] text-foreground/80"
                                >
                                  {path}
                                </code>
                              ))}
                            </div>
                          </section>
                        )}
                      </div>
                      <footer className="flex flex-wrap items-center justify-between gap-3 border-t bg-background px-4 py-3">
                        <p className="text-xs text-muted-foreground">
                          {linked
                            ? t("paper.discovery.linkedHint")
                            : t("paper.discovery.sendHint")}
                        </p>
                        {proposal.task_project_id ? (
                          <Button
                            size="sm"
                            onClick={() =>
                              openEvolution(proposal.task_project_id as string)
                            }
                          >
                            <Rocket className="size-3.5" />
                            {t("paper.discovery.openEvolution")}
                            <ArrowUpRight className="size-3.5" />
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            disabled={sending || !packageValidated}
                            onClick={() =>
                              void sendToProjectManagement(proposal)
                            }
                          >
                            {sending ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Send className="size-3.5" />
                            )}
                            {t("paper.discovery.send")}
                          </Button>
                        )}
                      </footer>
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        </ScrollArea>
      )}
    </div>
  )
}
