import {
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  GitBranch,
  Loader2,
  TriangleAlert,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import type {
  ARLRole,
  ARLStreamItem,
  ARLSubagent,
  ARLSubagentStatus,
} from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

import ARLMessageItem from "./ARLMessageItem"

/** Collapsed strip shows at most this many chips; the rest fold into "+N". */
const MAX_VISIBLE_CHIPS = 4

interface Props {
  role: ARLRole
  subagents: ARLSubagent[]
  /** Fetch one subagent's transcript (resolves with renderable rows). */
  onFetchTimeline: (
    role: ARLRole,
    subagentId: string,
  ) => Promise<ARLStreamItem[]>
}

/** Status icon per subagent lifecycle state. */
function SubagentStatusIcon({ status }: { status: ARLSubagentStatus }) {
  switch (status) {
    case "running":
      return (
        <Loader2 className="size-3 shrink-0 animate-spin text-(--arl-accent-fg)" />
      )
    case "completed":
      return (
        <CheckCircle2 className="size-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
      )
    case "failed":
      return <TriangleAlert className="size-3 shrink-0 text-destructive" />
    default:
      return (
        <CircleSlash className="size-3 shrink-0 text-muted-foreground/70" />
      )
  }
}

/** Display label: the task description names siblings in a fan-out best. */
function subagentLabel(subagent: ARLSubagent, fallback: string): string {
  return subagent.description ?? subagent.title ?? fallback
}

/**
 * Subagents of a container's main agent, shown above the composer.
 *
 * Mirrors the native Paseo subagent track: subagent transcripts are kept out
 * of the main conversation (which already carries a `sub_agent` tool row per
 * spawn), and only their status is shown inline. Clicking a chip opens the
 * transcript in a side sheet, fetched on demand.
 *
 * Renders nothing while the agent has no subagents.
 */
export default function ARLSubagentStrip({
  role,
  subagents,
  onFetchTimeline,
}: Props) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)

  if (subagents.length === 0) return null

  const running = subagents.filter((s) => s.status === "running").length
  // Running ones first, newest first within each group: the strip is about
  // what is happening now.
  const ordered = [...subagents].sort((a, b) => {
    const rank = (s: ARLSubagent) => (s.status === "running" ? 0 : 1)
    return rank(a) - rank(b) || b.createdAt.localeCompare(a.createdAt)
  })
  const visible = expanded ? ordered : ordered.slice(0, MAX_VISIBLE_CHIPS)
  const hidden = ordered.length - visible.length
  const open = subagents.find((s) => s.id === openId) ?? null
  const fallback = t("autoResearch.arl.subagentUntitled")

  return (
    <div className="mb-2 flex min-w-0 flex-wrap items-center gap-1.5">
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
        <GitBranch className="size-3" />
        {running > 0
          ? t("autoResearch.arl.subagentsRunning", {
              running,
              total: subagents.length,
            })
          : t("autoResearch.arl.subagentsTotal", { total: subagents.length })}
      </span>
      {visible.map((subagent) => {
        const label = subagentLabel(subagent, fallback)
        return (
          <button
            key={subagent.id}
            type="button"
            onClick={() => setOpenId(subagent.id)}
            title={subagent.subtitle ? `${label}\n${subagent.subtitle}` : label}
            className={cn(
              "inline-flex h-6 max-w-[14rem] min-w-0 items-center gap-1.5 rounded-full border px-2 text-[11.5px] transition-colors",
              subagent.status === "running"
                ? "border-(--arl-accent)/35 bg-(--arl-accent)/8 text-foreground hover:border-(--arl-accent)/60"
                : "border-border/60 bg-background/60 text-muted-foreground hover:text-foreground",
            )}
          >
            <SubagentStatusIcon status={subagent.status} />
            <span className="min-w-0 truncate">{label}</span>
          </button>
        )
      })}
      {(hidden > 0 || expanded) && ordered.length > MAX_VISIBLE_CHIPS && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="h-6 shrink-0 rounded-full px-2 text-[11.5px] text-muted-foreground hover:text-foreground"
        >
          {expanded ? t("autoResearch.arl.subagentsCollapse") : `+${hidden}`}
        </button>
      )}

      <Sheet
        open={open !== null}
        onOpenChange={(value) => !value && setOpenId(null)}
      >
        <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-xl">
          {open && (
            <SubagentTranscript
              key={open.id}
              role={role}
              subagent={open}
              label={subagentLabel(open, fallback)}
              onFetchTimeline={onFetchTimeline}
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}

/** Sheet body: header plus the subagent's transcript, refreshed on status change. */
function SubagentTranscript({
  role,
  subagent,
  label,
  onFetchTimeline,
}: {
  role: ARLRole
  subagent: ARLSubagent
  label: string
  onFetchTimeline: Props["onFetchTimeline"]
}) {
  const { t } = useTranslation()
  const [items, setItems] = useState<ARLStreamItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Refetch whenever the subagent's status or update time moves, so an open
  // sheet follows a running subagent to completion without streaming its
  // timeline into the pane state.
  // biome-ignore lint/correctness/useExhaustiveDependencies: status and update time are the refresh triggers
  useEffect(() => {
    let alive = true
    setError(null)
    onFetchTimeline(role, subagent.id).then(
      (rows) => alive && setItems(rows),
      (err: Error) => {
        if (alive && err.message !== "Superseded") setError(err.message)
      },
    )
    return () => {
      alive = false
    }
  }, [role, subagent.id, subagent.status, subagent.updatedAt, onFetchTimeline])

  return (
    <>
      <SheetHeader className="border-b border-border/60 px-5 py-4 pr-12">
        <SheetTitle className="flex min-w-0 items-center gap-2 text-[14px]">
          <SubagentStatusIcon status={subagent.status} />
          <span className="min-w-0 truncate">{label}</span>
        </SheetTitle>
        <SheetDescription className="truncate text-[11.5px]">
          {[
            t(`autoResearch.arl.subagentStatus.${subagent.status}`),
            subagent.subtitle,
          ]
            .filter(Boolean)
            .join(" · ")}
        </SheetDescription>
      </SheetHeader>
      <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-5 py-3">
        {error ? (
          <p className="py-6 text-center text-[12px] text-destructive">
            {error}
          </p>
        ) : items === null ? (
          <p className="flex items-center justify-center gap-2 py-6 text-[12px] text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            {t("autoResearch.arl.loadingHistory")}
          </p>
        ) : items.length === 0 ? (
          <p className="py-6 text-center text-[12px] text-muted-foreground">
            {t("autoResearch.arl.subagentEmpty")}
          </p>
        ) : (
          items.map((item) => <ARLMessageItem key={item.id} item={item} />)
        )}
        {subagent.status === "running" && items !== null && (
          <p className="flex items-center gap-1.5 py-2 text-[11.5px] text-muted-foreground">
            <ChevronRight className="size-3" />
            {t("autoResearch.arl.subagentLive")}
          </p>
        )}
      </div>
    </>
  )
}
