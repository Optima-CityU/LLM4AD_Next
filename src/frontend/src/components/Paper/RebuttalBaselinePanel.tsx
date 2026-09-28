import {
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  FileText,
  ScanSearch,
  ShieldAlert,
  Target,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import type {
  RebuttalBaselineConcern,
  RebuttalBaselineView,
} from "@/lib/rebuttalBaseline"

type RebuttalBaselinePanelProps = {
  baseline: RebuttalBaselineView
  stale: boolean
}

type ConcernGroup = {
  reviewerId: string
  displayLabel: string
  concerns: RebuttalBaselineConcern[]
}

const severityTone: Record<RebuttalBaselineConcern["severity"], string> = {
  high: "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  medium:
    "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  low: "border-slate-500/25 bg-slate-500/10 text-slate-600 dark:text-slate-300",
}

function ConcernRow({ concern }: { concern: RebuttalBaselineConcern }) {
  const { t } = useTranslation()
  const hasResponsePlan = Boolean(
    concern.answerSource || concern.draftMove || concern.sourceRefs.length,
  )

  return (
    <article className="min-w-0 px-5 py-6 sm:px-6">
      <div className="flex min-w-0 items-start gap-4">
        <span className="grid size-10 shrink-0 place-items-center border bg-muted/25 font-serif text-base font-semibold">
          {concern.label}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="break-all font-mono text-xs text-muted-foreground"
              translate="no"
            >
              {concern.id}
            </span>
            <Badge
              variant="outline"
              className={`text-xs font-medium ${severityTone[concern.severity]}`}
            >
              {t(`paper.rebuttal.severity.${concern.severity}`)}
            </Badge>
            {concern.concernType && (
              <span className="break-words text-xs text-muted-foreground">
                {concern.concernType}
              </span>
            )}
          </div>
          <p className="mt-3 break-words text-[15px] leading-7 text-foreground [overflow-wrap:anywhere]">
            {concern.concern}
          </p>

          {hasResponsePlan && (
            <details className="group mt-3 min-w-0">
              <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md py-1.5 text-sm font-medium text-sky-700 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none dark:text-sky-300 [&::-webkit-details-marker]:hidden">
                <span>{t("paper.rebuttal.responsePlan")}</span>
                <ChevronDown
                  aria-hidden="true"
                  className="size-4 transition-transform group-open:rotate-180 motion-reduce:transition-none"
                />
              </summary>
              <div className="mt-2 space-y-3 border-l-2 border-sky-500/20 pl-4 text-[15px] leading-7">
                {concern.answerSource && (
                  <p className="break-words [overflow-wrap:anywhere]">
                    <span className="font-semibold">
                      {t("paper.rebuttal.answerSource")}：
                    </span>
                    <span className="text-muted-foreground">
                      {concern.answerSource}
                    </span>
                  </p>
                )}
                {concern.draftMove && (
                  <p className="break-words [overflow-wrap:anywhere]">
                    <span className="font-semibold">
                      {t("paper.rebuttal.draftMove")}：
                    </span>
                    <span className="text-muted-foreground">
                      {concern.draftMove}
                    </span>
                  </p>
                )}
                {concern.sourceRefs.length > 0 && (
                  <div className="flex min-w-0 flex-wrap gap-2">
                    {concern.sourceRefs.map((sourceRef) => (
                      <Badge
                        key={sourceRef}
                        variant="outline"
                        className="max-w-full break-all font-mono text-xs font-normal"
                        translate="no"
                      >
                        {sourceRef}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </details>
          )}
        </div>
      </div>
    </article>
  )
}

function NoteList({ items }: { items: string[] }) {
  return (
    <ul className="space-y-3 text-[15px] leading-7 text-muted-foreground">
      {items.map((item, index) => (
        <li key={`${index}-${item}`} className="flex min-w-0 gap-3">
          <span
            aria-hidden="true"
            className="mt-2.5 size-1.5 shrink-0 rounded-full bg-current"
          />
          <span className="min-w-0 break-words [overflow-wrap:anywhere]">
            {item}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Present one reviewer slice of the published concern baseline at a time. */
export default function RebuttalBaselinePanel({
  baseline,
  stale,
}: RebuttalBaselinePanelProps) {
  const { t } = useTranslation()
  const [requestedReviewerId, setRequestedReviewerId] = useState<string | null>(
    null,
  )
  const concernGroups = useMemo<ConcernGroup[]>(() => {
    const concernsByReviewer = new Map<string, RebuttalBaselineConcern[]>()
    for (const concern of baseline.concerns) {
      const concerns = concernsByReviewer.get(concern.reviewerId) ?? []
      concerns.push(concern)
      concernsByReviewer.set(concern.reviewerId, concerns)
    }

    const groups = baseline.reviewerSources.map((source) => ({
      reviewerId: source.reviewerId,
      displayLabel: source.displayLabel,
      concerns: concernsByReviewer.get(source.reviewerId) ?? [],
    }))
    const knownReviewers = new Set(groups.map((group) => group.reviewerId))
    for (const [reviewerId, concerns] of concernsByReviewer) {
      if (!knownReviewers.has(reviewerId)) {
        groups.push({ reviewerId, displayLabel: reviewerId, concerns })
      }
    }
    return groups
  }, [baseline.concerns, baseline.reviewerSources])
  const selectedGroup =
    concernGroups.find((group) => group.reviewerId === requestedReviewerId) ??
    concernGroups[0]
  const highPriorityCount = baseline.concerns.filter(
    (concern) => concern.severity === "high",
  ).length
  const venue = [baseline.constraints.venue, baseline.constraints.venueYear]
    .filter(Boolean)
    .join(" ")
  const constraintLabels = [
    venue,
    baseline.constraints.responseMode,
    baseline.constraints.outputFormat,
  ].filter(Boolean)

  return (
    <section
      data-testid="rebuttal-baseline"
      className="@container min-w-0 overflow-hidden border border-border/80 border-t-4 border-t-primary/45 bg-card/70 shadow-[0_16px_40px_-32px_hsl(var(--foreground)/0.4)]"
    >
      <header className="border-b bg-background/75 px-5 py-6 sm:px-7">
        <div className="flex min-w-0 items-start gap-4">
          <span className="grid size-11 shrink-0 place-items-center border border-sky-500/20 bg-sky-500/[0.08] text-sky-700 dark:text-sky-300">
            <ScanSearch aria-hidden="true" className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2.5">
              <h3 className="font-serif text-xl font-semibold tracking-tight text-pretty">
                {t("paper.rebuttal.baselineTitle")}
              </h3>
              <Badge
                variant="outline"
                className={
                  stale
                    ? "border-amber-500/30 bg-amber-500/10 text-xs text-amber-700 dark:text-amber-300"
                    : baseline.readyForGeneration
                      ? "border-emerald-500/30 bg-emerald-500/10 text-xs text-emerald-700 dark:text-emerald-300"
                      : "border-amber-500/30 bg-amber-500/10 text-xs text-amber-700 dark:text-amber-300"
                }
              >
                {!stale && baseline.readyForGeneration ? (
                  <CheckCircle2 aria-hidden="true" className="size-3.5" />
                ) : (
                  <ShieldAlert aria-hidden="true" className="size-3.5" />
                )}
                {t(
                  stale
                    ? "paper.rebuttal.baselineStale"
                    : baseline.readyForGeneration
                      ? "paper.rebuttal.baselineReady"
                      : "paper.rebuttal.baselineNeedsInput",
                )}
              </Badge>
            </div>
            <p className="mt-3 max-w-3xl break-words text-[15px] leading-7 text-muted-foreground [overflow-wrap:anywhere]">
              {baseline.summary}
            </p>
            <div className="mt-5 grid grid-cols-3 divide-x border-y text-center tabular-nums">
              <div className="px-2 py-3">
                <div className="font-serif text-xl font-semibold text-foreground">
                  {concernGroups.length}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {t("paper.rebuttal.baselineReviewers")}
                </div>
              </div>
              <div className="px-2 py-3">
                <div className="font-serif text-xl font-semibold text-foreground">
                  {baseline.concerns.length}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {t("paper.rebuttal.baselineConcerns")}
                </div>
              </div>
              <div className="px-2 py-3">
                <div className="font-serif text-xl font-semibold text-rose-700 dark:text-rose-300">
                  {highPriorityCount}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {t("paper.rebuttal.highPriority")}
                </div>
              </div>
            </div>
            {constraintLabels.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {constraintLabels.map((label) => (
                  <Badge
                    key={label}
                    variant="outline"
                    className="max-w-full break-all bg-background/70 font-mono text-xs font-normal"
                    translate="no"
                  >
                    {label}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="space-y-6 p-5 sm:p-7">
        {concernGroups.length > 0 && selectedGroup ? (
          <div className="grid min-w-0 gap-5 @min-[38rem]:grid-cols-[11.5rem_minmax(0,1fr)]">
            <section
              aria-labelledby="baseline-reviewer-navigation"
              className="min-w-0"
            >
              <h4
                id="baseline-reviewer-navigation"
                className="mb-3 font-serif text-base font-semibold"
              >
                {t("paper.rebuttal.reviewerConcernNav")}
              </h4>
              <div className="flex max-w-full gap-2 overflow-x-auto pb-1 @min-[38rem]:flex-col @min-[38rem]:overflow-visible">
                {concernGroups.map((group, index) => {
                  const selected = group.reviewerId === selectedGroup.reviewerId
                  return (
                    <button
                      key={group.reviewerId}
                      type="button"
                      aria-pressed={selected}
                      className={`flex min-h-12 shrink-0 items-center gap-2 border-l-2 px-3 py-2.5 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none @min-[38rem]:w-full ${
                        selected
                          ? "border-l-primary bg-primary/[0.06] text-foreground"
                          : "border-l-transparent text-muted-foreground hover:bg-muted/35 hover:text-foreground"
                      }`}
                      onClick={() => setRequestedReviewerId(group.reviewerId)}
                    >
                      <span className="font-mono text-xs">R{index + 1}</span>
                      <span className="max-w-36 truncate font-medium">
                        {group.displayLabel}
                      </span>
                      <span className="tabular-nums text-xs">
                        {group.concerns.length}
                      </span>
                    </button>
                  )
                })}
              </div>
            </section>

            <section
              aria-labelledby="selected-reviewer-concerns"
              className="min-w-0 overflow-hidden border bg-background/70"
            >
              <div className="flex min-w-0 items-center justify-between gap-3 border-b bg-muted/15 px-5 py-4 sm:px-6">
                <div className="min-w-0">
                  <h4
                    id="selected-reviewer-concerns"
                    className="truncate font-serif text-lg font-semibold"
                  >
                    {selectedGroup.displayLabel}
                  </h4>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    {t("paper.rebuttal.concernTotal", {
                      count: selectedGroup.concerns.length,
                    })}
                  </p>
                </div>
              </div>
              {selectedGroup.concerns.length > 0 ? (
                <div className="divide-y">
                  {selectedGroup.concerns.map((concern) => (
                    <ConcernRow key={concern.id} concern={concern} />
                  ))}
                </div>
              ) : (
                <div className="px-5 py-8 text-center text-sm text-muted-foreground">
                  {t("paper.rebuttal.noBaselineConcerns")}
                </div>
              )}
            </section>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed px-5 py-8 text-center text-sm text-muted-foreground">
            {t("paper.rebuttal.noBaselineConcerns")}
          </div>
        )}

        {(baseline.paperSummary ||
          baseline.findings.length > 0 ||
          baseline.openQuestions.length > 0) && (
          <details className="group min-w-0 border bg-muted/[0.06]">
            <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 font-serif text-base font-semibold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
              <FileText
                aria-hidden="true"
                className="size-4 text-muted-foreground"
              />
              <span className="min-w-0 flex-1">
                {t("paper.rebuttal.baselineSupportingContext")}
              </span>
              <ChevronDown
                aria-hidden="true"
                className="size-4 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none"
              />
            </summary>
            <div className="space-y-6 border-t px-5 py-5 sm:px-6">
              {baseline.paperSummary && (
                <section>
                  <h5 className="mb-2 font-serif text-base font-semibold">
                    {t("paper.rebuttal.paperSummary")}
                  </h5>
                  <p className="break-words text-[15px] leading-7 text-muted-foreground [overflow-wrap:anywhere]">
                    {baseline.paperSummary}
                  </p>
                </section>
              )}
              {baseline.findings.length > 0 && (
                <section>
                  <h5 className="mb-2 flex items-center gap-2 font-serif text-base font-semibold">
                    <Target aria-hidden="true" className="size-4" />
                    {t("paper.rebuttal.baselineFindings")}
                  </h5>
                  <NoteList items={baseline.findings} />
                </section>
              )}
              {baseline.openQuestions.length > 0 && (
                <section>
                  <h5 className="mb-2 flex items-center gap-2 font-serif text-base font-semibold text-amber-700 dark:text-amber-300">
                    <CircleHelp aria-hidden="true" className="size-4" />
                    {t("paper.rebuttal.baselineOpenQuestions")}
                  </h5>
                  <NoteList items={baseline.openQuestions} />
                </section>
              )}
            </div>
          </details>
        )}
      </div>
    </section>
  )
}
