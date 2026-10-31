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
  ARLPeerAgent,
  ARLRole,
  ARLStreamItem,
  ARLSubagent,
  ARLSubagentStatus,
} from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

import ARLMessageItem from "./ARLMessageItem"
import { peerDisplayStatus } from "./arlStream"

/** Collapsed strip shows at most this many chips; the rest fold into "+N". */
const MAX_VISIBLE_CHIPS = 4

type FetchTimeline = (role: ARLRole, id: string) => Promise<ARLStreamItem[]>

interface Props {
  role: ARLRole
  subagents: ARLSubagent[]
  /** Peer agents the validation flow spawned in the same container. */
  peers: ARLPeerAgent[]
  /** Fetch one subagent's transcript (resolves with renderable rows). */
  onFetchTimeline: FetchTimeline
  /** Fetch one peer agent's transcript. */
  onFetchPeerTimeline: FetchTimeline
}

/** One chip in the strip: a provider subagent or a peer agent. */
interface StripEntry {
  key: string
  id: string
  kind: "subagent" | "peer"
  label: string
  /** Extra line for the tooltip and the sheet header. */
  subtitle: string | null
  status: ARLSubagentStatus
  /** Peer not loaded in the daemon (`closed`): viewing it would resume it. */
  unavailable: boolean
  createdAt: string
  updatedAt: string
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

function subagentEntry(subagent: ARLSubagent, fallback: string): StripEntry {
  return {
    key: `subagent:${subagent.id}`,
    id: subagent.id,
    kind: "subagent",
    // The task description names siblings in a fan-out best.
    label: subagent.description ?? subagent.title ?? fallback,
    subtitle: subagent.subtitle,
    status: subagent.status,
    unavailable: false,
    createdAt: subagent.createdAt,
    updatedAt: subagent.updatedAt,
  }
}

function peerEntry(peer: ARLPeerAgent, fallback: string): StripEntry {
  return {
    key: `peer:${peer.id}`,
    id: peer.id,
    kind: "peer",
    label: peer.title ?? peer.submission ?? fallback,
    subtitle: [peer.peerRole, peer.submission].filter(Boolean).join(" · "),
    status: peerDisplayStatus(peer),
    unavailable: peer.status === "closed",
    createdAt: peer.createdAt,
    updatedAt: peer.updatedAt,
  }
}

/**
 * Subagents and peer agents of a container, shown above the composer.
 *
 * Mirrors the native Paseo subagent track: their transcripts are kept out of
 * the main conversation and only their status is shown inline. Clicking a
 * chip opens the transcript in a side sheet, fetched on demand.
 *
 * Peers are top-level agents the validation flow spawned (`paseo run`), not
 * children of the main agent; a small badge tells them apart. A `closed` peer
 * is greyed out and cannot be opened, because fetching its timeline would
 * make the daemon resume its provider process.
 *
 * Renders nothing while there is neither.
 */
export default function ARLSubagentStrip({
  role,
  subagents,
  peers,
  onFetchTimeline,
  onFetchPeerTimeline,
}: Props) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [openKey, setOpenKey] = useState<string | null>(null)

  if (subagents.length === 0 && peers.length === 0) return null

  const subagentFallback = t("autoResearch.arl.subagentUntitled")
  const peerFallback = t("autoResearch.arl.peerUntitled")
  const entries = [
    ...subagents.map((s) => subagentEntry(s, subagentFallback)),
    ...peers.map((p) => peerEntry(p, peerFallback)),
  ]

  const running = entries.filter((e) => e.status === "running").length
  // Running ones first, newest first within each group: the strip is about
  // what is happening now.
  const ordered = [...entries].sort((a, b) => {
    const rank = (e: StripEntry) => (e.status === "running" ? 0 : 1)
    return rank(a) - rank(b) || b.createdAt.localeCompare(a.createdAt)
  })
  const visible = expanded ? ordered : ordered.slice(0, MAX_VISIBLE_CHIPS)
  const hidden = ordered.length - visible.length
  const open = entries.find((e) => e.key === openKey && !e.unavailable) ?? null

  return (
    <div className="mb-2 flex min-w-0 flex-wrap items-center gap-1.5">
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
        <GitBranch className="size-3" />
        {running > 0
          ? t("autoResearch.arl.subagentsRunning", {
              running,
              total: entries.length,
            })
          : t("autoResearch.arl.subagentsTotal", { total: entries.length })}
      </span>
      {visible.map((entry) => {
        const tooltip = entry.unavailable
          ? `${entry.label}\n${t("autoResearch.arl.peerClosed")}`
          : entry.subtitle
            ? `${entry.label}\n${entry.subtitle}`
            : entry.label
        return (
          <button
            key={entry.key}
            type="button"
            onClick={() => setOpenKey(entry.key)}
            disabled={entry.unavailable}
            title={tooltip}
            className={cn(
              "inline-flex h-6 max-w-[14rem] min-w-0 items-center gap-1.5 rounded-full border px-2 text-[11.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              entry.status === "running"
                ? "border-(--arl-accent)/35 bg-(--arl-accent)/8 text-foreground hover:border-(--arl-accent)/60"
                : "border-border/60 bg-background/60 text-muted-foreground enabled:hover:text-foreground",
            )}
          >
            <SubagentStatusIcon status={entry.status} />
            <span className="min-w-0 truncate">{entry.label}</span>
            {entry.kind === "peer" && (
              <span className="shrink-0 rounded-sm bg-muted px-1 text-[10px] leading-4 text-muted-foreground">
                {t("autoResearch.arl.peerBadge")}
              </span>
            )}
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
        onOpenChange={(value) => !value && setOpenKey(null)}
      >
        <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-xl">
          {open && (
            <SubagentTranscript
              key={open.key}
              role={role}
              entry={open}
              onFetchTimeline={
                open.kind === "peer" ? onFetchPeerTimeline : onFetchTimeline
              }
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}

/** Sheet body: header plus the transcript, refreshed on status change. */
function SubagentTranscript({
  role,
  entry,
  onFetchTimeline,
}: {
  role: ARLRole
  entry: StripEntry
  onFetchTimeline: FetchTimeline
}) {
  const { t } = useTranslation()
  const [items, setItems] = useState<ARLStreamItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Refetch whenever the status or update time moves, so an open sheet
  // follows a running agent to completion without streaming its timeline
  // into the pane state.
  // biome-ignore lint/correctness/useExhaustiveDependencies: status and update time are the refresh triggers
  useEffect(() => {
    let alive = true
    setError(null)
    onFetchTimeline(role, entry.id).then(
      (rows) => alive && setItems(rows),
      (err: Error) => {
        if (alive && err.message !== "Superseded") setError(err.message)
      },
    )
    return () => {
      alive = false
    }
  }, [role, entry.id, entry.status, entry.updatedAt, onFetchTimeline])

  return (
    <>
      <SheetHeader className="border-b border-border/60 px-5 py-4 pr-12">
        <SheetTitle className="flex min-w-0 items-center gap-2 text-[14px]">
          <SubagentStatusIcon status={entry.status} />
          <span className="min-w-0 truncate">{entry.label}</span>
        </SheetTitle>
        <SheetDescription className="truncate text-[11.5px]">
          {[
            t(`autoResearch.arl.subagentStatus.${entry.status}`),
            entry.kind === "peer" ? t("autoResearch.arl.peerBadge") : null,
            entry.subtitle,
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
        {entry.status === "running" && items !== null && (
          <p className="flex items-center gap-1.5 py-2 text-[11.5px] text-muted-foreground">
            <ChevronRight className="size-3" />
            {t("autoResearch.arl.subagentLive")}
          </p>
        )}
      </div>
    </>
  )
}
