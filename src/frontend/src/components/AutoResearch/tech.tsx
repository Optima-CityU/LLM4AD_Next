/**
 * AutoResearch 共享「炫酷」设计原语。
 *
 * 复用演化页（/evolution）的科技风视觉语言：粒子背景、发光边框、TechCorner
 * 装饰角、青色主色（dark=`#00d4ff` / light=`#2563eb`，均来自 `--primary`）。
 * 所有 AutoResearch 组件统一从这里取样式，保证与演化页风格一致。
 */

import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"

import { useTheme } from "@/components/theme-provider"
import { cn } from "@/lib/utils"

// 直接复用演化页的科技原语（同一套视觉语言，避免重复实现）
export { default as TechBackground } from "@/components/Evolution/TechBackground"
export { default as TechCorner } from "@/components/Evolution/TechCorner"
export { default as TechPanel } from "@/components/Evolution/TechPanel"

// ────────────────────────────────────────────────────────────────────────────
// 23 阶段分组（1-based，权威顺序见 AUTORESEARCH_FRONTEND_GUIDE §5）
// 阶段名由后端派生（state.stages[].name），前端只按阶段号分组，不硬编码名称。
// ────────────────────────────────────────────────────────────────────────────

export interface StageGroup {
  key: string
  /** Phase 字母（A–H），显示在分组标签前。 */
  phase: string
  from: number
  to: number
}

/**
 * 8 个 ARC Phase（A–H），权威分组见
 * `AutoResearchClaw/docs/LLM4AD_PIPELINE_COMPARISON.md` §3 v3 流程图：
 * A 立题(1-2) · B 文献(3-6) · C 综合(7-8) · D 设计(9-11) · E 执行(12-13) ·
 * F 决策(14-15) · G 写作(16-19) · H 收尾(20-23)。
 */
export const STAGE_GROUPS: StageGroup[] = [
  { key: "scoping", phase: "A", from: 1, to: 2 },
  { key: "literature", phase: "B", from: 3, to: 6 },
  { key: "synthesis", phase: "C", from: 7, to: 8 },
  { key: "design", phase: "D", from: 9, to: 11 },
  { key: "execution", phase: "E", from: 12, to: 13 },
  { key: "decision", phase: "F", from: 14, to: 15 },
  { key: "writing", phase: "G", from: 16, to: 19 },
  { key: "finalize", phase: "H", from: 20, to: 23 },
]

/** GATE 阶段（HITL 强制暂停点）：文献筛选 / 实验设计 / 质量门。 */
export const GATE_STAGES = new Set([5, 9, 20])

export const TOTAL_STAGES = 23

/**
 * 23 阶段权威名称（1-based，index 0 = stage 1；见 GUIDE §5）。
 *
 * 仅作「未跑到的阶段」的兜底名——后端 `state.stages[].name` 存在时**永远优先**。
 * 有了它，进度轨才能在首屏 / 失败早退时也画出完整的 23 阶段路线图（未来阶段
 * 以待运行态呈现），而不是只画后端已返回的那几颗。
 */
export const STAGE_NAMES: readonly string[] = [
  "TOPIC_INIT",
  "PROBLEM_DECOMPOSE",
  "SEARCH_STRATEGY",
  "LITERATURE_COLLECT",
  "LITERATURE_SCREEN",
  "KNOWLEDGE_EXTRACT",
  "SYNTHESIS",
  "HYPOTHESIS_GEN",
  "EXPERIMENT_DESIGN",
  "CODE_GENERATION",
  "RESOURCE_PLANNING",
  "EXPERIMENT_RUN",
  "ITERATIVE_REFINE",
  "RESULT_ANALYSIS",
  "RESEARCH_DECISION",
  "PAPER_OUTLINE",
  "PAPER_DRAFT",
  "PEER_REVIEW",
  "PAPER_REVISION",
  "QUALITY_GATE",
  "KNOWLEDGE_ARCHIVE",
  "EXPORT_PUBLISH",
  "CITATION_VERIFY",
]

/**
 * 23 阶段中文短名（1-based，与 `STAGE_NAMES` 逐项对应）。
 *
 * 标签视图默认展示中文短名，让新手无需悬停即可读懂流水线。后端 `name` 为英文
 * 代号时用本表按阶段号翻译；查不到再回退英文代号。名称保持 4–6 字紧凑，便于
 * 每框两列平铺。
 */
export const STAGE_NAMES_ZH: readonly string[] = [
  "课题初始化",
  "问题分解",
  "检索策略",
  "文献采集",
  "文献筛选",
  "知识抽取",
  "综合归纳",
  "假设生成",
  "实验设计",
  "代码生成",
  "资源规划",
  "实验运行",
  "迭代优化",
  "结果分析",
  "研究决策",
  "论文提纲",
  "论文初稿",
  "同行评审",
  "论文修订",
  "质量门禁",
  "知识归档",
  "导出发布",
  "引用核验",
]

/**
 * 23 阶段英文短名（1-based，与 `STAGE_NAMES` 逐项对应）。
 *
 * 英文界面展示这套友好短名（而非后端全大写代号如 `TOPIC_INIT`），与中文短名
 * 对齐、保持紧凑。查不到再回退英文代号。
 */
export const STAGE_NAMES_EN: readonly string[] = [
  "TOPIC_INIT",
  "PROBLEM_DECOMPOSE",
  "SEARCH_STRATEGY",
  "LITERATURE_COLLECT",
  "LITERATURE_SCREEN",
  "KNOWLEDGE_EXTRACT",
  "SYNTHESIS",
  "HYPOTHESIS_GEN",
  "EXPERIMENT_DESIGN",
  "CODE_GENERATION",
  "RESOURCE_PLANNING",
  "EXPERIMENT_RUN",
  "ITERATIVE_REFINE",
  "RESULT_ANALYSIS",
  "RESEARCH_DECISION",
  "PAPER_OUTLINE",
  "PAPER_DRAFT",
  "PEER_REVIEW",
  "PAPER_REVISION",
  "QUALITY_GATE",
  "KNOWLEDGE_ARCHIVE",
  "EXPORT_PUBLISH",
  "CITATION_VERIFY",
]

/** 按阶段号取中文短名（1-based）；越界回退空串。 */
export function stageNameZh(stage: number): string {
  return STAGE_NAMES_ZH[stage - 1] ?? ""
}

/**
 * 按语言取阶段短名（1-based）：中文界面用中文短名，其它语言用英文短名。
 * 越界回退空串（调用方再回退后端 `name`）。
 */
export function stageNameByLang(stage: number, lang: string): string {
  const zh = lang?.startsWith("zh")
  const table = zh ? STAGE_NAMES_ZH : STAGE_NAMES_EN
  return table[stage - 1] ?? ""
}

/** 进度轨用的一格阶段快照（后端字段的最小子集，允许合成未来阶段）。 */
export interface StageCell {
  stage: number
  name: string
  status: StageStatus
}

/**
 * 把后端返回的（可能不完整的）阶段列表补齐成完整 23 格路线图。
 *
 * 后端已返回的阶段保留其真实 status / name；未返回的阶段合成为 `pending` +
 * 兜底名。这样进度轨在任何时刻（首屏 / 早退 / 回跳）都展示完整流水线轮廓。
 */
export function buildStageRoadmap(
  stages: { stage: number; name?: string | null; status?: string | null }[],
): StageCell[] {
  const byStage = new Map(stages.map((s) => [s.stage, s]))
  const cells: StageCell[] = []
  for (let n = 1; n <= TOTAL_STAGES; n++) {
    const s = byStage.get(n)
    cells.push({
      stage: n,
      name: s?.name || STAGE_NAMES[n - 1] || `stage-${n}`,
      status: (s?.status as StageStatus) ?? "pending",
    })
  }
  return cells
}

// ────────────────────────────────────────────────────────────────────────────
// 状态配色（固定色，不随主题；与演化页 TASK_STATUS_DOT 一致）
// ────────────────────────────────────────────────────────────────────────────

export type StageStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "skipped"
  | "waiting"

export type SessionStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"

/** 会话状态胶囊配色（class 串）。 */
export function sessionStatusClasses(status: string): string {
  switch (status) {
    case "running":
      return "border-primary/40 text-primary bg-primary/10"
    case "completed":
      return "border-emerald-500/40 text-emerald-500 bg-emerald-500/10"
    case "paused":
      return "border-amber-500/40 text-amber-500 bg-amber-500/10"
    case "failed":
      return "border-red-500/40 text-red-500 bg-red-500/10"
    case "cancelled":
      return "border-border/60 text-muted-foreground bg-muted/40"
    default:
      return "border-border/60 text-muted-foreground bg-muted/30"
  }
}

/** 侧栏会话行左侧的状态小圆点（class 串）。 */
export function sessionDotClasses(status: string): string {
  switch (status) {
    case "running":
      return "bg-primary animate-pulse glow-dot"
    case "paused":
      return "bg-amber-500 animate-pulse"
    case "completed":
      return "bg-emerald-500"
    case "failed":
      return "bg-red-500"
    case "cancelled":
      return "bg-muted-foreground/50"
    default:
      return "bg-muted-foreground/40"
  }
}

// ────────────────────────────────────────────────────────────────────────────
// 复用组件
// ────────────────────────────────────────────────────────────────────────────

/** 小节标签：等宽大写、追踪字距、发光弱化——科技面板通用小标题。 */
export function SectionLabel({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        "text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground/80",
        className,
      )}
    >
      {children}
    </span>
  )
}

/**
 * 科技卡片：暗色下带主色内发光，亮色下细边框 + 轻阴影。
 * 用于右侧结果面板、最优解卡等结果类信息块。
 */
export function TechCard({
  children,
  className,
  glow = false,
}: {
  children: ReactNode
  className?: string
  glow?: boolean
}) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === "dark"
  return (
    <div
      className={cn(
        "relative rounded-lg border bg-card/50 backdrop-blur-sm",
        glow ? "border-primary/30" : "border-border/50",
        className,
      )}
      style={
        isDark
          ? {
              boxShadow: glow
                ? "inset 0 0 20px color-mix(in srgb, var(--primary) 6%, transparent), 0 0 16px color-mix(in srgb, var(--primary) 10%, transparent)"
                : "inset 0 0 15px color-mix(in srgb, var(--primary) 3%, transparent)",
            }
          : { boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }
      }
    >
      {children}
    </div>
  )
}

/** 会话状态胶囊（含 i18n 文案）。 */
export function StatusPill({
  status,
  className,
}: {
  status: string
  className?: string
}) {
  const { t } = useTranslation()
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider shrink-0",
        sessionStatusClasses(status),
        className,
      )}
    >
      {t(`autoResearch.status.${status}`, status)}
    </span>
  )
}

/**
 * 实时脉冲点（对齐演化页 ConnectionStatus）：一个实心点 + 背后 animate-ping 层。
 * color 传 hex（如 #10b981）。
 */
export function LiveDot({
  color,
  className,
}: {
  color: string
  className?: string
}) {
  return (
    <span className={cn("relative inline-flex size-2", className)}>
      <span
        className="absolute inset-0 rounded-full opacity-60 animate-ping"
        style={{ backgroundColor: color }}
      />
      <span
        className="relative inline-flex size-2 rounded-full"
        style={{ backgroundColor: color }}
      />
    </span>
  )
}

/** 全屏扫描线叠层（与演化页一致，暗色下极淡）。放在页面 shell 顶层 z-50。 */
export function ScanlineOverlay() {
  return (
    <div
      className="pointer-events-none fixed inset-0 z-50 opacity-0 dark:opacity-[0.03]"
      style={{
        backgroundImage:
          "repeating-linear-gradient(0deg, transparent, transparent 2px, color-mix(in srgb, var(--primary) 10%, transparent) 2px, color-mix(in srgb, var(--primary) 10%, transparent) 4px)",
      }}
    />
  )
}
