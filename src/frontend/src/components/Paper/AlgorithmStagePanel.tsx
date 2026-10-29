import { useQuery } from "@tanstack/react-query"
import {
  ArrowUpRight,
  BookOpenText,
  CircleHelp,
  ClipboardList,
  Compass,
  FlaskConical,
  Loader2,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import { Llm4AdPapersService, type ProposalStageState } from "@/client"
import AlgorithmDiscoveryPanel from "@/components/Paper/AlgorithmDiscoveryPanel"
import type { PaperAlgorithmProposalResponse } from "@/client"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"

type DiscoveryStage =
  | "discovery_baseline"
  | "discovery_candidates"
  | "discovery_evaluation"
  | "discovery"

type Evidence = { claim: string; source_path: string; source_quote?: string }
type Candidate = {
  id: string
  title: string
  evolvable_boundary: string
  rationale: string
  feasibility: string
  evaluation_risks: string[]
  source_refs: string[]
}
type Baseline = {
  summary: string
  algorithm_summary: string
  evidence: Evidence[]
  unknowns: string[]
}
type Candidates = {
  summary: string
  candidates: Candidate[]
  selected_candidate_id: string | null
  open_questions: string[]
}
type Evaluation = {
  summary: string
  candidate_id: string
  metrics: string[]
  optimization_direction: "minimize" | "maximize" | null
  baseline: string | null
  data_protocol: string | null
  validity_checks: string[]
  reproducibility: string | null
  source_refs: string[]
  unknowns: string[]
  ready_for_build: boolean
}

const stageIcons = {
  discovery_baseline: BookOpenText,
  discovery_candidates: Compass,
  discovery_evaluation: FlaskConical,
  discovery: ClipboardList,
}

function ResultSection({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="min-w-0 rounded-xl border border-border/70 bg-card p-5 shadow-sm">
      <h3 className="font-serif text-lg font-semibold text-foreground">
        {title}
      </h3>
      <div className="mt-3 text-sm leading-7 text-foreground/85">
        {children}
      </div>
    </section>
  )
}

function Notes({ items }: { items: string[] }) {
  return (
    <ol className="space-y-2">
      {items.map((item, index) => (
        <li key={`${index}-${item}`} className="flex min-w-0 gap-3">
          <span className="shrink-0 font-mono text-xs text-muted-foreground">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span className="min-w-0 break-words">{item}</span>
        </li>
      ))}
    </ol>
  )
}

export default function AlgorithmStagePanel({
  workspaceId,
  sourceVersionId,
  stage,
  stageState,
  proposals,
  onOpenSource,
}: {
  workspaceId: string
  sourceVersionId: string
  stage: DiscoveryStage
  stageState?: ProposalStageState
  proposals: PaperAlgorithmProposalResponse[]
  onOpenSource: (path: string) => void
}) {
  const { t } = useTranslation()
  const Icon = stageIcons[stage]
  const artifact = useQuery({
    queryKey: [
      "discovery-stage-artifact",
      workspaceId,
      stage,
      stageState?.run_id,
      stageState?.iteration,
      stageState?.status,
    ],
    queryFn: () => Llm4AdPapersService.getStageArtifact({ workspaceId, stage }),
    enabled: stage !== "discovery" && Boolean(stageState?.run_id),
    refetchOnWindowFocus: false,
  })
  if (stage === "discovery") {
    return (
      <AlgorithmDiscoveryPanel
        workspaceId={workspaceId}
        sourceVersionId={sourceVersionId}
        currentRunId={stageState?.run_id}
        proposals={proposals}
      />
    )
  }
  const payload = artifact.data?.artifact
  const stale =
    stageState?.status === "stale" ||
    (artifact.data && artifact.data.source_version_id !== sourceVersionId)
  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="shrink-0 border-b border-border/70 bg-muted/20 px-6 py-5">
        <div className="flex items-start gap-4">
          <span className="grid size-10 shrink-0 place-items-center rounded-lg border bg-background text-primary shadow-sm">
            <Icon className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-serif text-xl font-semibold">
                {t(`paper.workflow.stage.${stage}`)}
              </h2>
              {stale && (
                <Badge variant="secondary">{t("paper.workflow.stale")}</Badge>
              )}
              {stageState?.status === "needs_revision" && (
                <Badge variant="outline">
                  {t("paper.discovery.stageNeedsInput")}
                </Badge>
              )}
            </div>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {t(`paper.workflow.stage.${stage}Hint`)}
            </p>
          </div>
        </div>
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-3xl space-y-5 p-5 pb-10 md:p-7">
          {artifact.isLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("paper.discovery.loadingStage")}
            </div>
          )}
          {!stageState?.run_id && (
            <div className="rounded-xl border border-dashed p-8 text-center">
              <Icon className="mx-auto size-7 text-muted-foreground" />
              <p className="mt-4 font-medium">
                {t("paper.discovery.stageEmpty")}
              </p>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {t("paper.discovery.stageEmptyHint")}
              </p>
            </div>
          )}
          {artifact.isError && (
            <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
              {t("paper.discovery.stageLoadFailed")}
            </p>
          )}
          {payload && (
            <>
              <p className="border-l-2 border-primary/50 pl-4 font-serif text-base leading-7 text-foreground">
                {String(payload.summary ?? "")}
              </p>
              {stage === "discovery_baseline" &&
                (() => {
                  const result = payload as Baseline
                  return (
                    <>
                      <ResultSection
                        title={t("paper.discovery.baselineMethod")}
                      >
                        <p className="whitespace-pre-wrap">
                          {result.algorithm_summary}
                        </p>
                      </ResultSection>
                      {result.evidence.length > 0 && (
                        <ResultSection
                          title={t("paper.discovery.sourceEvidence")}
                        >
                          <div className="space-y-4">
                            {result.evidence.map((item, index) => (
                              <div
                                key={`${index}-${item.source_path}`}
                                className="border-b pb-4 last:border-0 last:pb-0"
                              >
                                <p>{item.claim}</p>
                                {item.source_quote && (
                                  <blockquote className="mt-2 border-l-2 pl-3 text-muted-foreground">
                                    {item.source_quote}
                                  </blockquote>
                                )}
                                <button
                                  type="button"
                                  onClick={() => onOpenSource(item.source_path)}
                                  className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
                                >
                                  {item.source_path}
                                  <ArrowUpRight className="size-3" />
                                </button>
                              </div>
                            ))}
                          </div>
                        </ResultSection>
                      )}
                      {result.unknowns.length > 0 && (
                        <ResultSection title={t("paper.discovery.unknowns")}>
                          <Notes items={result.unknowns} />
                        </ResultSection>
                      )}
                    </>
                  )
                })()}
              {stage === "discovery_candidates" &&
                (() => {
                  const result = payload as Candidates
                  return (
                    <>
                      {result.candidates.map((candidate) => (
                        <ResultSection
                          key={candidate.id}
                          title={candidate.title}
                        >
                          <div className="mb-3 flex flex-wrap gap-2">
                            <Badge
                              variant={
                                candidate.id === result.selected_candidate_id
                                  ? "default"
                                  : "secondary"
                              }
                            >
                              {candidate.id === result.selected_candidate_id
                                ? t("paper.discovery.selected")
                                : t("paper.discovery.candidate")}
                            </Badge>
                            <code className="text-xs text-muted-foreground">
                              {candidate.id}
                            </code>
                          </div>
                          <p className="font-medium">
                            {candidate.evolvable_boundary}
                          </p>
                          <p className="mt-2">{candidate.rationale}</p>
                          <p className="mt-2 text-muted-foreground">
                            {candidate.feasibility}
                          </p>
                          {candidate.evaluation_risks.length > 0 && (
                            <div className="mt-4">
                              <p className="font-medium">
                                {t("paper.discovery.risks")}
                              </p>
                              <Notes items={candidate.evaluation_risks} />
                            </div>
                          )}
                          <p className="mt-3 text-xs text-muted-foreground">
                            {candidate.source_refs.join(" · ")}
                          </p>
                        </ResultSection>
                      ))}
                      {result.open_questions.length > 0 && (
                        <ResultSection title={t("paper.discovery.unknowns")}>
                          <Notes items={result.open_questions} />
                        </ResultSection>
                      )}
                    </>
                  )
                })()}
              {stage === "discovery_evaluation" &&
                (() => {
                  const result = payload as Evaluation
                  return (
                    <>
                      <ResultSection
                        title={t("paper.discovery.evaluationContract")}
                      >
                        <div className="grid gap-4 sm:grid-cols-2">
                          <div>
                            <p className="text-xs text-muted-foreground">
                              {t("paper.discovery.metricDirection")}
                            </p>
                            <p>
                              {result.metrics.join(" · ") || "—"} ·{" "}
                              {result.optimization_direction ?? "—"}
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-muted-foreground">
                              {t("paper.discovery.baseline")}
                            </p>
                            <p>{result.baseline || "—"}</p>
                          </div>
                        </div>
                        <div className="mt-4">
                          <p className="text-xs text-muted-foreground">
                            {t("paper.discovery.dataProtocol")}
                          </p>
                          <p>{result.data_protocol || "—"}</p>
                        </div>
                        <div className="mt-4">
                          <p className="text-xs text-muted-foreground">
                            {t("paper.discovery.reproducibility")}
                          </p>
                          <p>{result.reproducibility || "—"}</p>
                        </div>
                      </ResultSection>
                      {result.validity_checks.length > 0 && (
                        <ResultSection
                          title={t("paper.discovery.validityChecks")}
                        >
                          <Notes items={result.validity_checks} />
                        </ResultSection>
                      )}
                      {result.unknowns.length > 0 && (
                        <ResultSection title={t("paper.discovery.unknowns")}>
                          <Notes items={result.unknowns} />
                        </ResultSection>
                      )}
                      <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <CircleHelp className="size-4" />
                        {t("paper.discovery.technicalOnly")}
                      </p>
                    </>
                  )
                })()}
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
