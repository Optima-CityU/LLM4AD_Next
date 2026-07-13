/**
 * AutoResearch 结果分析视图（右侧「报告分析」全屏弹层内容）。
 *
 * 两层内容：
 *  1. 结构化聚合（零 LLM、直接读盘）——总览瓦片、阶段耗时甘特、质量门弱点链、
 *     决策/回滚时间线、实验指标、交付物清单。数据来自 `getAnalysis().data`。
 *  2. AI 分析报告——选模型 + 语言，触发后台 LLM 生成，SSE 流式渲染 Markdown。
 *
 * 视觉对齐 `autoresearch_analysis_mockup.html` 的暗色科技风，但用项目内 Tailwind
 * token（bg-card / border-border / text-primary…）落地，保证明暗主题一致。
 */

import {
  AlertTriangle,
  FileBarChart,
  FileText,
  Flag,
  GitBranch,
  LayoutDashboard,
  Loader2,
  RefreshCw,
  Sparkles,
  Square,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type {
  ResearchAnalysisData,
  ResearchAnalysisDecisionItem,
  ResearchAnalysisStageItem,
} from "@/client"
import ReportMarkdown from "@/components/Evolution/TaskDetail/ReportMarkdown"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  useAnalysisStream,
  useGenerateAnalysis,
  useResearchAnalysis,
  useStopAnalysis,
} from "@/hooks/useResearchAnalysis"
import { cn } from "@/lib/utils"

import ProviderModelPicker from "./ProviderModelPicker"
import { stageNameByLang } from "./tech"

interface Props {
  sessionId: string
}

// ────────────────────────────────────────────────────────────────────────────
// 小工具
// ────────────────────────────────────────────────────────────────────────────

/** 秒 → 人类可读（≥60s 用分钟）。 */
function fmtDuration(sec: number): string {
  if (sec >= 60) return `${(sec / 60).toFixed(1)} min`
  return `${sec.toFixed(1)} s`
}

/** 从松散 dict 里安全取数值。 */
function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v)))
    return Number(v)
  return null
}

/** 从松散 dict 里安全取字符串。 */
function str(v: unknown): string | null {
  if (typeof v === "string") return v
  if (typeof v === "number") return String(v)
  return null
}

type Dict = Record<string, unknown>

function asDict(v: unknown): Dict {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Dict) : {}
}

// ────────────────────────────────────────────────────────────────────────────
// 主组件
// ────────────────────────────────────────────────────────────────────────────

export default function AnalysisReport({ sessionId }: Props) {
  const { t } = useTranslation()
  const { data, isLoading, refetch, isFetching } =
    useResearchAnalysis(sessionId)

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground p-8">
        <Loader2 className="size-4 animate-spin" />
        {t("autoResearch.analysis.loading")}
      </div>
    )
  }

  const analysis = data?.data
  const hasContent =
    analysis && ((analysis.stages?.length ?? 0) > 0 || analysis.run_dir)

  if (!analysis?.run_dir) {
    return (
      <div className="grid place-items-center p-8 text-sm text-muted-foreground/70 text-center">
        {t("autoResearch.analysis.noRunDir")}
      </div>
    )
  }

  if (!hasContent) {
    return (
      <div className="grid place-items-center p-8 text-sm text-muted-foreground/70 text-center">
        {t("autoResearch.analysis.empty")}
      </div>
    )
  }

  return (
    <div className="w-full h-full overflow-y-auto">
      <div className="mx-auto max-w-[1240px] px-6 py-6">
        <Tabs defaultValue="structured" className="gap-0">
          {/* 顶部：Tab 切换（结构化 / AI 报告）+ run_dir 说明 + 刷新 */}
          <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
            <TabsList>
              <TabsTrigger value="structured">
                <LayoutDashboard className="size-4" />
                {t("autoResearch.analysis.tabs.structured")}
              </TabsTrigger>
              <TabsTrigger value="ai">
                <Sparkles className="size-4" />
                {t("autoResearch.analysis.tabs.ai")}
              </TabsTrigger>
            </TabsList>
            <div className="flex items-center gap-3 min-w-0">
              <p className="text-xs text-muted-foreground/70 truncate">
                {t("autoResearch.analysis.subtitle", {
                  session: sessionId.slice(0, 8),
                  run: analysis.run_dir,
                })}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                <RefreshCw
                  className={cn("size-3.5", isFetching && "animate-spin")}
                />
                {t("autoResearch.analysis.refresh")}
              </Button>
            </div>
          </div>

          {/* 结构化聚合（零 LLM，直接读盘）：总览 / 甘特 / 原因链 / 实验 */}
          <TabsContent value="structured" className="space-y-7">
            <QualityGateBanner analysis={analysis} />
            <OverviewSection analysis={analysis} />
            <TimelineSection analysis={analysis} />
            <ReasonAndDecisionSection analysis={analysis} />
            <ExperimentAndDeliverablesSection analysis={analysis} />
          </TabsContent>

          {/* AI 分析报告（LLM 生成 + SSE 流式渲染） */}
          <TabsContent value="ai">
            <ReportSection
              sessionId={sessionId}
              savedContent={data?.report?.content ?? null}
              savedStatus={data?.report?.status ?? null}
              savedProviderModel={data?.report?.provider_model ?? null}
              savedUpdatedAt={data?.report?.updated_at ?? null}
            />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// Section 容器
// ────────────────────────────────────────────────────────────────────────────

function Section({
  n,
  title,
  hint,
  children,
}: {
  n?: number
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3.5">
      <div className="flex items-center gap-2.5">
        {n != null && (
          <span className="grid place-items-center size-5 rounded-md bg-primary/10 text-primary text-[11px] font-semibold border border-primary/20">
            {n}
          </span>
        )}
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h2>
        {hint && (
          <span className="text-[11px] font-normal normal-case tracking-normal text-muted-foreground/60">
            · {hint}
          </span>
        )}
      </div>
      {children}
    </section>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// 质量门红带
// ────────────────────────────────────────────────────────────────────────────

/** 从 quality_gate dict 解析 score / threshold / verdict / weaknesses。 */
function parseGate(gate: Dict) {
  const score = num(gate.score) ?? num(gate.quality_score)
  const threshold = num(gate.threshold) ?? num(gate.pass_threshold) ?? 5
  const verdictRaw =
    str(gate.verdict) ?? str(gate.decision) ?? str(gate.status) ?? ""
  const verdict = verdictRaw.toLowerCase()
  const weaknessesRaw =
    gate.weaknesses ?? gate.issues ?? gate.reasons ?? gate.findings
  const weaknesses = Array.isArray(weaknessesRaw)
    ? weaknessesRaw
        .map((w) =>
          typeof w === "string"
            ? w
            : str(asDict(w).description ?? asDict(w).reason),
        )
        .filter((w): w is string => !!w)
    : []
  const isReject = verdict.includes("reject") || verdict.includes("fail")
  const isDegraded =
    verdict.includes("degrad") ||
    (score != null && threshold != null && score < threshold)
  return {
    score,
    threshold,
    verdict: verdictRaw,
    weaknesses,
    isReject,
    isDegraded,
  }
}

function QualityGateBanner({ analysis }: { analysis: ResearchAnalysisData }) {
  const { t } = useTranslation()
  const gate = analysis.quality_gate
  if (!gate) return null
  const { score, threshold, verdict, isReject, isDegraded } = parseGate(gate)
  if (!isReject && !isDegraded) return null

  return (
    <div
      className={cn(
        "flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm",
        isReject
          ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-300"
          : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300",
      )}
    >
      <Flag className="size-4 mt-0.5 shrink-0" />
      <p className="leading-relaxed">
        {t("autoResearch.analysis.gate.rejectBanner", {
          score: score != null ? `${score} / ${threshold}` : "—",
          threshold,
          verdict:
            verdict ||
            (isReject
              ? t("autoResearch.analysis.gate.reject")
              : t("autoResearch.analysis.gate.degraded")),
        })}
      </p>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// ① 总览瓦片
// ────────────────────────────────────────────────────────────────────────────

function Tile({
  label,
  children,
  tone = "default",
}: {
  label: string
  children: React.ReactNode
  tone?: "default" | "warn" | "bad" | "good" | "cyan"
}) {
  return (
    <div
      className={cn(
        "rounded-xl border bg-card/60 px-3.5 py-3",
        tone === "warn" && "border-amber-500/40 bg-amber-500/[0.06]",
        tone === "bad" && "border-red-500/40 bg-red-500/[0.06]",
        tone === "default" && "border-border/60",
        tone === "good" && "border-border/60",
        tone === "cyan" && "border-border/60",
      )}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground/60 mb-1.5">
        {label}
      </div>
      <div
        className={cn(
          "text-xl font-bold tabular-nums tracking-tight",
          tone === "warn" && "text-amber-500",
          tone === "bad" && "text-red-500",
          tone === "good" && "text-emerald-500",
          tone === "cyan" && "text-primary",
        )}
      >
        {children}
      </div>
    </div>
  )
}

function OverviewSection({ analysis }: { analysis: ResearchAnalysisData }) {
  const { t, i18n } = useTranslation()
  const stages = analysis.stages ?? []
  const overview = asDict(analysis.overview)
  const gate = analysis.quality_gate ? parseGate(analysis.quality_gate) : null

  const stagesRun = stages.length
  const totalSteps = stages.reduce(
    (a, s) => a + Math.max(1, s.versions?.length ?? 1),
    0,
  )
  const failedSteps = stages.filter((s) => s.status === "failed").length
  const rerunStages = stages.filter((s) => (s.versions?.length ?? 1) > 1)
  const totalCompute = stages.reduce((a, s) => a + (s.duration_sec ?? 0), 0)
  const rollbacks = (analysis.decisions ?? []).filter((d) =>
    ["pivot", "refine"].includes((d.decision ?? "").toLowerCase()),
  ).length

  const checkpoint = asDict(overview.checkpoint)
  const hitl =
    num(overview.hitl_count) ??
    num(checkpoint.hitl) ??
    (checkpoint.hitl ? 1 : 0)

  const lastStage = stages[stages.length - 1]
  const finalStage = lastStage ? `${lastStage.stage} · ${lastStage.name}` : "—"

  const delivery = gate?.isReject
    ? "Reject"
    : gate?.isDegraded
      ? "Degraded"
      : (str(overview.status) ?? "OK")

  return (
    <Section n={1} title={t("autoResearch.analysis.sections.overview")}>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Tile label={t("autoResearch.analysis.overview.stagesRun")}>
          {stagesRun}
          <span className="text-xs text-muted-foreground font-medium">
            {" "}
            / 23
          </span>
        </Tile>
        <Tile
          label={t("autoResearch.analysis.overview.totalSteps")}
          tone="cyan"
        >
          {totalSteps}
        </Tile>
        <Tile
          label={t("autoResearch.analysis.overview.failedSteps")}
          tone={failedSteps > 0 ? "bad" : "good"}
        >
          {failedSteps}
        </Tile>
        <Tile
          label={t("autoResearch.analysis.overview.delivery")}
          tone={gate?.isReject ? "bad" : gate?.isDegraded ? "warn" : "good"}
        >
          <span className="text-base">{delivery}</span>
        </Tile>
        {gate && (
          <Tile
            label={t("autoResearch.analysis.overview.qualityGate")}
            tone={gate.isReject ? "bad" : gate.isDegraded ? "warn" : "good"}
          >
            <span className="text-base">
              {gate.verdict || (gate.isReject ? "Reject" : "Pass")}
            </span>
            {gate.score != null && (
              <span className="text-xs text-muted-foreground font-medium">
                {" "}
                {gate.score}/{gate.threshold}
              </span>
            )}
          </Tile>
        )}
        <Tile label={t("autoResearch.analysis.overview.hitl")}>
          {hitl ?? 0}
          <span className="text-xs text-muted-foreground font-medium">
            {" "}
            {t("autoResearch.analysis.overview.times")}
          </span>
        </Tile>
        <Tile label={t("autoResearch.analysis.overview.computeTime")}>
          <span className="text-base">{fmtDuration(totalCompute)}</span>
        </Tile>
        <Tile
          label={t("autoResearch.analysis.overview.rollbacks")}
          tone={rollbacks > 0 ? "warn" : "default"}
        >
          {rollbacks}
          <span className="text-xs text-muted-foreground font-medium">
            {" "}
            {t("autoResearch.analysis.overview.times")}
          </span>
        </Tile>
        {rerunStages.length > 0 && (
          <Tile label={t("autoResearch.analysis.overview.rerunStages")}>
            <span className="text-base">
              {rerunStages.map((s) => s.stage).join(", ")}
            </span>
          </Tile>
        )}
        <Tile label={t("autoResearch.analysis.overview.finalStage")}>
          <span className="text-sm">
            {lastStage
              ? `${lastStage.stage} · ${
                  stageNameByLang(lastStage.stage, i18n.language) ||
                  lastStage.name
                }`
              : finalStage}
          </span>
        </Tile>
      </div>
    </Section>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// ② 阶段耗时甘特
// ────────────────────────────────────────────────────────────────────────────

const HOT_THRESHOLD = 180 // 秒；瓶颈高亮阈值

function TimelineSection({ analysis }: { analysis: ResearchAnalysisData }) {
  const { t, i18n } = useTranslation()
  const stages = analysis.stages ?? []
  const maxTotal = useMemo(
    () => Math.max(1, ...stages.map((s) => s.duration_sec ?? 0)),
    [stages],
  )
  if (stages.length === 0) return null

  return (
    <Section
      n={2}
      title={t("autoResearch.analysis.sections.timeline")}
      hint={t("autoResearch.analysis.timelineHint")}
    >
      <div className="rounded-xl border border-border/60 bg-card/60 p-4 space-y-3">
        {/* 图例 */}
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
          <LegendItem className="bg-gradient-to-b from-sky-400 to-cyan-600">
            {t("autoResearch.analysis.legend.done")}
          </LegendItem>
          <LegendItem className="bg-cyan-500/70">
            {t("autoResearch.analysis.legend.round2")}
          </LegendItem>
          <LegendItem className="bg-cyan-500/45">
            {t("autoResearch.analysis.legend.round3")}
          </LegendItem>
          <LegendItem className="bg-gradient-to-b from-amber-400 to-amber-600">
            {t("autoResearch.analysis.legend.degraded")}
          </LegendItem>
          <LegendItem className="bg-purple-500 rounded-full">
            {t("autoResearch.analysis.legend.gate")}
          </LegendItem>
        </div>

        <div className="space-y-1">
          {stages.map((s) => (
            <GanttRow
              key={s.stage}
              stage={s}
              maxTotal={maxTotal}
              lang={i18n.language}
            />
          ))}
        </div>
      </div>
    </Section>
  )
}

function LegendItem({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i className={cn("inline-block size-2.5 rounded-[3px]", className)} />
      {children}
    </span>
  )
}

function GanttRow({
  stage,
  maxTotal,
  lang,
}: {
  stage: ResearchAnalysisStageItem
  maxTotal: number
  lang: string
}) {
  const total = stage.duration_sec ?? 0
  const versions = stage.versions?.length ? stage.versions : [total]
  const runs = versions.length
  const hot = total >= HOT_THRESHOLD
  const isDeg =
    (stage.decision ?? "").toLowerCase().includes("degrad") ||
    stage.stage === 20
  const name = stageNameByLang(stage.stage, lang) || stage.name

  return (
    <div className="grid grid-cols-[150px_1fr_54px] items-center gap-3 h-6">
      <div className="flex items-center gap-1.5 text-xs overflow-hidden">
        {stage.is_gate && (
          <span
            className="size-1.5 rounded-full bg-purple-500 shrink-0 shadow-[0_0_6px] shadow-purple-500"
            title="gate"
          />
        )}
        <span className="text-muted-foreground/50 tabular-nums w-5 text-right shrink-0">
          {stage.stage}
        </span>
        <span className="truncate text-foreground/80" title={stage.name}>
          {name}
        </span>
        {runs > 1 && (
          <span className="text-[9px] px-1.5 rounded-full bg-amber-500/15 text-amber-500 border border-amber-500/30 shrink-0">
            ×{runs}
          </span>
        )}
      </div>
      <div className="min-w-0">
        <div
          className={cn(
            "relative h-3.5 rounded-[5px] bg-muted/40 flex overflow-hidden",
            hot && "ring-1 ring-primary/25 shadow-[0_0_14px] shadow-primary/15",
          )}
          style={{ width: `${(total / maxTotal) * 100}%`, minWidth: 2 }}
        >
          {versions.map((d, idx) => (
            <div
              key={idx}
              className={cn(
                "h-full",
                isDeg
                  ? "bg-gradient-to-b from-amber-400 to-amber-600"
                  : idx === 0
                    ? "bg-gradient-to-b from-sky-400 to-cyan-600"
                    : idx === 1
                      ? "bg-cyan-500/70"
                      : "bg-cyan-500/45",
              )}
              style={{ width: `${total > 0 ? (d / total) * 100 : 0}%` }}
              title={`round ${idx + 1} · ${d.toFixed(2)}s`}
            />
          ))}
        </div>
      </div>
      <span className="text-[11px] text-muted-foreground whitespace-nowrap tabular-nums text-right">
        {fmtDuration(total)}
      </span>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// ③ 失败原因链 + 决策/回滚时间线
// ────────────────────────────────────────────────────────────────────────────

function ReasonAndDecisionSection({
  analysis,
}: {
  analysis: ResearchAnalysisData
}) {
  const { t } = useTranslation()
  const gate = analysis.quality_gate ? parseGate(analysis.quality_gate) : null
  const weaknesses = gate?.weaknesses ?? []
  const decisions = analysis.decisions ?? []

  if (weaknesses.length === 0 && decisions.length === 0) return null

  const twoCol = weaknesses.length > 0 && decisions.length > 0

  return (
    <Section n={3} title={t("autoResearch.analysis.sections.reasons")}>
      <div
        className={cn(
          "grid gap-4",
          twoCol ? "lg:grid-cols-[1.15fr_1fr]" : "grid-cols-1",
        )}
      >
        {/* 弱点链 */}
        {weaknesses.length > 0 && (
          <div className="min-w-0 rounded-xl border border-red-500/40 bg-red-500/[0.05] p-4 space-y-3">
            <h3 className="flex items-center gap-2 text-sm font-medium text-foreground/90">
              <AlertTriangle className="size-4 text-red-500" />
              {t("autoResearch.analysis.gate.weaknessesTitle", {
                count: weaknesses.length,
              })}
            </h3>
            <ul className="space-y-2">
              {weaknesses.map((w, i) => (
                <li
                  key={i}
                  className="flex gap-2 rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2.5 text-xs leading-relaxed text-red-700 dark:text-red-200/90"
                >
                  <AlertTriangle className="size-3.5 mt-0.5 shrink-0 text-red-500" />
                  <span>{w}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 决策时间线 */}
        {decisions.length > 0 && (
          <div className="min-w-0 rounded-xl border border-border/60 bg-card/60 p-4 space-y-3">
            <h3 className="flex items-center gap-2 text-sm font-medium text-foreground/90">
              <GitBranch className="size-4 text-primary" />
              {t("autoResearch.analysis.sections.decisions")}
            </h3>
            <DecisionTimeline decisions={decisions} gate={gate} />
          </div>
        )}
      </div>
    </Section>
  )
}

function decisionPillClasses(decision: string): string {
  const d = decision.toLowerCase()
  if (d.includes("pivot"))
    return "bg-purple-500/15 text-purple-500 border-purple-500/30"
  if (d.includes("refine"))
    return "bg-primary/15 text-primary border-primary/30"
  if (d.includes("proceed"))
    return "bg-emerald-500/15 text-emerald-500 border-emerald-500/30"
  return "bg-muted text-muted-foreground border-border/60"
}

function DecisionTimeline({
  decisions,
  gate,
}: {
  decisions: ResearchAnalysisDecisionItem[]
  gate: ReturnType<typeof parseGate> | null
}) {
  const { t } = useTranslation()
  return (
    <div className="relative pl-4 before:absolute before:left-[5px] before:top-1 before:bottom-1 before:w-px before:bg-border/70">
      {decisions.map((d, i) => {
        const target =
          d.rollback_target ??
          (d.rollback_stage_num != null
            ? `Stage ${d.rollback_stage_num}`
            : null)
        return (
          <div key={i} className="relative pb-4 pl-3.5 last:pb-0">
            <span className="absolute -left-[13px] top-1 size-2.5 rounded-full border-2 border-amber-500 bg-background" />
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span
                className={cn(
                  "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase",
                  decisionPillClasses(d.decision),
                )}
              >
                {d.decision}
              </span>
              {target && (
                <span className="font-medium text-foreground/90">
                  {t("autoResearch.analysis.decisions.rollbackTo", { target })}
                </span>
              )}
              {d.timestamp && (
                <span className="text-muted-foreground/50 tabular-nums">
                  {d.timestamp.slice(11, 19) || d.timestamp}
                </span>
              )}
            </div>
            {d.attempt != null && (
              <div className="text-[11px] text-muted-foreground/70 mt-1">
                {t("autoResearch.analysis.decisions.attempt", { n: d.attempt })}
              </div>
            )}
          </div>
        )
      })}
      {gate && (gate.isReject || gate.isDegraded) && (
        <div className="relative pb-0 pl-3.5">
          <span className="absolute -left-[13px] top-1 size-2.5 rounded-full border-2 border-red-500 bg-red-500/20" />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span
              className={cn(
                "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase",
                gate.isReject
                  ? "bg-red-500/15 text-red-500 border-red-500/30"
                  : "bg-amber-500/15 text-amber-500 border-amber-500/30",
              )}
            >
              {gate.verdict ||
                (gate.isReject
                  ? t("autoResearch.analysis.gate.reject")
                  : t("autoResearch.analysis.gate.degraded"))}
            </span>
            <span className="font-medium text-foreground/90">
              {t("autoResearch.analysis.decisions.final")}
            </span>
            {gate.score != null && (
              <span className="text-muted-foreground/60 tabular-nums">
                {gate.score}/{gate.threshold}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// ④ 实验指标 + 交付物
// ────────────────────────────────────────────────────────────────────────────

/** 把 experiment dict 拍平成 [key, value] 指标对（浅层 + 常见嵌套）。 */
function flattenExperiment(exp: Dict): [string, unknown][] {
  const out: [string, unknown][] = []
  const push = (k: string, v: unknown) => {
    if (v == null) return
    if (typeof v === "object") return
    // 跳过日志 / LaTeX / 多行文本等长文本块：它们不是可展示的标量指标，
    // 直接铺进指标网格会撑破卡片、横向溢出到相邻栏。
    if (
      typeof v === "string" &&
      (v.length > 80 || v.includes("\n") || v.includes("\r"))
    )
      return
    out.push([k, v])
  }
  for (const [k, v] of Object.entries(exp)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Dict)) push(`${k}.${k2}`, v2)
    } else {
      push(k, v)
    }
  }
  return out
}

function ExperimentAndDeliverablesSection({
  analysis,
}: {
  analysis: ResearchAnalysisData
}) {
  const { t } = useTranslation()
  const exp = analysis.experiment ? asDict(analysis.experiment) : null
  const deliverables = analysis.deliverables
    ? asDict(analysis.deliverables)
    : null

  if (!exp && !deliverables) return null

  const metrics = exp ? flattenExperiment(exp).slice(0, 12) : []
  const twoCol = !!exp && !!deliverables

  return (
    <Section n={4} title={t("autoResearch.analysis.sections.experiment")}>
      <div
        className={cn(
          "grid gap-4",
          twoCol ? "lg:grid-cols-[1.15fr_1fr]" : "grid-cols-1",
        )}
      >
        {/* 实验指标 */}
        {exp && (
          <div className="min-w-0 rounded-xl border border-border/60 bg-card/60 p-4 space-y-3">
            <h3 className="flex items-center gap-2 text-sm font-medium text-foreground/90">
              <FileBarChart className="size-4 text-primary" />
              {t("autoResearch.analysis.sections.experiment")}
            </h3>
            {metrics.length > 0 ? (
              <>
                <div className="grid sm:grid-cols-2 gap-2">
                  {metrics.map(([k, v]) => {
                    const isZeroFlag =
                      /figure|script|chart/i.test(k) && num(v) === 0
                    return (
                      <div
                        key={k}
                        className={cn(
                          "flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs",
                          isZeroFlag
                            ? "border-red-500/40 bg-red-500/[0.05]"
                            : "border-border/50 bg-background/40",
                        )}
                      >
                        <span
                          className="text-muted-foreground truncate shrink"
                          title={k}
                        >
                          {k}
                        </span>
                        <span
                          className={cn(
                            "font-semibold tabular-nums truncate text-right max-w-[60%]",
                            isZeroFlag && "text-red-500",
                          )}
                          title={formatMetricValue(v)}
                        >
                          {formatMetricValue(v)}
                        </span>
                      </div>
                    )
                  })}
                </div>
                <p className="text-[11px] text-muted-foreground/60 leading-relaxed">
                  {t("autoResearch.analysis.experiment.flagHint")}
                </p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground/60">
                {t("autoResearch.analysis.experiment.empty")}
              </p>
            )}
          </div>
        )}

        {/* 交付物 */}
        {deliverables && (
          <div className="min-w-0 rounded-xl border border-border/60 bg-card/60 p-4 space-y-3">
            <h3 className="flex items-center gap-2 text-sm font-medium text-foreground/90">
              <FileText className="size-4 text-primary" />
              {t("autoResearch.analysis.sections.deliverables")}
            </h3>
            <DeliverablesView deliverables={deliverables} />
          </div>
        )}
      </div>
    </Section>
  )
}

function formatMetricValue(v: unknown): string {
  if (typeof v === "boolean") return v ? "✓" : "✕"
  const n = num(v)
  if (n != null) return Number.isInteger(n) ? String(n) : n.toFixed(4)
  // 折叠空白并夹断超长字符串，避免撑破指标卡片。
  const s = String(v).replace(/\s+/g, " ").trim()
  return s.length > 60 ? `${s.slice(0, 60)}…` : s
}

/** 从 manifest dict 收集文件名与图表名（尽量兼容不同结构）。 */
function collectDeliverableFiles(d: Dict): {
  charts: string[]
  files: string[]
} {
  const charts: string[] = []
  const files: string[] = []
  const eat = (v: unknown) => {
    if (typeof v === "string") {
      if (/\.(png|jpg|jpeg|svg|pdf)$/i.test(v) && /fig|chart|plot/i.test(v))
        charts.push(v.split("/").pop() ?? v)
      else files.push(v.split("/").pop() ?? v)
    } else if (Array.isArray(v)) {
      for (const it of v) {
        if (typeof it === "string") eat(it)
        else if (it && typeof it === "object") {
          const name = str(
            asDict(it).name ?? asDict(it).path ?? asDict(it).file,
          )
          if (name) eat(name)
        }
      }
    }
  }
  for (const [k, v] of Object.entries(d)) {
    if (/chart|figure|image/i.test(k)) {
      if (Array.isArray(v))
        for (const it of v) {
          const name =
            typeof it === "string"
              ? it
              : str(asDict(it).name ?? asDict(it).path)
          if (name) charts.push(name.split("/").pop() ?? name)
        }
      else eat(v)
    } else {
      eat(v)
    }
  }
  return {
    charts: [...new Set(charts)].filter(Boolean),
    files: [...new Set(files)].filter(Boolean),
  }
}

function DeliverablesView({ deliverables }: { deliverables: Dict }) {
  const { t } = useTranslation()
  const { charts, files } = collectDeliverableFiles(deliverables)

  if (charts.length === 0 && files.length === 0) {
    return (
      <p className="text-xs text-muted-foreground/60">
        {t("autoResearch.analysis.deliverables.empty")}
      </p>
    )
  }

  return (
    <div className="space-y-3">
      {charts.length > 0 && (
        <div className="grid grid-cols-3 gap-2">
          {charts.slice(0, 6).map((c) => (
            <div
              key={c}
              className="aspect-[4/3] rounded-lg border border-border/50 bg-muted/30 flex items-end p-1.5 text-[9px] text-muted-foreground/60 overflow-hidden"
              title={c}
            >
              <span className="truncate">{c}</span>
            </div>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {files.slice(0, 24).map((f) => {
            const isPdf = /\.pdf$/i.test(f)
            return (
              <li
                key={f}
                className={cn(
                  "text-[11px] font-mono rounded-md border px-2 py-1",
                  isPdf
                    ? "border-primary/40 text-primary bg-primary/[0.06]"
                    : "border-border/50 text-muted-foreground bg-background/40",
                )}
              >
                {f}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────
// ⑤ AI 分析报告（LLM 生成 + SSE 流）
// ────────────────────────────────────────────────────────────────────────────

function ReportSection({
  sessionId,
  savedContent,
  savedStatus,
  savedProviderModel,
  savedUpdatedAt,
}: {
  sessionId: string
  savedContent: string | null
  savedStatus: string | null
  savedProviderModel: string | null
  savedUpdatedAt: string | null
}) {
  const { t, i18n } = useTranslation()
  const [provider, setProvider] = useState("default")
  const [model, setModel] = useState("")
  const [streaming, setStreaming] = useState(false)

  const generate = useGenerateAnalysis()
  const stopMut = useStopAnalysis()
  const stream = useAnalysisStream(sessionId, streaming)

  // 报告语言直接跟随网站当前国际化语言（导航栏切换即生效）。
  const language: "zh" | "en" = i18n.language.startsWith("zh") ? "zh" : "en"

  const handleGenerate = () => {
    generate.mutate(
      {
        sessionId,
        body: {
          language,
          provider_id: provider || "default",
          model_name: model || null,
        },
      },
      {
        onSuccess: () => setStreaming(true),
        onError: (e: unknown) =>
          toast.error((e as Error)?.message ?? "generate failed"),
      },
    )
  }

  const handleStop = () => {
    stopMut.mutate(sessionId, {
      onSettled: () => {
        stream.stop()
        setStreaming(false)
      },
    })
  }

  // 流结束（完成/失败/取消）后退出流式态，让保存的报告回填。
  const streamDone =
    stream.status === "completed" ||
    stream.status === "failed" ||
    stream.status === "cancelled"
  // 退出流式态前先留存最后的流式内容：stream hook 在 disabled 时会清空自身
  // content，若不留存，从退出流式到持久化报告回填的空窗会闪空。
  const [lastStreamed, setLastStreamed] = useState("")
  useEffect(() => {
    if (streaming && streamDone && !stream.isStreaming) {
      setLastStreamed(stream.content)
      setStreaming(false)
    }
  }, [streaming, streamDone, stream.isStreaming, stream.content])

  const isBusy = streaming || stream.isStreaming || generate.isPending
  const displayContent = streaming
    ? stream.content
    : savedContent || lastStreamed
  const hasReport = !!displayContent

  return (
    <Section title={t("autoResearch.analysis.sections.report")}>
      <div className="rounded-xl border border-border/60 bg-card/60 p-4 space-y-3">
        {/* 控制条 */}
        <div className="flex flex-wrap items-end gap-3">
          <ProviderModelPicker
            provider={provider}
            model={model}
            onChange={(p, m) => {
              setProvider(p)
              setModel(m)
            }}
            className="flex-1 min-w-[280px]"
          />
          {isBusy ? (
            <Button
              variant="outline"
              size="sm"
              onClick={handleStop}
              disabled={stopMut.isPending}
            >
              <Square className="size-3.5" />
              {t("autoResearch.analysis.report.stop")}
            </Button>
          ) : (
            <Button size="sm" onClick={handleGenerate}>
              <Sparkles className="size-3.5" />
              {hasReport
                ? t("autoResearch.analysis.report.regenerate")
                : t("autoResearch.analysis.report.generate")}
            </Button>
          )}
        </div>

        {/* 元信息 */}
        {savedProviderModel && !streaming && (
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground/60">
            <span>
              {t("autoResearch.analysis.report.provider")}: {savedProviderModel}
            </span>
            {savedUpdatedAt && (
              <span>
                {t("autoResearch.analysis.report.lastGenerated")}:{" "}
                {savedUpdatedAt.slice(0, 19).replace("T", " ")}
              </span>
            )}
          </div>
        )}

        {/* 状态 / 错误 */}
        {stream.error && (
          <p className="text-xs text-red-500">
            {t("autoResearch.analysis.report.failed")}: {stream.error}
          </p>
        )}

        {/* 内容 */}
        {hasReport ? (
          <div className="rounded-lg border border-border/50 bg-background/40 p-4">
            <ReportMarkdown content={displayContent as string} />
            {stream.isStreaming && (
              <span className="inline-block w-1.5 h-4 ml-0.5 bg-primary animate-pulse align-text-bottom" />
            )}
          </div>
        ) : isBusy ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground p-4">
            <Loader2 className="size-4 animate-spin" />
            {t("autoResearch.analysis.report.generating")}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground/60 py-3">
            {savedStatus === "cancelled"
              ? t("autoResearch.analysis.report.cancelled")
              : t("autoResearch.analysis.report.empty")}
          </p>
        )}
      </div>
    </Section>
  )
}
