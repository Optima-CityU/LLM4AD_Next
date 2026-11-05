import {
  Bot,
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  GitBranch,
  Loader2,
} from "lucide-react"
import { Collapsible } from "radix-ui"
import { useTranslation } from "react-i18next"
import type { ProposalStageState } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { ResearchAgentProgress } from "@/lib/researchAgentProgress"
import { cn } from "@/lib/utils"

type Props = {
  stages: string[]
  stageStates: Record<string, ProposalStageState>
  progress: ResearchAgentProgress | null
  onOpenStage: (stage: string) => void
}

/** Shared execution timeline for the proposal and rebuttal coordinators. */
export default function ResearchAgentProgressPanel({
  stages,
  stageStates,
  progress,
  onOpenStage,
}: Props) {
  const { t } = useTranslation()
  const completed = stages.filter(
    (stage) => stageStates[stage]?.status === "ready",
  ).length
  const status =
    completed === stages.length &&
    stages.length > 0 &&
    progress?.status !== "running" &&
    progress?.status !== "waiting" &&
    progress?.status !== "interrupted"
      ? "completed"
      : (progress?.status ?? "ready")
  const running = status === "running" || status === "waiting"
  const toolLabel = (name: string) =>
    t(`paper.agentProgress.tools.${name}`, {
      defaultValue: t("paper.agentProgress.tools.other"),
    })

  const renderWorker = (agent: ResearchAgentProgress["agents"][number]) => (
    <Collapsible.Root
      key={agent.id}
      className="mt-3 rounded-lg border bg-muted/15"
    >
      <Collapsible.Trigger className="group flex w-full items-start gap-2 p-3 text-left text-sm hover:bg-muted/30">
        <GitBranch
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1 break-words leading-5">
          {agent.description || t("paper.agentProgress.worker")}
        </span>
        <ChevronRight
          className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none"
          aria-hidden="true"
        />
      </Collapsible.Trigger>
      <Collapsible.Content className="border-t px-3 pb-3">
        <p className="pt-3 text-xs text-muted-foreground">
          {t("paper.agentProgress.toolCount", {
            count: agent.toolCount,
          })}
        </p>
        <ul className="mt-2 flex flex-col gap-2">
          {agent.tools.map((tool, i) => (
            <li
              key={`${i}-${tool.name}`}
              className="flex items-center gap-2 text-sm"
            >
              {tool.failed ? (
                <CircleAlert className="size-3.5 text-destructive" />
              ) : tool.done ? (
                <Check className="size-3.5 text-primary" />
              ) : agent.status === "running" ? (
                <Loader2 className="size-3.5 motion-safe:animate-spin" />
              ) : (
                <Circle className="size-3.5 text-muted-foreground" />
              )}
              {toolLabel(tool.name)}
            </li>
          ))}
        </ul>
      </Collapsible.Content>
    </Collapsible.Root>
  )

  return (
    <div
      data-testid="research-agent-progress"
      className="h-full overflow-y-auto overscroll-contain px-4 py-5 sm:px-6"
    >
      <div className="mb-6 flex items-start gap-3 border-b pb-5">
        <div className="grid size-10 shrink-0 place-items-center rounded-xl border bg-primary/5 text-primary">
          <Bot className="size-5" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-serif text-lg font-semibold">
              {t("paper.agentProgress.coordinator")}
            </h2>
            <Badge variant="secondary" className="gap-1.5">
              {running && (
                <span className="size-1.5 rounded-full bg-primary motion-safe:animate-pulse" />
              )}
              {t(`paper.agentProgress.status.${status}`)}
            </Badge>
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {progress?.currentTool && running
              ? toolLabel(progress.currentTool)
              : t("paper.agentProgress.description")}
          </p>
          <p className="mt-2 text-xs tabular-nums text-muted-foreground">
            {t("paper.agentProgress.published", {
              count: completed,
              total: stages.length,
            })}
          </p>
        </div>
      </div>
      <ol
        className="flex flex-col"
        aria-label={t("paper.agentProgress.timeline")}
      >
        {stages.map((stage, index) => {
          const workers =
            progress?.agents.filter((agent) => agent.stage === stage) ?? []
          const worker = workers[workers.length - 1]
          const state = stageStates[stage]
          const published = state?.status === "ready"
          const active = worker?.status === "running"
          const failed = worker?.status === "failed"
          const stageStatus = active
            ? "running"
            : published
              ? "completed"
              : state?.status === "needs_revision"
                ? "needsInput"
                : failed
                  ? "failed"
                  : worker?.status === "interrupted"
                    ? "interrupted"
                    : state?.status === "stale"
                      ? "stale"
                      : worker?.status === "completed"
                        ? "reviewing"
                        : "pending"
          const Icon = active
            ? Loader2
            : published
              ? Check
              : failed
                ? CircleAlert
                : Circle
          return (
            <li key={stage} className="relative flex gap-3 pb-6 last:pb-0">
              {index < stages.length - 1 && (
                <span
                  aria-hidden="true"
                  className="absolute left-[13px] top-8 bottom-0 w-px bg-border"
                />
              )}
              <span
                className={cn(
                  "relative z-0 mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border bg-background",
                  active && "border-primary text-primary",
                  published && "text-primary",
                  failed && "text-destructive",
                )}
              >
                <Icon
                  className={cn(
                    "size-3.5",
                    active && "motion-safe:animate-spin",
                  )}
                  aria-hidden="true"
                />
              </span>
              <div className="min-w-0 flex-1 pt-0.5">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <h3 className="text-base font-medium">
                    {t(`paper.workflow.stage.${stage}`)}
                  </h3>
                  <span
                    className={cn(
                      "text-xs text-muted-foreground",
                      active && "text-primary",
                    )}
                  >
                    {t(`paper.agentProgress.status.${stageStatus}`)}
                  </span>
                </div>
                <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
                  {t(`paper.workflow.stage.${stage}Hint`)}
                </p>
                {workers.map(renderWorker)}
                {state && state.status !== "stale" && (
                  <Button
                    type="button"
                    size="sm"
                    variant="link"
                    className="mt-2 h-auto px-0"
                    onClick={() => onOpenStage(stage)}
                  >
                    {t("paper.agentProgress.openResult")}
                    <ChevronRight data-icon="inline-end" />
                  </Button>
                )}
              </div>
            </li>
          )
        })}
      </ol>
      {progress?.agents.some((agent) => !stages.includes(agent.stage)) && (
        <section
          className="mt-6 border-t pt-5"
          aria-label={t("paper.agentProgress.helpers")}
        >
          <h3 className="text-base font-medium">
            {t("paper.agentProgress.helpers")}
          </h3>
          {progress.agents
            .filter((agent) => !stages.includes(agent.stage))
            .map(renderWorker)}
        </section>
      )}
      <p role="status" aria-live="polite" className="sr-only">
        {t(`paper.agentProgress.status.${status}`)}
      </p>
    </div>
  )
}
