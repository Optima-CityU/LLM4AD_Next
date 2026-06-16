import { useVirtualizer } from "@tanstack/react-virtual"
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronUp,
  Download,
  Loader2,
  Search,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import type { TaskResponse } from "@/client"
import { Llm4AdTasksService } from "@/client"
import { Skeleton } from "@/components/ui/skeleton"
import { useEvolution } from "@/hooks/useEvolution"
import { normalizeLogEntries, useTaskLogsList } from "@/hooks/useTaskLogs"
import { INPUT_LIMITS } from "@/lib/inputLimits"
import { cn } from "@/lib/utils"
import {
  formatLogTime,
  LOG_LEVELS,
  type LogLevel,
  matchesLogLevels,
  renderEntry,
} from "./log-renderers"

interface LogsPanelProps {
  task: TaskResponse
}

export default function LogsPanel({ task }: LogsPanelProps) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const { effectiveTaskId, effectiveStatus, logEntries, logLoading } =
    useEvolution()

  const isActive =
    effectiveStatus === "pending" || effectiveStatus === "running"

  const [searchQuery, setSearchQuery] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [isExporting, setIsExporting] = useState(false)
  const [levelFilter, setLevelFilter] = useState<Set<LogLevel>>(new Set())

  const isNearBottomRef = useRef(true)

  const taskId = effectiveTaskId || task.id

  // Reset filter/search state on task switch
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on task switch
  useEffect(() => {
    setSearchQuery("")
    setSearchInput("")
    isNearBottomRef.current = true
    setLevelFilter(new Set())
  }, [taskId])

  // Shared REST query (terminal mode only) — multiple subscribers dedupe.
  // Active streaming uses logEntries from SSE, so disable here.
  const {
    entries: restEntries,
    isLoading: isInitialLoading,
    isFetchingMore: isLoadingMore,
    hasMore,
    loadMore,
  } = useTaskLogsList({
    taskId,
    searchQuery,
    levelFilter,
    enabled: !isActive && !!taskId,
  })

  const handleSearch = useCallback(() => {
    setSearchQuery(searchInput.trim())
  }, [searchInput])

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") handleSearch()
    },
    [handleSearch],
  )

  const clearSearch = useCallback(() => {
    setSearchInput("")
    setSearchQuery("")
  }, [])

  const handleExport = useCallback(async () => {
    if (isExporting || !taskId) return
    setIsExporting(true)
    try {
      const res = await Llm4AdTasksService.getTaskLogs({
        taskId,
        limit: 0,
        logType: "log",
      })
      const entries = normalizeLogEntries(
        (res.entries ?? []) as Record<string, unknown>[],
      )
      const lines = entries.map((entry) => {
        const ts = entry.timestamp ?? entry.ts
        const time = formatLogTime(ts)
        const level = String(entry.level ?? "").toUpperCase()
        const message = (entry.message ?? entry.msg ?? "") as string
        const mod = entry.module as string | undefined
        const fn = entry.function as string | undefined
        const line = entry.line as number | undefined
        let location = ""
        if (mod || fn) {
          const parts = [mod, fn].filter(Boolean).join(".")
          location = line != null ? ` ${parts}:${line}` : ` ${parts}`
        }
        return `[${time}] ${level} ${message}${location}`
      })
      const text = lines.join("\n")
      const fileName = `log${taskId.replace(/-/g, "")}.log`
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = fileName
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setIsExporting(false)
    }
  }, [taskId, isExporting])

  const rawEntries = isActive ? logEntries : restEntries
  // Streaming mode filters client-side; terminal mode uses server-side filters
  // (already baked into the query key) so rawEntries is the final list.
  const displayEntries = useMemo(() => {
    if (!isActive) return rawEntries
    let filtered = rawEntries
    if (levelFilter.size > 0) {
      filtered = filtered.filter((e) => matchesLogLevels(e, levelFilter))
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter((e) => {
        const msg = String(e.message ?? e.msg ?? "").toLowerCase()
        return msg.includes(q)
      })
    }
    return filtered
  }, [rawEntries, levelFilter, isActive, searchQuery])
  const isLoading = isActive ? logLoading : isInitialLoading

  const virtualizer = useVirtualizer({
    count: displayEntries.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 20,
    overscan: 30,
  })

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const threshold = 40
    isNearBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  }, [])

  // biome-ignore lint/correctness/useExhaustiveDependencies: taskId triggers scroll on task switch
  useEffect(() => {
    if (displayEntries.length > 0 && isNearBottomRef.current) {
      virtualizer.scrollToIndex(displayEntries.length - 1, { align: "end" })
    }
  }, [displayEntries.length, virtualizer, taskId])

  if (isLoading) {
    return (
      <div className="h-full w-full px-4 py-3 space-y-2 bg-card">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton
            key={i}
            className="h-4"
            style={{ width: `${60 + Math.random() * 35}%` }}
          />
        ))}
      </div>
    )
  }

  return (
    <div className="relative h-full w-full flex flex-col">
      {/* Search bar (always shown; export only in terminal mode) */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border/30 shrink-0">
        <Search className="size-3.5 text-muted-foreground shrink-0" />
        <input
          type="text"
          value={searchInput}
          maxLength={INPUT_LIMITS.search}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={handleSearchKeyDown}
          placeholder={t("evolution.logs.searchPlaceholder")}
          className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/50"
        />
        {searchInput && (
          <button
            type="button"
            onClick={clearSearch}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="size-3" />
          </button>
        )}
        <button
          type="button"
          onClick={handleSearch}
          className="text-[10px] px-1.5 py-0.5 rounded border border-border/60 text-muted-foreground
            hover:text-primary hover:border-primary/40 transition-colors"
        >
          {t("evolution.logs.search")}
        </button>
        {!isActive && (
          <button
            type="button"
            onClick={handleExport}
            disabled={isExporting}
            className="text-muted-foreground hover:text-primary transition-colors disabled:opacity-50 disabled:pointer-events-none"
            title={t("evolution.logs.export")}
          >
            {isExporting ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Download className="size-3.5" />
            )}
          </button>
        )}
      </div>

      {/* Level filter */}
      <div className="flex items-center gap-0.5 px-3 py-1.5 border-b border-border/30 shrink-0">
        <span className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mr-1">
          {t("evolution.logs.level", { defaultValue: "Level" })}
        </span>
        {LOG_LEVELS.map((lv) => {
          const isActiveLv = levelFilter.has(lv)
          return (
            <button
              key={lv}
              type="button"
              onClick={() =>
                setLevelFilter((prev) => {
                  const next = new Set(prev)
                  if (next.has(lv)) next.delete(lv)
                  else next.add(lv)
                  return next
                })
              }
              className={cn(
                "px-1.5 py-0.5 text-[10px] rounded font-medium transition-colors",
                isActiveLv
                  ? "text-primary bg-primary/10 border border-primary/30"
                  : "text-muted-foreground hover:text-foreground border border-transparent hover:border-border/40",
              )}
            >
              {lv}
            </button>
          )
        })}
        {levelFilter.size > 0 && (
          <button
            type="button"
            onClick={() => setLevelFilter(new Set())}
            className="ml-0.5 p-0.5 rounded text-muted-foreground/60 hover:text-foreground hover:bg-accent/60 transition-colors"
            title={t("evolution.logs.clearLevel", {
              defaultValue: "Clear filter",
            })}
          >
            <X className="size-3" />
          </button>
        )}
      </div>

      {displayEntries.length === 0 ? (
        <div className="h-full w-full px-4 py-3 text-xs font-mono bg-card">
          <div className="text-muted-foreground py-4">
            {searchQuery
              ? t("evolution.logs.noSearchResults")
              : levelFilter.size > 0 && rawEntries.length > 0
                ? t("evolution.logs.noLevelMatch", {
                    defaultValue: "No logs at selected levels",
                  })
                : t("evolution.logs.empty")}
          </div>
        </div>
      ) : (
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="flex-1 w-full overflow-auto px-4 py-3 text-xs font-mono bg-card"
        >
          {/* Load more button (terminal mode only) */}
          {!isActive && hasMore && (
            <div className="flex justify-center pb-2">
              <button
                type="button"
                onClick={loadMore}
                disabled={isLoadingMore}
                className="flex items-center gap-1.5 px-3 py-1 rounded text-[10px] font-medium
                  border border-border/60 text-muted-foreground
                  hover:text-primary hover:border-primary/40 transition-colors
                  disabled:opacity-50"
              >
                {isLoadingMore ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <ChevronUp className="size-3" />
                )}
                {t("evolution.logs.loadMore")}
              </button>
            </div>
          )}
          <div
            style={{
              height: virtualizer.getTotalSize(),
              width: "100%",
              position: "relative",
            }}
          >
            {virtualizer.getVirtualItems().map((virtualRow) => (
              <div
                key={virtualRow.key}
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "max-content",
                  minWidth: "100%",
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                {renderEntry(displayEntries[virtualRow.index])}
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="absolute bottom-2 right-4 flex flex-col gap-1">
        <button
          type="button"
          onClick={() => virtualizer.scrollToIndex(0, { align: "start" })}
          className="p-1 rounded bg-muted/80 text-muted-foreground opacity-80 hover:opacity-100 hover:bg-muted transition-opacity"
          title={t("evolution.logs.scrollToTop")}
        >
          <ArrowUpToLine className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={() =>
            virtualizer.scrollToIndex(displayEntries.length - 1, {
              align: "end",
            })
          }
          className="p-1 rounded bg-muted/80 text-muted-foreground opacity-80 hover:opacity-100 hover:bg-muted transition-opacity"
          title={t("evolution.logs.scrollToBottom")}
        >
          <ArrowDownToLine className="size-3.5" />
        </button>
      </div>
    </div>
  )
}
