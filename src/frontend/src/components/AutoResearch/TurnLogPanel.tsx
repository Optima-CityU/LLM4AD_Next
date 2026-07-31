import { ChevronDown, ChevronRight, Loader2 } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { useResearchLogs } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import {
  logItemToStreamEntry,
  type StreamLogEntry,
  StreamLogList,
} from "./StreamLogConsole"

/**
 * 单个 turn 的内联折叠日志区。
 *
 * 默认折叠；展开时才按 ``turn_id`` 懒加载该轮持久化日志
 * （``useResearchLogs` + `turnId``，独立 research_log 表双端游标窗口）。活跃轮把
 * SSE 实时 tail（``liveLogs``）叠加进来，与已拉取的持久日志按 ``event_key`` 去重，
 * 保证「实时推送」与「刷新后重放」看到同一份、不重复。
 *
 * 排序：``useResearchLogs`` 恒返回升序，日志天然按时间升序；展开/新增时自动
 * 滚到底部看最新。向上「加载更早」用 ``loadOlder``（older_cursor + order=desc）。
 */
export default function TurnLogPanel({
  sessionId,
  turnId,
  defaultOpen,
  liveLogs,
}: {
  sessionId: string
  turnId: string
  /** 末轮默认展开；其余轮默认折叠、展开才拉取。 */
  defaultOpen: boolean
  /** 仅活跃轮传入 SSE 实时日志；非活跃轮传空数组。 */
  liveLogs: StreamLogEntry[]
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(defaultOpen)

  const q = useResearchLogs(sessionId, {
    turnId,
    enabled: open,
  })

  // 持久化日志（升序）+ 实时 tail，按 event_key（缺失退回 id）去重合并。
  const entries = useMemo<StreamLogEntry[]>(() => {
    const seen = new Set<string>()
    const out: StreamLogEntry[] = []
    const push = (e: StreamLogEntry) => {
      // 去重键优先级：stream_id（REST/SSE 同源、全局唯一，修 retry 复用 turn_id 时
      // event_key="<type>:<seq>" per-turn 计数器归零导致的撞键）> event_key > 合成 id。
      const sig = e.streamId ?? e.eventKey ?? e.id
      if (seen.has(sig)) return
      seen.add(sig)
      out.push(e)
    }
    for (const item of q.entries) push(logItemToStreamEntry(item))
    for (const e of liveLogs) push(e)
    return out
  }, [q.entries, liveLogs])

  // 展开态、条目变化时自动滚到底部（看最新）。
  const scrollRef = useRef<HTMLDivElement>(null)
  // biome-ignore lint/correctness/useExhaustiveDependencies: 随日志条数变化滚到底
  useEffect(() => {
    if (!open) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [open, entries.length])

  const count = entries.length

  return (
    <div className="mx-4 my-2 rounded-lg border border-border/60 bg-muted/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 hover:bg-muted/40"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <span>{t("autoResearch.chat.streamLogs.toggle")}</span>
        {count > 0 && (
          <span className="ml-1 font-normal normal-case text-muted-foreground/50">
            {t("autoResearch.chat.streamLogs.count", { count })}
          </span>
        )}
        {open && q.isLoading && (
          <Loader2 className="ml-auto h-3 w-3 animate-spin" />
        )}
      </button>

      {open && (
        <div
          ref={scrollRef}
          className="max-h-64 overflow-y-auto px-3 py-2 font-mono text-[11px]"
        >
          {q.hasOlder && (
            <button
              type="button"
              onClick={q.loadOlder}
              disabled={q.isFetchingOlder}
              className={cn(
                "mb-1 w-full rounded py-0.5 text-center text-[10px] text-muted-foreground/60 hover:bg-muted/40",
                q.isFetchingOlder && "opacity-60",
              )}
            >
              {q.isFetchingOlder ? (
                <Loader2 className="mx-auto h-3 w-3 animate-spin" />
              ) : (
                t("autoResearch.chat.streamLogs.loadEarlier")
              )}
            </button>
          )}
          {count === 0 && !q.isLoading ? (
            <div className="py-1 text-muted-foreground/60 italic">
              {t("autoResearch.chat.streamLogs.empty")}
            </div>
          ) : (
            <StreamLogList entries={entries} />
          )}
        </div>
      )}
    </div>
  )
}
