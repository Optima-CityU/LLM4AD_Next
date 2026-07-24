import {
  ChevronDown,
  Cpu,
  History,
  Info,
  ListStart,
  Loader2,
  Play,
  RotateCcw,
  Send,
  Square,
  Terminal,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type {
  ResearchMessageItem,
  ResearchMode,
  ResearchSessionItem,
  ResearchStageSnapshot,
} from "@/client"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { cn } from "@/lib/utils"

import GatePanel from "./GatePanel"
import HistoryLogDrawer, { type DrawerTab } from "./HistoryLogDrawer"
import ProviderModelPicker from "./ProviderModelPicker"
import type { StreamLogEntry } from "./StreamLogConsole"
import { MODE_OPTIONS } from "./shared"
import { stageNameByLang } from "./tech"

// 门控动作里哪些必须给理由 / 哪些把理由当 guidance 传入。理由取自底部输入框。
const NEEDS_REASON = new Set(["reject", "inject"])
const GUIDANCE_ACTIONS = new Set(["inject"])

export interface RunOverrides {
  provider_id?: string | null
  model_name?: string | null
  mode?: ResearchMode
  from_stage?: string | null
}

interface Props {
  session: ResearchSessionItem
  /** 当前待回复的门控 form 消息（无则底部只有输入框）。 */
  gateMessage: ResearchMessageItem | null
  /** collab agent 是否正在跑一轮（禁用输入 + 显示进行态）。 */
  collabBusy: boolean
  /** pipeline 触发/重试进行中。 */
  running: boolean
  paused: boolean
  sending: boolean
  stages: ResearchStageSnapshot[]
  canRetry: boolean
  /** 运行配置（受控，提升到 ChatPanel 与顶部阶段轨共用）。 */
  provider: string
  model: string
  mode: ResearchMode
  fromStage: string
  onProviderModelChange: (provider: string, model: string) => void
  onModeChange: (mode: ResearchMode) => void
  onFromStageChange: (fromStage: string) => void
  onCollabSend: (message: string) => void
  onRun: (overrides: RunOverrides) => void
  onStop: () => void
  onRetry: () => void
  onGateSubmit: (messageId: string, submission: Record<string, unknown>) => void
  /** 实时 + 历史合并后的日志（供底部日志/历史抽屉的日志 tab 展示）。 */
  logs: StreamLogEntry[]
  /** SSE 是否在跑：为 true 时即便零条也让日志入口可点（可看「等待日志」）。 */
  logsActive: boolean
  /** 是否还有更早的日志可加载。 */
  logsHasMore?: boolean
  /** 是否正在加载更多日志。 */
  logsFetchingMore?: boolean
  /** 加载更早的日志。 */
  onLoadMoreLogs?: () => void
}

/**
 * 底部操作区：门控按钮条（有门控时贴输入框上方）+ 常驻输入框 + 运行控制。
 *
 * - running：只显示停止条。
 * - 其它（pending / paused / 终态）：输入框常驻；有门控时上方带门控按钮；
 *   pending / 终态额外带运行工具行（provider / mode / from_stage / 运行 / 重试）。
 */
export default function BottomComposer({
  session,
  gateMessage,
  collabBusy,
  running,
  paused,
  sending,
  stages,
  canRetry,
  provider,
  model,
  mode,
  fromStage,
  onProviderModelChange,
  onModeChange,
  onFromStageChange,
  onCollabSend,
  onRun,
  onStop,
  onRetry,
  onGateSubmit,
  logs,
  logsActive,
  logsHasMore,
  logsFetchingMore,
  onLoadMoreLogs,
}: Props) {
  const { t, i18n } = useTranslation()
  const { data: providersData } = useProviders()
  const { data: defaultModels } = useUserDefaultModels()
  const [text, setText] = useState("")
  const [noteError, setNoteError] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("logs")
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const logListRef = useRef<HTMLDivElement>(null)

  // 打开抽屉并激活指定 tab（日志 / 历史）。
  const openDrawer = (tab: DrawerTab) => {
    setDrawerTab(tab)
    setDrawerOpen(true)
  }
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 输入框随内容自动增高：内容变化时重算高度（上限 max-h-32，超出滚动）。
  // 撑开/收起由 textarea 的 transition-[height] 补间，外框随内容自然增高。
  // biome-ignore lint/correctness/useExhaustiveDependencies: text 变化触发重算高度（body 未直接引用）
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [text])

  // 日志抽屉打开时先跳到底部。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅在打开 / 切 tab 时定位
  useEffect(() => {
    if (!drawerOpen || drawerTab !== "logs") return
    const el = logListRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [drawerOpen, drawerTab])

  // 追尾新日志：仅当用户已贴近底部时才随新日志滚到底，上滚回看 / 翻页时不打断。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 依赖 logs.length 追尾新日志
  useEffect(() => {
    if (!drawerOpen || drawerTab !== "logs") return
    const el = logListRef.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
      el.scrollTop = el.scrollHeight
    }
  }, [logs.length])

  const status = session.status
  const terminal =
    status === "completed" || status === "failed" || status === "cancelled"
  const showRunTools = status === "pending" || terminal

  // 日志/历史抽屉：running 分支与主分支共用同一个抽屉元素。
  const drawer = (
    <HistoryLogDrawer
      sessionId={session.id}
      open={drawerOpen}
      onOpenChange={setDrawerOpen}
      tab={drawerTab}
      onTabChange={setDrawerTab}
      logs={logs}
      logListRef={logListRef}
      logsHasMore={logsHasMore}
      logsFetchingMore={logsFetchingMore}
      onLoadMoreLogs={onLoadMoreLogs}
    />
  )

  // running：整条只留停止。
  if (running) {
    return (
      <div className="border-t border-border/40 bg-primary/[0.04] backdrop-blur px-4 py-3 flex items-center gap-3">
        <span className="flex items-center gap-2 text-xs text-muted-foreground flex-1 min-w-0">
          <Loader2 className="size-3.5 animate-spin shrink-0 text-primary" />
          <span className="truncate">
            {t("autoResearch.input.runningHint")}
          </span>
        </span>
        <LogButton
          count={logs.length}
          active={logsActive}
          onOpen={() => openDrawer("logs")}
          label={t("autoResearch.chat.streamLogs.title")}
        />
        <StopButton onStop={onStop} label={t("autoResearch.chat.stopTurn")} />
        {drawer}
      </div>
    )
  }

  const inputDisabled = collabBusy || sending
  const sendMsg = () => {
    const v = text.trim()
    if (!v || inputDisabled) return
    onCollabSend(v)
    setText("")
  }

  // 门控按钮：理由取底部输入框。reject/inject 必填，空则高亮报错；
  // 提交成功后清空输入框，submission 只组装表单值；mode 由父层顶层请求体传递。
  // collabBusy（问 AI 进行中）时禁止提交，避免与协作轮抢同一个输入框。
  const handleGateAction = (value: string) => {
    if (!gateMessage || inputDisabled) return
    const trimmed = text.trim()
    if (NEEDS_REASON.has(value) && !trimmed) {
      setNoteError(true)
      return
    }
    const submission: Record<string, unknown> = { action: value }
    if (trimmed) {
      submission.message = trimmed
      if (GUIDANCE_ACTIONS.has(value)) submission.guidance = trimmed
    }
    onGateSubmit(gateMessage.id, submission)
    setText("")
    setNoteError(false)
  }

  const run = () => {
    if (sending) return
    onRun({
      provider_id: provider.trim() || undefined,
      model_name: model.trim() || undefined,
      mode,
      from_stage: terminal && fromStage ? fromStage : undefined,
    })
  }

  const providerList = providersData?.items ?? []
  const selectedProvider = providerList.find((p) => p.id === provider)
  const defaultProviderName = defaultModels?.planner_provider_name || t("autoResearch.provider.default")
  const defaultModelName = defaultModels?.planner_model_name || ""

  const providerLabel = (() => {
    if (!provider || provider === "default") {
      const label = defaultProviderName
      return defaultModelName ? `${label} / ${defaultModelName}` : label
    }
    if (provider === "mock") {
      return t("autoResearch.provider.mock")
    }
    const label = selectedProvider?.name || provider
    return model ? `${label} / ${model}` : label
  })()

  // 输入框行：
  // - 非门控：透明贴合外层大卡片（单层框，聚焦光晕由外层承载），支持多行自增高。
  // - 门控：套「字段」外观（边框+内陷背景），嵌入 GatePanel（理由在上、按钮在下）。
  // 发送/运行/停止按钮随行尾（重试已移至工具栏）。
  const composerRow = (
    <div
      className={cn(
        "flex items-end gap-2",
        gateMessage
          ? "mx-3.5 mb-1 mt-1 rounded-lg border border-input bg-background px-3 py-2 shadow-inner focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/15 transition-all"
          : "px-3 py-2",
      )}
    >
      <textarea
        ref={textareaRef}
        rows={1}
        value={text}
        disabled={inputDisabled}
        onChange={(e) => {
          setText(e.target.value)
          if (noteError) setNoteError(false)
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            sendMsg()
          }
        }}
        placeholder={
          collabBusy
            ? t("autoResearch.collab.thinking")
            : gateMessage
              ? t("autoResearch.collab.placeholderGate")
              : t("autoResearch.collab.placeholder")
        }
        className="flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground/60 max-h-32 py-1 disabled:opacity-60 transition-[height] duration-150 ease-out"
      />

      {/* 日志抽屉入口：随输入框（含门控时）；pending/终态由工具行承载 */}
      {!showRunTools && (
        <LogButton
          count={logs.length}
          active={logsActive}
          onOpen={() => openDrawer("logs")}
          label={t("autoResearch.chat.streamLogs.title")}
        />
      )}

      {/* 发送（agent） */}
      <button
        type="button"
        onClick={sendMsg}
        disabled={inputDisabled || !text.trim()}
        title={t("autoResearch.chat.sendMessage")}
        className="shrink-0 inline-flex items-center justify-center size-8 rounded-lg bg-primary/15 text-primary border border-primary/40 hover:bg-primary/25 disabled:opacity-40 transition-all"
      >
        {collabBusy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Send className="size-4" />
        )}
      </button>

      {/* 运行（pending 开跑 / 终态再跑一轮，同一逻辑同一名字） */}
      {showRunTools && (
        <button
          type="button"
          onClick={run}
          disabled={sending}
          title={t("autoResearch.chat.startTurn")}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 h-8 rounded-lg text-xs font-semibold bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-60 transition-all"
        >
          {sending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Play className="size-4" />
          )}
          {t("autoResearch.chat.startTurn")}
        </button>
      )}

      {/* 停止：仅协作轮真在跑时显示（paused 时任务已结束，无可停对象） */}
      {collabBusy && (
        <StopButton
          onStop={onStop}
          label={t("autoResearch.chat.stopTurn")}
          compact
        />
      )}
    </div>
  )

  return (
    <div
      className="shrink-0"
      style={{
        background:
          "linear-gradient(to top, color-mix(in srgb, var(--primary) 4%, var(--card)) 0%, transparent 100%)",
      }}
    >
      <div className="mx-auto w-full max-w-3xl xl:max-w-4xl 2xl:max-w-5xl px-4 pb-3 pt-1">
        <div
          className={cn(
            "relative overflow-hidden rounded-2xl border-2 bg-card/95 backdrop-blur-md shadow-lg transition-all",
            noteError
              ? "border-destructive/60 shadow-destructive/8 focus-within:border-destructive focus-within:ring-2 focus-within:ring-destructive/15"
              : gateMessage
                ? "border-amber-500/45 shadow-amber-500/10 focus-within:border-amber-500/60 focus-within:ring-2 focus-within:ring-amber-500/15"
                : "border-primary/20 shadow-primary/8 hover:border-primary/30 focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/15",
          )}
        >
          {/* 门控决策区：上下文/产物 → 输入框 → 决策按钮，同框一体。
              日志入口随输入框（发送按钮旁），标题右上角只留「结束运行」。 */}
          {gateMessage && (
            <GatePanel
              message={gateMessage}
              sessionId={session.id}
              disabled={inputDisabled}
              mode={mode}
              onModeChange={onModeChange}
              onAction={handleGateAction}
            >
              {composerRow}
            </GatePanel>
          )}

          {/* 运行工具行：仅 pending / 终态显示。控件为幽灵文字按钮 + 下拉，省空间。 */}
          {showRunTools && (
            <div className="flex items-center gap-0.5 flex-wrap px-2.5 pt-2 pb-1.5 border-b border-border/40">
              {/* 运行模式：两态分段开关（与门控面板一致），选中项高亮。放最左。 */}
              <div
                role="group"
                aria-label={t("autoResearch.create.modeLabel")}
                className="inline-flex items-center rounded-md border border-border/60 bg-background/50 p-0.5"
              >
                {MODE_OPTIONS.map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => onModeChange(m)}
                    className={cn(
                      "px-1.5 py-0.5 rounded text-[11px] font-medium transition-colors",
                      mode === m
                        ? "bg-primary/15 text-primary"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`autoResearch.mode.${m}`)}
                  </button>
                ))}
              </div>

              <Popover open={settingsOpen} onOpenChange={setSettingsOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className={cn(
                      "inline-flex h-6 items-center gap-1 px-1.5 rounded-md text-[11px] font-medium transition-colors",
                      provider && provider !== "default"
                        ? "text-primary hover:bg-primary/10"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
                    )}
                  >
                    <Cpu className="size-3 shrink-0" />
                    <span className="max-w-[160px] truncate">
                      {providerLabel}
                    </span>
                    <ChevronDown className="size-3 shrink-0 opacity-60" />
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  className="w-[420px] p-3"
                  align="start"
                  side="top"
                  sideOffset={8}
                >
                  <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5 px-0.5">
                    {t("autoResearch.create.providerLabel")} / Model
                  </p>
                  <ProviderModelPicker
                    provider={provider}
                    model={model}
                    onChange={onProviderModelChange}
                  />
                </PopoverContent>
              </Popover>

              {/* 起始阶段：终态可从某一步重跑。带 tooltip 说明用途，放模式左侧。 */}
              {terminal && stages.length > 0 && (
                <Select
                  value={fromStage || "__begin__"}
                  onValueChange={(v) =>
                    onFromStageChange(v === "__begin__" ? "" : v)
                  }
                >
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <SelectTrigger
                        size="sm"
                        aria-label={t("autoResearch.input.fromStageLabel")}
                        className="h-6 w-auto gap-1 rounded-md border-0 bg-transparent dark:bg-transparent dark:hover:bg-transparent px-1.5 py-0 text-[11px] font-medium text-muted-foreground shadow-none hover:text-foreground focus-visible:ring-0 [&>svg:last-child]:size-3 [&>svg:last-child]:opacity-60"
                      >
                        <ListStart className="size-3 shrink-0" />
                        <SelectValue />
                      </SelectTrigger>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="max-w-[240px]">
                      {t("autoResearch.input.fromStageHint")}
                    </TooltipContent>
                  </Tooltip>
                  <SelectContent>
                    <SelectItem value="__begin__" className="text-xs">
                      {t("autoResearch.input.fromStageBegin")}
                    </SelectItem>
                    {stages.map((s) => (
                      <SelectItem
                        key={s.stage}
                        value={String(s.stage)}
                        className="text-xs"
                      >
                        #{s.stage} {stageNameByLang(s.stage, i18n.language) || s.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}

              <div className="ml-auto flex items-center gap-0.5">
                {/* 重试（终态且可重试）：琥珀色区分于普通图标钮，提示「重试失败轮」 */}
                {canRetry && (
                  <button
                    type="button"
                    onClick={onRetry}
                    disabled={sending}
                    title={t("autoResearch.chat.retryTurn")}
                    className="size-7 grid place-items-center rounded-md text-amber-600 dark:text-amber-500 hover:bg-amber-500/10 disabled:opacity-40 transition-colors"
                  >
                    <RotateCcw className="size-3.5" />
                  </button>
                )}
                {/* 实时日志：放在运行历史左侧，带条数 badge */}
                <button
                  type="button"
                  onClick={() => openDrawer("logs")}
                  disabled={logs.length === 0 && !logsActive}
                  title={t("autoResearch.chat.streamLogs.title")}
                  className="relative size-7 grid place-items-center rounded-md text-muted-foreground/70 hover:text-primary hover:bg-primary/10 disabled:opacity-40 disabled:pointer-events-none transition-colors"
                >
                  <Terminal className="size-3.5" />
                  {logs.length > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 min-w-3.5 h-3.5 px-1 grid place-items-center rounded-full bg-primary text-primary-foreground text-[10px] font-mono leading-none tabular-nums">
                      {logs.length > 99 ? "99+" : logs.length}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => openDrawer("history")}
                  title={t("autoResearch.chat.turnHistory")}
                  className="size-7 grid place-items-center rounded-md text-muted-foreground/70 hover:text-primary hover:bg-primary/10 transition-colors"
                >
                  <History className="size-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* 输入框行：非门控时贴合卡片；门控时已嵌入上方 GatePanel */}
          {!gateMessage && composerRow}
        </div>

        {/* 提示文案 */}
        <div className="flex items-center gap-1.5 px-2 pt-1.5">
          <Info
            className={cn(
              "size-3 shrink-0",
              noteError ? "text-destructive" : "text-muted-foreground/40",
            )}
          />
          <span
            className={cn(
              "text-[11px] flex-1 min-w-0 truncate",
              noteError ? "text-destructive" : "text-muted-foreground/60",
            )}
          >
            {noteError
              ? t("autoResearch.form.noteRequired")
              : gateMessage
                ? t("autoResearch.collab.hintGate")
                : paused
                  ? t("autoResearch.collab.hintPaused")
                  : t("autoResearch.collab.hint")}
          </span>
        </div>
      </div>
      {drawer}
    </div>
  )
}

function StopButton({
  onStop,
  label,
  compact,
}: {
  onStop: () => void
  label: string
  compact?: boolean
}) {
  if (compact) {
    return (
      <button
        type="button"
        onClick={onStop}
        title={label}
        className="shrink-0 inline-flex items-center justify-center size-8 rounded-lg border border-destructive/40 text-destructive hover:bg-destructive/10 transition-colors"
      >
        <Square className="size-4" />
      </button>
    )
  }
  return (
    <button
      type="button"
      onClick={onStop}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium shrink-0 border border-destructive/40 text-destructive hover:bg-destructive/10 transition-colors"
    >
      <Square className="size-4" />
      {label}
    </button>
  )
}

/** 日志抽屉入口：终端图标 + 条数 badge。无日志且 SSE 未跑时禁用。 */
function LogButton({
  count,
  active,
  onOpen,
  label,
  compact,
}: {
  count: number
  active: boolean
  onOpen: () => void
  label: string
  compact?: boolean
}) {
  const disabled = count === 0 && !active
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={disabled}
      title={label}
      className={cn(
        "relative shrink-0 inline-flex items-center justify-center rounded-lg border border-border/60 text-muted-foreground/80 hover:text-primary hover:border-primary/30 hover:bg-primary/5 disabled:opacity-40 disabled:pointer-events-none transition-all",
        compact ? "size-6" : "size-8",
      )}
    >
      <Terminal className={compact ? "size-3.5" : "size-4"} />
      {count > 0 && (
        <span className="absolute -top-1 -right-1 min-w-4 h-4 px-1 grid place-items-center rounded-full bg-primary text-primary-foreground text-[10px] font-mono leading-none tabular-nums">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  )
}
