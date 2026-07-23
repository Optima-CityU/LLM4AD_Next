import { useTranslation } from "react-i18next"

import type { ResearchMessageItem } from "@/client"
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
  level: string
  message: string
  module?: string
  source?: string
  ts?: string
}

/**
 * 把持久化的 ``event_type === "log"`` 消息还原成 :class:`StreamLogEntry`。
 *
 * log 消息落库时 payload 即事件本体（含 level/message/module/source/ts），与实时
 * SSE ``type: "log"`` 同源，故两处可按 ``event_key`` 去重合并。
 */
export function messageToLogEntry(
  message: ResearchMessageItem,
): StreamLogEntry {
  const payload = (message.payload ?? {}) as Record<string, unknown>
  return {
    id: message.id,
    eventKey: message.event_key || undefined,
    level: (payload.level as string) || "INFO",
    message: (payload.message as string) ?? message.content ?? "",
    module: payload.module as string | undefined,
    source: payload.source as string | undefined,
    ts: (payload.ts as string | undefined) ?? message.created_time,
  }
}

/** 最多渲染的日志行数：长跑可产生上万行，只保留尾部若干行进 DOM。 */
const MAX_RENDERED = 500

/**
 * 纯日志列表渲染：空态提示、尾部截断保护、逐行渲染。
 *
 * 由调用方（底部输入栏的日志抽屉）套上容器与滚动区。entries 由 ChatPanel 合并
 * 持久化历史（``event_type === "log"``）与实时 SSE ``type: "log"`` tail 提供，
 * 因此刷新后与已结束会话仍可见完整日志。
 */
export function StreamLogList({ entries }: { entries: StreamLogEntry[] }) {
  const { t } = useTranslation()

  if (entries.length === 0) {
    return (
      <div className="py-1 text-muted-foreground/60 italic">
        {t("autoResearch.chat.streamLogs.waiting")}
      </div>
    )
  }

  // 只渲染尾部 MAX_RENDERED 行，超出部分用一条提示替代（DOM 上限保护）。
  const overflow = entries.length - MAX_RENDERED
  const rendered = overflow > 0 ? entries.slice(overflow) : entries

  return (
    <>
      {overflow > 0 && (
        <div className="py-1 text-muted-foreground/50 italic">
          {t("autoResearch.chat.streamLogs.truncated", { count: overflow })}
        </div>
      )}
      {rendered.map((e) => (
        <LogLine key={e.id} entry={e} />
      ))}
    </>
  )
}

function LogLine({ entry }: { entry: StreamLogEntry }) {
  const time = entry.ts
    ? new Date(entry.ts).toLocaleTimeString(undefined, {
        hour12: false,
      })
    : ""
  return (
    <div className="flex gap-2 py-0.5 whitespace-pre-wrap wrap-break-word">
      {time && (
        <span className="text-muted-foreground/60 shrink-0">{time}</span>
      )}
      <span
        className={cn(
          "shrink-0 w-16 whitespace-nowrap uppercase font-semibold",
          levelColor(entry.level),
        )}
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
      <span className="text-foreground/90 min-w-0">{entry.message}</span>
    </div>
  )
}

function levelColor(level: string) {
  switch (level?.toUpperCase()) {
    case "ERROR":
    case "CRITICAL":
    case "FATAL":
      return "text-destructive"
    case "WARN":
    case "WARNING":
      return "text-amber-500"
    case "INFO":
      return "text-emerald-500"
    case "DEBUG":
    case "TRACE":
      return "text-muted-foreground"
    default:
      return "text-foreground/70"
  }
}
