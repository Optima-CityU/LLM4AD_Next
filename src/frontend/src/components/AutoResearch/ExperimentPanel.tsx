import { Activity, Loader2, TrendingUp } from "lucide-react"
import { useEffect, useId, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ResearchGeneratedItem } from "@/client"
import { useResearchGenerated } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

/**
 * 右侧「实验」区：llm4ad 演化的仿真概览 + 趋势分析，接入真实 generated 数据。
 *
 * 数据来自 `listGenerated`（按 stage 分组，因为一个会话里演化可能在多个 stage
 * 跑多次）。图表数据转换逻辑对齐 evolution 页面：score 取 `evaluation.score`，
 * 散点用 min-max 归一化着色，趋势按 generation 累积算 max/avg（用原始分）。
 * 这里显示简化的小图，点击全屏按钮查看完整功能。
 */

const MAX_COLOR = "#00d4ff"
const AVG_COLOR = "#a66cff"
// island 配色（与 evolution ISLAND_COLORS 一致，循环取用）。
const ISLAND_COLORS = [
  "#00d4ff",
  "#ff6b6b",
  "#ffd93d",
  "#6bcb77",
  "#a66cff",
  "#ff8c42",
  "#e84393",
  "#00b894",
  "#fd79a8",
  "#74b9ff",
]

export type ExperimentTab = "simulation" | "trend"

interface EvoNode {
  id: string
  generation: number
  island: number
  rawScore: number
  score: number // [0,1] 归一化
  name: string
  parentIds: string[]
  unscored: boolean
}

interface ExpData {
  scored: EvoNode[]
  unscored: EvoNode[]
  islandCount: number
  maxGeneration: number
  population: number
  bestScore: number | null
  gens: { g: number; max: number; avg: number }[]
}

/** 从某 stage 的个体列表构造图表数据（复用 evolution 的提取 / 归一化 / 累积逻辑）。 */
function buildExpData(items: ResearchGeneratedItem[]): ExpData {
  const scored: EvoNode[] = []
  const unscored: EvoNode[] = []
  for (const it of items) {
    const d = it.data as Record<string, unknown> | null
    if (!d) continue
    const evaluation = d.evaluation as
      | Record<string, unknown>
      | null
      | undefined
    const rawVal =
      (evaluation?.score as number | undefined) ??
      (evaluation?.objective as number | undefined) ??
      (evaluation?.fitness as number | undefined)
    const node: EvoNode = {
      id: String(d.id ?? it.name),
      generation: Number(d.generation ?? 0),
      island: Number(d.island_id ?? 0),
      rawScore: typeof rawVal === "number" ? rawVal : 0,
      score: 0,
      name: String(d.name ?? d.id ?? it.name),
      parentIds: Array.isArray(d.parent_ids)
        ? (d.parent_ids as unknown[]).map(String)
        : [],
      unscored: typeof rawVal !== "number",
    }
    if (node.unscored) unscored.push(node)
    else scored.push(node)
  }

  // min-max 归一化（仅有分数的节点）
  if (scored.length > 0) {
    let min = Number.POSITIVE_INFINITY
    let max = Number.NEGATIVE_INFINITY
    for (const n of scored) {
      if (n.rawScore < min) min = n.rawScore
      if (n.rawScore > max) max = n.rawScore
    }
    const range = max - min
    for (const n of scored) {
      n.score = range > 0 ? (n.rawScore - min) / range : 0.5
    }
  }

  const all = [...scored, ...unscored]
  const islandCount = new Set(all.map((n) => n.island)).size || 1
  let maxGeneration = 0
  for (const n of all) {
    if (n.generation > maxGeneration) maxGeneration = n.generation
  }
  let bestScore: number | null = null
  for (const n of scored) {
    if (bestScore === null || n.rawScore > bestScore) bestScore = n.rawScore
  }

  // 趋势：按 generation 分组，累积算 max/avg（用原始分，保证 max 线单调）。
  const genMap = new Map<number, number[]>()
  for (const n of scored) {
    if (!genMap.has(n.generation)) genMap.set(n.generation, [])
    genMap.get(n.generation)?.push(n.rawScore)
  }
  const genKeys = [...genMap.keys()].sort((a, b) => a - b)
  const gens: { g: number; max: number; avg: number }[] = []
  let runningMax = Number.NEGATIVE_INFINITY
  let runningSum = 0
  let runningCount = 0
  for (const g of genKeys) {
    for (const s of genMap.get(g) ?? []) {
      if (s > runningMax) runningMax = s
      runningSum += s
      runningCount += 1
    }
    gens.push({
      g,
      max: runningMax,
      avg: runningCount ? runningSum / runningCount : 0,
    })
  }

  return {
    scored,
    unscored,
    islandCount,
    maxGeneration,
    population: all.length,
    bestScore,
    gens,
  }
}

/** 演化仿真 ⇄ 趋势分析切换段控件（放在区域标题行，与标题水平对齐）。 */
export function ExperimentTabToggle({
  value,
  onChange,
}: {
  value: ExperimentTab
  onChange: (v: ExperimentTab) => void
}) {
  const { t } = useTranslation()
  const items: { key: ExperimentTab; icon: typeof Activity; label: string }[] =
    [
      {
        key: "simulation",
        icon: Activity,
        label: t("autoResearch.experiment.simulation"),
      },
      {
        key: "trend",
        icon: TrendingUp,
        label: t("autoResearch.experiment.trend"),
      },
    ]
  return (
    <div className="flex items-center gap-0.5 rounded-md border border-border/60 bg-card/60 p-0.5">
      {items.map(({ key, icon: Icon, label }) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={cn(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors",
            value === key
              ? "bg-primary/15 text-primary"
              : "text-muted-foreground hover:text-foreground/80",
          )}
        >
          <Icon className="size-3" />
          {label}
        </button>
      ))}
    </div>
  )
}

export default function ExperimentPanel({
  sessionId,
  running,
}: {
  sessionId: string
  running?: boolean
}) {
  const { t } = useTranslation()
  const genQ = useResearchGenerated(sessionId, running)
  const [tab, setTab] = useState<ExperimentTab>("simulation")

  // 只保留有个体的 stage 分组。
  const groups = useMemo(
    () => (genQ.data?.groups ?? []).filter((g) => (g.items?.length ?? 0) > 0),
    [genQ.data],
  )

  const [stage, setStage] = useState<number | null>(null)
  useEffect(() => {
    if (groups.length === 0) return
    const stages = groups.map((g) => g.stage ?? -1)
    if (stage == null || !stages.includes(stage)) {
      setStage(stages[stages.length - 1])
    }
  }, [groups, stage])

  const activeGroup =
    groups.find((g) => (g.stage ?? -1) === stage) ?? groups[groups.length - 1]
  const data = useMemo(
    () => buildExpData(activeGroup?.items ?? []),
    [activeGroup?.items],
  )

  if (genQ.isLoading) {
    return (
      <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("autoResearch.artifacts.loading")}
      </div>
    )
  }

  if (groups.length === 0) {
    return (
      <p className="py-6 text-center text-xs text-muted-foreground/60">
        {t("autoResearch.experiment.empty")}
      </p>
    )
  }

  return (
    <div>
      {/* 视图切换（演化仿真在左 / 趋势分析在右） */}
      <div className="mb-2 flex items-center gap-1">
        <ExperimentTabToggle value={tab} onChange={setTab} />
      </div>
      {/* stage 选择 */}
      {groups.length > 1 && (
        <div className="mb-2 flex items-center gap-1.5">
          <span className="text-[10px] text-muted-foreground">
            {t("autoResearch.stages.title")}
          </span>
          <select
            value={stage ?? ""}
            onChange={(e) => setStage(Number(e.target.value))}
            className="h-6 rounded border border-border/60 bg-background/60 px-1.5 text-[11px] focus:border-primary/50 focus:outline-none"
          >
            {groups.map((g) => (
              <option key={g.stage ?? -1} value={g.stage ?? -1}>
                #{g.stage ?? "?"}
              </option>
            ))}
          </select>
        </div>
      )}

      {tab === "simulation" ? (
        <SimulationView data={data} />
      ) : (
        <TrendView data={data} />
      )}

      <p className="mt-1.5 text-[10px] text-muted-foreground/50">
        {t("autoResearch.experiment.population")}: {data.population}
      </p>
    </div>
  )
}

/**
 * 演化仿真简化视图：关键指标 + 小型岛屿泳道图。
 */
function SimulationView({ data }: { data: ExpData }) {
  const { t } = useTranslation()

  const all = useMemo(
    () => [...data.scored, ...data.unscored],
    [data.scored, data.unscored],
  )

  const layout = useMemo(() => {
    const islands = [...new Set(all.map((n) => n.island))].sort((a, b) => a - b)
    const islandIdx = new Map(islands.map((is, i) => [is, i]))
    const laneCount = Math.max(1, islands.length)

    const W = 300
    const PAD_L = 24
    const PAD_R = 10
    const PAD_T = 6
    const PAD_B = 6
    const LANE_H = 34
    const H = PAD_T + PAD_B + laneCount * LANE_H
    const iw = W - PAD_L - PAD_R
    const gmax = Math.max(1, data.maxGeneration)
    const xOf = (g: number) => PAD_L + (g / gmax) * iw
    const laneTop = (is: number) => PAD_T + (islandIdx.get(is) ?? 0) * LANE_H
    const laneCenter = (is: number) => laneTop(is) + LANE_H / 2

    const groups = new Map<string, EvoNode[]>()
    for (const n of all) {
      const k = `${n.generation}-${n.island}`
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k)?.push(n)
    }
    const pos = new Map<string, { x: number; y: number; node: EvoNode }>()
    for (const [k, nodes] of groups) {
      const [g, is] = k.split("-").map(Number)
      const cx = xOf(g)
      const cy = laneCenter(is)
      const gap = Math.min(9, (LANE_H - 6) / Math.max(1, nodes.length))
      nodes.forEach((n, i) => {
        pos.set(n.id, {
          x: cx,
          y: cy + (i - (nodes.length - 1) / 2) * gap,
          node: n,
        })
      })
    }

    const links: { x1: number; y1: number; x2: number; y2: number }[] = []
    for (const n of all) {
      const cp = pos.get(n.id)
      if (!cp) continue
      for (const pid of n.parentIds) {
        const pp = pos.get(pid)
        if (pp) links.push({ x1: pp.x, y1: pp.y, x2: cp.x, y2: cp.y })
      }
    }

    return { islands, laneTop, W, H, PAD_L, PAD_R, LANE_H, xOf, pos, links }
  }, [all, data.maxGeneration])

  const bestId = useMemo(() => {
    let best: EvoNode | null = null
    for (const n of data.scored)
      if (best == null || n.rawScore > best.rawScore) best = n
    return best?.id ?? null
  }, [data.scored])

  const metrics = [
    {
      label: t("autoResearch.experiment.current"),
      value: `${data.maxGeneration}`,
    },
    {
      label: t("autoResearch.experiment.population"),
      value: `${data.population}`,
    },
    {
      label: t("autoResearch.experiment.islands"),
      value: `${data.islandCount}`,
    },
    {
      label: t("autoResearch.experiment.best"),
      value: data.bestScore != null ? fmt(data.bestScore) : "—",
    },
  ]

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-4 gap-1.5">
        {metrics.map((m) => (
          <div
            key={m.label}
            className="rounded-md border border-border/60 bg-card dark:border-border/50 dark:bg-card/40 px-1.5 py-1 text-center"
          >
            <div className="text-sm font-bold tabular-nums text-foreground/90 truncate">
              {m.value}
            </div>
            <div className="text-[10px] text-muted-foreground truncate">
              {m.label}
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-md border border-border/60 bg-card dark:border-border/50 dark:bg-card/30 p-1.5">
        <svg
          viewBox={`0 0 ${layout.W} ${layout.H}`}
          className="w-full h-auto"
          role="img"
          aria-label="island evolution graph"
        >
          <title>island evolution</title>
          {layout.islands.map((is, i) => (
            <g key={is}>
              <rect
                x={layout.PAD_L}
                y={layout.laneTop(is)}
                width={layout.W - layout.PAD_L - layout.PAD_R}
                height={layout.LANE_H}
                fill={ISLAND_COLORS[is % ISLAND_COLORS.length]}
                opacity={i % 2 === 0 ? 0.06 : 0.03}
              />
              <text
                x={layout.PAD_L - 4}
                y={layout.laneTop(is) + layout.LANE_H / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={8}
                fontWeight={600}
                fill={ISLAND_COLORS[is % ISLAND_COLORS.length]}
              >
                {is + 1}
              </text>
            </g>
          ))}
          {layout.links.map((l, idx) => (
            <line
              key={idx}
              x1={l.x1}
              y1={l.y1}
              x2={l.x2}
              y2={l.y2}
              stroke="currentColor"
              strokeWidth={0.6}
              className="text-muted-foreground/30"
            />
          ))}
          {data.unscored.map((n) => {
            const p = layout.pos.get(n.id)
            if (!p) return null
            return (
              <circle
                key={n.id}
                cx={p.x}
                cy={p.y}
                r={2}
                className="fill-muted-foreground/40"
              />
            )
          })}
          {data.scored.map((n) => {
            const p = layout.pos.get(n.id)
            if (!p) return null
            const isBest = n.id === bestId
            return (
              <circle
                key={n.id}
                cx={p.x}
                cy={p.y}
                r={isBest ? 4.5 : 2.8}
                fill={ISLAND_COLORS[n.island % ISLAND_COLORS.length]}
                opacity={isBest ? 1 : 0.85}
                stroke={isBest ? "var(--background)" : "none"}
                strokeWidth={isBest ? 1.2 : 0}
              />
            )
          })}
        </svg>
      </div>
    </div>
  )
}

/** 趋势分析简化视图 */
function TrendView({ data }: { data: ExpData }) {
  const { t } = useTranslation()
  const gid = useId()
  const W = 300
  const H = 120
  const PL = 34
  const PR = 10
  const PT = 12
  const PB = 20
  const iw = W - PL - PR
  const ih = H - PT - PB
  const gmax = Math.max(1, data.maxGeneration)

  const vals = data.gens.flatMap((d) => [d.max, d.avg])
  const rawMin = vals.length ? Math.min(...vals) : 0
  const rawMax = vals.length ? Math.max(...vals) : 1
  const {
    niceMin: yMin,
    niceMax: yMax,
    ticks: yTicks,
  } = niceScale(rawMin, rawMax, 4)
  const yRange = yMax - yMin || 1
  const xOf = (g: number) => PL + (g / gmax) * iw
  const yOf = (v: number) => PT + (1 - (v - yMin) / yRange) * ih
  const maxPts = data.gens.map((d) => `${xOf(d.g)},${yOf(d.max)}`).join(" ")
  const avgPts = data.gens.map((d) => `${xOf(d.g)},${yOf(d.avg)}`).join(" ")
  const xTickIdx = pickIndices(data.gens.length, 4)

  if (data.gens.length === 0) {
    return (
      <p className="py-6 text-center text-xs text-muted-foreground/60">
        {t("autoResearch.experiment.noScored")}
      </p>
    )
  }

  return (
    <div className="rounded-md border border-border/60 bg-card dark:border-border/50 dark:bg-card/30 p-1.5">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        role="img"
        aria-label="evolution trend"
      >
        <title>evolution trend</title>
        {yTicks.map((v) => (
          <g key={v}>
            <line
              x1={PL}
              y1={yOf(v)}
              x2={W - PR}
              y2={yOf(v)}
              stroke="currentColor"
              strokeWidth={0.5}
              className="text-border/40"
            />
            <text
              x={PL - 4}
              y={yOf(v)}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-muted-foreground"
              fontSize={7}
            >
              {fmt(v)}
            </text>
          </g>
        ))}
        {xTickIdx.map((i) => {
          const g = data.gens[i].g
          const anchor =
            i === 0 ? "start" : i === data.gens.length - 1 ? "end" : "middle"
          return (
            <text
              key={`x-${g}`}
              x={xOf(g)}
              y={H - PB + 10}
              textAnchor={anchor}
              className="fill-muted-foreground"
              fontSize={7}
            >
              {g}
            </text>
          )
        })}
        <text
          x={PL + iw / 2}
          y={H - 1}
          textAnchor="middle"
          className="fill-muted-foreground/60"
          fontSize={6.5}
        >
          {t("autoResearch.experiment.generation")}
        </text>
        <polyline
          points={avgPts}
          fill="none"
          stroke={AVG_COLOR}
          strokeWidth={1.4}
          strokeDasharray="5 3"
        />
        <polyline
          points={maxPts}
          fill="none"
          stroke={MAX_COLOR}
          strokeWidth={2}
          style={{ filter: `drop-shadow(0 0 3px ${MAX_COLOR}80)` }}
        />
        {data.gens.map((d) => (
          <circle
            key={`${gid}-${d.g}`}
            cx={xOf(d.g)}
            cy={yOf(d.max)}
            r={1.6}
            fill={MAX_COLOR}
          />
        ))}
      </svg>
      <div className="mt-1 flex items-center justify-center gap-3">
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <span
            className="h-0.5 w-3 rounded-full"
            style={{ backgroundColor: MAX_COLOR }}
          />
          {t("autoResearch.experiment.best")}
        </span>
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <span
            className="h-0 w-3 border-t border-dashed"
            style={{ borderColor: AVG_COLOR }}
          />
          {t("autoResearch.experiment.avg")}
        </span>
      </div>
    </div>
  )
}

function fmt(v: number): string {
  if (!Number.isFinite(v)) return "—"
  const abs = Math.abs(v)
  if (abs >= 1000) return `${(v / 1000).toFixed(1)}k`
  if (Number.isInteger(v)) return String(v)
  return v.toFixed(2)
}

function niceNum(range: number, round: boolean): number {
  const exp = Math.floor(Math.log10(range || 1))
  const frac = (range || 1) / 10 ** exp
  let nf: number
  if (round) {
    nf = frac < 1.5 ? 1 : frac < 3 ? 2 : frac < 7 ? 5 : 10
  } else {
    nf = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10
  }
  return nf * 10 ** exp
}

function niceScale(
  min: number,
  max: number,
  maxTicks = 4,
): { niceMin: number; niceMax: number; ticks: number[] } {
  let lo = min
  let hi = max
  if (lo === hi) {
    lo -= 1
    hi += 1
  }
  const range = niceNum(hi - lo, false)
  const step = niceNum(range / Math.max(1, maxTicks - 1), true) || 1
  const niceMin = Math.floor(lo / step) * step
  const niceMax = Math.ceil(hi / step) * step
  const ticks: number[] = []
  for (let v = niceMin; v <= niceMax + step * 0.5; v += step) ticks.push(v)
  return { niceMin, niceMax, ticks }
}

function pickIndices(len: number, n: number): number[] {
  if (len <= n) return Array.from({ length: len }, (_, i) => i)
  const out: number[] = []
  for (let i = 0; i < n; i++) out.push(Math.round((i * (len - 1)) / (n - 1)))
  return [...new Set(out)]
}
