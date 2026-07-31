import { History, Loader2, Maximize2, Minimize2, Terminal } from "lucide-react"
import { useState } from "react"
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

import ResearchLogPanel from "./ResearchLogPanel"
import { TechCard } from "./tech"

export type ResearchDrawerTab = "logs" | "history"

interface Props {
  sessionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 打开时激活的 tab（日志 / 运行历史）。 */
  tab: ResearchDrawerTab
  onTabChange: (tab: ResearchDrawerTab) => void
}

/**
 * 右侧面板唤起的底部抽屉：日志 / 运行历史二合一。
 *
 * 默认半屏（``h-[55vh]``），可在半屏 / 全屏（``h-screen``）间切换。日志走
 * {@link ResearchLogPanel}（双端游标窗口 + 搜索/筛选/下载/双向翻页）；运行历史
 * 走 ``useResearchTurns``，仅在抽屉打开且切到 history 时才请求。
 */
export default function ResearchLogDrawer({
  sessionId,
  open,
  onOpenChange,
  tab,
  onTabChange,
}: Props) {
  const { t } = useTranslation()
  const [full, setFull] = useState(false)

  // 只有抽屉打开且在历史 tab 时才拉取轮次，避免无谓请求。
  const turnsQ = useResearchTurns(
    open && tab === "history" ? sessionId : null,
  )
  const turns = turnsQ.data?.items ?? []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        className={cn(
          "gap-0 p-0 sm:max-w-none transition-[height] duration-200",
          full ? "h-screen" : "h-[55vh]",
        )}
      >
        <SheetHeader className="border-b border-border/40 px-4 py-2.5 space-y-0">
          <SheetTitle className="sr-only">
            {t("autoResearch.chat.streamLogs.title")} /{" "}
            {t("autoResearch.chat.turnHistory")}
          </SheetTitle>
          <div className="flex items-center gap-2">
            <Tabs
              value={tab}
              onValueChange={(v) => onTabChange(v as ResearchDrawerTab)}
            >
              <TabsList className="h-8">
                <TabsTrigger value="logs" className="text-xs gap-1.5">
                  <Terminal className="size-3.5" />
                  {t("autoResearch.chat.streamLogs.title")}
                </TabsTrigger>
                <TabsTrigger value="history" className="text-xs gap-1.5">
                  <History className="size-3.5" />
                  {t("autoResearch.chat.turnHistory")}
                </TabsTrigger>
              </TabsList>
            </Tabs>
            {/* 半屏 / 全屏切换，靠右；给右上角关闭按钮留位。 */}
            <button
              type="button"
              onClick={() => setFull((v) => !v)}
              title={
                full
                  ? t("autoResearch.chat.logs.halfScreen", {
                      defaultValue: "半屏",
                    })
                  : t("autoResearch.chat.logs.fullScreen", {
                      defaultValue: "全屏",
                    })
              }
              className="ml-auto mr-6 grid place-items-center size-7 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
            >
              {full ? (
                <Minimize2 className="size-3.5" />
              ) : (
                <Maximize2 className="size-3.5" />
              )}
            </button>
          </div>
        </SheetHeader>

        {tab === "logs" ? (
          <div className="min-h-0 flex-1">
            {open && (
              <ResearchLogPanel
                sessionId={sessionId}
                enabled={open && tab === "logs"}
              />
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
        <span className="font-mono text-primary/80 tabular-nums">#{index}</span>
        <TurnStatusBadge status={turn.status} />
        {turn.mode && (
          <span className="text-[10px] text-muted-foreground">
            {t(`autoResearch.mode.${turn.mode}`, turn.mode)}
          </span>
        )}
        <span className="ml-auto text-[10px] text-muted-foreground/60 tabular-nums">
          {new Date(turn.created_time).toLocaleString()}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
        {(turn.from_stage || turn.to_stage) && (
          <span className="tabular-nums">
            {t("autoResearch.chat.stageFlow", {
              from: turn.from_stage ?? "?",
              to: turn.to_stage ?? "?",
            })}
          </span>
        )}
        {duration != null && (
          <span className="tabular-nums">
            {t("autoResearch.chat.duration")}:{" "}
            {t("autoResearch.chat.durationSeconds", { seconds: duration })}
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
  // 进行中的轮次状态字加脉冲，与侧栏状态点、状态徽章保持一致。
  const live = status === "running" || status === "collaborating"
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-semibold",
        color,
      )}
    >
      {live && (
        <span className="relative inline-flex size-1.5">
          <span className="absolute inset-0 rounded-full bg-current opacity-60 animate-ping" />
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {t(`autoResearch.turnStatus.${status}`, status)}
    </span>
  )
}
