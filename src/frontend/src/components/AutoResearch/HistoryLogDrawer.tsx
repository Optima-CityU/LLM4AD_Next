import { ArrowDown, ArrowUp, ChevronUp, History, Loader2, Terminal } from "lucide-react"
import { useRef } from "react"
import { useTranslation } from "react-i18next"

import type { ResearchTurnItem } from "@/client"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useResearchTurns } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import { type StreamLogEntry, StreamLogList } from "./StreamLogConsole"
import { TechCard } from "./tech"

export type DrawerTab = "logs" | "history"

interface Props {
  sessionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 打开时激活的 tab（点日志→logs，点历史→history）。 */
  tab: DrawerTab
  onTabChange: (tab: DrawerTab) => void
  /** 实时 + 历史合并后的日志。 */
  logs: StreamLogEntry[]
  /** 日志列表容器 ref（供打开/新日志到达时追尾）。 */
  logListRef?: React.Ref<HTMLDivElement>
  /** 是否还有更早的日志可加载。 */
  logsHasMore?: boolean
  /** 是否正在加载更多日志。 */
  logsFetchingMore?: boolean
  /** 加载更早的日志。 */
  onLoadMoreLogs?: () => void
}

/**
 * 底部抽屉：日志 / 运行历史二合一，顶部 tab 切换。
 *
 * 从底部输入栏的「日志」或「历史」按钮打开，`tab` 决定默认激活哪一个。
 * 日志复用 ``StreamLogList``；历史通过 ``useResearchTurns`` 拉取（仅在
 * 抽屉打开且切到 history 时才请求）。
 */
export default function HistoryLogDrawer({
  sessionId,
  open,
  onOpenChange,
  tab,
  onTabChange,
  logs,
  logListRef,
  logsHasMore,
  logsFetchingMore,
  onLoadMoreLogs,
}: Props) {
  const { t } = useTranslation()
  // 只有抽屉打开且当前在历史 tab 时才拉取轮次，避免无谓请求。
  const turnsQ = useResearchTurns(open && tab === "history" ? sessionId : null)
  const turns = turnsQ.data?.items ?? []

  // 内部日志容器 ref（用于到顶/到底滚动），与外部 logListRef 共享
  const innerLogRef = useRef<HTMLDivElement | null>(null)
  const setLogRef = (el: HTMLDivElement | null) => {
    innerLogRef.current = el
    if (typeof logListRef === "function") logListRef(el)
    else if (logListRef)
      (logListRef as React.MutableRefObject<HTMLDivElement | null>).current = el
  }

  const scrollLogsToTop = () => {
    innerLogRef.current?.scrollTo({ top: 0, behavior: "smooth" })
  }
  const scrollLogsToBottom = () => {
    if (innerLogRef.current) {
      innerLogRef.current.scrollTo({
        top: innerLogRef.current.scrollHeight,
        behavior: "smooth",
      })
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="h-[60vh] gap-0 p-0 sm:max-w-none">
        <SheetHeader className="border-b border-border/40 px-4 py-2.5 space-y-0">
          <SheetTitle className="sr-only">
            {t("autoResearch.chat.streamLogs.title")} /{" "}
            {t("autoResearch.chat.turnHistory")}
          </SheetTitle>
          <Tabs
            value={tab}
            onValueChange={(v) => onTabChange(v as DrawerTab)}
            className="w-full"
          >
            <div className="flex items-center gap-2">
              <TabsList className="h-8">
                <TabsTrigger value="logs" className="text-xs gap-1.5">
                  <Terminal className="size-3.5" />
                  {t("autoResearch.chat.streamLogs.title")}
                  <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-mono text-primary leading-none">
                    {logs.length}
                  </span>
                </TabsTrigger>
                <TabsTrigger value="history" className="text-xs gap-1.5">
                  <History className="size-3.5" />
                  {t("autoResearch.chat.turnHistory")}
                </TabsTrigger>
              </TabsList>
            </div>
          </Tabs>
        </SheetHeader>

        {tab === "logs" ? (
          <div className="relative min-h-0 flex-1">
            <div
              ref={setLogRef}
              className="h-full overflow-y-auto px-4 py-2 font-mono text-[11px] leading-relaxed"
            >
              {/* 加载更早日志按钮 */}
              {logsHasMore && (
                <div className="flex justify-center py-2">
                  <button
                    type="button"
                    onClick={onLoadMoreLogs}
                    disabled={logsFetchingMore}
                    className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
                  >
                    {logsFetchingMore ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <ChevronUp className="size-3" />
                    )}
                    {t("autoResearch.chat.loadMore")}
                  </button>
                </div>
              )}
              <StreamLogList entries={logs} />
            </div>

            {/* 滚动到顶部/底部按钮 */}
            {logs.length > 0 && (
              <div className="absolute bottom-4 right-6 flex flex-col gap-2 z-10">
                <button
                  type="button"
                  onClick={scrollLogsToTop}
                  title={t("autoResearch.chat.scrollToTop", {
                    defaultValue: "回到顶部",
                  })}
                  className="size-8 grid place-items-center rounded-full bg-background/90 backdrop-blur border border-border shadow-lg hover:bg-primary/10 hover:border-primary transition-colors"
                >
                  <ArrowUp className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={scrollLogsToBottom}
                  title={t("autoResearch.chat.scrollToBottom", {
                    defaultValue: "回到底部",
                  })}
                  className="size-8 grid place-items-center rounded-full bg-background/90 backdrop-blur border border-border shadow-lg hover:bg-primary/10 hover:border-primary transition-colors"
                >
                  <ArrowDown className="size-4" />
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
            {turnsQ.isLoading ? (
              <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
                <Loader2 className="size-4 animate-spin mr-2" />
                {t("autoResearch.chat.loading")}
              </div>
            ) : turns.length === 0 ? (
              <p className="py-8 text-center text-xs text-muted-foreground/60">
                {t("autoResearch.chat.noTurns")}
              </p>
            ) : (
              <ul className="space-y-1.5 py-1">
                {turns.map((turn, idx) => (
                  <TurnRow
                    key={turn.id}
                    turn={turn}
                    index={turns.length - idx}
                  />
                ))}
              </ul>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

function TurnRow({ turn, index }: { turn: ResearchTurnItem; index: number }) {
  const { t } = useTranslation()
  const duration =
    turn.started_at && turn.ended_at
      ? Math.max(
          0,
          Math.round(
            (new Date(turn.ended_at).getTime() -
              new Date(turn.started_at).getTime()) /
              1000,
          ),
        )
      : null

  return (
    <TechCard className="px-3 py-2.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-mono text-primary/80">#{index}</span>
        <TurnStatusBadge status={turn.status} />
        {turn.mode && (
          <span className="text-[10px] text-muted-foreground">{turn.mode}</span>
        )}
        <span className="ml-auto text-[10px] text-muted-foreground/60">
          {new Date(turn.created_time).toLocaleString()}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
        {(turn.from_stage || turn.to_stage) && (
          <span>
            stage {turn.from_stage ?? "?"} → {turn.to_stage ?? "?"}
          </span>
        )}
        {duration != null && (
          <span>
            {t("autoResearch.chat.duration")}: {duration}s
          </span>
        )}
        {turn.user_input && (
          <span className="truncate max-w-[220px]" title={turn.user_input}>
            "{turn.user_input}"
          </span>
        )}
      </div>
      {turn.error && (
        <p className="mt-1 text-[11px] text-destructive break-words">
          {turn.error}
        </p>
      )}
    </TechCard>
  )
}

function TurnStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const color =
    status === "running"
      ? "text-primary"
      : status === "paused_gate"
        ? "text-amber-500"
        : status === "collaborating"
          ? "text-primary"
          : status === "completed"
            ? "text-emerald-500"
            : status === "failed"
              ? "text-red-500"
              : "text-muted-foreground"
  return (
    <span
      className={cn(
        "text-[10px] uppercase tracking-wider font-semibold",
        color,
      )}
    >
      {t(`autoResearch.turnStatus.${status}`, status)}
    </span>
  )
}
