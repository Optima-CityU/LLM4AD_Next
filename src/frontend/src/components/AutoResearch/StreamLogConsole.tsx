import type { ResearchLogItem } from "@/client"
import {
  LEVEL_MESSAGE_STYLES,
  LEVEL_STYLES,
} from "@/components/Evolution/TaskDetail/log-renderers"
import { cn } from "@/lib/utils"

/**
 * A single log line captured from the SSE `type: "log"` frame.
 *
 * Fields mirror the backend payload emitted by `researchclaw` /
 * `LLM4ADAgentSandbox`:
 *   {"type": "log", "level": "INFO", "message": "...",
 *    "module": "...", "source": "arc" | "llm4ad" | ...,
 *    "ts": "2026-07-14T02:41:02.884+00:00"}
 */
export interface StreamLogEntry {
  /** 稳定 key：实时日志用 ``l:<seq>``，历史回放用 ``h:<message id>``。 */
  id: string
  eventKey?: string
  /** 本条对应的 Redis Stream entry id（<ms>-<seq>）。REST 与 SSE 同源一致、
   *  全局唯一（不受 retry 复用 turn_id 影响），作首选精确去重键。 */
  streamId?: string
  level: string
  message: string
  module?: string
  source?: string
  ts?: string
}

/**
 * 把独立 ``research_log`` 表的 :class:`ResearchLogItem` 还原成 :class:`StreamLogEntry`。
 *
 * 后端已把 log 从消息表拆到 ``/logs`` 端点，字段直接平铺（level/message/module/
 * source/ts），与实时 SSE ``type: "log"`` 同源，故两处可按 ``event_key`` 去重合并。
 */
export function logItemToStreamEntry(item: ResearchLogItem): StreamLogEntry {
  return {
    id: item.id,
    eventKey: item.event_key || undefined,
    streamId: item.stream_id ?? undefined,
    level: item.level || "INFO",
    message: item.message ?? "",
    module: item.module ?? undefined,
    source: item.source || undefined,
    ts: item.ts ?? item.created_time,
  }
}

/**
 * 单条日志行渲染（时刻 + level 色 + [source] + message）。
 *
 * 供 {@link TurnLogPanel} 的虚拟列表逐行调用。此前的 `StreamLogList`（带
 * `MAX_RENDERED` 尾部截断）已被虚拟列表取代删除——尾部截断与「加载更早」前插
 * 方向相反，会吃掉前插的更旧日志导致上翻失效，虚拟列表下不再需要 DOM 上限保护。
 */
const DEFAULT_MESSAGE_CLS = "text-gray-800 dark:text-gray-200"

export function LogLine({
  entry,
  wrap = false,
}: {
  entry: StreamLogEntry
  /** 长日志是否折行显示（默认 false=不换行、超宽横向滚动，贴合终端）。 */
  wrap?: boolean
}) {
  const time = entry.ts
    ? new Date(entry.ts).toLocaleTimeString(undefined, {
        hour12: false,
      })
    : ""
  // 等级色对齐右侧抽屉 ResearchLogPanel：label 用 LEVEL_STYLES、正文用 LEVEL_MESSAGE_STYLES
  // （比 label 柔和，整屏日志仍可读、又能一眼辨严重度）。缺省等级回退灰色。
  const level = String(entry.level ?? "").toUpperCase()
  const levelCls = LEVEL_STYLES[level] ?? "text-gray-500 dark:text-gray-400"
  const messageCls = LEVEL_MESSAGE_STYLES[level] ?? DEFAULT_MESSAGE_CLS
  // 默认 whitespace-pre（不换行、超宽横向滚动）对齐抽屉：长日志用横向滚动而非换行，
  // 更贴合终端。wrap=true 时容器去掉 whitespace-pre、正文 flex-1 折行——虚拟列表用
  // measureElement 动态测每行真实高度，折行变高会被自动校正，不破坏滚动定位。
  return (
    <div className={cn("flex gap-2 py-0.5 leading-5", !wrap && "whitespace-pre")}>
      {time && (
        <span className="text-muted-foreground/60 shrink-0 select-none">
          {time}
        </span>
      )}
      <span
        className={`shrink-0 w-16 whitespace-nowrap uppercase font-semibold select-none ${levelCls}`}
      >
        {entry.level || "LOG"}
      </span>
      {entry.source && (
        <span
          className="shrink-0 min-w-16 whitespace-nowrap text-primary/70"
          title={entry.module || undefined}
        >
          [{entry.source}]
        </span>
      )}
      <span
        className={cn(
          messageCls,
          wrap && "flex-1 min-w-0 whitespace-pre-wrap break-words",
        )}
      >
        {entry.message}
      </span>
    </div>
  )
}
