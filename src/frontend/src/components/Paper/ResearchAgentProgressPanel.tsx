import {
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  Lightbulb,
  Loader2,
} from "lucide-react"
import { Collapsible } from "radix-ui"
import { useTranslation } from "react-i18next"
import type { ProposalStageState } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  isPublishedResearchStage,
  isResearchStepActive,
  type ResearchAgentProgress,
  researchProgressSteps,
} from "@/lib/researchAgentProgress"
import { cn } from "@/lib/utils"

type Props = {
  stages?: string[]
  stageStates: Record<string, ProposalStageState>
  progress: ResearchAgentProgress | null
  emptyMessage?: string
  onOpenStage: (stage: string) => void
}

/** Present research goals, design reasoning and outcomes as stable logical stages. */
export default function ResearchAgentProgressPanel({
  stageStates,
  progress,
  emptyMessage,
  onOpenStage,
}: Props) {
  const { t } = useTranslation()
  const steps = researchProgressSteps(progress, stageStates)
  const rawStatus = progress?.status ?? "ready"
  const activeIndex = steps.reduce(
    (latest, step, index) => (isResearchStepActive(step) ? index : latest),
    -1,
  )
  const currentIndex =
    activeIndex >= 0
      ? activeIndex
      : ["failed", "interrupted"].includes(
            steps[steps.length - 1]?.status ?? "",
          )
        ? steps.length - 1
        : -1
  const status =
    rawStatus === "failed" || rawStatus === "interrupted" || activeIndex < 0
      ? rawStatus
      : steps[activeIndex].status
  const running = status === "running" || status === "waiting"

  return (
    <div
      data-testid="research-agent-progress"
      className="h-full overflow-y-auto overscroll-contain px-4 py-5 sm:px-6"
    >
      <header className="mb-6 flex items-start gap-3 border-b pb-5">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl border bg-primary/5 text-primary">
          <Lightbulb className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-serif text-xl font-semibold">
              {t("paper.agentProgress.title")}
            </h2>
            <Badge variant="secondary" className="gap-1.5 text-sm">
              {running && (
                <span className="size-1.5 rounded-full bg-primary motion-safe:animate-pulse" />
              )}
              {t(`paper.agentProgress.status.${status}`)}
            </Badge>
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {t("paper.agentProgress.description")}
          </p>
        </div>
      </header>
      <ol
        className="flex flex-col"
        aria-label={t("paper.agentProgress.timeline")}
      >
        {steps.map((step, index) => {
          const active = isResearchStepActive(step)
          const current = index === currentIndex
          const canOpen =
            step.status === "completed" &&
            isPublishedResearchStage(stageStates[step.stage])
          const title =
            step.title.trim() ||
            t(`paper.workflow.stage.${step.stage}`, {
              defaultValue: t("paper.agentProgress.logicalStep"),
            })
          const goal =
            step.goal.trim() ||
            t(`paper.agentProgress.guidance.${step.stage}.goal`, {
              defaultValue: t("paper.agentProgress.defaultGoal"),
            })
          const approach =
            step.approach.trim() ||
            t(`paper.agentProgress.guidance.${step.stage}.approach`, {
              defaultValue: t(`paper.workflow.stage.${step.stage}Hint`, {
                defaultValue: t("paper.agentProgress.defaultApproach"),
              }),
            })
          const outcome =
            step.summary.trim() ||
            t(
              step.status === "completed"
                ? "paper.agentProgress.outcomeCompleted"
                : step.status === "waiting"
                  ? "paper.agentProgress.outcomeWaiting"
                  : step.status === "failed" || step.status === "interrupted"
                    ? "paper.agentProgress.outcomeInterrupted"
                    : "paper.agentProgress.outcomePending",
            )
          const Icon =
            step.status === "running"
              ? Loader2
              : step.status === "completed"
                ? Check
                : step.status === "failed" || step.status === "interrupted"
                  ? CircleAlert
                  : Circle
          return (
            <li
              key={step.id}
              data-step-id={step.id}
              data-step-status={step.status}
              className="relative flex gap-3 pb-6 last:pb-0"
            >
              {index < steps.length - 1 && (
                <span
                  aria-hidden="true"
                  className="absolute left-[13px] top-8 bottom-0 w-px bg-border"
                />
              )}
              <span
                className={cn(
                  "relative z-0 mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border bg-background",
                  active && "border-primary text-primary",
                  step.status === "completed" && "text-primary",
                  ["failed", "interrupted"].includes(step.status) &&
                    "text-destructive",
                )}
              >
                <Icon
                  aria-hidden="true"
                  className={cn(
                    "size-4",
                    step.status === "running" && "motion-safe:animate-spin",
                  )}
                />
              </span>
              <Collapsible.Root
                key={`${step.id}:${current}:${step.status}`}
                defaultOpen={current}
                className="min-w-0 flex-1 overflow-hidden rounded-lg border bg-card"
              >
                <Collapsible.Trigger className="group flex w-full items-start gap-3 px-4 py-4 text-left hover:bg-muted/25 focus-visible:outline-2 focus-visible:outline-ring">
                  <span className="min-w-0 flex-1 break-words font-serif text-lg font-semibold leading-7">
                    {title}
                  </span>
                  <span
                    className={cn(
                      "mt-1 shrink-0 text-sm text-muted-foreground",
                      active && "text-primary",
                    )}
                  >
                    {t(`paper.agentProgress.status.${step.status}`)}
                  </span>
                  <ChevronRight
                    aria-hidden="true"
                    className="mt-1.5 size-4 shrink-0 text-muted-foreground group-data-[state=open]:rotate-90"
                  />
                </Collapsible.Trigger>
                <Collapsible.Content className="border-t px-4 py-4">
                  <dl className="space-y-5">
                    {[
                      [t("paper.agentProgress.goal"), goal],
                      [t("paper.agentProgress.approach"), approach],
                      [t("paper.agentProgress.outcome"), outcome],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <dt className="mb-1 text-sm font-semibold text-foreground">
                          {label}
                        </dt>
                        <dd className="whitespace-pre-wrap break-words text-[15px] leading-7 text-foreground/85">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </Collapsible.Content>
                {canOpen && (
                  <div className="border-t px-4 py-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="link"
                      className="h-auto px-0 text-sm"
                      onClick={() => onOpenStage(step.stage)}
                    >
                      {t("paper.agentProgress.openResult")}
                      <ChevronRight data-icon="inline-end" />
                    </Button>
                  </div>
                )}
              </Collapsible.Root>
            </li>
          )
        })}
      </ol>
      {steps.length === 0 && (
        <p className="text-[15px] leading-7 text-muted-foreground">
          {emptyMessage ?? t("paper.agentProgress.empty")}
        </p>
      )}
      <p role="status" aria-live="polite" className="sr-only">
        {t(`paper.agentProgress.status.${status}`)}
      </p>
    </div>
  )
}
