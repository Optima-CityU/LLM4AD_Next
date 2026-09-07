import { useVirtualizer } from "@tanstack/react-virtual"
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Download,
  DownloadCloud,
  Loader2,
  WrapText,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdResearchService, type ResearchLogItem } from "@/client"
import {
  formatLogTime,
  type LogLevel,
  LOG_LEVELS,
} from "@/components/Evolution/TaskDetail/log-renderers"
import { useResearchLogs } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import {
  logItemToStreamEntry,
  LogLine,
  type StreamLogEntry,
} from "./StreamLogConsole"

/** 每个日志等级筛选按钮的专属配色（选中 / 未选中），对齐右侧抽屉。 */
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

/** 把一条持久化日志格式化成一行纯文本（导出用）。 */
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

/** 把 StreamLogEntry 格式化成一行纯文本（导出内存里已合并的 entries 用）。 */
function formatStreamLine(e: StreamLogEntry): string {
  const time = formatLogTime(e.ts)
  const level = String(e.level ?? "").toUpperCase()
  const location = e.source ? ` ${e.source}` : ""
  return `[${time}] ${level} ${e.message}${location}`
}

/** 触发浏览器下载一个 .log 文件。 */
function triggerLogDownload(text: string, sessionId: string, turnId: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `research-log-${sessionId.slice(0, 8)}-turn-${turnId.slice(0, 8)}.log`
  a.click()
  URL.revokeObjectURL(url)
}

/** liveLogs（SSE tail）的客户端等级过滤：服务端筛选只覆盖持久日志，实时 tail 需同步过滤。 */
function liveMatchesLevel(e: StreamLogEntry, selected: Set<LogLevel>): boolean {
  if (selected.size === 0) return true
  const level = String(e.level ?? "").toUpperCase()
  if (selected.has("ERROR") && (level === "ERROR" || level === "CRITICAL"))
    return true
  if (selected.has("WARNING") && (level === "WARNING" || level === "WARN"))
    return true
  if (
    selected.has("INFO") &&
    !["DEBUG", "TRACE", "WARNING", "WARN", "ERROR", "CRITICAL"].includes(level)
  )
    return true
  return false
}

/**
 * 单个 turn 的内联折叠日志区。
 *
 * 默认折叠；展开时才按 ``turn_id`` 懒加载该轮持久化日志
 * （``useResearchLogs` + `turnId``，独立 research_log 表双端游标窗口）。活跃轮把
 * SSE 实时 tail（``liveLogs``）叠加进来，与已拉取的持久日志按 ``stream_id`` /
 * ``event_key`` 去重，保证「实时推送」与「刷新后重放」看到同一份、不重复。
 *
 * 渲染走虚拟列表（对齐右侧抽屉 ResearchLogPanel）：去掉 DOM 行数上限，上万条也能
 * 顺滑上下翻。滚动策略同抽屉——首屏贴底、顶部「加载更早」后滚到最前、实时新增仅在
 * 用户贴近底部时才追尾（手动上滚回看时不打断）。
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

  // 等级筛选：服务端筛选（进 useResearchLogs 的 query，自动重载持久日志）。
  const [levelFilter, setLevelFilter] = useState<Set<LogLevel>>(new Set())
  const [isExporting, setIsExporting] = useState(false)
  // 长日志折行：默认开（贴合面板宽度、不用横向拖）。虚拟列表用 measureElement 动态
  // 测高，折行变高会自动校正，不破坏滚动定位。
  const [wrap, setWrap] = useState(true)
  const levelArr = useMemo(() => Array.from(levelFilter), [levelFilter])

  const q = useResearchLogs(sessionId, {
    turnId,
    level: levelArr,
    enabled: open,
    limit: 500,
  })

  // 持久化日志（升序，已按服务端 level 筛选）+ 实时 tail，按 stream_id（缺失退回
  // event_key，再退回 id）去重合并。stream_id 是 REST/SSE 同源全局唯一键，修 retry
  // 复用 turn_id 时 event_key per-turn 计数器归零导致的撞键。
  //
  // liveLogs 未经服务端筛选，故这里对其做客户端 level 过滤，保证筛选激活时实时 tail
  // 与持久日志口径一致（不会漏出被筛掉的实时行）。
  const entries = useMemo<StreamLogEntry[]>(() => {
    const seen = new Set<string>()
    const out: StreamLogEntry[] = []
    const push = (e: StreamLogEntry) => {
      const sig = e.streamId ?? e.eventKey ?? e.id
      if (seen.has(sig)) return
      seen.add(sig)
      out.push(e)
    }
    for (const item of q.entries) push(logItemToStreamEntry(item))
    for (const e of liveLogs) {
      if (!liveMatchesLevel(e, levelFilter)) continue
      push(e)
    }
    return out
  }, [q.entries, liveLogs, levelFilter])

  // 下载当前：纯前端，导出内存里已合并的 entries（含当前筛选 + 实时 tail）。
  const handleExportLoaded = useCallback(() => {
    if (entries.length === 0) return
    triggerLogDownload(
      entries.map(formatStreamLine).join("\n"),
      sessionId,
      turnId,
    )
  }, [entries, sessionId, turnId])

  // 下载全部：limit=0 从后端拉本轮全部匹配行（含当前 level 筛选）。
  const handleExportAll = useCallback(async () => {
    if (isExporting) return
    setIsExporting(true)
    try {
      const res = await Llm4AdResearchService.listLogs({
        sessionId,
        turnId,
        limit: 0,
        level: levelArr.length ? levelArr : undefined,
      })
      triggerLogDownload(
        (res.items ?? []).map(formatLogLine).join("\n"),
        sessionId,
        turnId,
      )
    } finally {
      setIsExporting(false)
    }
  }, [sessionId, turnId, levelArr, isExporting])

  return (
    <div className="group/turnlog arc-log mx-4 my-2 rounded-lg border border-border/60 bg-muted/20 overflow-hidden">
      {/* 顶部单行：左=可点击折叠开关，右=等级筛选/下载（仅展开时显示，不占额外高度行）。 */}
      <div className="flex items-center gap-1.5 px-3 py-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 hover:text-foreground transition-colors"
        >
          {open ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          <span>{t("autoResearch.chat.streamLogs.toggle")}</span>
          {entries.length > 0 ? (
            <span className="font-normal normal-case text-muted-foreground/50">
              {t("autoResearch.chat.streamLogs.count", {
                count: entries.length,
              })}
            </span>
          ) : (
            // 展开且加载完仍为空：只在括号里标「空 / 无匹配」，不再撑开空 body 占高度。
            open &&
            !q.isLoading && (
              <span className="font-normal normal-case text-muted-foreground/40">
                (
                {levelFilter.size > 0
                  ? t("autoResearch.chat.streamLogs.noMatchTag", {
                      defaultValue: "无匹配",
                    })
                  : t("autoResearch.chat.streamLogs.emptyTag", {
                      defaultValue: "空",
                    })}
                )
              </span>
            )
          )}
        </button>
        {open && q.isLoading && <Loader2 className="h-3 w-3 animate-spin" />}

        {/* 右簇：等级筛选 + 下载。默认隐藏、hover 面板才浮现，减少并排多面板时的杂乱；
            但有筛选激活或正在导出时保持常显，避免「已加筛选却看不见」。 */}
        {open && (
          <div
            className={cn(
              "ml-auto flex items-center gap-1 transition-opacity",
              levelFilter.size > 0 || isExporting
                ? "opacity-100"
                : "opacity-0 group-hover/turnlog:opacity-100 focus-within:opacity-100",
            )}
          >
            {/* 折行开关：开=长日志按面板宽折行；关=不换行、超宽横向滚动（贴合终端）。 */}
            <button
              type="button"
              onClick={() => setWrap((v) => !v)}
              className={cn(
                "shrink-0 grid place-items-center size-5 rounded border transition-colors",
                wrap
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border/60 text-muted-foreground hover:text-foreground hover:bg-accent/60",
              )}
              title={t("autoResearch.chat.logs.wrap", {
                defaultValue: "长日志折行显示",
              })}
              aria-pressed={wrap}
            >
              <WrapText className="size-2.5" />
            </button>
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
                    "shrink-0 px-1 h-5 text-[9px] rounded font-medium border transition-colors",
                    on ? style.on : style.off,
                  )}
                >
                  {lv}
                </button>
              )
            })}

            {/* 下载当前已加载 */}
            <button
              type="button"
              onClick={handleExportLoaded}
              disabled={entries.length === 0}
              className="shrink-0 grid place-items-center size-5 rounded border border-border/60 text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors disabled:opacity-50 disabled:pointer-events-none"
              title={t("autoResearch.chat.logs.exportLoaded", {
                count: entries.length,
                defaultValue: "下载当前已加载的 {{count}} 条",
              })}
            >
              <Download className="size-2.5" />
            </button>
            {/* 下载全部（本轮） */}
            <button
              type="button"
              onClick={handleExportAll}
              disabled={isExporting}
              className="shrink-0 grid place-items-center size-5 rounded border border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary/60 transition-colors disabled:opacity-50 disabled:pointer-events-none"
              title={t("autoResearch.chat.logs.exportAll", {
                defaultValue: "下载全部日志（从后端拉取所有匹配行）",
              })}
            >
              {isExporting ? (
                <Loader2 className="size-2.5 animate-spin" />
              ) : (
                <DownloadCloud className="size-2.5" />
              )}
            </button>
          </div>
        )}
      </div>

      {/* 有日志才渲染 body；展开但为空时只在标题括号里标「空」，不占高度。
          加载中的反馈由标题行的 Loader2 承担，故此处无需空/加载态 body。 */}
      {open && entries.length > 0 && (
        <TurnLogBody
          entries={entries}
          isLoading={q.isLoading}
          isFetchingOlder={q.isFetchingOlder}
          hasOlder={q.hasOlder}
          loadOlder={q.loadOlder}
          filtered={levelFilter.size > 0}
          wrap={wrap}
        />
      )}
    </div>
  )
}

/**
 * 日志正文：虚拟列表 + 滚动策略（对齐 ResearchLogPanel）。
 *
 * - 首屏：贴底（scrollToIndex 末行，measure 后自我校正精确落底）。
 * - 顶部「加载更早」：加载完滚到最前（pendingScrollRef="top"）。
 * - 实时新增（末条 id 变化）：仅当用户此前贴近底部才追尾，手动上滚回看时不打断。
 */
function TurnLogBody({
  entries,
  isLoading,
  isFetchingOlder,
  hasOlder,
  loadOlder,
  filtered,
  wrap,
}: {
  entries: StreamLogEntry[]
  isLoading: boolean
  isFetchingOlder: boolean
  hasOlder: boolean
  loadOlder: () => void
  /** 是否处于筛选/搜索态：影响空态文案（无匹配 vs 空日志）。 */
  filtered: boolean
  /** 长日志是否折行（切换时需重测行高）。 */
  wrap: boolean
}) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)
  // 内容是否真的溢出（可滚动）：只有溢出时才显示到顶/到底悬浮按钮，短日志不显。
  const [canScroll, setCanScroll] = useState(false)
  // 追尾判定：上一帧末条 id 与条数（区分首屏 / 后续追加）。
  const prevLastIdRef = useRef<string | null>(null)
  const prevCountRef = useRef(0)
  // 顶部「加载更早」后要滚到最前的标记。
  const pendingScrollTopRef = useRef(false)

  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 18,
    overscan: 20,
  })

  // 切换折行 / 不折行时，每行真实高度变了，强制虚拟列表重测，避免总高与定位错位。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅 wrap 变化时需重测
  useEffect(() => {
    virtualizer.measure()
  }, [wrap])

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    isNearBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setCanScroll(el.scrollHeight - el.clientHeight > 1)
  }, [])

  // 走 scrollToIndex 而非原生 scrollTo：虚拟列表未测量行的 scrollHeight 只是估算，
  // scrollToIndex 会在 measure 后自我校正重滚，精确落到首/末行。
  const scrollToTop = useCallback(() => {
    virtualizer.scrollToIndex(0, { align: "start" })
  }, [virtualizer])
  const scrollToBottom = useCallback(() => {
    virtualizer.scrollToIndex(virtualizer.options.count - 1, { align: "end" })
  }, [virtualizer])

  const handleLoadOlder = useCallback(() => {
    pendingScrollTopRef.current = true
    loadOlder()
  }, [loadOlder])

  // 加载后按标记滚动；后续追加了更新一批时贴底才追尾。用 useEffect（绘制后）而非
  // useLayoutEffect：scrollToIndex 会触发 measureElement 重测→总高变化→virtualizer
  // 重试重滚，放绘制前同步回合会「一直抖」。依赖收敛到 entries.length（稳定数字）。
  // biome-ignore lint/correctness/useExhaustiveDependencies: entries.length 聚合内容变化；scroll 回调随 virtualizer 稳定
  useEffect(() => {
    const len = entries.length
    const last = len ? entries[len - 1].id : null
    const prevLast = prevLastIdRef.current
    const prevCount = prevCountRef.current
    prevLastIdRef.current = last
    prevCountRef.current = len

    // 内容增删后重算是否溢出（用于悬浮按钮的显隐），不依赖用户滚动触发。
    const el = containerRef.current
    if (el) setCanScroll(el.scrollHeight - el.clientHeight > 1)

    if (len === 0) return

    // 首屏：贴底。body 现在是带首页数据直接挂载的，挂载瞬间 virtualizer 尚未测量
    // 行高，单次 scrollToBottom 可能落不到底；故再在下一帧补滚一次，等测量完成精确落底。
    if (prevCount === 0) {
      scrollToBottom()
      requestAnimationFrame(scrollToBottom)
      return
    }
    // 顶部加载更早 → 滚到最前。
    if (pendingScrollTopRef.current) {
      pendingScrollTopRef.current = false
      scrollToTop()
      return
    }
    // 追加了更新的一批、且此前贴底：追尾（手动上滚回看时 isNearBottom=false，不打断）。
    if (last !== prevLast && isNearBottomRef.current) {
      scrollToBottom()
    }
  }, [entries.length])

  if (isLoading && entries.length === 0) {
    return (
      <div className="flex items-center justify-center py-3 text-[11px] text-muted-foreground">
        <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
        {t("evolution.logs.loading", { defaultValue: "加载中..." })}
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div className="px-3 py-2 text-[11px] font-mono text-muted-foreground/60 italic">
        {filtered
          ? t("evolution.logs.noSearchResults", {
              defaultValue: "无匹配日志",
            })
          : t("autoResearch.chat.streamLogs.empty")}
      </div>
    )
  }

  return (
    <div className="group/logbody relative">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="arc-log-scroll max-h-64 overflow-auto overscroll-contain px-3 py-2 font-mono text-[11px]"
      >
        {/* 顶部：加载更早（没有更多时给到顶提示） */}
      {hasOlder ? (
        <div className="flex justify-center pb-1.5">
          <button
            type="button"
            onClick={handleLoadOlder}
            disabled={isFetchingOlder}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium border border-border/60 text-muted-foreground hover:text-primary hover:border-primary/40 transition-colors disabled:opacity-50"
          >
            {isFetchingOlder ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <ChevronUp className="h-3 w-3" />
            )}
            {t("autoResearch.chat.streamLogs.loadEarlier")}
          </button>
        </div>
      ) : (
        <div className="flex justify-center pb-1.5 text-[10px] text-muted-foreground/50 select-none">
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
            <LogLine entry={entries[row.index]} wrap={wrap} />
          </div>
        ))}
      </div>
    </div>

      {/* 到顶 / 到底：右侧上下居中悬浮。仅内容溢出时渲染；默认隐藏，悬停日志区才浮现，
          避免与外层对话列表那套滚动按钮同时抢眼。 */}
      {canScroll && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex flex-col gap-1.5 opacity-0 group-hover/logbody:opacity-100 focus-within:opacity-100 transition-opacity">
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
