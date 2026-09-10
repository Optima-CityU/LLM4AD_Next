import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Cpu,
  FileText,
  Globe,
  Info,
  Link2,
  ListChecks,
  type LucideIcon,
  Search,
  Sparkles,
  XCircle,
} from "lucide-react"
import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

/**
 * 自定义产物渲染器共享视觉原语。
 *
 * 所有具名 JSON 产物（decision / hardware_profile / stage_health /
 * topic_evaluation / novelty_report / queries / search_meta / sources /
 * web_search_result）复用同一套原语（Hero / StatGrid / SectionCard / Pill /
 * QueryList 等）与同一套语义色调，确保跨文件排版风格统一协调。
 */

// ── 语义色调 ────────────────────────────────────────────────────────────────

export type Tone = "neutral" | "positive" | "warning" | "danger" | "info"

interface ToneStyle {
  text: string
  border: string
  bg: string
  bar: string
  softBg: string
}

const TONE_STYLES: Record<Tone, ToneStyle> = {
  neutral: {
    text: "text-foreground/80",
    border: "border-border/60",
    bg: "bg-muted/40",
    bar: "bg-muted-foreground/40",
    softBg: "bg-muted/20",
  },
  positive: {
    text: "text-emerald-600 dark:text-emerald-400",
    border: "border-emerald-500/20",
    bg: "bg-emerald-500/10",
    bar: "bg-emerald-500",
    softBg: "bg-emerald-500/[0.06]",
  },
  warning: {
    text: "text-amber-600 dark:text-amber-400",
    border: "border-amber-500/20",
    bg: "bg-amber-500/10",
    bar: "bg-amber-500",
    softBg: "bg-amber-500/[0.06]",
  },
  danger: {
    text: "text-destructive",
    border: "border-destructive/20",
    bg: "bg-destructive/10",
    bar: "bg-destructive",
    softBg: "bg-destructive/[0.06]",
  },
  info: {
    text: "text-primary",
    border: "border-primary/20",
    bg: "bg-primary/10",
    bar: "bg-primary",
    softBg: "bg-primary/[0.05]",
  },
}

export function toneOf(tone: Tone = "neutral"): ToneStyle {
  return TONE_STYLES[tone]
}

// ── 容器 ─────────────────────────────────────────────────────────────────────

/** 每个渲染器的统一外层：内边距 + 纵向节奏。 */
export function ArtifactCanvas({ children }: { children: ReactNode }) {
  return <div className="space-y-4 p-4">{children}</div>
}

// ── Hero 头部 ─────────────────────────────────────────────────────────────────

/**
 * 渲染器顶部的主视觉条：左侧图标 + 标题 / 副标题，右侧状态徽章。
 * 用一个带柔和渐变的卡片承托，替代原来平铺的字段网格，视觉上更有层次。
 */
export function Hero({
  icon: Icon,
  tone = "info",
  title,
  subtitle,
  badge,
}: {
  icon: LucideIcon
  tone?: Tone
  title: ReactNode
  subtitle?: ReactNode
  badge?: ReactNode
}) {
  const s = toneOf(tone)
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-xl border px-4 py-3.5",
        s.border,
        s.softBg,
      )}
    >
      <div
        className={cn(
          "flex size-10 shrink-0 items-center justify-center rounded-lg border",
          s.border,
          s.bg,
        )}
      >
        <Icon className={cn("size-5", s.text)} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-foreground">
          {title}
        </div>
        {subtitle != null && (
          <div className="mt-0.5 truncate text-xs text-muted-foreground">
            {subtitle}
          </div>
        )}
      </div>
      {badge != null && <div className="shrink-0">{badge}</div>}
    </div>
  )
}

// ── 状态徽章 / Pill ────────────────────────────────────────────────────────────

export function Pill({
  tone = "neutral",
  icon: Icon,
  children,
}: {
  tone?: Tone
  icon?: LucideIcon
  children: ReactNode
}) {
  const s = toneOf(tone)
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
        s.border,
        s.bg,
        s.text,
      )}
    >
      {Icon && <Icon className="size-3.5" />}
      {children}
    </span>
  )
}

// ── 统计瓦片 ─────────────────────────────────────────────────────────────────

export interface StatItem {
  label: ReactNode
  value: ReactNode
  /** 以 Pill 形式呈现值（用于状态 / 决策等枚举字段）。 */
  pill?: boolean
  tone?: Tone
  mono?: boolean
  /** 值跨整行显示（如长 ID、时间戳）。 */
  full?: boolean
}

function StatTile({ item }: { item: StatItem }) {
  const s = toneOf(item.tone ?? "neutral")
  return (
    <div
      className={cn(
        "rounded-lg border border-border/50 bg-background/70 px-3 py-2.5",
        item.full && "sm:col-span-2 xl:col-span-3",
      )}
    >
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
        {item.label}
      </div>
      {item.pill ? (
        <div className="mt-2">
          <span
            className={cn(
              "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium",
              s.border,
              s.bg,
              s.text,
            )}
          >
            {item.value}
          </span>
        </div>
      ) : (
        <div
          className={cn(
            "mt-1.5 break-words text-sm text-foreground",
            item.mono && "font-mono text-[12px] break-all",
            item.tone && item.tone !== "neutral" && s.text,
          )}
        >
          {item.value}
        </div>
      )}
    </div>
  )
}

/** 统计瓦片网格：响应式 1 / 2 / 3 列。 */
export function StatGrid({ items }: { items: StatItem[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((item, i) => (
        <StatTile key={i} item={item} />
      ))}
    </div>
  )
}

// ── 区块卡 ───────────────────────────────────────────────────────────────────

/** 带标题的内容区块，用于承托列表 / 长文本 / 标签组。 */
export function SectionCard({
  title,
  icon: Icon,
  tone = "neutral",
  count,
  children,
}: {
  title: ReactNode
  icon?: LucideIcon
  tone?: Tone
  count?: number
  children: ReactNode
}) {
  const s = toneOf(tone)
  return (
    <div
      className={cn(
        "rounded-xl border px-4 py-3.5",
        tone === "neutral"
          ? "border-border/50 bg-background/70"
          : cn(s.border, s.softBg),
      )}
    >
      <div className="mb-2.5 flex items-center gap-1.5">
        {Icon && (
          <Icon
            className={cn(
              "size-3.5",
              tone === "neutral" ? "text-muted-foreground/70" : s.text,
            )}
          />
        )}
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
          {title}
        </span>
        {count != null && (
          <span className="ml-0.5 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
            {count}
          </span>
        )}
      </div>
      {children}
    </div>
  )
}

/** 空态占位（区块内无数据时）。 */
export function EmptyHint({ children }: { children?: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className="rounded-lg border border-dashed border-border/50 px-3 py-4 text-center text-xs text-muted-foreground/70">
      {children ?? t("autoResearch.artifacts.noData")}
    </div>
  )
}

// ── 标签组 / 引用列表 ──────────────────────────────────────────────────────────

export function TagGroup({
  items,
  tone = "info",
  icon: Icon,
}: {
  items: string[]
  tone?: Tone
  icon?: LucideIcon
}) {
  const s = toneOf(tone)
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item, i) => (
        <span
          key={i}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-foreground/85",
            s.border,
            s.bg,
          )}
        >
          {Icon && <Icon className={cn("size-3", s.text)} />}
          {item}
        </span>
      ))}
    </div>
  )
}

/** 等宽引用行列表（如 evidence_refs、证据路径）。 */
export function RefList({ items }: { items: string[] }) {
  return (
    <div className="space-y-1.5">
      {items.map((item, i) => (
        <div
          key={i}
          className="break-all rounded-md bg-muted/40 px-2.5 py-1.5 font-mono text-[12px] text-foreground/85"
        >
          {item}
        </div>
      ))}
    </div>
  )
}

/** 编号查询列表：用于 queries / search_meta 的查询串展示。 */
export function QueryList({ items }: { items: string[] }) {
  return (
    <ol className="space-y-1.5">
      {items.map((q, i) => (
        <li
          key={i}
          className="flex items-start gap-2.5 rounded-lg border border-border/40 bg-background/60 px-3 py-2"
        >
          <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 text-[11px] font-semibold tabular-nums text-primary">
            {i + 1}
          </span>
          <span className="min-w-0 break-words text-sm text-foreground/90">
            {q}
          </span>
        </li>
      ))}
    </ol>
  )
}

// ── 长文本块 ─────────────────────────────────────────────────────────────────

/** 带语义色的多行文本块（建议 / 提醒 / 错误信息）。 */
export function ProseBlock({
  title,
  icon: Icon,
  tone = "neutral",
  text,
  emptyText,
}: {
  title: ReactNode
  icon?: LucideIcon
  tone?: Tone
  text?: string | null
  emptyText?: string
}) {
  const s = toneOf(tone)
  const empty = !text
  return (
    <div
      className={cn(
        "rounded-xl border px-4 py-3.5",
        tone === "neutral"
          ? "border-border/50 bg-background/70"
          : cn(s.border, s.softBg),
      )}
    >
      <div className="mb-2 flex items-center gap-1.5">
        {Icon && (
          <Icon
            className={cn(
              "size-3.5",
              tone === "neutral" ? "text-muted-foreground/70" : s.text,
            )}
          />
        )}
        <span
          className={cn(
            "text-[11px] font-medium uppercase tracking-wider",
            tone === "neutral" ? "text-muted-foreground/70" : s.text,
          )}
        >
          {title}
        </span>
      </div>
      <div
        className={cn(
          "whitespace-pre-wrap break-words text-sm leading-relaxed",
          empty ? "text-muted-foreground" : "text-foreground/85",
        )}
      >
        {text || emptyText || "—"}
      </div>
    </div>
  )
}

// ── 评分卡（保留 topic_evaluation 用） ──────────────────────────────────────────

function scoreTone(score: number | null | undefined, max: number): Tone {
  if (score == null) return "neutral"
  const ratio = score / max
  if (ratio >= 0.7) return "positive"
  if (ratio >= 0.5) return "warning"
  return "danger"
}

export function ScoreCard({
  label,
  score,
  max = 10,
  highlight,
}: {
  label: ReactNode
  score: number | null | undefined
  max?: number
  highlight?: boolean
}) {
  const tone = scoreTone(score, max)
  const s = toneOf(tone)
  const pct =
    score == null ? 0 : Math.max(0, Math.min(100, (score / max) * 100))
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2.5",
        highlight ? cn(s.border, s.bg) : "border-border/50 bg-background/70",
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
          {label}
        </span>
        <span className={cn("text-sm font-semibold tabular-nums", s.text)}>
          {score == null ? "—" : score}
          <span className="ml-0.5 text-[11px] font-normal text-muted-foreground/60">
            /{max}
          </span>
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
        <div
          className={cn("h-full rounded-full transition-all", s.bar)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── 共享工具 ─────────────────────────────────────────────────────────────────

/** 依据常见状态词返回语义色调。 */
export function statusTone(status: string | null | undefined): Tone {
  const s = (status ?? "").toLowerCase()
  if (
    s === "done" ||
    s === "success" ||
    s === "ok" ||
    s === "available" ||
    s === "proceed"
  )
    return "positive"
  if (s === "failed" || s === "error" || s === "unavailable") return "danger"
  if (s === "warning" || s === "partial") return "warning"
  return "neutral"
}

/** 安全解析 JSON，失败返回 null。 */
export function safeParseJson(content: string): unknown {
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

// ── decision.json ────────────────────────────────────────────────────────────

interface DecisionData {
  stage_id?: string | null
  run_id?: string | null
  status?: string | null
  decision?: string | null
  output_artifacts?: string[] | null
  evidence_refs?: string[] | null
  error?: string | null
  ts?: string | null
  next_stage?: number | null
}

function DecisionView({ data }: { data: DecisionData }) {
  const { t } = useTranslation()
  const f = (k: string) => t(`autoResearch.artifacts.decisionFields.${k}`)
  const decisionTone = statusTone(data.decision)

  const stats: StatItem[] = [
    {
      label: f("status"),
      value: data.status || "—",
      pill: true,
      tone: statusTone(data.status),
    },
    {
      label: f("nextStage"),
      value: data.next_stage == null ? "—" : `#${data.next_stage}`,
    },
    { label: f("timestamp"), value: data.ts || "—", mono: true },
    { label: f("runId"), value: data.run_id || "—", mono: true, full: true },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={CheckCircle2}
        tone={decisionTone}
        title={data.stage_id || f("stageId")}
        subtitle={f("heroSubtitle")}
        badge={
          <Pill
            tone={decisionTone}
            icon={decisionTone === "positive" ? CheckCircle2 : XCircle}
          >
            {data.decision || "—"}
          </Pill>
        }
      />

      <StatGrid items={stats} />

      {data.error ? (
        <ProseBlock
          title={f("error")}
          icon={AlertTriangle}
          tone="danger"
          text={data.error}
        />
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <SectionCard
          title={f("outputArtifacts")}
          icon={FileText}
          count={data.output_artifacts?.length}
        >
          {data.output_artifacts?.length ? (
            <TagGroup
              items={data.output_artifacts}
              tone="info"
              icon={FileText}
            />
          ) : (
            <EmptyHint />
          )}
        </SectionCard>

        <SectionCard
          title={f("evidenceRefs")}
          icon={Link2}
          count={data.evidence_refs?.length}
        >
          {data.evidence_refs?.length ? (
            <RefList items={data.evidence_refs} />
          ) : (
            <EmptyHint />
          )}
        </SectionCard>
      </div>
    </ArtifactCanvas>
  )
}

// ── hardware_profile.json ─────────────────────────────────────────────────────

interface HardwareData {
  has_gpu?: boolean | null
  gpu_type?: string | null
  gpu_name?: string | null
  vram_mb?: number | null
  tier?: string | null
  warning?: string | null
}

function HardwareView({ data }: { data: HardwareData }) {
  const { t } = useTranslation()
  const f = (k: string) => t(`autoResearch.artifacts.hardwareFields.${k}`)
  const gpuTone: Tone = data.has_gpu ? "positive" : "warning"

  const stats: StatItem[] = [
    {
      label: f("hasGpu"),
      value:
        data.has_gpu == null
          ? "—"
          : data.has_gpu
            ? t("common.yes")
            : t("common.no"),
      pill: true,
      tone: gpuTone,
    },
    {
      label: f("gpuType"),
      value: data.gpu_type || "—",
      pill: true,
      tone: "info",
    },
    {
      label: f("tier"),
      value: data.tier || "—",
      pill: true,
      tone: data.tier === "cpu_only" ? "warning" : "neutral",
    },
    { label: f("gpuName"), value: data.gpu_name || "—" },
    {
      label: f("vram"),
      value: data.vram_mb == null ? "—" : `${data.vram_mb} MB`,
      mono: true,
    },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={Cpu}
        tone={gpuTone}
        title={data.gpu_name || data.gpu_type || f("heroTitle")}
        subtitle={data.tier || undefined}
        badge={
          <Pill tone={gpuTone}>
            {data.has_gpu ? t("common.yes") : t("common.no")}
          </Pill>
        }
      />
      <StatGrid items={stats} />
      {data.warning ? (
        <ProseBlock
          title={f("warning")}
          icon={AlertTriangle}
          tone="warning"
          text={data.warning}
        />
      ) : null}
    </ArtifactCanvas>
  )
}

// ── stage_health.json ─────────────────────────────────────────────────────────

interface StageHealthData {
  stage_id?: string | null
  run_id?: string | null
  duration_sec?: number | null
  status?: string | null
  artifacts_count?: number | null
  error?: string | null
  timestamp?: string | null
}

function StageHealthView({ data }: { data: StageHealthData }) {
  const { t } = useTranslation()
  const f = (k: string) => t(`autoResearch.artifacts.stageHealthFields.${k}`)
  const tone = statusTone(data.status)

  const stats: StatItem[] = [
    { label: f("status"), value: data.status || "—", pill: true, tone },
    {
      label: f("duration"),
      value:
        data.duration_sec == null
          ? "—"
          : t("autoResearch.artifacts.stageHealthFields.durationValue", {
              seconds: data.duration_sec,
            }),
      mono: true,
    },
    {
      label: f("artifactsCount"),
      value: data.artifacts_count == null ? "—" : String(data.artifacts_count),
      mono: true,
    },
    { label: f("timestamp"), value: data.timestamp || "—", mono: true },
    { label: f("runId"), value: data.run_id || "—", mono: true, full: true },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={data.error ? AlertTriangle : CheckCircle2}
        tone={data.error ? "danger" : tone}
        title={data.stage_id || f("heroTitle")}
        subtitle={f("heroSubtitle")}
        badge={
          <Pill tone={data.error ? "danger" : tone}>{data.status || "—"}</Pill>
        }
      />
      <StatGrid items={stats} />
      <ProseBlock
        title={f("error")}
        icon={data.error ? AlertTriangle : CheckCircle2}
        tone={data.error ? "danger" : "positive"}
        text={data.error}
        emptyText={f("noError")}
      />
    </ArtifactCanvas>
  )
}

// ── topic_evaluation.json ─────────────────────────────────────────────────────

interface TopicEvalData {
  novelty?: number | null
  specificity?: number | null
  feasibility?: number | null
  overall?: number | null
  suggestion?: string | null
}

function TopicEvalView({ data }: { data: TopicEvalData }) {
  const { t } = useTranslation()
  const f = (k: string) => t(`autoResearch.artifacts.topicEvalFields.${k}`)

  return (
    <ArtifactCanvas>
      <ScoreCard label={f("overall")} score={data.overall} highlight />
      <div className="grid gap-3 md:grid-cols-3">
        <ScoreCard label={f("novelty")} score={data.novelty} />
        <ScoreCard label={f("specificity")} score={data.specificity} />
        <ScoreCard label={f("feasibility")} score={data.feasibility} />
      </div>
      <ProseBlock
        title={f("suggestion")}
        icon={Sparkles}
        tone="info"
        text={data.suggestion}
      />
    </ArtifactCanvas>
  )
}

// ── novelty_report.json ───────────────────────────────────────────────────────

interface SimilarPaper {
  title?: string | null
  score?: number | null
  url?: string | null
}

interface NoveltyData {
  topic?: string | null
  hypotheses_checked?: number | null
  search_queries?: string[] | null
  similar_papers_found?: number | null
  novelty_score?: number | null
  assessment?: string | null
  similar_papers?: SimilarPaper[] | null
  recommendation?: string | null
  similarity_threshold?: number | null
  search_coverage?: string | null
  total_papers_retrieved?: number | null
  generated?: string | null
}

function assessmentTone(a: string | null | undefined): Tone {
  const s = (a ?? "").toLowerCase()
  if (s === "high") return "positive"
  if (s === "medium" || s === "moderate") return "warning"
  if (s === "low") return "danger"
  return "neutral"
}

function NoveltyView({ data }: { data: NoveltyData }) {
  const { t } = useTranslation()
  const f = (k: string) => t(`autoResearch.artifacts.noveltyFields.${k}`)
  const tone = assessmentTone(data.assessment)
  // novelty_score 归一到 0-1，评分卡按满分 1 呈现。
  const scoreMax = 1

  const stats: StatItem[] = [
    {
      label: f("recommendation"),
      value: data.recommendation || "—",
      pill: true,
      tone: statusTone(data.recommendation),
    },
    {
      label: f("similarPapersFound"),
      value:
        data.similar_papers_found == null
          ? "—"
          : String(data.similar_papers_found),
      mono: true,
      tone: data.similar_papers_found ? "warning" : "positive",
    },
    {
      label: f("hypothesesChecked"),
      value:
        data.hypotheses_checked == null ? "—" : String(data.hypotheses_checked),
      mono: true,
    },
    {
      label: f("totalPapers"),
      value:
        data.total_papers_retrieved == null
          ? "—"
          : String(data.total_papers_retrieved),
      mono: true,
    },
    {
      label: f("searchCoverage"),
      value: data.search_coverage || "—",
      pill: true,
      tone: "info",
    },
    {
      label: f("similarityThreshold"),
      value:
        data.similarity_threshold == null
          ? "—"
          : String(data.similarity_threshold),
      mono: true,
    },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={Sparkles}
        tone={tone}
        title={data.topic || f("heroTitle")}
        subtitle={data.generated || undefined}
        badge={
          <Pill tone={tone} icon={Sparkles}>
            {(data.assessment || "—").toUpperCase()}
          </Pill>
        }
      />

      <ScoreCard
        label={f("noveltyScore")}
        score={data.novelty_score}
        max={scoreMax}
        highlight
      />

      <StatGrid items={stats} />

      {data.search_queries?.length ? (
        <SectionCard
          title={f("searchQueries")}
          icon={Search}
          count={data.search_queries.length}
        >
          <QueryList items={data.search_queries} />
        </SectionCard>
      ) : null}

      <SectionCard
        title={f("similarPapers")}
        icon={FileText}
        count={data.similar_papers?.length ?? 0}
      >
        {data.similar_papers?.length ? (
          <div className="space-y-1.5">
            {data.similar_papers.map((p, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-3 rounded-lg border border-border/40 bg-background/60 px-3 py-2"
              >
                <span className="min-w-0 break-words text-sm text-foreground/90">
                  {p.title || "—"}
                </span>
                {p.score != null && (
                  <span className="shrink-0 font-mono text-[12px] text-muted-foreground tabular-nums">
                    {p.score.toFixed(2)}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <EmptyHint>{f("noSimilarPapers")}</EmptyHint>
        )}
      </SectionCard>
    </ArtifactCanvas>
  )
}

// ── queries.json ──────────────────────────────────────────────────────────────

interface QueriesData {
  queries?: string[] | null
  year_min?: number | null
}

function QueriesView({ data }: { data: QueriesData }) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.queriesFields.${k}`, opts ?? {})

  return (
    <ArtifactCanvas>
      <Hero
        icon={Search}
        tone="info"
        title={f("heroTitle")}
        subtitle={f("heroSubtitle", { count: data.queries?.length ?? 0 })}
        badge={
          data.year_min != null ? (
            <Pill tone="neutral">{f("yearMin", { year: data.year_min })}</Pill>
          ) : undefined
        }
      />
      <SectionCard
        title={f("queries")}
        icon={ListChecks}
        count={data.queries?.length ?? 0}
      >
        {data.queries?.length ? (
          <QueryList items={data.queries} />
        ) : (
          <EmptyHint />
        )}
      </SectionCard>
    </ArtifactCanvas>
  )
}

// ── search_meta.json ──────────────────────────────────────────────────────────

interface SearchMetaData {
  real_search?: boolean | null
  queries_used?: string[] | null
  year_min?: number | null
  total_candidates?: number | null
  bibtex_entries?: number | null
  ts?: string | null
}

function SearchMetaView({ data }: { data: SearchMetaData }) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.searchMetaFields.${k}`, opts ?? {})
  const realTone: Tone = data.real_search ? "positive" : "warning"

  const stats: StatItem[] = [
    {
      label: f("realSearch"),
      value: data.real_search ? t("common.yes") : t("common.no"),
      pill: true,
      tone: realTone,
    },
    {
      label: f("totalCandidates"),
      value:
        data.total_candidates == null ? "—" : String(data.total_candidates),
      mono: true,
      tone: "info",
    },
    {
      label: f("bibtexEntries"),
      value: data.bibtex_entries == null ? "—" : String(data.bibtex_entries),
      mono: true,
    },
    {
      label: f("yearMin"),
      value: data.year_min == null ? "—" : String(data.year_min),
      mono: true,
    },
    { label: f("timestamp"), value: data.ts || "—", mono: true, full: true },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={Search}
        tone={realTone}
        title={f("heroTitle")}
        subtitle={f("heroSubtitle", { count: data.total_candidates ?? 0 })}
        badge={
          <Pill tone={realTone}>
            {data.real_search ? f("real") : f("mock")}
          </Pill>
        }
      />
      <StatGrid items={stats} />
      {data.queries_used?.length ? (
        <SectionCard
          title={f("queriesUsed")}
          icon={Search}
          count={data.queries_used.length}
        >
          <QueryList items={data.queries_used} />
        </SectionCard>
      ) : null}
    </ArtifactCanvas>
  )
}

// ── sources.json ──────────────────────────────────────────────────────────────

interface SourceEntry {
  id?: string | null
  name?: string | null
  type?: string | null
  url?: string | null
  status?: string | null
  query?: string | null
  verified_at?: string | null
}

interface SourcesData {
  sources?: SourceEntry[] | null
  count?: number | null
  generated?: string | null
}

function SourcesView({ data }: { data: SourcesData }) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.sourcesFields.${k}`, opts ?? {})
  const sources = data.sources ?? []
  const availableCount = sources.filter(
    (s) => (s.status ?? "").toLowerCase() === "available",
  ).length

  return (
    <ArtifactCanvas>
      <Hero
        icon={Globe}
        tone={availableCount > 0 ? "positive" : "warning"}
        title={f("heroTitle")}
        subtitle={data.generated || undefined}
        badge={
          <Pill tone={availableCount > 0 ? "positive" : "warning"}>
            {f("availableCount", {
              available: availableCount,
              total: sources.length,
            })}
          </Pill>
        }
      />

      {sources.length ? (
        <div className="space-y-2.5">
          {sources.map((s, i) => {
            const tone = statusTone(s.status)
            return (
              <div
                key={i}
                className="rounded-xl border border-border/50 bg-background/70 px-4 py-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <Globe
                      className={cn("size-4 shrink-0", toneOf(tone).text)}
                    />
                    <span className="truncate text-sm font-semibold text-foreground">
                      {s.name || s.id || "—"}
                    </span>
                    {s.type && (
                      <span className="shrink-0 rounded bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        {s.type}
                      </span>
                    )}
                  </div>
                  <Pill
                    tone={tone}
                    icon={
                      tone === "positive"
                        ? CheckCircle2
                        : tone === "danger"
                          ? XCircle
                          : Circle
                    }
                  >
                    {s.status || "—"}
                  </Pill>
                </div>
                {s.url && (
                  <div className="mt-2 break-all rounded-md bg-muted/40 px-2.5 py-1.5 font-mono text-[11px] text-muted-foreground">
                    {s.url}
                  </div>
                )}
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                  {s.query && (
                    <span>
                      <span className="text-muted-foreground/60">
                        {f("query")}:{" "}
                      </span>
                      {s.query}
                    </span>
                  )}
                  {s.verified_at && (
                    <span className="tabular-nums">
                      <span className="text-muted-foreground/60">
                        {f("verifiedAt")}:{" "}
                      </span>
                      {s.verified_at}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <EmptyHint />
      )}
    </ArtifactCanvas>
  )
}

// ── web_search_result.json ────────────────────────────────────────────────────

interface WebSearchData {
  topic?: string | null
  web_results_count?: number | null
  scholar_papers_count?: number | null
  crawled_pages_count?: number | null
  pdf_extractions_count?: number | null
  has_search_answer?: boolean | null
  elapsed_seconds?: number | null
  web_results?: unknown[] | null
  scholar_papers?: unknown[] | null
}

function WebSearchView({ data }: { data: WebSearchData }) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.webSearchFields.${k}`, opts ?? {})
  const totalHits =
    (data.web_results_count ?? 0) +
    (data.scholar_papers_count ?? 0) +
    (data.crawled_pages_count ?? 0) +
    (data.pdf_extractions_count ?? 0)
  const tone: Tone = totalHits > 0 ? "positive" : "warning"

  const stats: StatItem[] = [
    {
      label: f("webResults"),
      value:
        data.web_results_count == null ? "—" : String(data.web_results_count),
      mono: true,
      tone: data.web_results_count ? "info" : "neutral",
    },
    {
      label: f("scholarPapers"),
      value:
        data.scholar_papers_count == null
          ? "—"
          : String(data.scholar_papers_count),
      mono: true,
      tone: data.scholar_papers_count ? "info" : "neutral",
    },
    {
      label: f("crawledPages"),
      value:
        data.crawled_pages_count == null
          ? "—"
          : String(data.crawled_pages_count),
      mono: true,
    },
    {
      label: f("pdfExtractions"),
      value:
        data.pdf_extractions_count == null
          ? "—"
          : String(data.pdf_extractions_count),
      mono: true,
    },
    {
      label: f("hasAnswer"),
      value: data.has_search_answer ? t("common.yes") : t("common.no"),
      pill: true,
      tone: data.has_search_answer ? "positive" : "neutral",
    },
    {
      label: f("elapsed"),
      value:
        data.elapsed_seconds == null
          ? "—"
          : f("elapsedValue", { seconds: data.elapsed_seconds.toFixed(2) }),
      mono: true,
    },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={Globe}
        tone={tone}
        title={data.topic || f("heroTitle")}
        subtitle={f("heroSubtitle", { count: totalHits })}
        badge={
          <Pill tone={tone} icon={totalHits > 0 ? CheckCircle2 : Info}>
            {f("totalHits", { count: totalHits })}
          </Pill>
        }
      />
      <StatGrid items={stats} />
      {totalHits === 0 ? (
        <ProseBlock
          title={f("noteTitle")}
          icon={Info}
          tone="warning"
          text={f("emptyNote")}
        />
      ) : null}
    </ArtifactCanvas>
  )
}

// ── llm4ad_comparison.json ────────────────────────────────────────────────────

/** 单个算法在本次对比中的结果。 */
interface ComparisonAlgorithm {
  baseline?: number | null
  evolved?: number | null
  /** 相对基线的变化百分比（如 0.3252 表示 +0.33%）。 */
  delta_pct?: number | null
  promoted?: boolean | null
  failed?: boolean | null
  n_instances_compared?: number | null
  n_instances_total?: number | null
  lost_instances?: number | null
  reason?: string | null
}

interface ComparisonData {
  generated?: string | null
  /** 指标方向：maximize / minimize。 */
  metric_direction?: string | null
  base?: string | null
  n_promoted?: number | null
  algorithms?: Record<string, ComparisonAlgorithm> | null
}

/** 数值格式化：比较结果通常带 6 位小数，太短会丢掉有效差异。 */
function formatCompareValue(
  value: number | null | undefined,
  digits = 6,
): string {
  if (value == null || !Number.isFinite(value)) return "—"
  return value.toFixed(digits)
}

/** 变化百分比带符号展示，正数补 “+”。 */
function formatDeltaPct(pct: number): string {
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`
}

/** 坐标轴刻度用的短格式：固定小数位后去掉多余的尾零。 */
function formatTick(value: number): string {
  return value.toFixed(4).replace(/\.?0+$/, "")
}

/** 单个算法的对比卡片：算法名 + 状态徽章，下面 baseline / evolved 双条，右侧变化幅度。 */
function AlgorithmCompareRow({
  name,
  item,
  domain,
  maxAbsDelta,
}: {
  name: string
  item: ComparisonAlgorithm
  /** baseline / evolved 两条共用的数值域（两个算法的量纲一致，共用才能直接比长度）。 */
  domain: { min: number; max: number }
  maxAbsDelta: number
}) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.comparisonFields.${k}`, opts ?? {})
  const failed = item.failed === true
  const promoted = item.promoted === true
  const delta = item.delta_pct ?? null

  // 状态优先级：失败 > 提升 > 未提升。
  const tone: Tone = failed ? "danger" : promoted ? "positive" : "neutral"
  const s = toneOf(tone)
  const deltaTone: Tone = failed
    ? "danger"
    : delta == null
      ? "neutral"
      : delta > 0
        ? "positive"
        : delta < 0
          ? "warning"
          : "neutral"

  // 基线 / 进化后两根条的长度，映射到共享数值域；域为 0 宽时退化为全长。
  const span = Math.max(domain.max - domain.min, Number.EPSILON)
  const pctOf = (v: number | null | undefined) =>
    v == null || !Number.isFinite(v)
      ? 0
      : ((v - domain.min) / span) * 100
  const baselinePct = pctOf(item.baseline)
  const evolvedPct = pctOf(item.evolved)

  // 变化幅度条：以中点为 0，向右为正、向左为负，长度对全表最大绝对变化归一。
  const deltaRatio =
    delta == null || maxAbsDelta <= 0
      ? 0
      : Math.max(-1, Math.min(1, delta / maxAbsDelta))
  const deltaWidth = Math.abs(deltaRatio) * 50

  const lost = item.lost_instances ?? 0
  const compared = item.n_instances_compared ?? null
  const total = item.n_instances_total ?? null

  return (
    <div
      className={cn(
        "rounded-xl border px-3.5 py-3",
        tone === "neutral"
          ? "border-border/50 bg-background/70"
          : cn(s.border, s.softBg),
      )}
    >
      {/* 标题行：算法名 + 状态徽章；右侧是变化幅度条与数值 */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-[12px] font-semibold text-foreground">
            {name}
          </span>
          <Pill
            tone={tone}
            icon={failed ? XCircle : promoted ? CheckCircle2 : Circle}
          >
            {failed
              ? f("failed")
              : promoted
                ? f("promoted")
                : f("notPromoted")}
          </Pill>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="relative hidden h-1.5 w-20 overflow-hidden rounded-full bg-muted/50 sm:block">
            <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
            {deltaRatio !== 0 && (
              <div
                className={cn(
                  "absolute top-0 h-full rounded-full",
                  deltaRatio > 0
                    ? "left-1/2 bg-emerald-500/80"
                    : "right-1/2 bg-amber-500/80",
                )}
                style={{ width: `${deltaWidth}%` }}
              />
            )}
          </div>
          <span
            className={cn(
              "text-[13px] font-semibold tabular-nums",
              toneOf(deltaTone).text,
            )}
          >
            {delta == null ? "—" : formatDeltaPct(delta)}
          </span>
        </div>
      </div>

      {/* 双条对比：同一数值域下直接比长度，数值另附在右侧 */}
      <div className="mt-3 space-y-2">
        <div className="flex items-center gap-2.5">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground/60">
            {f("baseline")}
          </span>
          <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/50">
            <div
              className="h-full rounded-full bg-muted-foreground/50"
              style={{ width: `${baselinePct}%` }}
            />
          </div>
          <span className="w-20 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
            {formatCompareValue(item.baseline)}
          </span>
        </div>
        <div className="flex items-center gap-2.5">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground/60">
            {f("evolved")}
          </span>
          <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/50">
            <div
              className={cn("h-full rounded-full", s.bar)}
              style={{ width: `${evolvedPct}%` }}
            />
          </div>
          <span
            className={cn(
              "w-20 shrink-0 text-right font-mono text-[11px] font-semibold tabular-nums",
              tone === "neutral" ? "text-foreground" : s.text,
            )}
          >
            {formatCompareValue(item.evolved)}
          </span>
        </div>
      </div>

      {/* 脚注：判定理由 + 实例数（对比数 / 总数，丢失实例单独标红） */}
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {item.reason && (
          <span className="min-w-0 truncate">
            <span className="text-muted-foreground/60">{f("reason")}: </span>
            {item.reason}
          </span>
        )}
        {compared != null && (
          <span className="tabular-nums">
            <span className="text-muted-foreground/60">{f("instances")}: </span>
            {f("instanceValue", {
              compared,
              total: total ?? compared,
            })}
          </span>
        )}
        {lost > 0 && (
          <span className="font-medium tabular-nums text-destructive">
            {f("lostInstances", { count: lost })}
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * 全局哑铃图：所有算法共用一根横轴，每行一个算法，灰点为基线、彩点为进化后，
 * 中间连线表示移动方向。
 *
 * 与逐卡对比的差别在于“跨算法可比”：卡片只看得到自己那套刻度，某个算法
 * 即便相对提升很大，绝对水平仍可能低于另一个算法的基线——只有共用横轴时
 * 这层位置关系才显形。
 */
function ComparisonDumbbell({
  rows,
  domain,
}: {
  rows: [string, ComparisonAlgorithm][]
  domain: { min: number; max: number }
}) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.comparisonFields.${k}`, opts ?? {})
  const span = Math.max(domain.max - domain.min, Number.EPSILON)
  const xOf = (v: number) => ((v - domain.min) / span) * 100

  // 端点样式显式写出：SVG 的 fill-* / stroke-* 无法由 TONE_STYLES.bar（bg-*）
  // 推导，这里与语义色调保持一致即可。
  const dotClass = (failed: boolean, promoted: boolean) =>
    failed
      ? "bg-destructive border-destructive"
      : promoted
        ? "bg-emerald-500 border-emerald-600"
        : "bg-muted-foreground/50 border-muted-foreground"

  // 连线颜色按移动方向：向右为涨（绿）、向左为跌（琥珀）。
  const linkClass = (item: ComparisonAlgorithm) => {
    const { baseline, evolved, failed } = item
    if (failed || baseline == null || evolved == null) return "bg-muted-foreground/30"
    if (evolved > baseline) return "bg-emerald-500/60"
    if (evolved < baseline) return "bg-amber-500/70"
    return "bg-muted-foreground/30"
  }

  return (
    <div className="space-y-1">
      {rows.map(([name, item]) => {
        const baseline =
          item.baseline != null && Number.isFinite(item.baseline)
            ? item.baseline
            : null
        const evolved =
          item.evolved != null && Number.isFinite(item.evolved)
            ? item.evolved
            : null
        const x1 = baseline == null ? null : xOf(baseline)
        const x2 = evolved == null ? null : xOf(evolved)
        const dot = dotClass(item.failed === true, item.promoted === true)

        return (
          <div key={name} className="flex items-center gap-3">
            <span className="w-28 shrink-0 truncate text-right font-mono text-[11px] text-foreground/85 sm:w-40">
              {name}
            </span>
            {/* 绘图区：纯 CSS 定位，随容器宽度自适应且点 / 线不被拉伸变形 */}
            <div className="relative h-7 min-w-0 flex-1">
              <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border/40" />
              {x1 != null && x2 != null && x1 !== x2 && (
                <div
                  className={cn(
                    "absolute top-1/2 h-[2px] -translate-y-1/2 rounded-full",
                    linkClass(item),
                  )}
                  style={{
                    left: `${Math.min(x1, x2)}%`,
                    width: `${Math.abs(x2 - x1)}%`,
                  }}
                />
              )}
              {x1 != null && (
                <div
                  className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border bg-muted-foreground/40 border-muted-foreground/60"
                  style={{ left: `${x1}%` }}
                  title={`${f("baseline")} ${formatCompareValue(item.baseline)}`}
                />
              )}
              {x2 != null && (
                <div
                  className={cn(
                    "absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2",
                    dot,
                  )}
                  style={{ left: `${x2}%` }}
                  title={`${f("evolved")} ${formatCompareValue(item.evolved)}`}
                />
              )}
            </div>
            <span className="w-20 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
              {formatCompareValue(item.evolved)}
            </span>
          </div>
        )
      })}

      {/* 横轴刻度：起止值贴边对齐，避免溢出绘图区 */}
      <div className="flex items-center gap-3 pt-0.5">
        <span className="w-28 shrink-0 sm:w-40" />
        <div className="relative h-3.5 min-w-0 flex-1 text-[10px] tabular-nums text-muted-foreground/60">
          <span className="absolute left-0">{formatTick(domain.min)}</span>
          <span className="absolute left-1/2 -translate-x-1/2">
            {formatTick((domain.min + domain.max) / 2)}
          </span>
          <span className="absolute right-0">{formatTick(domain.max)}</span>
        </div>
        <span className="w-20 shrink-0" />
      </div>
    </div>
  )
}

/**
 * 实例覆盖条（按需出现）：只有存在丢失或未比满的实例时才渲染，否则每行都是
 * 一条满格条，纯噪声。分段含义见图例：已比 / 丢失 / 未比。
 */
function InstanceCoverage({ rows }: { rows: [string, ComparisonAlgorithm][] }) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.comparisonFields.${k}`, opts ?? {})
  const seg = (
    n: number,
    total: number,
    cls: string,
    label: string,
  ): ReactNode =>
    n > 0 ? (
      <div
        className={cn("h-full", cls)}
        style={{ width: `${(n / total) * 100}%` }}
        title={`${label} ${n}`}
      />
    ) : null

  return (
    <div className="space-y-2.5">
      {rows.map(([name, item]) => {
        const total = item.n_instances_total ?? 0
        if (total <= 0) return null
        const compared = item.n_instances_compared ?? 0
        const lost = item.lost_instances ?? 0
        const skipped = Math.max(0, total - compared - lost)
        return (
          <div key={name} className="flex items-center gap-2.5">
            <span className="w-32 shrink-0 truncate font-mono text-[11px] text-foreground/85">
              {name}
            </span>
            <div className="flex h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/50">
              {seg(compared - lost, total, "bg-emerald-500/70", f("compared"))}
              {seg(lost, total, "bg-destructive/80", f("lost"))}
              {seg(skipped, total, "bg-amber-500/60", f("skipped"))}
            </div>
            <span className="w-16 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
              {f("instanceValue", { compared, total })}
            </span>
          </div>
        )
      })}
      <div className="flex flex-wrap gap-x-4 gap-y-1 pt-0.5 text-[10px] text-muted-foreground/60">
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-emerald-500/70" />
          {f("compared")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-destructive/80" />
          {f("lost")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-amber-500/60" />
          {f("skipped")}
        </span>
      </div>
    </div>
  )
}

/**
 * llm4ad_comparison.json（算法晋级对比）专属视图。
 *
 * 该产物汇总各算法“进化前 vs 进化后”的分数与晋级判定，故可视化以
 * 成对条形对比为主体：所有算法共用同一数值域，条长可直接横向比较；
 * 卡片右上是相对基线的变化幅度条（中点为零，右正左负），颜色随方向
 * 与晋级状态变化。列表按变化百分比降序，最优算法排在最前。
 * 其下补一张共用横轴的哑铃图给出跨算法的绝对水平位置；若本表存在
 * 丢失 / 未比满的实例，再追加一张实例覆盖条。
 */
function ComparisonView({ data }: { data: ComparisonData }) {
  const { t } = useTranslation()
  const f = (k: string, opts?: Record<string, unknown>) =>
    t(`autoResearch.artifacts.comparisonFields.${k}`, opts ?? {})

  const entries = Object.entries(data.algorithms ?? {})
  const direction = (data.metric_direction ?? "").toLowerCase()

  const promotedCount =
    data.n_promoted ?? entries.filter(([, a]) => a.promoted === true).length
  const failedCount = entries.filter(([, a]) => a.failed === true).length

  // 分数域：取本表出现的最小 / 最大分数（含两端留白），供所有算法共用。
  const scores = entries
    .flatMap(([, a]) => [a.baseline, a.evolved])
    .filter((v): v is number => v != null && Number.isFinite(v))
  const rawMin = scores.length ? Math.min(...scores) : 0
  const rawMax = scores.length ? Math.max(...scores) : 1
  const pad = Math.max((rawMax - rawMin) * 0.6, Math.abs(rawMax) * 0.002, 1e-9)
  const domain = { min: rawMin - pad, max: rawMax + pad }

  // 变化幅度条归一基准。
  const maxAbsDelta = entries.reduce(
    (m, [, a]) =>
      a.delta_pct != null && Number.isFinite(a.delta_pct)
        ? Math.max(m, Math.abs(a.delta_pct))
        : m,
    0,
  )
  const sorted = [...entries].sort(
    (a, b) => (b[1].delta_pct ?? -Infinity) - (a[1].delta_pct ?? -Infinity),
  )

  // 实例覆盖条只在“跑得不完整”时才有信息量：全是 compared == total 且无丢失时
  // 每行都是满格条，属于噪声，直接不渲染。
  const hasCoverageGap = entries.some(([, a]) => {
    const total = a.n_instances_total ?? 0
    const compared = a.n_instances_compared ?? 0
    return (a.lost_instances ?? 0) > 0 || (total > 0 && compared < total)
  })

  const tone: Tone =
    failedCount > 0
      ? "warning"
      : promotedCount > 0
        ? "positive"
        : entries.length
          ? "neutral"
          : "warning"

  const stats: StatItem[] = [
    {
      label: f("metricDirection"),
      value: data.metric_direction || "—",
      pill: true,
      tone: direction === "minimize" ? "warning" : "info",
    },
    {
      label: f("nPromoted"),
      value: f("promotedValue", { n: promotedCount, total: entries.length }),
      tone: promotedCount > 0 ? "positive" : "neutral",
    },
    {
      label: f("algorithmsCompared"),
      value: String(entries.length),
      mono: true,
    },
    {
      label: f("failedCount"),
      value: String(failedCount),
      tone: failedCount > 0 ? "danger" : "neutral",
    },
    {
      label: f("generated"),
      value: data.generated || "—",
      mono: true,
    },
    { label: f("base"), value: data.base || "—", mono: true, full: true },
  ]

  return (
    <ArtifactCanvas>
      <Hero
        icon={Sparkles}
        tone={tone}
        title={f("heroTitle")}
        subtitle={data.generated || undefined}
        badge={
          <Pill tone={tone} icon={Sparkles}>
            {f("promotedValue", { n: promotedCount, total: entries.length })}
          </Pill>
        }
      />

      <StatGrid items={stats} />

      {entries.length ? (
        <SectionCard
          title={f("comparison")}
          icon={ListChecks}
          tone="info"
          count={entries.length}
        >
          <div className="space-y-2.5">
            {sorted.map(([name, item]) => (
              <AlgorithmCompareRow
                key={name}
                name={name}
                item={item}
                domain={domain}
                maxAbsDelta={maxAbsDelta}
              />
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground/70">
            {direction === "minimize"
              ? f("axisNoteMinimize")
              : f("axisNote")}
          </p>
        </SectionCard>
      ) : (
        <EmptyHint />
      )}

      {entries.length ? (
        <SectionCard title={f("dumbbellTitle")} icon={Sparkles} tone="info">
          <ComparisonDumbbell rows={sorted} domain={domain} />
          {/* 图例 + 刻度口径说明 */}
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted-foreground/60">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full border border-muted-foreground/60 bg-muted-foreground/40" />
              {f("baseline")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-emerald-500" />
              {f("promoted")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-destructive" />
              {f("failed")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-muted-foreground/50" />
              {f("notPromoted")}
            </span>
          </div>
          {/* 刻度口径必须写明：横轴刻意不从 0 起，否则读图会误判差距大小 */}
          <p className="mt-1.5 text-[10px] text-muted-foreground/50">
            {f("domainNote")}
          </p>
        </SectionCard>
      ) : null}

      {hasCoverageGap && (
        <SectionCard title={f("coverageTitle")} icon={ListChecks} count={entries.length}>
          <InstanceCoverage rows={sorted} />
        </SectionCard>
      )}
    </ArtifactCanvas>
  )
}

// ── 分发入口 ─────────────────────────────────────────────────────────────────

const NAMED_RENDERERS: Record<
  string,
  (data: Record<string, unknown>) => ReactNode
> = {
  "llm4ad_comparison.json": (d) => <ComparisonView data={d} />,
  "decision.json": (d) => <DecisionView data={d} />,
  "hardware_profile.json": (d) => <HardwareView data={d} />,
  "stage_health.json": (d) => <StageHealthView data={d} />,
  "topic_evaluation.json": (d) => <TopicEvalView data={d} />,
  "novelty_report.json": (d) => <NoveltyView data={d} />,
  "queries.json": (d) => <QueriesView data={d} />,
  "search_meta.json": (d) => <SearchMetaView data={d} />,
  "sources.json": (d) => <SourcesView data={d} />,
  "web_search_result.json": (d) => <WebSearchView data={d} />,
}

/** 该文件名是否有专属的友好渲染器。 */
export function hasNamedArtifactRenderer(fileName: string): boolean {
  return fileName.toLowerCase() in NAMED_RENDERERS
}

/**
 * 依据文件名渲染专属视图。命中且 JSON 解析为对象时返回对应视图，
 * 否则返回 null（由调用方回退到通用只读渲染）。
 */
export function renderNamedArtifact(
  fileName: string,
  content: string,
): ReactNode | null {
  const renderer = NAMED_RENDERERS[fileName.toLowerCase()]
  if (!renderer) return null
  const parsed = safeParseJson(content)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null
  return renderer(parsed as Record<string, unknown>)
}
