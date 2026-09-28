import {
  ChevronDown,
  Cpu,
  Footprints,
  Info,
  ListStart,
  Loader2,
  Play,
  Send,
  Square,
  StepForward,
  X,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type {
  ResearchMessageItem,
  ResearchMode,
  ResearchSessionItem,
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
import { type StageCell, stageNameByLang, TOTAL_STAGES } from "./tech"

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
  /**
   * ARC ``--to-stage``：跑到该阶段即停（**闭区间**，见 ARC
   * ``execute_pipeline`` 的 `if stage == to_stage: break`）。不传 = 一路跑到底。
   * 「仅运行一步」就是 ``from_stage === to_stage``。
   */
  to_stage?: string | null
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
  /** 完整 23 阶段清单（合成未跑到的阶段为 pending），供「起始阶段」选择器列出全部
   *  可起步阶段——未执行过的后续阶段也可选。 */
  stages: StageCell[]
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
  /**
   * 「仅运行一步」的**落点**：由会话历史算出的下一步阶段号（见
   * ``tech.oneStepStageFromMessages``）。`null` = 23 步已全部跑完，没有下一步，
   * 此时开关置灰不可点。
   */
  nextStage: number | null
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
  nextStage,
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
  // 「仅运行一步」的**待发**（armed）态：点右侧小钮进入，点了就跑、跑完自动退出。
  // 一次性旋钮语义——不做持久模式，所以不存在「忘了关，下次误只跑一步」的坑；
  // 未跑之前再点一次小钮可以手动静默退出（用户看到药丸变形后改变主意）。
  const [oneStepArmed, setOneStepArmed] = useState(false)
  // 药丸本体上的一次性「按下」脉冲：arm 时触发，用最朴素的 class 摘除/重加来重放，
  // 不走 state（避免多一轮渲染，也避免动画被中途打断）。
  const pillRef = useRef<HTMLDivElement>(null)

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

  // 起始阶段：pending 与终态都可用。选择器的值就是父层给的 `fromStage`（父层已按
  // 「会话历史里最后一条阶段消息」推好天然起点，见 tech.naturalStageFromMessages）；
  // 空串＝从头开始。是否**显式下发**同样看它：pending 下天然起点不传，让后端按自己的
  // 规则决定（普通会话从头跑；模板种子会话自动取 10，见 turns.start_turn）；终态没有
  // 天然起点（用户就是要从某步重跑），一律显式带上。
  const showStagePicker =
    (terminal || status === "pending") && stages.length > 0
  const forwardFromStage = fromStage ? fromStage : undefined

  // 「仅运行一步」的落点。**只有一个真值来源：起始阶段选择器**——它选中了哪一阶段，
  // 单步就跑那一阶段（选择器为空＝「从头开始」时，回退到会话历史算出的下一步，
  // 见 tech.oneStepStageFromMessages）。这样选择器和单步按钮永远显示同一个数字，
  // 不会出现「上面选了 10、下面按钮还写着 11」。
  const pickedStageNo = fromStage ? Number(fromStage) : null
  const oneStepTargetNo =
    pickedStageNo && pickedStageNo > 0
      ? Math.min(pickedStageNo, TOTAL_STAGES)
      : (nextStage ?? 0)
  // 锁住的唯一情形：选择器为空（从头开始）且会话历史也推不出下一步（全跑完了）。
  // 「从头跑一步」没有意义（那就是跑全程），所以此时整个单步入口禁用。
  const stepLocked = oneStepTargetNo <= 0

  // 统一的「运行中」：协作轮（问 AI）与流水线轮底层都是「正在运行」，底部这块
  // 不再区分二者——都禁用输入、走边框流光、只显示停止。pipelineRunning 仅在需要
  // 判定「是否流水线态」的极少数处保留（当前已无差异，统一用 isRunning）。
  const pipelineRunning = running
  const isRunning = collabBusy || pipelineRunning

  const inputDisabled = isRunning || sending

  // 单步 armed 是一次性的：**只要这一步真的跑出去了就立刻收回**（sending 由父层在
  // onRun 后置起），所以不存在「开了单步忘了关、下次误只跑一步」的坑。
  // 另两条兜底：会话开始跑（协作/流水线）时收回，避免药丸停在琥珀态里被隐藏；
  // 23 步全跑完（stepLocked）时收回，避免留下一个点不动的变形按钮。
  // 这里不需要 biome-ignore：`setOneStepArmed` 是稳定的 setter，本就不参与依赖推导。
  useEffect(() => {
    if (sending || isRunning || stepLocked) setOneStepArmed(false)
  }, [sending, isRunning, stepLocked])

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

  // 运行：一路跑到最后一步。
  const run = () => {
    if (sending) return
    onRun({
      provider_id: provider.trim() || undefined,
      model_name: model.trim() || undefined,
      mode,
      from_stage: forwardFromStage,
    })
  }

  // 「仅运行一步」：落点就是主按钮上写的那个号（`oneStepTargetNo`，与起始阶段选择器
  // 同源，见下面的派生态）。ARC 的 `--from-stage N --to-stage N` 恰好跑一步
  // （to_stage 是闭区间，跑到该阶段即 break）。
  const runOneStep = () => {
    if (sending || stepLocked) return
    const target = String(oneStepTargetNo)
    onRun({
      provider_id: provider.trim() || undefined,
      model_name: model.trim() || undefined,
      mode,
      from_stage: target,
      to_stage: target,
    })
  }

  // 一次性旋钮：off → armed 时给本体一个轻微脉冲，把「这颗药丸换了身份」说清楚；
  // armed → off 时不动（恢复是常态，安静地收回去即可）。
  const armOneStep = () => {
    if (stepLocked) return
    if (oneStepArmed) {
      setOneStepArmed(false)
      return
    }
    setOneStepArmed(true)
    const el = pillRef.current
    if (el) {
      el.classList.remove("bc-pill-bump")
      // 强制重排，让同名 class 能重新触发同一段动画。
      void el.offsetWidth
      el.classList.add("bc-pill-bump")
    }
  }

  const providerList = providersData?.items ?? []
  const selectedProvider = providerList.find((p) => p.id === provider)
  const defaultProviderName =
    defaultModels?.planner_provider_name || t("autoResearch.provider.default")
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
              title={t("autoResearch.chat.clearInput", {
                defaultValue: "清空",
              })}
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

  // 工具栏左簇：模式开关 + 模型选择器 + 起始阶段（pending / 终态）。三态位置恒定。
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
        <PopoverContent
          className="w-105 p-3"
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

      {/* 起始阶段：pending / 终态都可用，运行中（协作/流水线）隐藏，保持一致。
          pending 下它就是「这次从第几步起跑」；终态下是「从哪一步重跑」。
          它也是「仅运行一步」的**唯一落点来源**：选谁就跑谁那一步，按钮上写的号
          跟着它走；选「从头开始」时才回退到会话历史推的下一步。 */}
      {showStagePicker && !isRunning && (
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
    </div>
  )

  // 主操作合并按钮的派生态：外壳恒定，仅主区语义与右侧运行段的展开随输入变化。
  // - 有文字 → 主区＝发送(协作)；可运行时右侧裂出 ▷ 直跑段 + 单步旋钮。
  // - 空 + 可运行 → 主区＝运行（协作对空消息无意义，直接当运行主操作）；同样裂出旋钮。
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

      {/* ── 主操作：一副会变形的药丸（外壳恒定，仅内部随状态过渡）──
          常规态：主区 = 协作（AI 自行判断要不要跑流水线），默认意图；空输入时直接当运行；
                  右侧 ▷ = 一路跑到最后一步，⏭ = 单步旋钮。
          单步态（armed）：点 ⏭ 后整颗药丸**变形**成「仅跑 #N」——主色转琥珀、图标换成
                  脚印、文案换成「仅跑 #N」，再点一下主区就跑这一步，然后自动变回常规态。
                  一次性，不留任何持久模式。
          armed 时主区**忽略输入框里有没有字**：它的身份已整体改读作「只跑一步」，语义必须
          绝对明确，所以不再随输入态在「发送/运行」之间摇摆（这也是 `mainSendMode` 没有出现
          在上面那个三元里的原因）。
          两类态用同一套宽度补间与交叉淡入做形变，几个点击区同色同壳、只用半透明细线
          分隔；运行中（协作或流水线）整钮隐藏，只留停止。 */}
      {!gateMessage && !isRunning && (
        <div
          ref={pillRef}
          data-armed={oneStepArmed}
          className={cn(
            "bc-send-pill bc-pill-tint relative inline-flex items-stretch h-8 rounded-lg overflow-hidden",
            oneStepArmed && "bc-send-pill-armed",
          )}
        >
          {/* 主区：常规=发送(协作)/运行，单步态=运行一步。语义随状态切换，文案交叉淡入。 */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={
                  oneStepArmed ? runOneStep : mainSendMode ? sendMsg : run
                }
                // 原写法是「armed → sending / 发送态 → inputDisabled / 运行态 → sending」
                // 三分支，但三者恒等：inputDisabled === isRunning || sending，而外层是
                // `!isRunning` 才渲染这颗药丸，故此处 inputDisabled === sending。写成三个
                // 分支会让它看起来像有三种情况，将来 inputDisabled 的定义一变就会掩盖不一致。
                disabled={sending || (mainSendMode && inputDisabled)}
                title={
                  oneStepArmed
                    ? t("autoResearch.input.oneStepRun", {
                        stage: oneStepTargetNo,
                      })
                    : mainSendMode
                      ? t("autoResearch.chat.sendMessage")
                      : t("autoResearch.chat.startTurn")
                }
                className={cn(
                  "relative inline-flex items-center gap-1.5 pl-2.5 pr-3.5 text-xs font-semibold",
                  "bg-transparent",
                  // 文字色也要跟着换：琥珀底上放白字只有 2.15:1，远低于 4.5:1；
                  // 换成 amber-950 是 6.97:1（hover 到 amber-400 时 8.97:1）。
                  oneStepArmed
                    ? "text-amber-950 hover:bg-amber-400/60"
                    : "text-primary-foreground hover:bg-primary/80",
                  "transition-colors duration-200 disabled:opacity-40",
                )}
              >
                {/* 图标形变：三种图标同槽叠放，只靠 opacity + 位移交叉，绝不改变宽度，
                    所以「换图标」不会连带把文字横推一下。 */}
                <span className="relative inline-grid size-4 shrink-0 place-items-center">
                  <Send
                    className={cn(
                      "col-start-1 row-start-1 size-4 transition-all duration-200",
                      !oneStepArmed && mainSendMode
                        ? "opacity-100 scale-100"
                        : "opacity-0 scale-75",
                    )}
                  />
                  <Loader2
                    className={cn(
                      "col-start-1 row-start-1 size-4 transition-all duration-200",
                      !oneStepArmed && !mainSendMode && sending
                        ? "opacity-100 animate-spin"
                        : "opacity-0",
                    )}
                  />
                  <Play
                    className={cn(
                      "col-start-1 row-start-1 size-4 transition-all duration-200",
                      !oneStepArmed && !mainSendMode && !sending
                        ? "opacity-100 scale-100"
                        : "opacity-0 scale-75",
                    )}
                  />
                  <Footprints
                    className={cn(
                      "col-start-1 row-start-1 size-4 transition-all duration-200",
                      oneStepArmed
                        ? "opacity-100 scale-100"
                        : "opacity-0 scale-125",
                    )}
                  />
                </span>
                {/* 文案形变：两套文本叠在同一格，只做 opacity 交叉。宽度取两行文字里
                    较宽的那条（grid 单元格 = max），所以变形时药丸**不会边变边跳宽**。
                    两条文案的长度是**配对选过的**：常规态最长是「发送/运行」（4 个汉字
                    宽），单步态是「仅跑 #22」——`#`、数字和空格都是半宽，合计约等于 4 个
                    汉字宽。两者齐平，所以蓝态与琥珀态的按钮总长一致；早期单步态是
                    「仅运行一步 #22」，比蓝态宽出一截，读起来就像「变身会把按钮拉长」。
                    改文案时两边要一起调，别只动一边。 */}
                <span className="relative inline-grid text-left">
                  <span
                    className={cn(
                      "col-start-1 row-start-1 whitespace-nowrap transition-opacity duration-200",
                      oneStepArmed
                        ? "opacity-0 pointer-events-none"
                        : "opacity-100 animate-in fade-in",
                    )}
                  >
                    {mainSendMode
                      ? t("autoResearch.chat.sendMessage")
                      : t("autoResearch.chat.startTurn")}
                  </span>
                  <span
                    className={cn(
                      "col-start-1 row-start-1 whitespace-nowrap transition-opacity duration-200",
                      oneStepArmed
                        ? "opacity-100 animate-in fade-in duration-200"
                        : "opacity-0 pointer-events-none",
                    )}
                  >
                    {t("autoResearch.input.oneStepRun", {
                      stage: oneStepTargetNo,
                    })}
                  </span>
                </span>
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-55">
              {oneStepArmed
                ? t("autoResearch.input.oneStepRunDesc", {
                    stage: oneStepTargetNo,
                  })
                : mainSendMode
                  ? t("autoResearch.chat.collabHint")
                  : t("autoResearch.chat.runHint")}
            </TooltipContent>
          </Tooltip>

          {/* 运行直跑段：仅「有文字 + 可运行」时裂开——主区被发送占用，所以这里补一个
              「直接跑」的入口。armed 时**不收起，只置暗**：整个药丸的宽度与分区在两种
              状态间必须一致，否则鼠标底下的按钮会自己变窄，「咔哒」的手感立刻变成
              「怎么歪了」。收拢只由输入态驱动（有字/无字）。
              宽度补间 + 内容淡入，收拢时归零并裁剪，外壳其余部分不动。 */}
          <div
            className={cn(
              "grid transition-[grid-template-columns] duration-300 ease-out",
              runSegVisible ? "grid-cols-[1fr]" : "grid-cols-[0fr]",
            )}
          >
            <div className="overflow-hidden flex items-stretch">
              <span
                className={cn(
                  "w-px self-stretch transition-colors duration-200",
                  oneStepArmed ? "bg-amber-950/25" : "bg-primary-foreground/20",
                )}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={run}
                    disabled={sending || oneStepArmed}
                    tabIndex={runSegVisible ? 0 : -1}
                    aria-hidden={!runSegVisible}
                    aria-label={t("autoResearch.chat.startTurn")}
                    className={cn(
                      "inline-flex items-center px-2 transition-colors duration-200",
                      oneStepArmed
                        ? "bg-transparent text-amber-950/45"
                        : "bg-transparent text-primary-foreground hover:bg-primary/80",
                      "disabled:opacity-100 disabled:cursor-default",
                    )}
                  >
                    {sending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Play className="size-4" />
                    )}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-55">
                  {oneStepArmed
                    ? t("autoResearch.input.oneStepExitHint", {
                        stage: oneStepTargetNo,
                      })
                    : t("autoResearch.chat.runHint")}
                </TooltipContent>
              </Tooltip>
            </div>
          </div>

          {/* 单步旋钮：仅「可运行」时存在。点一下把整颗药丸**变形**成「仅跑 #N」
              （一次性 armed，见 armOneStep）；再点一下取消。
              三个状态各有明确外观：可点（主色 ⏭）→ 已 armed（琥珀 ✕）
              → 23 步全跑完（置灰，而不是把第 23 步默默重跑一遍）。
              图标选型与理由见下面那段注释。 */}
          {showRunTools && (
            <>
              <span
                className={cn(
                  "w-px self-stretch transition-colors duration-200",
                  oneStepArmed
                    ? "bg-amber-950/25"
                    : "bg-primary-foreground/20",
                )}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={armOneStep}
                    disabled={inputDisabled || stepLocked}
                    aria-pressed={oneStepArmed}
                    aria-label={
                      oneStepArmed
                        ? t("autoResearch.input.oneStepExit")
                        : t("autoResearch.input.oneStepMenuLabel")
                    }
                    className={cn(
                      "inline-flex items-center justify-center px-1.5 transition-colors duration-200 disabled:opacity-40",
                      // 图标色跟着底色走：琥珀底上是 amber-950（6.97:1），蓝底上是
                      // primary-foreground（5.17:1）。白字压琥珀只有 2.15:1，不能用。
                      oneStepArmed
                        ? "text-amber-950 hover:bg-amber-950/15"
                        : "text-primary-foreground/85 hover:bg-primary-foreground/20",
                    )}
                  >
                    {/* 图标形变：两个图标同槽叠放（固定 size-3.5 格，宽度不参与计算，
                        所以切换时按钮不会抽动），只做 opacity + 旋转交叉。
                        选 `StepForward`（三角 + 一根竖杠）而不是 ▾ / ▸ / ⏭：
                        - ▾ 读作「展开菜单」，这里没有菜单，是上一版最别扭的地方；
                        - ▸ 与主区的 Play 撞形，而左侧直跑段本来就是个 ▸，三个三角会打架；
                        - ⏭（双三角）读作「跳到末尾 / 快进」，语义正好反了——它是只跑一步。
                        `StepForward` 是单三角 + 终止杠，正好是「走一步、到这为止」，
                        与「仅跑 #N」同义。
                        armed 态换成 ✕：此刻这个钮的唯一动作是**取消**，✕ 是唯一无歧义的
                        答案（曾经试过 ✓，那读作「确认执行」，而执行是左边主区的职责）。
                        两态一个「进入」一个「退出」，形状差别大反而好认——不靠图标本身猜，
                        而是：颜色（蓝/琥珀）+ 主区文案（发送 → 仅跑 #N）已经说清当前态，
                        这里只需要说清「点下去会发生什么」。 */}
                    <span className="relative inline-grid size-3.5 shrink-0 place-items-center">
                      <StepForward
                        className={cn(
                          "col-start-1 row-start-1 size-3.5 transition-all duration-200",
                          oneStepArmed
                            ? "opacity-0 -rotate-90"
                            : "opacity-85 rotate-0",
                        )}
                      />
                      <X
                        className={cn(
                          "col-start-1 row-start-1 size-3.5 transition-all duration-200",
                          oneStepArmed
                            ? "opacity-100 rotate-0"
                            : "opacity-0 rotate-90",
                        )}
                      />
                    </span>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-[240px]">
                  {stepLocked
                    ? t("autoResearch.input.oneStepDone")
                    : oneStepArmed
                      ? t("autoResearch.input.oneStepExitHint", {
                          stage: oneStepTargetNo,
                        })
                      : t("autoResearch.input.oneStepMenuHint", {
                          stage: oneStepTargetNo,
                        })}
                </TooltipContent>
              </Tooltip>
            </>
          )}
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
