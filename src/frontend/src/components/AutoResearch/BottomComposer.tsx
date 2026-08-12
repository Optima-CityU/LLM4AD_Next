import {
  ChevronDown,
  Cpu,
  Info,
  ListStart,
  Loader2,
  Play,
  Send,
  Square,
  X,
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

import GateHeader, { gateActionClass, getGateActions } from "./GatePanel"
import ProviderModelPicker from "./ProviderModelPicker"
import { MODE_OPTIONS } from "./shared"
import { stageNameByLang } from "./tech"

// 门控动作里哪些必须给理由 / 哪些把理由当 guidance 传入。理由取自底部输入框。
const NEEDS_REASON = new Set(["reject", "inject"])
const GUIDANCE_ACTIONS = new Set(["inject"])

// 输入框字数上限：协作消息 / 门控说明都取自同一输入框，给一个合理上限防止
// 无限输入。接近上限（>90%）字数统计转琥珀提醒，触顶转红。
const MAX_INPUT_CHARS = 5000

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
  onGateSubmit,
}: Props) {
  const { t, i18n } = useTranslation()
  const { data: providersData } = useProviders()
  const { data: defaultModels } = useUserDefaultModels()
  const [text, setText] = useState("")
  const [noteError, setNoteError] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
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

  const status = session.status
  const terminal =
    status === "completed" || status === "failed" || status === "cancelled"
  const showRunTools = status === "pending" || terminal

  // 统一的「运行中」：协作轮（问 AI）与流水线轮底层都是「正在运行」，底部这块
  // 不再区分二者——都禁用输入、走边框流光、只显示停止。pipelineRunning 仅在需要
  // 判定「是否流水线态」的极少数处保留（当前已无差异，统一用 isRunning）。
  const pipelineRunning = running
  const isRunning = collabBusy || pipelineRunning

  const inputDisabled = isRunning || sending
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

  // ── 固定骨架：头部 → 输入框 → 工具栏，三态槽位一致 ──
  // 控件永远在同一位置：模式开关最左、模型选择器挨着它、主操作永远在工具栏最右。
  // 状态差异只体现在「头部显示什么」与「主操作是什么」，骨架不动。

  const { actions: gateActions } = gateMessage
    ? getGateActions(gateMessage.payload as never)
    : { actions: [] as string[] }
  const decisionActions = gateActions.filter((v) => v !== "abort")

  const hasText = text.trim().length > 0

  // 输入槽位：idle/gate 显示 textarea；运行中在同一槽位改显等高的「运行中」指示，
  // 故槽位高度三态恒定，运行结束回到 idle 时总高不跳变。仅 placeholder 随态变。
  // 换行/发送键位：纯回车＝发送；Shift/Ctrl/Alt/⌘+回车＝换行（后三者浏览器默认
  // 不会插入换行，需手动在光标处插入 \n）。
  const insertNewline = (el: HTMLTextAreaElement) => {
    if (text.length >= MAX_INPUT_CHARS) return
    const start = el.selectionStart
    const end = el.selectionEnd
    const next = `${text.slice(0, start)}\n${text.slice(end)}`
    setText(next)
    // 换行后把光标移到新行首（等 React 重渲染回填 value 后再设 selection）。
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + 1
    })
  }
  const inputSlot = (
    <div className="relative px-3.5 pt-3 pb-2">
      {isRunning ? (
        // 运行中：同一槽位改显等高的「运行中」指示（min-h-7 与 textarea 单行等高）。
        <div className="flex items-center gap-2 min-h-7 animate-in fade-in duration-200">
          <span className="relative flex size-2 shrink-0">
            <span className="absolute inline-flex h-full w-full rounded-full bg-primary/60 animate-ping" />
            <span className="relative inline-flex size-2 rounded-full bg-primary" />
          </span>
          <span className="text-sm font-medium text-primary/90">
            {t("autoResearch.chat.runningNow", { defaultValue: "运行中" })}
          </span>
        </div>
      ) : (
        <>
          <textarea
            ref={textareaRef}
            rows={1}
            value={text}
            disabled={sending}
            maxLength={MAX_INPUT_CHARS}
            onChange={(e) => {
              setText(e.target.value)
              if (noteError) setNoteError(false)
            }}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return
              // Shift+回车：走浏览器默认换行。
              if (e.shiftKey) return
              // Ctrl / Alt / ⌘ + 回车：手动插入换行（默认不换行）。
              if (e.ctrlKey || e.altKey || e.metaKey) {
                e.preventDefault()
                insertNewline(e.currentTarget)
                return
              }
              // 纯回车：发送。
              e.preventDefault()
              sendMsg()
            }}
            placeholder={
              gateMessage
                ? t("autoResearch.collab.placeholderGate")
                : t("autoResearch.collab.placeholder")
            }
            className="w-full resize-none bg-transparent text-sm leading-relaxed outline-none placeholder:text-muted-foreground/50 min-h-7 max-h-40 py-0.5 pr-8 disabled:opacity-60 transition-[height] duration-150 ease-out"
          />
          {/* 清空：钉在输入框右上角。pr-8 已给 textarea 让位，长文不会压到按钮下。 */}
          {hasText && (
            <button
              type="button"
              onClick={() => {
                setText("")
                if (noteError) setNoteError(false)
                textareaRef.current?.focus()
              }}
              title={t("autoResearch.chat.clearInput", { defaultValue: "清空" })}
              aria-label={t("autoResearch.chat.clearInput", {
                defaultValue: "清空",
              })}
              className="absolute top-2 right-2 grid size-6 place-items-center rounded-md text-muted-foreground/50 hover:text-foreground hover:bg-muted/60 transition-colors animate-in fade-in duration-200"
            >
              <X className="size-3.5" />
            </button>
          )}
        </>
      )}
    </div>
  )

  // 工具栏左簇：模式开关 + 模型选择器 + （终态）起始阶段。三态位置恒定。
  const toolbarLeft = (
    <div className="flex items-center gap-1 min-w-0">
      <div
        role="group"
        aria-label={t("autoResearch.create.modeLabel")}
        className="inline-flex items-center rounded-md border border-border/60 bg-background/50 p-0.5 shrink-0"
      >
        {MODE_OPTIONS.map((m) => (
          <button
            key={m}
            type="button"
            disabled={inputDisabled}
            onClick={() => onModeChange(m)}
            className={cn(
              "px-1.5 py-0.5 rounded text-[11px] font-medium transition-colors disabled:opacity-50",
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
            disabled={inputDisabled}
            className={cn(
              "inline-flex h-6 items-center gap-1 px-1.5 rounded-md text-[11px] font-medium transition-colors disabled:opacity-50 shrink-0",
              provider && provider !== "default"
                ? "text-primary hover:bg-primary/10"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
            )}
          >
            <Cpu className="size-3 shrink-0" />
            <span className="max-w-32 truncate">{providerLabel}</span>
            <ChevronDown className="size-3 shrink-0 opacity-60" />
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-105 p-3" align="start" side="top" sideOffset={8}>
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

      {/* 起始阶段：仅终态可从某步重跑。运行中（协作/流水线）隐藏，保持一致。 */}
      {terminal && !isRunning && stages.length > 0 && (
        <Select
          value={fromStage || "__begin__"}
          onValueChange={(v) => onFromStageChange(v === "__begin__" ? "" : v)}
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <SelectTrigger
                size="sm"
                aria-label={t("autoResearch.input.fromStageLabel")}
                className="h-6 w-auto gap-1 rounded-md border-0 bg-transparent dark:bg-transparent dark:hover:bg-transparent px-1.5 py-0 text-[11px] font-medium text-muted-foreground shadow-none hover:text-foreground focus-visible:ring-0 [&>svg:last-child]:size-3 [&>svg:last-child]:opacity-60 shrink-0"
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
              <SelectItem key={s.stage} value={String(s.stage)} className="text-xs">
                #{s.stage} {stageNameByLang(s.stage, i18n.language) || s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  )

  // 主操作合并按钮的派生态：外壳恒定，仅主区语义与右侧运行段的展开随输入变化。
  // - 有文字 → 主区＝发送(协作)；可运行时右侧裂出 ▷ 直跑段。
  // - 空 + 可运行 → 主区＝运行(协作对空消息无意义，直接当运行主操作)。
  // - 空 + 不可运行(paused/collaborating) → 主区＝发送但禁用，占位保持槽位恒定。
  const mainSendMode = hasText || !showRunTools
  const runSegVisible = showRunTools && hasText

  // 工具栏右簇：主操作（发送/运行分段按钮）+ 运行中的停止。永远靠右，位置恒定。
  const toolbarRight = (
    <div className="ml-auto flex items-center gap-1.5 shrink-0">
      {/* 字数统计：有输入时才出现；接近上限转琥珀、触顶转红。tabular-nums 防跳动。 */}
      {hasText && !isRunning && (
        <span
          className={cn(
            "text-[11px] tabular-nums shrink-0 transition-colors animate-in fade-in duration-200",
            text.length >= MAX_INPUT_CHARS
              ? "text-destructive font-medium"
              : text.length >= MAX_INPUT_CHARS * 0.9
                ? "text-amber-600 dark:text-amber-500"
                : "text-muted-foreground/50",
          )}
        >
          {text.length}/{MAX_INPUT_CHARS}
        </span>
      )}

      {/* ── 主操作：合并的分段按钮（外壳恒定，仅内部随输入态过渡）──
          方案 A：回车 / 点主区 = 协作（默认，AI 自行判断要不要跑流水线）；
          点右段 ▷ = 直接运行流水线（更快、不消耗对话 token）。两个意图同一个
          控件、各自可点。切换时右侧运行段以宽度补间「裂开/收拢」，主区图标做
          交叉淡入，避免整钮硬替换的突兀。运行中（协作或流水线）整钮隐藏，只留停止。 */}
      {!gateMessage && !isRunning && (
        <div className="bc-send-pill relative inline-flex items-stretch h-8 rounded-lg overflow-hidden">
          {/* 主区：发送(协作) / 运行，语义随输入切换；文案做交叉淡入。
              tooltip 也随语义切换：协作＝答疑/改产物/推进，运行＝直接推进流水线。 */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={mainSendMode ? sendMsg : run}
                disabled={mainSendMode ? inputDisabled : sending}
                title={
                  mainSendMode
                    ? t("autoResearch.chat.sendMessage")
                    : t("autoResearch.chat.startTurn")
                }
                className="relative inline-flex items-center gap-1.5 px-3.5 text-xs font-semibold bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors"
              >
                {mainSendMode ? (
                  <Send className="size-4" />
                ) : sending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Play className="size-4" />
                )}
                <span
                  key={mainSendMode ? "send" : "run"}
                  className="animate-in fade-in duration-200"
                >
                  {mainSendMode
                    ? t("autoResearch.chat.sendMessage")
                    : t("autoResearch.chat.startTurn")}
                </span>
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-55">
              {mainSendMode
                ? t("autoResearch.chat.collabHint")
                : t("autoResearch.chat.runHint")}
            </TooltipContent>
          </Tooltip>

          {/* 运行直跑段：仅「有文字 + 可运行」时裂开。与主区同为实心主色，只用一道
              半透明细线分隔、hover 微变色——读作「一颗药丸的两个点击区」而非两个按钮。
              宽度补间 + 内容淡入，收拢时归零并裁剪，外壳其余部分不动。 */}
          <div
            className={cn(
              "grid transition-[grid-template-columns] duration-300 ease-out",
              runSegVisible ? "grid-cols-[1fr]" : "grid-cols-[0fr]",
            )}
          >
            <div className="overflow-hidden flex items-stretch">
              <span className="w-px self-stretch bg-primary-foreground/20" />
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={run}
                    disabled={sending}
                    tabIndex={runSegVisible ? 0 : -1}
                    aria-hidden={!runSegVisible}
                    aria-label={t("autoResearch.chat.startTurn")}
                    className="inline-flex items-center px-2 bg-primary text-primary-foreground hover:bg-primary/80 disabled:opacity-40 transition-colors"
                  >
                    {sending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Play className="size-4" />
                    )}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-55">
                  {t("autoResearch.chat.runHint")}
                </TooltipContent>
              </Tooltip>
            </div>
          </div>
        </div>
      )}

      {/* 门控：决策按钮（approve 主色实心、其余中性）。理由取输入框。 */}
      {gateMessage &&
        decisionActions.map((v) => (
          <button
            key={v}
            type="button"
            disabled={inputDisabled}
            onClick={() => handleGateAction(v)}
            className={cn(
              "inline-flex items-center h-8 px-3.5 rounded-lg text-xs font-semibold border transition-all disabled:opacity-60",
              gateActionClass(v),
            )}
          >
            {t(`autoResearch.form.actions.${v}`, v)}
          </button>
        ))}

      {/* 运行中（协作或流水线，底层同为「正在运行」）：只显示停止。 */}
      {isRunning && (
        <button
          type="button"
          onClick={onStop}
          className="inline-flex items-center gap-1.5 px-3.5 h-8 rounded-lg text-xs font-semibold border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors"
        >
          <Square className="size-3.5" />
          {t("autoResearch.chat.stopTurn")}
        </button>
      )}
    </div>
  )

  // 头部槽：仅门控态显示门控头部。运行态的「运行中」指示已移入输入槽位（等高替换
  // textarea），不再另加头部，从而与默认态总高一致。
  const headerSlot = gateMessage ? (
    <div className="animate-in fade-in slide-in-from-bottom-1 duration-200">
      <GateHeader
        message={gateMessage}
        sessionId={session.id}
        disabled={inputDisabled}
        onAction={handleGateAction}
      />
    </div>
  ) : null

  // 卡片外框态：错误 > 门控（琥珀）> 运行（主色 + 边框流光）> 空闲。
  const shellCls = noteError
    ? "border-destructive/60 shadow-destructive/8 focus-within:border-destructive focus-within:ring-2 focus-within:ring-destructive/15"
    : gateMessage
      ? "border-amber-500/45 shadow-amber-500/10 focus-within:border-amber-500/60 focus-within:ring-2 focus-within:ring-amber-500/15"
      : isRunning
        ? "border-primary/40 bc-running-breathe"
        : "border-primary/20 shadow-primary/8 hover:border-primary/30 focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/15"

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
            "relative rounded-2xl border-2 bg-card/95 backdrop-blur-md shadow-lg transition-[border-color,box-shadow] duration-300",
            shellCls,
          )}
        >
          {/* ── 固定三分区：头部 → 输入槽位 → 工具栏。输入槽位三态等高（运行中
              显示等高的运行指示），故高度稳定、无跳变。 ── */}
          {headerSlot}

          {/* 头部与输入槽之间的分隔线（仅门控头部时画，让层次清晰） */}
          {headerSlot && <div className="mx-3.5 border-t border-border/40" />}

          {inputSlot}

          {/* 工具栏：控件槽位三态恒定。压扁竖向高度，让上方输入框成为主区。 */}
          <div className="flex items-center gap-1 flex-wrap px-2.5 py-1 border-t border-border/30">
            {toolbarLeft}
            {toolbarRight}
          </div>
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
                : isRunning
                  ? t("autoResearch.input.runningHint")
                  : paused
                    ? t("autoResearch.collab.hintPaused")
                    : t("autoResearch.collab.hint")}
          </span>
        </div>
      </div>
    </div>
  )
}
