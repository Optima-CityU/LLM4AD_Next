import { useVirtualizer } from "@tanstack/react-virtual"
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  ChevronUp,
  Download,
  DownloadCloud,
  Loader2,
  Search,
  WrapText,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ResearchLogItem } from "@/client"
import { Llm4AdResearchService } from "@/client"
import {
  LEVEL_MESSAGE_STYLES,
  LEVEL_STYLES,
  LOG_LEVELS,
  formatLogTime,
  type LogLevel,
} from "@/components/Evolution/TaskDetail/log-renderers"
import { useResearchLogs } from "@/hooks/useAutoResearch"
import { INPUT_LIMITS } from "@/lib/inputLimits"
import { cn } from "@/lib/utils"

interface Props {
  sessionId: string
  /** 只看某一轮的日志（默认整会话）。 */
  turnId?: string | null
  /** 抽屉打开时才拉取，折叠不请求。 */
  enabled?: boolean
}

const DEFAULT_MESSAGE_CLS = "text-gray-800 dark:text-gray-200"

/** 把一条日志格式化成一行纯文本（导出用，与正文渲染同构）。 */
function formatLogLine(entry: ResearchLogItem): string {
  const time = formatLogTime(entry.ts ?? entry.created_time)
  const level = String(entry.level ?? "").toUpperCase()
  const location = [
    entry.module,
    entry.stage != null ? `stage${entry.stage}` : null,
  ]
    .filter(Boolean)
    .join(" ")
  return `[${time}] ${level} ${entry.message}${location ? ` ${location}` : ""}`
}

/** 把若干条日志导出为 .log 文件并触发浏览器下载。 */
function downloadLogFile(items: ResearchLogItem[], sessionId: string) {
  const blob = new Blob([items.map(formatLogLine).join("\n")], {
    type: "text/plain;charset=utf-8",
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `research-log-${sessionId.slice(0, 8)}.log`
  a.click()
  URL.revokeObjectURL(url)
}

/** 每个日志等级筛选按钮的专属配色（选中 / 未选中），与日志正文等级色一致。 */
const LEVEL_FILTER_STYLES: Record<LogLevel, { on: string; off: string }> = {
  INFO: {
    on: "text-green-700 bg-green-500/15 border-green-500/40 dark:text-green-300",
    off: "text-green-600/70 dark:text-green-400/70 border-transparent hover:border-green-500/30 hover:text-green-600 dark:hover:text-green-400",
  },
  WARNING: {
    on: "text-yellow-700 bg-yellow-500/15 border-yellow-500/40 dark:text-yellow-300",
    off: "text-yellow-600/70 dark:text-yellow-400/70 border-transparent hover:border-yellow-500/30 hover:text-yellow-600 dark:hover:text-yellow-400",
  },
  ERROR: {
    on: "text-red-700 bg-red-500/15 border-red-500/40 dark:text-red-300",
    off: "text-red-600/70 dark:text-red-400/70 border-transparent hover:border-red-500/30 hover:text-red-600 dark:hover:text-red-400",
  },
}

/** 渲染单条 research 日志行（对齐 evolution 日志样式）。wrap=true 时长日志折行。 */
function renderResearchLog(entry: ResearchLogItem, wrap: boolean) {
  const level = String(entry.level ?? "").toUpperCase()
  const levelCls = LEVEL_STYLES[level] ?? "text-gray-500 dark:text-gray-400"
  const messageCls = LEVEL_MESSAGE_STYLES[level] ?? DEFAULT_MESSAGE_CLS
  const time = formatLogTime(entry.ts ?? entry.created_time)
  const location = [
    entry.module,
    entry.stage != null ? `stage${entry.stage}` : null,
  ]
    .filter(Boolean)
    .join(" ")

  return (
    <div
      className={cn(
        "leading-5 hover:bg-black/[0.03] dark:hover:bg-white/[0.03]",
        wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre",
      )}
    >
      {time && <span className="text-gray-500 select-none">[{time}] </span>}
      {level && <span className={`${levelCls} select-none`}>{level}</span>}
      <span className={messageCls}> {entry.message}</span>
      {location && (
        <span className="text-gray-500 dark:text-gray-600 ml-2 text-[10px]">
          {location}
        </span>
      )}
    </div>
  )
}

/**
 * AutoResearch 日志面板：搜索 + 等级筛选 + 下载 + 虚拟滚动，
 * 顶部「加载更早」、底部「获取最新」双向翻页，右下角到顶/到底。
 *
 * 数据来自 {@link useResearchLogs}（双端游标窗口）。搜索与等级筛选走服务端
 * （直接进 query key），面板本身只管交互与渲染。
 */
export default function ResearchLogPanel({
  sessionId,
  turnId,
  enabled = true,
}: Props) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)
  // 追尾判定：上一帧末条 id 与条数（区分首屏 / 后续追加）。
  const prevLastIdRef = useRef<string | null>(null)
  const prevCountRef = useRef(0)
  // 翻页后要滚到的一端：'top'=顶部加载更早后滚到最前，'bottom'=底部获取最新后滚到最底。
  const pendingScrollRef = useRef<"top" | "bottom" | null>(null)

  const [searchInput, setSearchInput] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [levelFilter, setLevelFilter] = useState<Set<LogLevel>>(new Set())
  const [isExporting, setIsExporting] = useState(false)
  // 长日志折行：默认开（贴合面板宽度、不用横向拖）。虚拟列表 measureElement 动态测高，
  // 折行变高会自动校正，不破坏滚动定位。
  const [wrap, setWrap] = useState(true)

  const levelArr = useMemo(() => Array.from(levelFilter), [levelFilter])

  const {
    entries,
    isLoading,
    isFetchingOlder,
    isFetchingLatest,
    hasOlder,
    error,
    loadOlder,
    loadLatest,
  } = useResearchLogs(sessionId, {
    turnId,
    level: levelArr,
    q: searchQuery,
    enabled,
    limit: 500,
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

  // 下载全部：limit=0 一次从后端拉全部匹配行（含当前搜索/等级筛选）。
  const handleExportAll = useCallback(async () => {
    if (isExporting || !sessionId) return
    setIsExporting(true)
    try {
      const res = await Llm4AdResearchService.listLogs({
        sessionId,
        turnId: turnId ?? undefined,
        limit: 0,
        level: levelArr.length ? levelArr : undefined,
        q: searchQuery || undefined,
      })
      downloadLogFile(res.items ?? [], sessionId)
    } finally {
      setIsExporting(false)
    }
  }, [sessionId, turnId, levelArr, searchQuery, isExporting])

  // 下载当前：纯前端，直接导出内存里已累积的 entries，不请求后端。
  const handleExportLoaded = useCallback(() => {
    if (!sessionId || entries.length === 0) return
    downloadLogFile(entries, sessionId)
  }, [sessionId, entries])

  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 20,
    overscan: 30,
  })

  // 切换折行时每行真实高度变了，强制虚拟列表重测，避免总高与定位错位。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅 wrap 变化时需重测
  useEffect(() => {
    virtualizer.measure()
  }, [wrap])

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    isNearBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }, [])

  // 滚到顶 / 底（右侧按钮 + 翻页后追随共用）。走 virtualizer.scrollToIndex 而非原生
  // scrollTo：虚拟列表未测量行的 scrollHeight 只是估算，原生滚不准；scrollToIndex 会在
  // measure 后自我校正重滚，精确落到首/末行。
  const scrollToTop = useCallback(() => {
    virtualizer.scrollToIndex(0, { align: "start" })
  }, [virtualizer])
  const scrollToBottom = useCallback(() => {
    virtualizer.scrollToIndex(virtualizer.options.count - 1, { align: "end" })
  }, [virtualizer])

  // 点「加载更早」：标记加载完滚到最前。
  const handleLoadOlder = useCallback(() => {
    pendingScrollRef.current = "top"
    loadOlder()
  }, [loadOlder])

  // 点「获取最新」：标记加载完滚到最底。
  const handleLoadLatest = useCallback(() => {
    pendingScrollRef.current = "bottom"
    loadLatest()
  }, [loadLatest])

  // 加载后按标记滚到对应一端；后续追加了更新一批时贴底才追尾。
  // 用 useEffect（绘制后）而非 useLayoutEffect：scrollToIndex 会触发 measureElement
  // 重测行高→总高变化→virtualizer 重试重滚，若放在绘制前的同步回合里反复跑会「一直
  // 抖」。依赖收敛到 entries.length（数字，稳定），避免数组新引用导致的重复触发。
  // biome-ignore lint/correctness/useExhaustiveDependencies: entries.length 聚合内容变化；scroll 回调随 virtualizer 稳定
  useEffect(() => {
    const len = entries.length
    const last = len ? entries[len - 1].id : null
    const prevLast = prevLastIdRef.current
    const prevCount = prevCountRef.current
    prevLastIdRef.current = last
    prevCountRef.current = len

    if (len === 0) return

    // 首屏：贴底。走 scrollToIndex，让它在 measure 后自我校正精确落到末行
    // （原生 scrollTop=scrollHeight 依据的是未测量的估算高，贴不到真底）。
    if (prevCount === 0) {
      scrollToBottom()
      return
    }

    // 顶部加载更早 → 滚到最前。
    if (pendingScrollRef.current === "top") {
      pendingScrollRef.current = null
      scrollToTop()
      return
    }
    // 底部获取最新 → 滚到最底。
    if (pendingScrollRef.current === "bottom") {
      pendingScrollRef.current = null
      scrollToBottom()
      return
    }

    // 追加了更新的一批、且此前贴底：追尾。
    if (last !== prevLast && isNearBottomRef.current) {
      scrollToBottom()
    }
  }, [entries.length])

  return (
    <div className="relative h-full w-full flex flex-col">
      {/* 工具栏：左簇（等级筛选 + 搜索）｜右簇（计数 + 下载） */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/30 shrink-0">
        {/* 等级筛选：label 提示 + 各等级专属样式按钮 */}
        <span className="shrink-0 text-[10px] font-medium text-muted-foreground/70">
          {t("autoResearch.chat.logs.filterLabel", { defaultValue: "筛选" })}
        </span>
        {LOG_LEVELS.map((lv) => {
          const on = levelFilter.has(lv)
          const style = LEVEL_FILTER_STYLES[lv]
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
                "shrink-0 px-1.5 h-7 text-[10px] rounded-md font-medium border transition-colors",
                on ? style.on : style.off,
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
            className="shrink-0 p-0.5 rounded text-muted-foreground/60 hover:text-foreground hover:bg-accent/60 transition-colors"
            title={t("evolution.logs.clearLevel", { defaultValue: "Clear" })}
          >
            <X className="size-3" />
          </button>
        )}

        {/* 分隔 */}
        <span className="h-4 w-px bg-border/50 shrink-0" />

        {/* 搜索：带边框/背景的输入框，聚焦高亮；限最大宽，不再无限撑开 */}
        <div className="flex-1 max-w-[360px] min-w-[140px] flex items-center gap-1.5 h-7 px-2 rounded-md border border-border/70 bg-background focus-within:border-primary/60 focus-within:ring-1 focus-within:ring-primary/20 transition-colors">
          <Search className="size-3.5 text-muted-foreground shrink-0" />
          <input
            type="text"
            value={searchInput}
            maxLength={INPUT_LIMITS.search}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={handleSearchKeyDown}
            placeholder={t("evolution.logs.searchPlaceholder")}
            className="flex-1 min-w-0 bg-transparent text-xs outline-none placeholder:text-muted-foreground/50"
          />
          {searchInput && (
            <button
              type="button"
              onClick={clearSearch}
              className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="size-3" />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={handleSearch}
          className="shrink-0 text-xs px-2.5 h-7 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary/90 transition-colors"
        >
          {t("evolution.logs.search")}
        </button>

        {/* 右簇：计数 + 下载，推到最右 */}
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/60 tabular-nums">
          {t("autoResearch.chat.logs.loaded", {
            count: entries.length,
            defaultValue: "已加载 {{count}} 条",
          })}
        </span>
        {/* 折行开关：开=长日志按面板宽折行；关=不换行、超宽横向滚动（贴合终端）。 */}
        <button
          type="button"
          onClick={() => setWrap((v) => !v)}
          className={cn(
            "shrink-0 grid place-items-center size-7 rounded-md border transition-colors",
            wrap
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border/70 text-muted-foreground hover:text-foreground hover:bg-accent/60",
          )}
          title={t("autoResearch.chat.logs.wrap", {
            defaultValue: "长日志折行显示",
          })}
          aria-pressed={wrap}
        >
          <WrapText className="size-3.5" />
        </button>
        {/* 下载当前已加载：纯前端导出内存里的 entries */}
        <button
          type="button"
          onClick={handleExportLoaded}
          disabled={entries.length === 0}
          className="shrink-0 grid place-items-center size-7 rounded-md border border-border/70 text-muted-foreground hover:text-foreground hover:border-border hover:bg-accent/60 transition-colors disabled:opacity-50 disabled:pointer-events-none"
          title={t("autoResearch.chat.logs.exportLoaded", {
            count: entries.length,
            defaultValue: "下载当前已加载的 {{count}} 条",
          })}
        >
          <Download className="size-3.5" />
        </button>
        {/* 下载全部：limit=0 从后端拉全部匹配行 */}
        <button
          type="button"
          onClick={handleExportAll}
          disabled={isExporting}
          className="shrink-0 grid place-items-center size-7 rounded-md border border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary/60 transition-colors disabled:opacity-50 disabled:pointer-events-none"
          title={t("autoResearch.chat.logs.exportAll", {
            defaultValue: "下载全部日志（从后端拉取所有匹配行）",
          })}
        >
          {isExporting ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <DownloadCloud className="size-3.5" />
          )}
        </button>
      </div>

      {/* 日志正文 */}
      {isLoading ? (
        <div className="flex items-center justify-center flex-1 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin mr-2" />
          {t("evolution.logs.loading", { defaultValue: "加载中..." })}
        </div>
      ) : error ? (
        <div className="flex-1 px-4 py-3 text-xs text-destructive font-mono">
          {t("evolution.logs.loadError", { error })}
        </div>
      ) : entries.length === 0 ? (
        <div className="flex-1 px-4 py-3 text-xs font-mono text-muted-foreground">
          {searchQuery
            ? t("evolution.logs.noSearchResults")
            : levelFilter.size > 0
              ? t("evolution.logs.noLevelMatch", {
                  defaultValue: "当前等级无匹配日志",
                })
              : t("evolution.logs.empty")}
        </div>
      ) : (
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="arc-visible-scroll flex-1 w-full overflow-auto px-4 py-3 text-xs font-mono bg-card"
        >
          {/* 顶部：加载更早；没有更多时给一句到顶提示 */}
          {hasOlder ? (
            <div className="flex justify-center pb-2">
              <button
                type="button"
                onClick={handleLoadOlder}
                disabled={isFetchingOlder}
                className="flex items-center gap-1.5 px-3 py-1 rounded text-[10px] font-medium border border-border/60 text-muted-foreground hover:text-primary hover:border-primary/40 transition-colors disabled:opacity-50"
              >
                {isFetchingOlder ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <ChevronUp className="size-3" />
                )}
                {t("evolution.logs.loadMore")}
              </button>
            </div>
          ) : (
            <div className="flex justify-center pb-2 text-[10px] text-muted-foreground/50 select-none">
              {t("autoResearch.chat.logs.noMoreOlder", {
                defaultValue: "已经到顶了，没有更早的日志",
              })}
            </div>
          )}
          <div
            style={{
              height: virtualizer.getTotalSize(),
              width: "100%",
              position: "relative",
            }}
          >
            {virtualizer.getVirtualItems().map((row) => (
              <div
                key={row.key}
                ref={virtualizer.measureElement}
                data-index={row.index}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: wrap ? "100%" : "max-content",
                  minWidth: "100%",
                  transform: `translateY(${row.start}px)`,
                }}
              >
                {renderResearchLog(entries[row.index], wrap)}
              </div>
            ))}
          </div>
          {/* 底部：获取最新（恒显示，拿不到也无妨） */}
          <div className="flex justify-center pt-2">
            <button
              type="button"
              onClick={handleLoadLatest}
              disabled={isFetchingLatest}
              className="flex items-center gap-1.5 px-3 py-1 rounded text-[10px] font-medium border border-border/60 text-muted-foreground hover:text-primary hover:border-primary/40 transition-colors disabled:opacity-50"
            >
              {isFetchingLatest ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <ChevronDown className="size-3" />
              )}
              {t("autoResearch.chat.logs.loadLatest", {
                defaultValue: "获取最新",
              })}
            </button>
          </div>
        </div>
      )}

      {/* 到顶 / 到底：右侧上下居中悬浮 */}
      {entries.length > 0 && (
        <div className="absolute right-4 top-1/2 -translate-y-1/2 flex flex-col gap-1.5">
          <button
            type="button"
            onClick={scrollToTop}
            className="size-7 grid place-items-center rounded-full bg-background/40 backdrop-blur border border-border/50 shadow-md text-muted-foreground hover:bg-background/90 hover:border-primary/50 hover:text-foreground active:scale-95 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            title={t("evolution.logs.scrollToTop")}
          >
            <ArrowUpToLine className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={scrollToBottom}
            className="size-7 grid place-items-center rounded-full bg-background/40 backdrop-blur border border-border/50 shadow-md text-muted-foreground hover:bg-background/90 hover:border-primary/50 hover:text-foreground active:scale-95 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            title={t("evolution.logs.scrollToBottom")}
          >
            <ArrowDownToLine className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  )
}
