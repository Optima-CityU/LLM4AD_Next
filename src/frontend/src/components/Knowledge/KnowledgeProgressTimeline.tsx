import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

import type { KnowledgeParseRun, KnowledgeProgressEvent } from "./types"

const phases = [
  {
    id: "prepare",
    stages: new Set<string>(["queued", "preparing", "prepared", "starting", "protocol_adapter", "initializing"]),
  },
  {
    id: "analyze",
    stages: new Set<string>(["analyzing", "resuming", "compacting"]),
  },
  {
    id: "generate",
    stages: new Set<string>(["generating", "verifying", "generated"]),
  },
  {
    id: "review",
    stages: new Set<string>(["persisting", "review", "completed", "ready"]),
  },
] as const

function phaseIndex(stage?: string) {
  const index = phases.findIndex((phase) => phase.stages.has(stage || ""))
  return index < 0 ? 1 : index
}

function dedupeSteps(events: KnowledgeProgressEvent[]) {
  const ordered: KnowledgeProgressEvent[] = []
  const indexes = new Map<string, number>()
  for (const [eventIndex, event] of events.entries()) {
    if (event.type !== "step") continue
    const key = event.step_id || `${event.stage || "event"}-${eventIndex}`
    const existing = indexes.get(key)
    if (existing === undefined) {
      indexes.set(key, ordered.length)
      ordered.push(event)
    } else {
      ordered[existing] = { ...ordered[existing], ...event }
    }
  }
  return ordered
}

export default function KnowledgeProgressTimeline({
  run,
  events,
}: {
  run: KnowledgeParseRun
  events: KnowledgeProgressEvent[]
}) {
  const { t } = useTranslation()
  const currentIndex = phaseIndex(run.stage)
  const [expanded, setExpanded] = useState<string | null>(phases[currentIndex]?.id || "prepare")
  const steps = useMemo(() => dedupeSteps(events), [events])

  useEffect(() => {
    if (run.status === "pending" || run.status === "running") {
      setExpanded(phases[currentIndex]?.id || "analyze")
    } else {
      setExpanded(null)
    }
  }, [currentIndex, run.status])

  return (
    <div className="space-y-0.5" data-testid="knowledge-progress-timeline">
      {phases.map((phase, index) => {
        const phaseSteps = steps.filter((event) => phaseIndex(event.stage) === index)
        const failed = run.status === "failed" && currentIndex === index
        const complete = run.status === "ready" || currentIndex > index
        const active =
          (run.status === "pending" || run.status === "running") &&
          !complete &&
          currentIndex === index
        const open = expanded === phase.id
        return (
          <div key={phase.id} className="relative pl-6">
            {index < phases.length - 1 && (
              <span className="absolute left-[9px] top-6 h-[calc(100%-8px)] w-px bg-border" />
            )}
            <button
              type="button"
              className="flex min-h-8 w-full items-center gap-2 py-1 text-left text-xs"
              onClick={() => setExpanded(open ? null : phase.id)}
            >
              <span className="absolute left-0 flex size-5 items-center justify-center bg-background">
                {failed ? (
                  <XCircle className="size-4 text-destructive" />
                ) : complete ? (
                  <CheckCircle2 className="size-4 text-emerald-500" />
                ) : active ? (
                  <Loader2 className="size-4 animate-spin text-primary" />
                ) : (
                  <Circle className="size-3.5 text-muted-foreground/50" />
                )}
              </span>
              <span className={cn("font-medium", !active && !complete && "text-muted-foreground")}>
                {t(`knowledge.progressGroups.${phase.id}`)}
              </span>
              {phaseSteps.length > 0 && (
                <span className="text-[10px] text-muted-foreground">
                  {t("knowledge.parseWorkspace.stepCount", { count: phaseSteps.length })}
                </span>
              )}
              <span className="ml-auto text-muted-foreground">
                {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              </span>
            </button>
            {open && (
              <div className="mb-2 space-y-1 border-l border-border/70 pl-3">
                {phaseSteps.length === 0 ? (
                  <p className="py-1 text-[11px] text-muted-foreground">
                    {active ? run.message : t("knowledge.parseWorkspace.activityEmpty")}
                  </p>
                ) : phaseSteps.map((event, eventIndex) => {
                  const label = event.tool_name
                    ? t(`knowledge.parseWorkspace.tools.${event.tool_name}`, { defaultValue: event.tool_name })
                    : t(`knowledge.parseWorkspace.steps.${event.step_kind || "model"}`)
                  return (
                    <div key={event.step_id || eventIndex} className="py-1 text-[11px]">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{label}</span>
                        <span className="text-muted-foreground">
                          {t(`knowledge.parseWorkspace.stepStatus.${event.step_status || "running"}`)}
                        </span>
                        {typeof event.elapsed_seconds === "number" && (
                          <span className="ml-auto tabular-nums text-muted-foreground">
                            {t("knowledge.parseWorkspace.stepElapsed", { seconds: event.elapsed_seconds })}
                          </span>
                        )}
                      </div>
                      {event.message && <p className="mt-0.5 leading-4 text-muted-foreground">{event.message}</p>}
                      {event.step_detail && <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">{event.step_detail}</p>}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
