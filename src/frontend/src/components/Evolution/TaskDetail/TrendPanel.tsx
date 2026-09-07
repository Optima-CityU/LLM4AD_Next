import * as d3 from "d3"
import { ChevronDown } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useEvolution } from "@/hooks/useEvolution"
import { cn, formatScore } from "@/lib/utils"
import { ISLAND_COLORS } from "./island-ga-mock-data"

type ViewMode = "global" | "instance"
/** Y 轴刻度模式：线性，或 symlog（对数式，压缩离群点、兼容 0/负分）。 */
type ScaleMode = "linear" | "log"

interface GenerationStats {
  generation: number
  maxScore: number
  genMaxScore: number
}

interface NodeNameInfo {
  name: string
  island: number
  generationCount: number
}

interface InstanceTrendLine {
  name: string
  color: string
  points: Array<{ generation: number; rawScore: number }>
}

const MARGIN = { top: 28, right: 20, bottom: 36, left: 54 }
const MAX_COLOR = "#00d4ff"
const GEN_MAX_COLOR = "#a66cff"

/**
 * Compute a left margin wide enough to fit the y-axis tick labels without
 * clipping them at the chart edge.
 *
 * The axis is rendered into a temporary (later removed) group so the real
 * label widths can be measured via `getBBox`, then the widest label width
 * plus padding for the tick marks is returned. Falls back to `MARGIN.left`
 * if measurement is unavailable (e.g. detached SVG).
 *
 * @param g - The chart's root `<g>` selection to measure within.
 * @param yScale - The y axis scale used to generate the ticks.
 * @param tickCount - Approximate number of ticks (matches the rendered axis).
 * @returns The left margin in pixels (never smaller than `MARGIN.left`).
 */
function measureAxisLeftMargin(
  g: d3.Selection<SVGGElement, unknown, null, undefined>,
  yScale: d3.ScaleContinuousNumeric<number, number>,
  tickCount: number,
): number {
  const probe = g.append("g").call(d3.axisLeft(yScale).ticks(tickCount))
  probe.selectAll(".tick text").style("font-size", "10px")

  let maxLabelW = 0
  probe.selectAll<SVGTextElement, unknown>(".tick text").each(function () {
    // getBBox can throw on a not-yet-laid-out element in some browsers; a
    // failed measurement just leaves maxLabelW at 0 and falls back below.
    try {
      const w = this.getBBox().width
      if (w > maxLabelW) maxLabelW = w
    } catch {
      // ignore — fall back to the default margin
    }
  })
  probe.remove()

  if (maxLabelW === 0) return MARGIN.left
  // Label width + gap between label and tick line + tick mark length.
  const needed = Math.ceil(maxLabelW + 12)
  return Math.max(MARGIN.left, needed)
}

const SUB_VIEWS: Array<{ value: ViewMode; labelKey: string }> = [
  { value: "global", labelKey: "evolution.panel.trend.globalTrend" },
  { value: "instance", labelKey: "evolution.panel.trend.instanceTrend" },
]

/**
 * Build the y-axis scale for the plotted scores in the chosen scale mode.
 *
 * Linear mode is the classic padded `[min, max]` domain. Log mode uses
 * `scaleSymlog` (symmetric log) instead of `scaleLog` so that scores at or
 * below zero — common for evolution objectives — stay well-defined; symlog is
 * linear near zero and compresses large magnitudes, pulling a single very-low
 * outlier back in so the rest of the series spreads out.
 *
 * The symlog `constant` (width of the near-zero linear window) is derived from
 * the data rather than left at d3's default of 1: it's set to the median of the
 * non-zero absolute scores. Without this, data whose magnitude is far below 1
 * (e.g. scores in 0.001–0.05) would fall entirely inside the linear window and
 * symlog would degrade to a near-linear axis — defeating the outlier
 * compression. Tying the constant to the typical score magnitude keeps the mid
 * band around the linear→log transition where it spreads out best.
 *
 * @param scores - All plotted score values (used for domain + symlog constant).
 * @param range - Pixel range `[bottom, top]` for the axis.
 * @param mode - `"linear"` or `"log"` (symlog).
 * @returns A d3 continuous scale mapping score → pixel.
 */
function buildYScale(
  scores: number[],
  range: [number, number],
  mode: ScaleMode,
): d3.ScaleContinuousNumeric<number, number> {
  const yMin = Math.min(...scores)
  const yMax = Math.max(...scores)
  const pad = (yMax - yMin) * 0.08 || 1
  if (mode === "log") {
    // symlog 的 constant C 决定「近零线性窗口」的宽度：C 越小，对数压缩越早
    // 介入 —— 小值区间被拉得越开、大值浮动被压得越小。取非零 |score| 的中位数
    // 作为数据尺度基准，再除以 LOG_STRENGTH 把窗口收窄、放大对数效果。
    // absSorted 对离群点本身鲁棒（中位数不受单个极低值影响）。全零时回退到 1。
    const LOG_STRENGTH = 8
    const absSorted = scores
      .map(Math.abs)
      .filter((v) => v > 0)
      .sort((a, b) => a - b)
    const median =
      absSorted.length > 0 ? absSorted[Math.floor(absSorted.length / 2)] : 1
    // 下限防止 C→0 造成数值退化（symlog 在 C 过小时接近纯 log 而对 0 附近敏感）。
    const constant = Math.max(median / LOG_STRENGTH, 1e-6)
    // symlog needs no padding to avoid a zero blow-up; `.nice()` is skipped
    // since symlog tick values aren't round.
    return d3
      .scaleSymlog<number, number>()
      .domain([yMin - pad, yMax + pad])
      .constant(constant)
      .range(range)
  }
  return d3
    .scaleLinear()
    .domain([yMin - pad, yMax + pad])
    .nice()
    .range(range)
}

export default function TrendPanel() {
  const { t } = useTranslation()
  const { evolutionData, currentGeneration } = useEvolution()

  const [viewMode, setViewMode] = useState<ViewMode>("global")
  const [scaleMode, setScaleMode] = useState<ScaleMode>("log")
  const [selectedNodeNames, setSelectedNodeNames] = useState<Set<string>>(
    new Set(),
  )
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const el = chartContainerRef.current
    if (!el) return
    let rafId = 0
    const ro = new ResizeObserver((entries) => {
      cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(() => {
        const { width, height } = entries[0].contentRect
        setDimensions({ width, height })
      })
    })
    ro.observe(el)
    return () => {
      cancelAnimationFrame(rafId)
      ro.disconnect()
    }
  }, [])

  const globalTrendData = useMemo<GenerationStats[]>(() => {
    const genMap = new Map<number, number[]>()
    for (const node of evolutionData.nodes) {
      if (node.generation > currentGeneration) continue
      if (!genMap.has(node.generation)) genMap.set(node.generation, [])
      genMap.get(node.generation)!.push(node.rawScore)
    }
    const generations = Array.from(genMap.keys()).sort((a, b) => a - b)
    const result: GenerationStats[] = []
    // maxScore is cumulative (best individual seen from the first generation
    // through the current one), which keeps that line monotonically
    // non-decreasing. genMaxScore is the best individual within each single
    // generation, so it reflects per-generation population quality.
    const cumulative: number[] = []
    for (const gen of generations) {
      const genScores = genMap.get(gen)!
      cumulative.push(...genScores)
      result.push({
        generation: gen,
        maxScore: Math.max(...cumulative),
        genMaxScore: Math.max(...genScores),
      })
    }
    return result
  }, [evolutionData.nodes, currentGeneration])

  const availableNodes = useMemo<NodeNameInfo[]>(() => {
    const nameMap = new Map<string, { island: number; gens: Set<number> }>()
    for (const node of evolutionData.nodes) {
      if (node.generation > currentGeneration) continue
      if (!nameMap.has(node.name)) {
        nameMap.set(node.name, { island: node.island, gens: new Set() })
      }
      nameMap.get(node.name)!.gens.add(node.generation)
    }
    return Array.from(nameMap.entries())
      .map(([name, info]) => ({
        name,
        island: info.island,
        generationCount: info.gens.size,
      }))
      .sort((a, b) => b.generationCount - a.generationCount)
  }, [evolutionData.nodes, currentGeneration])

  const instanceTrendData = useMemo<InstanceTrendLine[]>(() => {
    const lines: InstanceTrendLine[] = []
    for (const name of selectedNodeNames) {
      const nodes = evolutionData.nodes
        .filter((n) => n.name === name && n.generation <= currentGeneration)
        .sort((a, b) => a.generation - b.generation)
      if (nodes.length === 0) continue
      const island = nodes[0].island
      lines.push({
        name,
        color: ISLAND_COLORS[island % ISLAND_COLORS.length],
        points: nodes.map((n) => ({
          generation: n.generation,
          rawScore: n.rawScore,
        })),
      })
    }
    return lines
  }, [evolutionData.nodes, selectedNodeNames, currentGeneration])

  const toggleNode = useCallback((name: string) => {
    setSelectedNodeNames((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  const selectAll = useCallback(() => {
    setSelectedNodeNames(new Set(availableNodes.map((n) => n.name)))
  }, [availableNodes])

  const clearAll = useCallback(() => {
    setSelectedNodeNames(new Set())
  }, [])

  // D3 rendering: global trend
  useEffect(() => {
    if (viewMode !== "global") return
    if (!svgRef.current || dimensions.width === 0) return

    const svg = d3.select(svgRef.current)
    svg.selectAll("*").remove()

    const { width, height } = dimensions

    if (globalTrendData.length === 0) {
      svg
        .append("text")
        .attr("x", width / 2)
        .attr("y", height / 2)
        .attr("text-anchor", "middle")
        .attr("class", "fill-muted-foreground text-sm")
        .text(t("evolution.panel.trend.noData"))
      return
    }

    const innerH = height - MARGIN.top - MARGIN.bottom
    if (innerH <= 0) return

    const allScores = globalTrendData.flatMap((d) => [d.maxScore, d.genMaxScore])

    const yScale = buildYScale(allScores, [height - MARGIN.bottom, MARGIN.top], scaleMode)

    const g = svg.append("g")

    // Derive a left margin wide enough for the actual y-axis labels so that
    // long numeric ticks are never clipped at the chart's left edge.
    const marginLeft = measureAxisLeftMargin(g, yScale, 5)
    const innerW = width - marginLeft - MARGIN.right
    if (innerW <= 0) return

    const xScale = d3
      .scaleLinear()
      .domain([0, Math.max(currentGeneration, 1)])
      .range([marginLeft, width - MARGIN.right])

    // grid
    const yTicks = yScale.ticks(5)
    g.selectAll(".grid-line")
      .data(yTicks)
      .join("line")
      .attr("x1", marginLeft)
      .attr("x2", width - MARGIN.right)
      .attr("y1", (d) => yScale(d))
      .attr("y2", (d) => yScale(d))
      .attr("stroke", "currentColor")
      .attr("class", "text-border")
      .attr("stroke-opacity", 0.15)

    // axes
    const xAxis = d3
      .axisBottom(xScale)
      .ticks(Math.min(currentGeneration, 10))
      .tickFormat((d) => String(Math.round(d as number)))
    const yAxis = d3.axisLeft(yScale).ticks(5)

    g.append("g")
      .attr("transform", `translate(0,${height - MARGIN.bottom})`)
      .call(xAxis)
      .call((sel) => sel.select(".domain").attr("class", "text-border"))
      .call((sel) =>
        sel.selectAll(".tick text").attr("class", "fill-muted-foreground"),
      )
      .call((sel) => sel.selectAll(".tick line").attr("class", "text-border"))

    g.append("g")
      .attr("transform", `translate(${marginLeft},0)`)
      .call(yAxis)
      .call((sel) => sel.select(".domain").attr("class", "text-border"))
      .call((sel) =>
        sel.selectAll(".tick text").attr("class", "fill-muted-foreground"),
      )
      .call((sel) => sel.selectAll(".tick line").attr("class", "text-border"))

    // axis labels
    g.append("text")
      .attr("x", width / 2)
      .attr("y", height - 4)
      .attr("text-anchor", "middle")
      .attr("class", "fill-muted-foreground")
      .style("font-size", "10px")
      .text(t("evolution.panel.trend.generation"))

    // max line
    const maxLine = d3
      .line<GenerationStats>()
      .x((d) => xScale(d.generation))
      .y((d) => yScale(d.maxScore))
      .curve(d3.curveMonotoneX)

    g.append("path")
      .datum(globalTrendData)
      .attr("fill", "none")
      .attr("stroke", MAX_COLOR)
      .attr("stroke-width", 2)
      .attr("d", maxLine)

    // per-generation max line (dashed)
    const genMaxLine = d3
      .line<GenerationStats>()
      .x((d) => xScale(d.generation))
      .y((d) => yScale(d.genMaxScore))
      .curve(d3.curveMonotoneX)

    g.append("path")
      .datum(globalTrendData)
      .attr("fill", "none")
      .attr("stroke", GEN_MAX_COLOR)
      .attr("stroke-width", 1.5)
      .attr("stroke-dasharray", "6,3")
      .attr("d", genMaxLine)

    // data points
    g.selectAll(".dot-max")
      .data(globalTrendData)
      .join("circle")
      .attr("cx", (d) => xScale(d.generation))
      .attr("cy", (d) => yScale(d.maxScore))
      .attr("r", 3)
      .attr("fill", MAX_COLOR)

    g.selectAll(".dot-gen-max")
      .data(globalTrendData)
      .join("circle")
      .attr("cx", (d) => xScale(d.generation))
      .attr("cy", (d) => yScale(d.genMaxScore))
      .attr("r", 2.5)
      .attr("fill", GEN_MAX_COLOR)

    // legend
    const legend = g
      .append("g")
      .attr("transform", `translate(${marginLeft + 8}, ${MARGIN.top - 12})`)

    legend
      .append("line")
      .attr("x1", 0)
      .attr("x2", 16)
      .attr("y1", 0)
      .attr("y2", 0)
      .attr("stroke", MAX_COLOR)
      .attr("stroke-width", 2)
    legend
      .append("text")
      .attr("x", 20)
      .attr("y", 4)
      .attr("class", "fill-muted-foreground")
      .style("font-size", "10px")
      .text(t("evolution.panel.trend.highestScore"))

    legend
      .append("line")
      .attr("x1", 92)
      .attr("x2", 108)
      .attr("y1", 0)
      .attr("y2", 0)
      .attr("stroke", GEN_MAX_COLOR)
      .attr("stroke-width", 1.5)
      .attr("stroke-dasharray", "6,3")
    legend
      .append("text")
      .attr("x", 112)
      .attr("y", 4)
      .attr("class", "fill-muted-foreground")
      .style("font-size", "10px")
      .text(t("evolution.panel.trend.genMaxScore"))

    // tooltip
    const tooltipLine = g
      .append("line")
      .attr("stroke", "currentColor")
      .attr("class", "text-muted-foreground")
      .attr("stroke-opacity", 0.3)
      .attr("stroke-dasharray", "3,3")
      .style("display", "none")

    const tooltipG = g
      .append("g")
      .style("display", "none")
      .style("pointer-events", "none")

    tooltipG
      .append("rect")
      .attr("rx", 4)
      .attr("ry", 4)
      .attr("class", "fill-card stroke-border")
      .attr("stroke-width", 1)

    const tooltipText = tooltipG
      .append("text")
      .attr("class", "fill-foreground")
      .style("font-size", "10px")

    const overlay = g
      .append("rect")
      .attr("x", marginLeft)
      .attr("y", MARGIN.top)
      .attr("width", innerW)
      .attr("height", innerH)
      .attr("fill", "transparent")

    overlay.on("mousemove", (event: MouseEvent) => {
      const [mx] = d3.pointer(event)
      const gen = Math.round(xScale.invert(mx))
      const d = globalTrendData.find((s) => s.generation === gen)
      if (!d) return

      const x = xScale(d.generation)
      tooltipLine
        .attr("x1", x)
        .attr("x2", x)
        .attr("y1", MARGIN.top)
        .attr("y2", height - MARGIN.bottom)
        .style("display", null)

      const maxLabel = `${t("evolution.panel.trend.highestScore")}: ${formatScore(d.maxScore)}`
      const genMaxLabel = `${t("evolution.panel.trend.genMaxScore")}: ${formatScore(d.genMaxScore)}`
      const genLabel = `${t("evolution.panel.trend.generation")} ${d.generation}`

      tooltipText.selectAll("tspan").remove()
      tooltipText
        .append("tspan")
        .attr("x", 8)
        .attr("dy", 14)
        .style("font-weight", "600")
        .text(genLabel)
      tooltipText.append("tspan").attr("x", 8).attr("dy", 14).text(maxLabel)
      tooltipText.append("tspan").attr("x", 8).attr("dy", 14).text(genMaxLabel)

      const bbox = tooltipText.node()!.getBBox()
      tooltipG
        .select("rect")
        .attr("width", bbox.width + 16)
        .attr("height", bbox.height + 10)

      const tx = x + 12 + bbox.width + 16 > width ? x - bbox.width - 28 : x + 12
      const ty = MARGIN.top + 4
      tooltipG
        .attr("transform", `translate(${tx},${ty})`)
        .style("display", null)
    })

    overlay.on("mouseleave", () => {
      tooltipLine.style("display", "none")
      tooltipG.style("display", "none")
    })
  }, [viewMode, globalTrendData, dimensions, currentGeneration, scaleMode, t])

  // D3 rendering: instance trend
  useEffect(() => {
    if (viewMode !== "instance") return
    if (!svgRef.current || dimensions.width === 0) return

    const svg = d3.select(svgRef.current)
    svg.selectAll("*").remove()

    const { width, height } = dimensions

    if (instanceTrendData.length === 0) {
      svg
        .append("text")
        .attr("x", width / 2)
        .attr("y", height / 2)
        .attr("text-anchor", "middle")
        .attr("class", "fill-muted-foreground text-sm")
        .text(
          selectedNodeNames.size === 0
            ? t("evolution.panel.trend.noSelection")
            : t("evolution.panel.trend.noData"),
        )
      return
    }

    const innerH = height - MARGIN.top - MARGIN.bottom
    if (innerH <= 0) return

    const allScores = instanceTrendData.flatMap((line) =>
      line.points.map((p) => p.rawScore),
    )

    const yScale = buildYScale(allScores, [height - MARGIN.bottom, MARGIN.top], scaleMode)

    const g = svg.append("g")

    // Derive a left margin wide enough for the actual y-axis labels so that
    // long numeric ticks are never clipped at the chart's left edge.
    const marginLeft = measureAxisLeftMargin(g, yScale, 5)
    const innerW = width - marginLeft - MARGIN.right
    if (innerW <= 0) return

    const xScale = d3
      .scaleLinear()
      .domain([0, Math.max(currentGeneration, 1)])
      .range([marginLeft, width - MARGIN.right])

    // grid
    const yTicks = yScale.ticks(5)
    g.selectAll(".grid-line")
      .data(yTicks)
      .join("line")
      .attr("x1", marginLeft)
      .attr("x2", width - MARGIN.right)
      .attr("y1", (d) => yScale(d))
      .attr("y2", (d) => yScale(d))
      .attr("stroke", "currentColor")
      .attr("class", "text-border")
      .attr("stroke-opacity", 0.15)

    // axes
    const xAxis = d3
      .axisBottom(xScale)
      .ticks(Math.min(currentGeneration, 10))
      .tickFormat((d) => String(Math.round(d as number)))
    const yAxis = d3.axisLeft(yScale).ticks(5)

    g.append("g")
      .attr("transform", `translate(0,${height - MARGIN.bottom})`)
      .call(xAxis)
      .call((sel) => sel.select(".domain").attr("class", "text-border"))
      .call((sel) =>
        sel.selectAll(".tick text").attr("class", "fill-muted-foreground"),
      )
      .call((sel) => sel.selectAll(".tick line").attr("class", "text-border"))

    g.append("g")
      .attr("transform", `translate(${marginLeft},0)`)
      .call(yAxis)
      .call((sel) => sel.select(".domain").attr("class", "text-border"))
      .call((sel) =>
        sel.selectAll(".tick text").attr("class", "fill-muted-foreground"),
      )
      .call((sel) => sel.selectAll(".tick line").attr("class", "text-border"))

    // axis label
    g.append("text")
      .attr("x", width / 2)
      .attr("y", height - 4)
      .attr("text-anchor", "middle")
      .attr("class", "fill-muted-foreground")
      .style("font-size", "10px")
      .text(t("evolution.panel.trend.generation"))

    // lines
    const lineGen = d3
      .line<{ generation: number; rawScore: number }>()
      .x((d) => xScale(d.generation))
      .y((d) => yScale(d.rawScore))
      .curve(d3.curveMonotoneX)

    for (const line of instanceTrendData) {
      g.append("path")
        .datum(line.points)
        .attr("fill", "none")
        .attr("stroke", line.color)
        .attr("stroke-width", 1.5)
        .attr("d", lineGen)

      g.selectAll(null)
        .data(line.points)
        .join("circle")
        .attr("cx", (d) => xScale(d.generation))
        .attr("cy", (d) => yScale(d.rawScore))
        .attr("r", 2.5)
        .attr("fill", line.color)
    }

    // legend
    const legendG = g
      .append("g")
      .attr("transform", `translate(${marginLeft + 8}, ${MARGIN.top - 12})`)

    let lx = 0
    for (const line of instanceTrendData) {
      legendG
        .append("circle")
        .attr("cx", lx + 4)
        .attr("cy", 0)
        .attr("r", 3)
        .attr("fill", line.color)
      legendG
        .append("text")
        .attr("x", lx + 10)
        .attr("y", 4)
        .attr("class", "fill-muted-foreground")
        .style("font-size", "9px")
        .text(line.name)
      lx += 10 + line.name.length * 7 + 12
      if (lx > innerW - 40) break
    }

    // tooltip
    const tooltipLine = g
      .append("line")
      .attr("stroke", "currentColor")
      .attr("class", "text-muted-foreground")
      .attr("stroke-opacity", 0.3)
      .attr("stroke-dasharray", "3,3")
      .style("display", "none")

    const tooltipG = g
      .append("g")
      .style("display", "none")
      .style("pointer-events", "none")

    tooltipG
      .append("rect")
      .attr("rx", 4)
      .attr("ry", 4)
      .attr("class", "fill-card stroke-border")
      .attr("stroke-width", 1)

    const tooltipText = tooltipG
      .append("text")
      .attr("class", "fill-foreground")
      .style("font-size", "10px")

    const overlay = g
      .append("rect")
      .attr("x", marginLeft)
      .attr("y", MARGIN.top)
      .attr("width", innerW)
      .attr("height", innerH)
      .attr("fill", "transparent")

    overlay.on("mousemove", (event: MouseEvent) => {
      const [mx] = d3.pointer(event)
      const gen = Math.round(xScale.invert(mx))

      const x = xScale(gen)
      tooltipLine
        .attr("x1", x)
        .attr("x2", x)
        .attr("y1", MARGIN.top)
        .attr("y2", height - MARGIN.bottom)
        .style("display", null)

      tooltipText.selectAll("tspan").remove()
      tooltipText
        .append("tspan")
        .attr("x", 8)
        .attr("dy", 14)
        .style("font-weight", "600")
        .text(`${t("evolution.panel.trend.generation")} ${gen}`)

      for (const line of instanceTrendData) {
        const pt = line.points.find((p) => p.generation === gen)
        if (pt) {
          tooltipText
            .append("tspan")
            .attr("x", 8)
            .attr("dy", 14)
            .attr("fill", line.color)
            .text(`${line.name}: ${formatScore(pt.rawScore)}`)
        }
      }

      const bbox = tooltipText.node()!.getBBox()
      tooltipG
        .select("rect")
        .attr("width", bbox.width + 16)
        .attr("height", bbox.height + 10)

      const tx = x + 12 + bbox.width + 16 > width ? x - bbox.width - 28 : x + 12
      const ty = MARGIN.top + 4
      tooltipG
        .attr("transform", `translate(${tx},${ty})`)
        .style("display", null)
    })

    overlay.on("mouseleave", () => {
      tooltipLine.style("display", "none")
      tooltipG.style("display", "none")
    })
  }, [
    viewMode,
    instanceTrendData,
    dimensions,
    currentGeneration,
    selectedNodeNames,
    scaleMode,
    t,
  ])

  return (
    <div className="flex h-full w-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-2 py-1">
        {/* Sub-view toggle */}
        <div className="flex items-center gap-0.5">
          {SUB_VIEWS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setViewMode(opt.value)}
              className={cn(
                "px-2 py-0.5 text-xs rounded transition-all duration-200",
                viewMode === opt.value
                  ? "text-primary bg-primary/10"
                  : "text-muted-foreground hover:text-secondary-foreground hover:bg-accent/30",
              )}
            >
              {t(opt.labelKey)}
            </button>
          ))}
        </div>

        {/* Y-axis scale toggle: log (symlog) compresses low outliers so the rest
            of the series is no longer flattened into a straight line. */}
        <div className="flex items-center gap-0.5 border-l border-border/40 pl-2">
          {(["linear", "log"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setScaleMode(m)}
              title={t(`evolution.panel.trend.scale.${m}Hint`, "")}
              className={cn(
                "px-2 py-0.5 text-xs rounded transition-all duration-200",
                scaleMode === m
                  ? "text-primary bg-primary/10"
                  : "text-muted-foreground hover:text-secondary-foreground hover:bg-accent/30",
              )}
            >
              {t(`evolution.panel.trend.scale.${m}`)}
            </button>
          ))}
        </div>

        {/* Node selector (instance mode only) */}
        {viewMode === "instance" && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-border/50 text-muted-foreground hover:text-primary hover:border-primary/30 transition-all duration-200"
              >
                {t("evolution.panel.trend.selectNodes")}
                {selectedNodeNames.size > 0 && (
                  <span className="text-primary ml-0.5">
                    (
                    {t("evolution.panel.trend.selectedCount", {
                      count: selectedNodeNames.size,
                    })}
                    )
                  </span>
                )}
                <ChevronDown className="size-3 ml-0.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="max-h-60 overflow-y-auto">
              <DropdownMenuLabel className="text-xs">
                {t("evolution.panel.trend.selectNodes")}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <div className="flex gap-2 px-2 py-1">
                <button
                  type="button"
                  onClick={selectAll}
                  className="text-[10px] text-primary hover:underline"
                >
                  {t("evolution.panel.trend.selectAll")}
                </button>
                <button
                  type="button"
                  onClick={clearAll}
                  className="text-[10px] text-muted-foreground hover:underline"
                >
                  {t("evolution.panel.trend.clearAll")}
                </button>
              </div>
              <DropdownMenuSeparator />
              {availableNodes.map((nodeInfo) => (
                <DropdownMenuCheckboxItem
                  key={nodeInfo.name}
                  checked={selectedNodeNames.has(nodeInfo.name)}
                  onCheckedChange={() => toggleNode(nodeInfo.name)}
                >
                  <span
                    className="size-2 rounded-full mr-1.5 inline-block shrink-0"
                    style={{
                      backgroundColor:
                        ISLAND_COLORS[nodeInfo.island % ISLAND_COLORS.length],
                    }}
                  />
                  <span className="text-xs truncate">{nodeInfo.name}</span>
                  <span className="text-[10px] text-muted-foreground ml-auto pl-2 shrink-0">
                    {nodeInfo.generationCount}
                    {t("evolution.panel.trend.generation")}
                  </span>
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Chart area */}
      <div ref={chartContainerRef} className="flex-1 min-h-0 w-full">
        <svg ref={svgRef} width={dimensions.width} height={dimensions.height} />
      </div>
    </div>
  )
}
