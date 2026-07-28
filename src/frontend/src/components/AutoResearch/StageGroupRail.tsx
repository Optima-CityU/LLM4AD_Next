import { Check, Cpu, Play, X } from "lucide-react"
import { Fragment } from "react"
import { useTranslation } from "react-i18next"

import {
  HoverCard,
  HoverCardArrow,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { cn } from "@/lib/utils"

import {
  GATE_STAGES,
  STAGE_GROUPS,
  type StageCell,
  type StageStatus,
  stageNameByLang,
} from "./tech"

interface Props {
  cells: StageCell[]
  activeStage: number | null
  onSelect: (cell: StageCell) => void
  /** 是否显示每步的「从此步运行」按钮（仅终态可从指定阶段重跑时为真）。 */
  canRunFromStage?: boolean
  /** 允许作为起点的阶段集合（仅这些阶段显示运行按钮）。 */
  runnableStages?: Set<number>
  /** 从指定阶段运行（等价于底部设好起始阶段再点运行）。 */
  onRunFromStage?: (stage: number) => void
  /**
   * 隐藏 LLM4AD 标识：ml_vision 画像下 9-13 阶段不接 LLM4AD 演化引擎，
   * 去掉分组的 LLM4AD 徽章 / 专属样式，浮层来源提示也改为 ARC 原生。
   */
  hideLlm4ad?: boolean
}

/** 接入本项目 llm4ad 的分组 key（实验设计 + 实验执行）——其余步骤走 ARC。 */
const LLM4AD_GROUPS = new Set(["design", "execution"])

/** 分组聚合态：优先级 failed > running > waiting > done(全完成) > partial > pending。 */
type GroupStatus =
  | "failed"
  | "running"
  | "waiting"
  | "done"
  | "partial"
  | "pending"

interface GroupInfo {
  status: GroupStatus
  done: number
  total: number
  /** 是否已被流转到（任一子步骤非 pending）——决定主轴光束是否点亮。 */
  reached: boolean
}

function aggregate(cells: StageCell[]): GroupInfo {
  const total = cells.length
  const done = cells.filter((c) => c.status === "done").length
  const reached = cells.some((c) => c.status !== "pending")
  const base = { done, total, reached }
  if (cells.some((c) => c.status === "failed"))
    return { status: "failed", ...base }
  if (cells.some((c) => c.status === "running"))
    return { status: "running", ...base }
  if (cells.some((c) => c.status === "waiting"))
    return { status: "waiting", ...base }
  if (done === total && total > 0) return { status: "done", ...base }
  if (done > 0) return { status: "partial", ...base }
  return { status: "pending", ...base }
}

/** 状态主色（十六进制，供 SVG stroke / 光晕使用）。 */
function accent(status: GroupStatus): string {
  switch (status) {
    case "failed":
      return "#ef4444"
    case "waiting":
      return "#f59e0b"
    default:
      // done / running / partial / pending 走品牌主色（蓝）；pending 不画进度弧仅底轨
      return "var(--primary)"
  }
}

// 节点几何：整体由 R 派生，改一处即缩放。
const NODE = 44 // 圆形节点直径
const R = 18 // 进度环半径
const C = 2 * Math.PI * R // 环周长

/**
 * 阶段「流水线主轴视图」（默认）：8 大 Phase 折叠成一条贯通的能量主轴——
 * 8 个带进度环的圆形节点由光束串联，光束随进度从左到右点亮（完成段实心发光、
 * 当前段流动、未来段暗淡），一眼读作"一步步推进的流水线"。
 *
 * 节点中心显示步骤序号（1–8）/ 完成打勾 / 失败叉；外环显示该组子步骤完成度；
 * 当前 Phase 放大 + 光晕脉冲。悬停某节点 → 浮出竖向 timeline 展开该组各阶段
 * （状态串珠 + GATE 徽章 + 当前高亮），点任一阶段打开注入引导抽屉（HITL）。
 */
export default function StageGroupRail({
  cells,
  activeStage,
  onSelect,
  canRunFromStage,
  runnableStages,
  onRunFromStage,
  hideLlm4ad,
}: Props) {
  const { t } = useTranslation()
  const byStage = new Map(cells.map((c) => [c.stage, c]))

  // 预计算每个分组的 present cells + 聚合信息。
  const groups = STAGE_GROUPS.map((g) => {
    const present: StageCell[] = []
    for (let n = g.from; n <= g.to; n++) {
      const c = byStage.get(n)
      if (c) present.push(c)
    }
    return { g, present, info: aggregate(present) }
  }).filter((x) => x.present.length > 0)

  return (
    <div className="overflow-x-auto px-8 pt-2 pb-1.5">
      <div className="mx-auto flex w-full min-w-fit max-w-3xl xl:max-w-4xl 2xl:max-w-5xl items-start">
        {groups.map(({ g, present, info }, gi) => {
          const isActivePhase =
            activeStage != null && activeStage >= g.from && activeStage <= g.to
          const isLlm4ad = !hideLlm4ad && LLM4AD_GROUPS.has(g.key)

          return (
            <Fragment key={g.key}>
              {gi > 0 && (
                <Beam
                  filled={groups[gi].info.reached}
                  flowing={
                    isActivePhase && groups[gi - 1].info.status === "done"
                  }
                />
              )}

              <HoverCard openDelay={60} closeDelay={80}>
                <HoverCardTrigger asChild>
                  <button
                    type="button"
                    className="group relative flex shrink-0 flex-col items-center gap-1 w-[68px]"
                  >
                    <StepNode
                      phase={g.phase}
                      info={info}
                      isActive={isActivePhase}
                      isLlm4ad={isLlm4ad}
                    />
                    {/* 组名 + 计数 */}
                    <div className="flex flex-col items-center leading-none">
                      <span
                        className={cn(
                          "text-[11px] font-semibold tracking-wide transition-colors",
                          isActivePhase
                            ? "text-primary"
                            : isLlm4ad
                              ? "text-primary/90"
                              : info.status === "done"
                                ? "text-foreground/80"
                                : "text-muted-foreground group-hover:text-foreground/80",
                        )}
                      >
                        {t(`autoResearch.stageGroups.${g.key}`, g.key)}
                      </span>
                      {/* 圈下数字改为该 Phase 覆盖的阶段序号区间（如「文献发现」= 3-6） */}
                      <span className="mt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground/60">
                        {g.from === g.to ? g.from : `${g.from}-${g.to}`}
                      </span>
                      {isLlm4ad && (
                        <span className="mt-0.5 inline-flex items-center gap-0.5 rounded-full bg-primary/15 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-primary ring-1 ring-primary/30">
                          <Cpu className="size-2" />
                          LLM4AD
                        </span>
                      )}
                    </div>
                  </button>
                </HoverCardTrigger>

                <HoverCardContent
                  side="bottom"
                  align="center"
                  sideOffset={10}
                  className="w-80 p-0 overflow-hidden"
                >
                  <HoverCardArrow width={12} height={6} />
                  <PhaseTimeline
                    phase={g.phase}
                    name={t(`autoResearch.stageGroups.${g.key}`, g.key)}
                    info={info}
                    present={present}
                    activeStage={activeStage}
                    isLlm4ad={isLlm4ad}
                    hideLlm4ad={hideLlm4ad}
                    onSelect={onSelect}
                    canRunFromStage={canRunFromStage}
                    runnableStages={runnableStages}
                    onRunFromStage={onRunFromStage}
                  />
                </HoverCardContent>
              </HoverCard>
            </Fragment>
          )
        })}
      </div>
    </div>
  )
}

/** 圆形步骤节点：进度环 + 中心 Phase 字母/勾/叉；当前 Phase 光晕脉冲 + 放大。 */
function StepNode({
  phase,
  info,
  isActive,
  isLlm4ad,
}: {
  phase: string
  info: GroupInfo
  isActive: boolean
  isLlm4ad?: boolean
}) {
  const color = accent(info.status)
  const frac = info.total > 0 ? info.done / info.total : 0
  const drawArc = info.status !== "pending" && frac > 0
  const solid = info.status === "done"

  return (
    <div
      className={cn(
        "relative grid place-items-center transition-transform duration-300",
        isActive && "scale-[1.12]",
      )}
      style={{ width: NODE, height: NODE }}
    >
      {/* 光晕：当前脉冲 / 完成柔光 */}
      {(isActive || solid) && (
        <span
          aria-hidden
          className={cn(
            "absolute inset-0 rounded-full blur-md",
            isActive && "animate-pulse",
          )}
          style={{
            background: `radial-gradient(circle, color-mix(in srgb, ${color} 45%, transparent) 0%, transparent 70%)`,
          }}
        />
      )}

      {/* 进度环 */}
      <svg
        width={NODE}
        height={NODE}
        viewBox={`0 0 ${NODE} ${NODE}`}
        className="absolute inset-0 -rotate-90"
      >
        <title>progress</title>
        {/* 底轨：llm4ad 步骤底轨也用主色淡显，强化"这两步不一样" */}
        <circle
          cx={NODE / 2}
          cy={NODE / 2}
          r={R}
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          className={isLlm4ad ? "text-primary/25" : "text-border/50"}
        />
        {/* 进度弧 */}
        {drawArc && (
          <circle
            cx={NODE / 2}
            cy={NODE / 2}
            r={R}
            fill="none"
            stroke={color}
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - (solid ? 1 : frac))}
            style={{
              transition: "stroke-dashoffset 0.5s ease",
              filter: `drop-shadow(0 0 3px color-mix(in srgb, ${color} 60%, transparent))`,
            }}
          />
        )}
      </svg>

      {/* 中心圆盘 + 内容：完成态不再单独描边（避免与外圈进度环重复），仅当前带淡底 */}
      <span
        className={cn(
          "relative grid place-items-center rounded-full bg-background/60 backdrop-blur-sm transition-colors",
          isLlm4ad && !isActive && "ring-1 ring-primary/30",
        )}
        style={{
          width: R * 2 - 6,
          height: R * 2 - 6,
          background: isActive
            ? `color-mix(in srgb, ${color} 14%, var(--background))`
            : isLlm4ad
              ? "color-mix(in srgb, var(--primary) 8%, var(--background))"
              : undefined,
        }}
      >
        <NodeContent phase={phase} status={info.status} color={color} />
      </span>

      {/* 完成角标：右下角小勾（主色描边 + 发光，不填充实体），与中心序号并存 */}
      {solid && (
        <span
          className="absolute bottom-0 right-0 grid size-3.5 place-items-center rounded-full bg-background text-primary ring-2 ring-background"
          style={{
            boxShadow: `inset 0 0 0 1.5px color-mix(in srgb, ${color} 75%, transparent), 0 0 5px color-mix(in srgb, ${color} 50%, transparent)`,
          }}
        >
          <Check className="size-2.5" strokeWidth={4} />
        </span>
      )}
    </div>
  )
}

/** 节点中心内容：failed=✕、其余（含完成）=Phase 字母；完成态的「已完成」由角标勾表达。 */
function NodeContent({
  phase,
  status,
  color,
}: {
  phase: string
  status: GroupStatus
  color: string
}) {
  if (status === "failed")
    return <X className="size-4 text-red-500" strokeWidth={3} />
  return (
    <span
      className="text-[15px] font-bold leading-none"
      style={{
        color:
          status === "done" ||
          status === "running" ||
          status === "partial" ||
          status === "waiting"
            ? color
            : "var(--muted-foreground)",
      }}
    >
      {phase}
    </span>
  )
}

/** 主轴光束（节点之间的连接段）：填充=已流转、flowing=当前前沿流动动画。 */
function Beam({ filled, flowing }: { filled: boolean; flowing: boolean }) {
  return (
    <div
      className="relative mt-[21px] h-[3px] flex-1 min-w-[16px] overflow-hidden rounded-full"
      style={{
        background: filled
          ? "linear-gradient(90deg, color-mix(in srgb, var(--primary) 55%, transparent), color-mix(in srgb, var(--primary) 85%, transparent))"
          : "color-mix(in srgb, var(--border) 60%, transparent)",
        boxShadow: filled
          ? "0 0 6px color-mix(in srgb, var(--primary) 45%, transparent)"
          : undefined,
      }}
    >
      {flowing && (
        <span
          aria-hidden
          className="absolute inset-y-0 -left-1/2 w-1/2 animate-[beamflow_1.1s_linear_infinite]"
          style={{
            background:
              "linear-gradient(90deg, transparent, color-mix(in srgb, var(--primary) 95%, white 40%), transparent)",
          }}
        />
      )}
    </div>
  )
}

/** 悬停浮层：竖向 timeline 展开该组各阶段（串珠 + GATE 徽章 + 当前高亮）。 */
function PhaseTimeline({
  phase,
  name,
  info,
  present,
  activeStage,
  isLlm4ad,
  hideLlm4ad,
  onSelect,
  canRunFromStage,
  runnableStages,
  onRunFromStage,
}: {
  phase: string
  name: string
  info: GroupInfo
  present: StageCell[]
  activeStage: number | null
  isLlm4ad?: boolean
  hideLlm4ad?: boolean
  onSelect: (cell: StageCell) => void
  canRunFromStage?: boolean
  runnableStages?: Set<number>
  onRunFromStage?: (stage: number) => void
}) {
  const { t } = useTranslation()
  const color = accent(info.status)
  const frac = info.total > 0 ? info.done / info.total : 0

  return (
    <div>
      {/* 头部：步骤序号徽章 + 组名 + 进度条 */}
      <div className="flex items-center gap-2 px-3 pt-2.5 pb-2 border-b border-border/40 bg-muted/20">
        <span
          className="grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white"
          style={{
            background: color,
            boxShadow: `0 0 8px color-mix(in srgb, ${color} 55%, transparent)`,
          }}
        >
          {info.status === "done" ? (
            <Check className="size-3.5" strokeWidth={3} />
          ) : (
            phase
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold text-foreground truncate">
              {name}
            </span>
            {isLlm4ad && (
              <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-primary/15 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-primary ring-1 ring-primary/30">
                <Cpu className="size-2" />
                LLM4AD
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-border/50">
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${Math.round(frac * 100)}%`,
                  background: color,
                }}
              />
            </div>
            <span className="font-mono text-[10px] tabular-nums text-muted-foreground/70 shrink-0">
              {info.done}/{info.total}
            </span>
          </div>
        </div>
      </div>

      {/* 阶段来源提示：llm4ad 步骤 / 其余 ARC */}
      <div className="px-3 pt-1.5 text-[10px] text-muted-foreground/70">
        {isLlm4ad
          ? t(
              "autoResearch.stages.poweredByLlm4ad",
              "本步由 LLM4AD 演化引擎驱动",
            )
          : t("autoResearch.stages.poweredByArc", "由 AutoResearchClaw 驱动")}
      </div>

      {/* 竖向 timeline：每个子阶段一串珠，点击打开注入引导 */}
      <ul className="px-2.5 py-2">
        {present.map((cell, i) => (
          <TimelineRow
            key={cell.stage}
            cell={cell}
            isActive={activeStage != null && cell.stage === activeStage}
            last={i === present.length - 1}
            hideLlm4ad={hideLlm4ad}
            onSelect={onSelect}
            canRunFromStage={canRunFromStage}
            runnableStages={runnableStages}
            onRunFromStage={onRunFromStage}
          />
        ))}
      </ul>

      <div className="px-3 pb-2 pt-0.5 border-t border-border/30 bg-muted/10">
        <span className="text-[10px] text-muted-foreground/60">
          💡 {t("autoResearch.stages.clickToInject", "点击阶段可注入引导文本")}
        </span>
      </div>
    </div>
  )
}

/** timeline 单行：左侧串珠(状态点)+竖连线，右侧阶段名 + GATE 徽章。 */
function TimelineRow({
  cell,
  isActive,
  last,
  hideLlm4ad,
  onSelect,
  canRunFromStage,
  runnableStages,
  onRunFromStage,
}: {
  cell: StageCell
  isActive: boolean
  last: boolean
  hideLlm4ad?: boolean
  onSelect: (cell: StageCell) => void
  canRunFromStage?: boolean
  runnableStages?: Set<number>
  onRunFromStage?: (stage: number) => void
}) {
  const { t, i18n } = useTranslation()
  const isGate = GATE_STAGES.has(cell.stage)
  const label = stageNameByLang(cell.stage, i18n.language) || cell.name
  // ml_vision 画像下 9-13/15 步骤改用不含 LLM4AD 文字的 ARC 原生描述；
  // 无对应覆盖（返回空串）时回退到默认描述。
  const mlVisionDesc = hideLlm4ad
    ? t(`autoResearch.stages.descriptionsMlVision.${cell.stage}`, "")
    : ""
  const description =
    mlVisionDesc || t(`autoResearch.stages.descriptions.${cell.stage}`, "")
  const dot = statusDot(cell.status)
  // 「从此步运行」按钮：显示逻辑与底部起始阶段选择器一致（终态可重跑时才出现），
  // 且仅对「允许作为起点」的阶段（真实快照阶段）显示；点击等价于把起始阶段设为该步再点运行。
  const showRun =
    !!canRunFromStage &&
    !!onRunFromStage &&
    (runnableStages?.has(cell.stage) ?? false)

  return (
    <li>
      <div
        className={cn(
          "group flex w-full items-stretch gap-2 rounded-md px-1.5 transition-all",
          isActive ? "bg-primary/10 shadow-sm" : "hover:bg-muted/60 hover:shadow-sm",
        )}
      >
        <button
          type="button"
          onClick={() => onSelect(cell)}
          title={`#${cell.stage} · ${t("autoResearch.stages.injectGuidance")}`}
          className="flex flex-1 min-w-0 items-stretch gap-2 text-left cursor-pointer"
        >
          {/* 串珠 + 竖连线 */}
          <div className="relative flex w-3 shrink-0 flex-col items-center pt-2">
            <span
              className={cn(
                "size-2.5 shrink-0 rounded-full ring-2 ring-background transition-colors",
                dot.cls,
              )}
              style={dot.style}
            />
            {!last && <span className="w-px flex-1 bg-border/60" />}
          </div>
          {/* 名称 + 描述 + 徽章 */}
          <div className="flex min-w-0 flex-1 flex-col gap-0.5 py-1.5">
            <div className="flex items-center gap-1.5">
              <span className="shrink-0 font-mono text-[11px] font-semibold tabular-nums text-foreground/70 group-hover:text-primary/80 transition-colors">
                #{cell.stage}
              </span>
              <span
                className={cn(
                  "flex-1 truncate text-[11px] transition-colors",
                  isActive
                    ? "font-semibold text-primary"
                    : cell.status === "done"
                      ? "text-foreground/70 group-hover:text-foreground/90"
                      : cell.status === "failed"
                        ? "text-red-500"
                        : "text-foreground/85 group-hover:text-foreground",
                )}
              >
                {label}
              </span>
              {isGate && (
                <span className="shrink-0 rounded bg-amber-500/15 px-1 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400">
                  GATE
                </span>
              )}
            </div>
            {/* 阶段描述 */}
            {description && (
              <p className="text-[10px] leading-snug text-muted-foreground/60 line-clamp-2 group-hover:text-muted-foreground/80 transition-colors">
                {description}
              </p>
            )}
          </div>
        </button>
        {/* 从此步运行：点击即以该阶段为起点触发运行（与底部运行按钮同逻辑）。 */}
        {showRun && (
          <button
            type="button"
            onClick={() => onRunFromStage?.(cell.stage)}
            title={t("autoResearch.stages.runFromHere", "从此步运行")}
            className="my-1 grid size-6 shrink-0 place-items-center self-center rounded-md text-muted-foreground opacity-0 transition-all hover:bg-primary/15 hover:text-primary focus:opacity-100 group-hover:opacity-100"
          >
            <Play className="size-3" />
          </button>
        )}
      </div>
    </li>
  )
}

/** timeline 串珠的状态配色。 */
function statusDot(status: StageStatus): {
  cls: string
  style?: React.CSSProperties
} {
  switch (status) {
    case "done":
      return { cls: "bg-emerald-500" }
    case "running":
      return { cls: "bg-primary animate-pulse" }
    case "waiting":
      return { cls: "bg-amber-500 animate-pulse" }
    case "failed":
      return { cls: "bg-red-500" }
    case "skipped":
      return { cls: "bg-muted-foreground/50" }
    default:
      return { cls: "bg-muted-foreground/30" }
  }
}
