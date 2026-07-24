import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import type { TaskStatus } from "@/client"
import { Llm4AdTasksService } from "@/client"
import type { LogLevel } from "@/components/Evolution/TaskDetail/log-renderers"
import {
  DEMO_LOG_ENTRIES,
  isDemoTaskId,
  visibleLogCount,
} from "@/data/demoFixtures"
import { useDemoState } from "@/hooks/useDemoMode"
import { taskKeys } from "@/lib/task-queries"
import { authFetch } from "@/utils/auth"

export type LogEntry = { _kind?: "system" | "log"; [key: string]: unknown }

export interface UseTaskLogsReturn {
  entries: LogEntry[]
  isLoading: boolean
  error: string | null
}

export interface UseTaskLogsOptions {
  onResetTask?: () => void
  onStatusChange?: (status: string) => void
  onGenerated?: (entry: Record<string, unknown>) => void
  onMemoryCardCreated?: (entry: Record<string, unknown>) => void
  onMemoryInjected?: (entry: Record<string, unknown>) => void
}

function nowISO() {
  return new Date().toISOString()
}

function reconnectDelayMs(attempt: number) {
  return Math.min(1000 * 2 ** Math.max(attempt - 1, 0), 5000)
}

export function normalizeLogEntries(
  raw: Record<string, unknown>[],
): LogEntry[] {
  return raw
    .filter((e) => {
      const t = e.type as string | undefined
      return t !== "generated" && t !== "status"
    })
    .map((e): LogEntry => {
      const t = e.type as string | undefined
      if (t === "log") return { _kind: "log", ...e }
      if (t === "system" || t === "error" || t === "end")
        return { _kind: "system", event: t, ...e }
      return e as LogEntry
    })
}

export function useTaskLogs(
  taskId: string,
  taskStatus: TaskStatus,
  enabled: boolean,
  options?: UseTaskLogsOptions,
): UseTaskLogsReturn {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const isDemo = isDemoTaskId(taskId)
  const demoState = useDemoState()
  // Demo tasks never go through SSE; treat them as inactive so the streaming
  // effect below short-circuits.
  const isActive =
    !isDemo && (taskStatus === "pending" || taskStatus === "running")

  const onResetTaskRef = useRef(options?.onResetTask)
  onResetTaskRef.current = options?.onResetTask
  const onStatusChangeRef = useRef(options?.onStatusChange)
  onStatusChangeRef.current = options?.onStatusChange
  const onGeneratedRef = useRef(options?.onGenerated)
  onGeneratedRef.current = options?.onGenerated
  const onMemoryCardCreatedRef = useRef(options?.onMemoryCardCreated)
  onMemoryCardCreatedRef.current = options?.onMemoryCardCreated
  const onMemoryInjectedRef = useRef(options?.onMemoryInjected)
  onMemoryInjectedRef.current = options?.onMemoryInjected

  // ---------- SSE path (active states only) ----------
  const [streamEntries, setStreamEntries] = useState<LogEntry[]>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)
  const entriesBuffer = useRef<LogEntry[]>([])
  const rafId = useRef<number>(0)

  useEffect(() => {
    if (!enabled || !isActive) {
      setStreamEntries([])
      setStreamLoading(false)
      setStreamError(null)
      entriesBuffer.current = []
      return
    }

    queryClient.removeQueries({ queryKey: taskKeys.logs(taskId) })

    setStreamLoading(true)
    setStreamError(null)
    entriesBuffer.current = []
    setStreamEntries([])

    let cancelled = false
    let reconnectAttempt = 0
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let activeAbortController: AbortController | null = null
    let lastEventId = "0-0"

    const flushEntries = () => {
      if (!cancelled) setStreamEntries([...entriesBuffer.current])
    }

    const scheduleFlush = () => {
      cancelAnimationFrame(rafId.current)
      rafId.current = requestAnimationFrame(flushEntries)
    }

    const pushSystem = (
      event: string,
      message: string,
      extra?: Record<string, unknown>,
    ) => {
      entriesBuffer.current.push({
        _kind: "system",
        event,
        message,
        timestamp: nowISO(),
        ...extra,
      })
      scheduleFlush()
    }

    const baseUrl = import.meta.env.VITE_API_URL || ""
    const buildStreamUrl = () => {
      const url = `${baseUrl}/api/v1/llm4ad/tasks/${taskId}/logs/stream`
      if (lastEventId === "0-0") return url
      const params = new URLSearchParams({ last_id: lastEventId })
      return `${url}?${params.toString()}`
    }

    const connect = async () => {
      activeAbortController?.abort()
      activeAbortController = new AbortController()
      let terminal = false
      try {
        const response = await authFetch(buildStreamUrl(), {
          signal: activeAbortController.signal,
        })

        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        setStreamLoading(false)
        setStreamError(null)
        reconnectAttempt = 0

        const reader = response.body!.getReader()
        const decoder = new TextDecoder()
        let buffer = ""
        let currentEvent = ""
        let currentData = ""
        let currentId = ""

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() ?? ""

          for (const line of lines) {
            if (line.startsWith("event:")) {
              currentEvent = line.slice(6).trim()
            } else if (line.startsWith("id:")) {
              currentId = line.slice(3).trim()
            } else if (line.startsWith("data:")) {
              currentData = line.slice(5).trim()
            } else if (line.trim() === "") {
              if (currentId) lastEventId = currentId
              switch (currentEvent) {
                case "connected":
                  pushSystem(
                    "connected",
                    currentData || t("evolution.logStream.connected"),
                  )
                  break

                case "heartbeat":
                  break

                case "timeout":
                  pushSystem("timeout", t("evolution.logStream.timeout"))
                  if (!cancelled) onResetTaskRef.current?.()
                  terminal = true
                  currentEvent = ""
                  currentData = ""
                  return

                case "done":
                  if (!cancelled) {
                    queryClient.invalidateQueries({
                      queryKey: taskKeys.detail(taskId),
                    })
                    queryClient.invalidateQueries({
                      queryKey: taskKeys.allTasks,
                    })
                    onStatusChangeRef.current?.("done")
                  }
                  terminal = true
                  currentEvent = ""
                  currentData = ""
                  return

                case "data":
                case "message":
                case "": {
                  if (!currentData) break
                  try {
                    const parsed = JSON.parse(currentData)
                    const type = parsed.type as string | undefined

                    switch (type) {
                      case "error":
                        pushSystem(
                          "error",
                          parsed.error_message ??
                            parsed.message ??
                            t("evolution.logStream.taskError"),
                        )
                        if (!cancelled) onResetTaskRef.current?.()
                        terminal = true
                        currentEvent = ""
                        currentData = ""
                        return

                      case "system":
                        pushSystem(
                          "system",
                          parsed.message ?? "",
                          parsed.timestamp
                            ? { timestamp: parsed.timestamp }
                            : undefined,
                        )
                        break

                      case "end":
                        pushSystem(
                          "end",
                          parsed.message ?? t("evolution.logStream.taskEnd"),
                        )
                        if (!cancelled) onResetTaskRef.current?.()
                        terminal = true
                        currentEvent = ""
                        currentData = ""
                        return

                      case "log":
                        entriesBuffer.current.push({
                          _kind: "log",
                          ...parsed,
                        })
                        scheduleFlush()
                        break

                      case "print":
                        entriesBuffer.current.push(parsed as LogEntry)
                        scheduleFlush()
                        break

                      case "generated":
                        if (!cancelled) onGeneratedRef.current?.(parsed)
                        break

                      case "memory_card_created":
                        if (!cancelled) onMemoryCardCreatedRef.current?.(parsed)
                        break

                      case "mindmemos_memory_injected":
                        if (!cancelled) onMemoryInjectedRef.current?.(parsed)
                        break

                      case "status":
                        if (!cancelled)
                          onStatusChangeRef.current?.(
                            parsed.status ?? parsed.data?.status,
                          )
                        break

                      default:
                        break
                    }
                  } catch {
                    // skip malformed JSON
                  }
                  break
                }

                default:
                  break
              }

              currentEvent = ""
              currentData = ""
              currentId = ""
            }
          }
        }
      } catch (err: unknown) {
        if (!cancelled && (err as Error).name !== "AbortError") {
          setStreamError(
            (err as Error).message || t("evolution.logStream.connectionFailed"),
          )
          setStreamLoading(false)
        }
      }

      if (!cancelled && !terminal) {
        reconnectAttempt += 1
        setStreamLoading(true)
        setStreamError(t("evolution.logStream.reconnecting"))
        reconnectTimer = setTimeout(connect, reconnectDelayMs(reconnectAttempt))
      }
    }

    void connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      activeAbortController?.abort()
      cancelAnimationFrame(rafId.current)
    }
  }, [taskId, isActive, enabled, queryClient, t])

  // ---------- Return ----------
  if (isDemo) {
    // Slice the demo fixture by phase so the logs "fill up" as the user
    // clicks through the walkthrough. Each entry already carries the full
    // {level, module, function, line, ...} shape that log-renderers expects,
    // so the panel renders identically to a real run.
    const visibleCount = visibleLogCount(demoState.phase)
    const entries = DEMO_LOG_ENTRIES.slice(0, visibleCount)
    return { entries, isLoading: false, error: null }
  }
  if (isActive) {
    return {
      entries: streamEntries,
      isLoading: streamLoading,
      error: streamError,
    }
  }

  return { entries: [], isLoading: false, error: null }
}

/**
 * Shared paginated logs query for terminal tasks.
 *
 * Backed by useInfiniteQuery so multiple panels (LogsPanel, RunLogsSection)
 * with the same (taskId, searchQuery, levelFilter) share a single network
 * fetch via React Query's cache. Cursor pages return older entries — they are
 * surfaced in chronological order (oldest first).
 */
export interface UseTaskLogsListResult {
  entries: LogEntry[]
  isLoading: boolean
  isFetchingMore: boolean
  hasMore: boolean
  loadMore: () => void
}

/**
 * Log types fetched for the evolution logs panel, joined as a comma-separated
 * `log_type` query param. The backend splits on commas and matches any of them.
 */
const EVOLUTION_LOG_TYPES = [
  "print",
  "status",
  "final_state",
  "system",
  "log",
  "error",
].join(",")

export function useTaskLogsList(opts: {
  taskId: string | null | undefined
  searchQuery: string
  levelFilter: Set<LogLevel>
  enabled: boolean
}): UseTaskLogsListResult {
  const { taskId, searchQuery, levelFilter, enabled } = opts
  const levelArr = useMemo(() => [...levelFilter], [levelFilter])
  const isDemo = isDemoTaskId(taskId)

  const query = useInfiniteQuery({
    queryKey: taskKeys.logsList(
      taskId ?? "",
      EVOLUTION_LOG_TYPES,
      searchQuery,
      levelArr,
    ),
    queryFn: ({ pageParam }) =>
      Llm4AdTasksService.getTaskLogs({
        taskId: taskId!,
        cursor: pageParam || undefined,
        q: searchQuery || undefined,
        logType: EVOLUTION_LOG_TYPES,
        level: levelArr.length > 0 ? levelArr : undefined,
        limit: 1000,
      }),
    initialPageParam: "" as string,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    // Demo short-circuit: never hit the live REST endpoint for the demo
    // task — its history comes from the fixture via `useTaskLogs` already.
    enabled: enabled && !!taskId && !isDemo,
    staleTime: 5 * 60_000,
  })

  const entries = useMemo<LogEntry[]>(() => {
    if (!query.data) return []
    // Pages are loaded newest → older. Reverse so display is oldest-first.
    const pages = [...query.data.pages].reverse()
    return pages.flatMap((p) =>
      normalizeLogEntries((p.entries ?? []) as Record<string, unknown>[]),
    )
  }, [query.data])

  if (isDemo) {
    // Demo task: return the fixture slice directly, never report loading
    // or "more pages available".
    return {
      entries: DEMO_LOG_ENTRIES,
      isLoading: false,
      isFetchingMore: false,
      hasMore: false,
      loadMore: () => {},
    }
  }

  const lastPage = query.data?.pages[query.data.pages.length - 1]

  return {
    entries,
    isLoading: query.isLoading,
    isFetchingMore: query.isFetchingNextPage,
    hasMore: lastPage?.has_more ?? false,
    loadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) query.fetchNextPage()
    },
  }
}
