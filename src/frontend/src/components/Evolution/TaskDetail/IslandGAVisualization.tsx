import * as d3 from "d3"
import { Check, Gauge, Minus, Network, Plus } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { type SelectedNodeInfo, useEvolution } from "@/hooks/useEvolution"
import { cn, formatScore } from "@/lib/utils"
import { getCSSVar, getPrimaryRGB } from "@/utils/theme-colors"
import {
  EMPTY_DATA,
  type GANode,
  ISLAND_COLORS,
  type IslandGAData,
} from "./island-ga-mock-data"
import {
  computeNodeClassifications,
  type NodeClassification,
} from "./node-classification"

// Layout constants
const MARGIN = { top: 60, right: 40, bottom: 40, left: 120 }
const GEN_WIDTH = 160
const NODE_SPACING = 26 // minimum vertical spacing between nodes
const ISLAND_PADDING = 24 // vertical padding top+bottom inside each island lane
const NODE_RADIUS = 6
const NODE_RADIUS_HOVER = 9

/** Render quality tiers. Higher tiers trade visual fidelity for performance. */
export type RenderQuality = "high" | "balanced" | "performance"
/** User-facing selection: an explicit tier or "auto" (resolved by node count). */
export type QualityMode = "auto" | RenderQuality

const QUALITY_STORAGE_KEY = "llm4ad.evolution.renderQuality"
const QUALITY_OPTIONS: QualityMode[] = [
  "auto",
  "high",
  "balanced",
  "performance",
]

/** Auto-tier thresholds based on total node count (scored + unscored). */
function resolveQuality(mode: QualityMode, nodeCount: number): RenderQuality {
  if (mode !== "auto") return mode
  if (nodeCount > 3000) return "performance"
  if (nodeCount >= 400) return "balanced"
  return "high"
}

function readQualityMode(): QualityMode {
  if (typeof window === "undefined") return "auto"
  const saved = window.localStorage.getItem(QUALITY_STORAGE_KEY)
  if (
    saved === "high" ||
    saved === "balanced" ||
    saved === "performance" ||
    saved === "auto"
  ) {
    return saved
  }
  return "auto"
}

function getNodeColor(island: number): string {
  return ISLAND_COLORS[island % ISLAND_COLORS.length]
}

function diamondPath(r: number): string {
  return `M0,${-r} L${r},0 L0,${r} L${-r},0 Z`
}

interface NodePosition {
  node: GANode
  x: number
  y: number
}

/** Compute per-island max population, return heights array and cumulative Y offsets */
function computeIslandHeights(data: IslandGAData): {
  heights: number[]
  yOffsets: number[]
} {
  const maxPop: number[] = new Array(data.islandCount).fill(0)

  // Count nodes per (gen, island) and track max per island
  const counts = new Map<string, number>()
  for (const node of data.nodes) {
    const key = `${node.generation}-${node.island}`
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  for (const node of data.unscoredNodes) {
    const key = `${node.generation}-${node.island}`
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  for (const [key, count] of counts) {
    const island = parseInt(key.split("-")[1], 10)
    if (count > maxPop[island]) maxPop[island] = count
  }

  // Height = max_pop * NODE_SPACING + ISLAND_PADDING
  const heights = maxPop.map(
    (pop) => Math.max(pop, 3) * NODE_SPACING + ISLAND_PADDING,
  )
  const yOffsets: number[] = [0]
  for (let i = 1; i < heights.length; i++) {
    yOffsets.push(yOffsets[i - 1] + heights[i - 1])
  }
  return { heights, yOffsets }
}

function computeLayout(
  data: IslandGAData,
  heights: number[],
  yOffsets: number[],
): NodePosition[] {
  const positions: NodePosition[] = []
  const groups = new Map<string, GANode[]>()
  for (const node of data.nodes) {
    const key = `${node.generation}-${node.island}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(node)
  }
  for (const node of data.unscoredNodes) {
    const key = `${node.generation}-${node.island}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(node)
  }

  for (const [, groupNodes] of groups) {
    const gen = groupNodes[0].generation
    const island = groupNodes[0].island
    const x0 = MARGIN.left + gen * GEN_WIDTH + GEN_WIDTH / 2
    const laneY = MARGIN.top + yOffsets[island]
    const laneH = heights[island]
    const usableH = laneH - ISLAND_PADDING
    const count = groupNodes.length

    groupNodes.forEach((node, i) => {
      const yOffset =
        count === 1
          ? laneH / 2
          : ISLAND_PADDING / 2 + (i / (count - 1)) * usableH
      positions.push({ node, x: x0, y: laneY + yOffset })
    })
  }

  return positions
}

// --- Island icon SVG path generators ---
function drawIslandIcon(
  g: d3.Selection<SVGGElement, unknown, null, undefined>,
  island: number,
  color: string,
) {
  const iconG = g.append("g").attr("class", "island-icon")
  const c = color

  switch (island % 10) {
    case 0: // Volcano island
      // Water line
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 18)
        .attr("ry", 5)
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Mountain body
      iconG
        .append("path")
        .attr("d", "M-14,10 L-4,-14 L0,-8 L4,-14 L14,10 Z")
        .attr("fill", `${c}15`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.6)
      // Smoke puffs
      iconG
        .append("circle")
        .attr("cx", 0)
        .attr("cy", -18)
        .attr("r", 3)
        .attr("fill", c)
        .attr("opacity", 0.15)
      iconG
        .append("circle")
        .attr("cx", 3)
        .attr("cy", -22)
        .attr("r", 2)
        .attr("fill", c)
        .attr("opacity", 0.1)
      break
    case 1: // Palm tree island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 16)
        .attr("ry", 5)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Trunk
      iconG
        .append("path")
        .attr("d", "M0,8 Q2,-2 -1,-12")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1.5)
        .attr("opacity", 0.5)
      // Leaves
      iconG
        .append("path")
        .attr("d", "M-1,-12 Q-12,-16 -16,-10")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.4)
      iconG
        .append("path")
        .attr("d", "M-1,-12 Q8,-20 16,-14")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.4)
      iconG
        .append("path")
        .attr("d", "M-1,-12 Q-6,-22 -2,-24")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.4)
      break
    case 2: // Mountain island with sun
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 18)
        .attr("ry", 5)
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      iconG
        .append("path")
        .attr("d", "M-16,10 L-6,-10 L0,-2 L8,-14 L16,10 Z")
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.5)
      // Sun
      iconG
        .append("circle")
        .attr("cx", -10)
        .attr("cy", -16)
        .attr("r", 4)
        .attr("fill", c)
        .attr("opacity", 0.15)
      break
    case 3: // Lighthouse island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 14)
        .attr("ry", 4)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Lighthouse body
      iconG
        .append("path")
        .attr("d", "M-4,10 L-3,-8 L3,-8 L4,10 Z")
        .attr("fill", `${c}12`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.5)
      // Top
      iconG
        .append("rect")
        .attr("x", -4)
        .attr("y", -11)
        .attr("width", 8)
        .attr("height", 3)
        .attr("rx", 1)
        .attr("fill", c)
        .attr("opacity", 0.3)
      // Light rays
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -14)
        .attr("x2", -8)
        .attr("y2", -20)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.25)
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -14)
        .attr("x2", 8)
        .attr("y2", -20)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.25)
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -14)
        .attr("x2", 0)
        .attr("y2", -22)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.25)
      break
    case 4: // Coral/reef island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 8)
        .attr("rx", 18)
        .attr("ry", 6)
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Coral branches
      iconG
        .append("path")
        .attr("d", "M-6,6 L-8,-4 M-8,-4 L-12,-8 M-8,-4 L-4,-10")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      iconG
        .append("path")
        .attr("d", "M4,6 L6,-2 M6,-2 L2,-8 M6,-2 L10,-6")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      // Bubbles
      iconG
        .append("circle")
        .attr("cx", -2)
        .attr("cy", -14)
        .attr("r", 2)
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 0.6)
        .attr("opacity", 0.25)
      iconG
        .append("circle")
        .attr("cx", 3)
        .attr("cy", -18)
        .attr("r", 1.5)
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 0.6)
        .attr("opacity", 0.2)
      break
    case 5: // Snowcap island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 18)
        .attr("ry", 5)
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Mountain body
      iconG
        .append("path")
        .attr("d", "M-14,10 L0,-16 L14,10 Z")
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.5)
      // Snow cap
      iconG
        .append("path")
        .attr("d", "M-5,-6 L0,-16 L5,-6 Q2,-4 0,-5 Q-2,-4 -5,-6 Z")
        .attr("fill", c)
        .attr("opacity", 0.25)
      // Snowflakes
      iconG
        .append("circle")
        .attr("cx", -8)
        .attr("cy", -14)
        .attr("r", 1.2)
        .attr("fill", c)
        .attr("opacity", 0.2)
      iconG
        .append("circle")
        .attr("cx", 6)
        .attr("cy", -18)
        .attr("r", 1)
        .attr("fill", c)
        .attr("opacity", 0.15)
      break
    case 6: // Castle island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 16)
        .attr("ry", 5)
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Castle body
      iconG
        .append("rect")
        .attr("x", -8)
        .attr("y", -4)
        .attr("width", 16)
        .attr("height", 14)
        .attr("rx", 1)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.5)
      // Battlement
      iconG
        .append("rect")
        .attr("x", -10)
        .attr("y", -8)
        .attr("width", 4)
        .attr("height", 4)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.5)
      iconG
        .append("rect")
        .attr("x", -2)
        .attr("y", -8)
        .attr("width", 4)
        .attr("height", 4)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.5)
      iconG
        .append("rect")
        .attr("x", 6)
        .attr("y", -8)
        .attr("width", 4)
        .attr("height", 4)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.5)
      // Tower
      iconG
        .append("rect")
        .attr("x", -2)
        .attr("y", -16)
        .attr("width", 4)
        .attr("height", 8)
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.4)
      // Flag
      iconG
        .append("path")
        .attr("d", "M2,-16 L2,-20 L8,-18 L2,-16")
        .attr("fill", c)
        .attr("opacity", 0.3)
      break
    case 7: // Windmill island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 16)
        .attr("ry", 5)
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Tower
      iconG
        .append("path")
        .attr("d", "M-4,10 L-3,-4 L3,-4 L4,10 Z")
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.5)
      // Blades
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -4)
        .attr("x2", -10)
        .attr("y2", -16)
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -4)
        .attr("x2", 10)
        .attr("y2", -16)
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -4)
        .attr("x2", -10)
        .attr("y2", 6)
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -4)
        .attr("x2", 10)
        .attr("y2", 6)
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      // Hub
      iconG
        .append("circle")
        .attr("cx", 0)
        .attr("cy", -4)
        .attr("r", 2)
        .attr("fill", c)
        .attr("opacity", 0.25)
      break
    case 8: // Anchor island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 16)
        .attr("ry", 5)
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Anchor shaft
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -16)
        .attr("x2", 0)
        .attr("y2", 6)
        .attr("stroke", c)
        .attr("stroke-width", 1.5)
        .attr("opacity", 0.5)
      // Crossbar
      iconG
        .append("line")
        .attr("x1", -8)
        .attr("y1", -10)
        .attr("x2", 8)
        .attr("y2", -10)
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      // Ring
      iconG
        .append("circle")
        .attr("cx", 0)
        .attr("cy", -18)
        .attr("r", 3)
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.35)
      // Flukes
      iconG
        .append("path")
        .attr("d", "M0,6 Q-10,8 -12,2")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      iconG
        .append("path")
        .attr("d", "M0,6 Q10,8 12,2")
        .attr("fill", "none")
        .attr("stroke", c)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.4)
      break
    case 9: // Crystal island
      iconG
        .append("ellipse")
        .attr("cx", 0)
        .attr("cy", 10)
        .attr("rx", 16)
        .attr("ry", 5)
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.3)
      // Large crystal
      iconG
        .append("path")
        .attr("d", "M-3,8 L-5,-8 L0,-16 L5,-8 L3,8 Z")
        .attr("fill", `${c}10`)
        .attr("stroke", c)
        .attr("stroke-width", 1)
        .attr("opacity", 0.5)
      // Facet line
      iconG
        .append("line")
        .attr("x1", 0)
        .attr("y1", -16)
        .attr("x2", 0)
        .attr("y2", 8)
        .attr("stroke", c)
        .attr("stroke-width", 0.6)
        .attr("opacity", 0.2)
      // Small crystal left
      iconG
        .append("path")
        .attr("d", "M-8,8 L-9,-2 L-6,-6 L-3,-2 L-4,8 Z")
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.35)
      // Small crystal right
      iconG
        .append("path")
        .attr("d", "M6,8 L5,0 L8,-4 L11,0 L10,8 Z")
        .attr("fill", `${c}08`)
        .attr("stroke", c)
        .attr("stroke-width", 0.8)
        .attr("opacity", 0.35)
      // Sparkle
      iconG
        .append("circle")
        .attr("cx", -2)
        .attr("cy", -10)
        .attr("r", 1)
        .attr("fill", c)
        .attr("opacity", 0.3)
      break
  }
}

export type ClassificationFilter =
  | "isElite"
  | "isIslandGenBest"
  | "isGlobalGenBest"
  | "isIslandOverallBest"
  | "isGlobalBest"

interface DrawOptions {
  currentGeneration: number
  selectedNodeIds: Set<string>
  classifications: Map<string, NodeClassification>
  activeFilters: Set<ClassificationFilter>
  quality: RenderQuality
  onNodeClick: (node: GANode, event: MouseEvent) => void
  onBlankClick: () => void
  labels: {
    globalBest: string
    globalGenBest: string
    islandBest: string
    generationBest: string
    eliteRetained: string
  }
}

function drawVisualization(
  svgEl: SVGSVGElement,
  data: IslandGAData,
  positions: NodePosition[],
  heights: number[],
  yOffsets: number[],
  options: DrawOptions,
) {
  const {
    currentGeneration,
    selectedNodeIds,
    classifications,
    activeFilters,
    quality,
    onNodeClick,
    onBlankClick,
    labels,
  } = options
  const svg = d3.select(svgEl)

  // Read theme colors once per render
  const primaryRGB = getPrimaryRGB()
  const primaryColor = getCSSVar("--primary") || "#00d4ff"
  const borderColor = getCSSVar("--border") || "#1e3a5f"
  const cardColor = getCSSVar("--card") || "#0d1a2d"
  const foregroundColor = getCSSVar("--foreground") || "#e0e8ff"
  const mutedColor = getCSSVar("--muted") || "#0a1628"
  const isDark = document.documentElement.classList.contains("dark")

  const totalH_content =
    yOffsets.length > 0
      ? yOffsets[yOffsets.length - 1] + heights[heights.length - 1]
      : 0
  const totalW =
    MARGIN.left + (data.maxGeneration + 1) * GEN_WIDTH + MARGIN.right
  const totalH = MARGIN.top + totalH_content + MARGIN.bottom

  svg.attr("viewBox", `0 0 ${totalW} ${totalH}`)

  // Clear and rebuild
  let canvas = svg.select<SVGGElement>("g.canvas")
  if (canvas.empty()) {
    canvas = svg.append("g").attr("class", "canvas")
  }
  canvas.selectAll("*").remove()

  // Defs: glow filter. Only built for the "high" tier — the Gaussian blur is
  // the single most expensive per-node effect at scale.
  const useGlow = quality === "high"
  let defs = svg.select<SVGDefsElement>("defs")
  if (defs.empty()) {
    defs = svg.append("defs")
  }
  defs.selectAll("*").remove()

  if (useGlow) {
    const filter = defs
      .append("filter")
      .attr("id", "node-glow")
      .attr("x", "-75%")
      .attr("y", "-75%")
      .attr("width", "250%")
      .attr("height", "250%")
    filter
      .append("feGaussianBlur")
      .attr("stdDeviation", "3")
      .attr("result", "blur")
    filter.append("feMerge").call((m) => {
      m.append("feMergeNode").attr("in", "blur")
      m.append("feMergeNode").attr("in", "SourceGraphic")
    })
  }

  // Position lookup
  const posMap = new Map<string, NodePosition>()
  for (const p of positions) posMap.set(p.node.id, p)

  // --- Clickable background for deselect ---
  canvas
    .append("rect")
    .attr("x", 0)
    .attr("y", 0)
    .attr("width", totalW)
    .attr("height", totalH)
    .attr("fill", "transparent")
    .style("cursor", "default")
    .on("click", () => onBlankClick())

  // --- Background grid ---
  const gridG = canvas.append("g").attr("class", "grid")

  for (let i = 0; i < data.islandCount; i++) {
    const y = MARGIN.top + yOffsets[i]
    const h = heights[i]
    const color = ISLAND_COLORS[i % ISLAND_COLORS.length]

    // Lane background
    gridG
      .append("rect")
      .attr("x", MARGIN.left - 10)
      .attr("y", y)
      .attr("width", (data.maxGeneration + 1) * GEN_WIDTH + 20)
      .attr("height", h)
      .attr(
        "fill",
        isDark
          ? i % 2 === 0
            ? `rgba(${primaryRGB},0.015)`
            : `rgba(${primaryRGB},0.03)`
          : "transparent",
      )
      .attr("rx", 4)

    // Separator line
    if (i > 0) {
      gridG
        .append("line")
        .attr("x1", MARGIN.left - 10)
        .attr("y1", y)
        .attr("x2", MARGIN.left + (data.maxGeneration + 1) * GEN_WIDTH + 10)
        .attr("y2", y)
        .attr("stroke", `rgba(${primaryRGB},0.08)`)
        .attr("stroke-width", 1)
    }

    // Island icon (left of label)
    const iconG = gridG
      .append("g")
      .attr("transform", `translate(${MARGIN.left - 70},${y + h / 2})`)
    drawIslandIcon(iconG, i, color)

    // "island" tag below icon — clarifies the visual is an island marker
    gridG
      .append("text")
      .attr("x", MARGIN.left - 70)
      .attr("y", y + h / 2 + 26)
      .attr("text-anchor", "middle")
      .attr("font-size", 9)
      .attr("font-weight", 500)
      .attr("fill", color)
      .attr("opacity", 0.55)
      .attr("letter-spacing", 0.5)
      .text("ISLAND")

    // Island label (below "island" tag)
    gridG
      .append("text")
      .attr("x", MARGIN.left - 70)
      .attr("y", y + h / 2 + 40)
      .attr("text-anchor", "middle")
      .attr("font-size", 10)
      .attr("font-weight", 600)
      .attr("fill", color)
      .attr("opacity", 0.8)
      .text(data.islandIds[i] || `Island ${i}`)
  }

  // Generation column lines and labels
  const totalIslandH = totalH_content
  for (let g = 0; g <= data.maxGeneration; g++) {
    const x = MARGIN.left + g * GEN_WIDTH + GEN_WIDTH / 2

    gridG
      .append("line")
      .attr("x1", x)
      .attr("y1", MARGIN.top - 5)
      .attr("x2", x)
      .attr("y2", MARGIN.top + totalIslandH + 5)
      .attr("stroke", `rgba(${primaryRGB},0.05)`)
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "4,4")

    gridG
      .append("text")
      .attr("x", x)
      .attr("y", MARGIN.top - 20)
      .attr("text-anchor", "middle")
      .attr("font-size", 10)
      .attr("font-weight", 500)
      .attr("fill", g <= currentGeneration ? primaryColor : mutedColor)
      .attr("opacity", g <= currentGeneration ? 0.9 : 0.4)
      .text(`Gen ${g}`)
  }

  // --- Connections ---
  const linksG = canvas.append("g").attr("class", "links")

  // Resolve the visual style of an edge. Returned values are identical to the
  // historical per-edge styling so high/balanced/performance look the same.
  const edgeStroke = (
    isHighlighted: boolean,
    isActive: boolean,
    isCrossIsland: boolean,
  ): string =>
    isHighlighted
      ? isCrossIsland
        ? "rgba(255,165,0,0.85)"
        : `rgba(${primaryRGB},0.85)`
      : isActive
        ? isCrossIsland
          ? "rgba(255,165,0,0.4)"
          : `rgba(${primaryRGB},0.32)`
        : `rgba(${primaryRGB},0.1)`

  // Clear any hover-link handlers from a previous performance-tier draw so they
  // don't linger after switching to high/balanced.
  if (quality !== "performance") {
    svg.on("mousemove.hoverlinks", null).on("mouseleave.hoverlinks", null)
  }

  // One record per edge. Style fields feed the merge buckets; island endpoints
  // drive the performance-tier per-island hover reveal.
  interface EdgeRecord {
    d: string
    stroke: string
    width: number
    dash: string
    opacity: number
    islandA: number
    islandB: number
    isHighlighted: boolean
  }

  // Bucket edges sharing an identical style into a single merged <path> to cut
  // the link DOM count from O(edges) to O(distinct styles).
  const appendMerged = (
    group: d3.Selection<SVGGElement, unknown, null, undefined>,
    edges: EdgeRecord[],
  ) => {
    const buckets = new Map<
      string,
      {
        stroke: string
        width: number
        dash: string
        opacity: number
        d: string[]
      }
    >()
    for (const e of edges) {
      const key = `${e.stroke}|${e.width}|${e.dash}|${e.opacity}`
      let bucket = buckets.get(key)
      if (!bucket) {
        bucket = {
          stroke: e.stroke,
          width: e.width,
          dash: e.dash,
          opacity: e.opacity,
          d: [],
        }
        buckets.set(key, bucket)
      }
      bucket.d.push(e.d)
    }
    for (const bucket of buckets.values()) {
      group
        .append("path")
        .attr("d", bucket.d.join(" "))
        .attr("fill", "none")
        .attr("stroke", bucket.stroke)
        .attr("stroke-width", bucket.width)
        .attr("stroke-dasharray", bucket.dash)
        .attr("opacity", bucket.opacity)
    }
  }

  const allEdges: EdgeRecord[] = []
  for (const pos of positions) {
    const child = pos.node
    const isChildActive = child.generation <= currentGeneration

    for (const parentId of child.parentIds) {
      const parentPos = posMap.get(parentId)
      if (!parentPos) continue

      const parentNode = parentPos.node
      const isParentActive = parentNode.generation <= currentGeneration
      const isActive = isChildActive && isParentActive
      const isCrossIsland = parentNode.island !== child.island
      const involvesUnscored = !!child.unscored || !!parentNode.unscored

      const isHighlighted =
        selectedNodeIds.size > 0
          ? selectedNodeIds.has(child.id) || selectedNodeIds.has(parentId)
          : false

      // Curved link path
      const dx = pos.x - parentPos.x
      const path = `M${parentPos.x},${parentPos.y} C${parentPos.x + dx * 0.4},${parentPos.y} ${pos.x - dx * 0.4},${pos.y} ${pos.x},${pos.y}`

      allEdges.push({
        d: path,
        stroke: edgeStroke(isHighlighted, isActive, isCrossIsland),
        width: isHighlighted ? 2 : 1.2,
        dash: isCrossIsland ? "6,4" : "none",
        opacity: involvesUnscored ? 0.25 : isActive ? 1 : 0.35,
        islandA: child.island,
        islandB: parentNode.island,
        isHighlighted,
      })
    }
  }

  if (quality === "high") {
    // One <path> per edge (highest fidelity, no merging).
    for (const e of allEdges) {
      linksG
        .append("path")
        .attr("d", e.d)
        .attr("fill", "none")
        .attr("stroke", e.stroke)
        .attr("stroke-width", e.width)
        .attr("stroke-dasharray", e.dash)
        .attr("opacity", e.opacity)
    }
  } else if (quality === "balanced") {
    appendMerged(linksG, allEdges)
  } else {
    // Performance: idle links are hidden to skip rasterizing thousands of
    // edges. Only the selected node's links stay on; the rest reveal per island
    // on hover (pointer's Y maps to an island lane).
    appendMerged(
      linksG,
      allEdges.filter((e) => e.isHighlighted),
    )

    const islandEdges = new Map<number, EdgeRecord[]>()
    for (const e of allEdges) {
      if (e.isHighlighted) continue
      const islands =
        e.islandA === e.islandB ? [e.islandA] : [e.islandA, e.islandB]
      for (const isl of islands) {
        let arr = islandEdges.get(isl)
        if (!arr) {
          arr = []
          islandEdges.set(isl, arr)
        }
        arr.push(e)
      }
    }

    const hoverLinksG = canvas
      .append("g")
      .attr("class", "hover-links")
      .style("pointer-events", "none")
    const canvasNode = canvas.node()
    let lastIsland = -1
    svg.on("mousemove.hoverlinks", (event: MouseEvent) => {
      if (!canvasNode) return
      const [, py] = d3.pointer(event, canvasNode)
      let isl = -1
      for (let i = 0; i < data.islandCount; i++) {
        const y0 = MARGIN.top + yOffsets[i]
        if (py >= y0 && py < y0 + heights[i]) {
          isl = i
          break
        }
      }
      if (isl === lastIsland) return
      lastIsland = isl
      hoverLinksG.selectAll("*").remove()
      if (isl >= 0) {
        const es = islandEdges.get(isl)
        if (es) appendMerged(hoverLinksG, es)
      }
    })
    svg.on("mouseleave.hoverlinks", () => {
      lastIsland = -1
      hoverLinksG.selectAll("*").remove()
    })
  }

  // --- Nodes ---
  const nodesG = canvas.append("g").attr("class", "nodes")

  const DEFAULT_CLS: NodeClassification = {
    isElite: false,
    isIslandGenBest: false,
    isGlobalGenBest: false,
    isIslandOverallBest: false,
    isGlobalBest: false,
  }

  // Tooltip is rendered into the shared "g.tooltip" group (created after this
  // loop). Both per-node listeners (high/balanced) and the delegated listener
  // (performance) funnel through here.
  const showTooltip = (
    node: GANode,
    cls: NodeClassification,
    x: number,
    y: number,
  ) => {
    const tooltip = canvas.select<SVGGElement>("g.tooltip")
    if (tooltip.empty()) return
    const tags: string[] = []
    if (cls.isGlobalBest) tags.push(labels.globalBest)
    if (cls.isGlobalGenBest) tags.push(labels.globalGenBest)
    if (cls.isIslandOverallBest) tags.push(labels.islandBest)
    if (cls.isIslandGenBest) tags.push(labels.generationBest)
    if (cls.isElite) tags.push(labels.eliteRetained)
    let label = `${node.name} (${formatScore(node.rawScore)})`
    if (tags.length) label += ` [${tags.join(", ")}]`

    tooltip
      .attr("transform", `translate(${x},${y - NODE_RADIUS - 18})`)
      .style("display", "block")
    tooltip.select("text").text(label)
    const textEl = tooltip.select<SVGTextElement>("text").node()
    if (textEl) {
      const bbox = textEl.getBBox()
      tooltip
        .select("rect")
        .attr("x", bbox.x - 6)
        .attr("y", bbox.y - 3)
        .attr("width", bbox.width + 12)
        .attr("height", bbox.height + 6)
    }
  }
  const hideTooltip = () => canvas.select("g.tooltip").style("display", "none")

  // Grow/restore a node's main shape and toggle its tooltip. Resolves all
  // per-node data from the group's data-id so it works for delegation too.
  // Hover size transitions only run in the "high" tier.
  const animateHover = quality === "high"
  const applyHover = (groupEl: Element, hovered: boolean) => {
    const id = groupEl.getAttribute("data-id")
    if (!id) return
    const p = posMap.get(id)
    if (!p) return
    const node = p.node
    const cls = classifications.get(id) ?? DEFAULT_CLS
    const isActive = node.generation <= currentGeneration
    if (hovered && !isActive) return
    const isSelected = selectedNodeIds.has(id)
    const targetR = hovered
      ? NODE_RADIUS_HOVER
      : isSelected
        ? NODE_RADIUS + 2
        : NODE_RADIUS
    const shape = d3.select(groupEl).select<SVGGraphicsElement>(".node-shape")
    if (animateHover) {
      if (cls.isElite) {
        shape.transition().duration(150).attr("d", diamondPath(targetR))
      } else {
        shape.transition().duration(150).attr("r", targetR)
      }
    } else if (cls.isElite) {
      shape.attr("d", diamondPath(targetR))
    } else {
      shape.attr("r", targetR)
    }
    if (hovered) showTooltip(node, cls, p.x, p.y)
    else hideTooltip()
  }

  for (const pos of positions) {
    const node = pos.node

    // Unscored nodes: gray, non-interactive, no classification rings, no labels.
    // Rendered for visibility only; click/hover are disabled so they never enter selection.
    if (node.unscored) {
      const isActive = node.generation <= currentGeneration
      const g = nodesG
        .append("g")
        .attr("class", "node-group node-unscored")
        .attr("transform", `translate(${pos.x},${pos.y})`)
        .style("cursor", "default")
        .style("pointer-events", "none")
      g.append("circle")
        .attr("r", NODE_RADIUS - 1)
        .attr("fill", borderColor)
        .attr("stroke", mutedColor)
        .attr("stroke-width", 1)
        .attr("opacity", isActive ? 0.45 : 0.18)
      continue
    }

    const isActive = node.generation <= currentGeneration
    const isSelected = selectedNodeIds.has(node.id)
    const cls = classifications.get(node.id) ?? {
      isElite: false,
      isIslandGenBest: false,
      isGlobalGenBest: false,
      isIslandOverallBest: false,
      isGlobalBest: false,
    }

    // Filter dimming: if filters are active and this node matches none, dim it
    const hasFilter = activeFilters.size > 0
    const matchesFilter =
      hasFilter &&
      ((activeFilters.has("isElite") && cls.isElite) ||
        (activeFilters.has("isIslandGenBest") && cls.isIslandGenBest) ||
        (activeFilters.has("isGlobalGenBest") && cls.isGlobalGenBest) ||
        (activeFilters.has("isIslandOverallBest") && cls.isIslandOverallBest) ||
        (activeFilters.has("isGlobalBest") && cls.isGlobalBest))
    const isDimmed = hasFilter && !matchesFilter && !isSelected

    const islandColor = ISLAND_COLORS[node.island % ISLAND_COLORS.length]

    const g = nodesG
      .append("g")
      .attr("class", "node-group")
      .attr("data-id", node.id)
      .attr("transform", `translate(${pos.x},${pos.y})`)
      .style("cursor", "pointer")
      .style("opacity", isDimmed ? 0.12 : 1)

    // --- Classification rings (multiple may stack independently) ---
    // Always drawn in every tier: these mirror the bottom-left legend, so
    // dropping them would make the legend lie about what's on screen.
    // Island-gen best: thin circle ring
    if (cls.isIslandGenBest) {
      g.append("circle")
        .attr("r", NODE_RADIUS + 3)
        .attr("fill", "none")
        .attr("stroke", islandColor)
        .attr("stroke-width", 1.5)
        .attr("opacity", isActive ? 0.7 : 0.1)
    }

    // Island overall best: triangle outline. Suppressed on the global best,
    // which is always also island-best and gets its own crown instead.
    if (cls.isIslandOverallBest && !cls.isGlobalBest) {
      const triR = NODE_RADIUS + 9
      g.append("path")
        .attr(
          "d",
          `M0,${-triR} L${triR * 0.866},${triR * 0.5} L${-triR * 0.866},${triR * 0.5} Z`,
        )
        .attr("fill", "none")
        .attr("stroke", islandColor)
        .attr("stroke-width", 2)
        .attr("stroke-linejoin", "round")
        .attr("opacity", isActive ? 0.85 : 0.15)
    }

    // Global-generation best: inverted triangle outline (vertex down).
    // Suppressed on the global best, which gets its own crown.
    if (cls.isGlobalGenBest && !cls.isGlobalBest) {
      const triR = NODE_RADIUS + 9
      g.append("path")
        .attr(
          "d",
          `M0,${triR} L${triR * 0.866},${-triR * 0.5} L${-triR * 0.866},${-triR * 0.5} Z`,
        )
        .attr("fill", "none")
        .attr("stroke", islandColor)
        .attr("stroke-width", 2)
        .attr("stroke-linejoin", "round")
        .attr("opacity", isActive ? 0.85 : 0.15)
    }

    // Selection ring (placed outside the outermost decoration)
    if (isSelected) {
      const ringOffset =
        cls.isIslandOverallBest || cls.isGlobalGenBest
          ? 12
          : cls.isIslandGenBest
            ? 6
            : 5
      g.append("circle")
        .attr("r", NODE_RADIUS + ringOffset)
        .attr("fill", "none")
        .attr("stroke", islandColor)
        .attr("stroke-width", 2)
        .attr("opacity", 0.6)

      g.append("circle")
        .attr("r", NODE_RADIUS + ringOffset + 4)
        .attr("fill", "none")
        .attr("stroke", islandColor)
        .attr("stroke-width", 1)
        .attr("opacity", 0.25)
    }

    // Main node shape: diamond for elite, circle for normal
    const nodeR = isSelected ? NODE_RADIUS + 2 : NODE_RADIUS
    if (cls.isElite) {
      g.append("path")
        .attr("class", "node-shape")
        .attr("d", diamondPath(nodeR))
        .attr("fill", isActive ? getNodeColor(node.island) : borderColor)
        .attr("stroke", isActive ? islandColor : mutedColor)
        .attr("stroke-width", isSelected ? 2 : 1)
        .attr("opacity", isActive ? 1 : 0.2)
        .attr("filter", useGlow && isActive ? "url(#node-glow)" : "none")
    } else {
      g.append("circle")
        .attr("class", "node-shape")
        .attr("r", nodeR)
        .attr("fill", isActive ? getNodeColor(node.island) : borderColor)
        .attr("stroke", isActive ? islandColor : mutedColor)
        .attr("stroke-width", isSelected ? 2 : 1)
        .attr("opacity", isActive ? 1 : 0.2)
        .attr("filter", useGlow && isActive ? "url(#node-glow)" : "none")
    }

    // Global best: gold crown badge at the top-right corner (drawn after the node so it sits on top).
    if (cls.isGlobalBest) {
      g.append("path")
        .attr(
          "d",
          "M-4.5,3 L-4.5,-2 L-2.2,0.8 L0,-3.6 L2.2,0.8 L4.5,-2 L4.5,3 Z",
        )
        .attr(
          "transform",
          `translate(${NODE_RADIUS + 2},${-(NODE_RADIUS + 3)})`,
        )
        .attr("fill", "#ffbf00")
        .attr("stroke", "#b8860b")
        .attr("stroke-width", 0.6)
        .attr("stroke-linejoin", "round")
        .attr("opacity", isActive ? 1 : 0.2)
        .attr("filter", useGlow && isActive ? "url(#node-glow)" : "none")
    }

    // 活跃且选中的节点显示分数标签
    if (isSelected && isActive && node.rawScore != null) {
      // Stack the label above the topmost decoration (triangle > ring)
      const labelY =
        cls.isIslandOverallBest || cls.isGlobalGenBest
          ? -(NODE_RADIUS + 18)
          : -(NODE_RADIUS + 10)
      g.append("text")
        .attr("y", labelY)
        .attr("text-anchor", "middle")
        .attr("font-size", 9)
        .attr("font-weight", 600)
        .attr("fill", islandColor)
        .text(formatScore(node.rawScore))
    }

    // Hover and click interactions. In the "performance" tier these are bound
    // once on the parent group via delegation (see after the loop) instead of
    // one closure per node.
    if (quality !== "performance") {
      g.on("mouseenter", function () {
        applyHover(this as Element, true)
      })
        .on("mouseleave", function () {
          applyHover(this as Element, false)
        })
        .on("click", (event: MouseEvent) => {
          event.stopPropagation()
          if (isActive) onNodeClick(node, event)
        })
    }
  }

  // Performance tier: one delegated listener for all nodes instead of N.
  if (quality === "performance") {
    const groupFromEvent = (event: Event): SVGGElement | null => {
      const el = (event.target as Element).closest<SVGGElement>(".node-group")
      if (!el || el.classList.contains("node-unscored")) return null
      return el
    }
    nodesG
      .on("mouseover", (event: MouseEvent) => {
        const el = groupFromEvent(event)
        if (el) applyHover(el, true)
      })
      .on("mouseout", (event: MouseEvent) => {
        const el = groupFromEvent(event)
        if (el) applyHover(el, false)
      })
      .on("click", (event: MouseEvent) => {
        const el = groupFromEvent(event)
        if (!el) return
        const id = el.getAttribute("data-id")
        if (!id) return
        const p = posMap.get(id)
        if (!p) return
        if (p.node.generation <= currentGeneration) {
          event.stopPropagation()
          onNodeClick(p.node, event)
        }
      })
  }

  // --- Tooltip group (on top, inside canvas for zoom) ---
  const tooltipG = canvas
    .append("g")
    .attr("class", "tooltip")
    .style("display", "none")
    .style("pointer-events", "none")
  tooltipG
    .append("rect")
    .attr("fill", cardColor)
    .attr("stroke", `rgba(${primaryRGB},0.3)`)
    .attr("stroke-width", 1)
    .attr("rx", 4)
  tooltipG
    .append("text")
    .attr("text-anchor", "middle")
    .attr("font-size", 10)
    .attr("fill", foregroundColor)
}

/** Dropdown to pick the render-quality tier (mirrors LayoutSelector styling). */
function RenderQualitySelector({
  mode,
  effective,
  onChange,
}: {
  mode: QualityMode
  effective: RenderQuality
  onChange: (mode: QualityMode) => void
}) {
  const { t } = useTranslation()
  const triggerLabel =
    mode === "auto"
      ? t("evolution.renderQuality.autoHint", {
          level: t(`evolution.renderQuality.${effective}`),
        })
      : t("evolution.renderQuality.label")
  return (
    <DropdownMenu>
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="p-1.5 rounded bg-card/90 border border-border/50
                  text-muted-foreground hover:text-primary hover:border-primary/30
                  transition-all duration-200 outline-none"
                aria-label={t("evolution.renderQuality.label")}
              >
                <Gauge className="size-3.5" />
              </button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs leading-relaxed">
            {triggerLabel}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DropdownMenuContent
        align="end"
        className="bg-popover border-border/60 w-64"
      >
        {QUALITY_OPTIONS.map((opt) => {
          const isSelected = mode === opt
          return (
            <DropdownMenuItem
              key={opt}
              onClick={() => onChange(opt)}
              className={cn(
                "flex flex-col items-start gap-0.5 py-1.5 cursor-pointer",
                isSelected && "bg-accent/40",
              )}
            >
              <div className="flex w-full items-center gap-1.5">
                <span
                  className={cn(
                    "text-xs font-medium",
                    isSelected ? "text-primary" : "text-foreground",
                  )}
                >
                  {t(`evolution.renderQuality.${opt}`)}
                </span>
                {opt === "auto" && (
                  <span className="text-[10px] text-muted-foreground">
                    · {t(`evolution.renderQuality.${effective}`)}
                  </span>
                )}
                {isSelected && (
                  <Check className="size-3.5 text-primary ml-auto shrink-0" />
                )}
              </div>
              <span className="text-[10px] leading-snug text-muted-foreground">
                {t(`evolution.renderQuality.desc.${opt}`)}
              </span>
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default function IslandGAVisualization() {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const [zoomLevel, setZoomLevel] = useState(100)
  const [activeFilters, setActiveFilters] = useState<Set<ClassificationFilter>>(
    new Set(),
  )
  const [qualityMode, setQualityMode] = useState<QualityMode>(readQualityMode)
  const { t } = useTranslation()

  const {
    currentGeneration,
    selectedNodes,
    setSelectedNodes,
    toggleSelectedNode,
    evolutionData,
    focusNodeRequest,
  } = useEvolution()

  const isEmpty =
    evolutionData.nodes.length + evolutionData.unscoredNodes.length === 0
  const data = isEmpty ? EMPTY_DATA : evolutionData

  // Resolve the active render tier. "auto" picks a tier from the node count.
  const nodeCount = data.nodes.length + data.unscoredNodes.length
  const effectiveQuality = useMemo(
    () => resolveQuality(qualityMode, nodeCount),
    [qualityMode, nodeCount],
  )
  // Read inside the one-shot focus effect without making it a dependency.
  const effectiveQualityRef = useRef(effectiveQuality)
  effectiveQualityRef.current = effectiveQuality

  const handleQualityChange = useCallback((mode: QualityMode) => {
    setQualityMode(mode)
    try {
      window.localStorage.setItem(QUALITY_STORAGE_KEY, mode)
    } catch {
      // Ignore storage failures (private mode / disabled storage).
    }
  }, [])

  const layoutRef = useRef<{
    positions: NodePosition[]
    heights: number[]
    yOffsets: number[]
    classifications: Map<string, NodeClassification>
  }>({ positions: [], heights: [], yOffsets: [], classifications: new Map() })

  // Compute layout once
  useEffect(() => {
    const { heights, yOffsets } = computeIslandHeights(data)
    const positions = computeLayout(data, heights, yOffsets)
    const classifications = computeNodeClassifications(data)
    layoutRef.current = { positions, heights, yOffsets, classifications }
  }, [data])

  const selectedNodeIds = useMemo(
    () => new Set(selectedNodes.map((n) => n.id)),
    [selectedNodes],
  )

  const handleNodeClick = useCallback(
    (node: GANode, event: MouseEvent) => {
      const info: SelectedNodeInfo = {
        id: node.id,
        generation: node.generation,
        island: node.island,
        islandId: node.islandId,
        name: node.name,
        fileName: node.fileName,
        score: node.score,
        rawScore: node.rawScore,
        parentIds: node.parentIds,
      }
      if (event.ctrlKey || event.metaKey) {
        toggleSelectedNode(info)
      } else {
        setSelectedNodes([info])
      }
    },
    [setSelectedNodes, toggleSelectedNode],
  )

  const handleBlankClick = useCallback(() => {
    setSelectedNodes([])
  }, [setSelectedNodes])

  // Initialize SVG and zoom
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    if (!svgRef.current) {
      const svg = d3
        .select(container)
        .append("svg")
        .attr("width", "100%")
        .attr("height", "100%")
        .style("background", "transparent")

      svgRef.current = svg.node()!

      const zoom = d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 16])
        .filter((event: Event) => {
          if (event.type === "wheel") return true
          if (
            event.type === "mousedown" &&
            (event as MouseEvent).button === 0
          ) {
            event.preventDefault()
            return true
          }
          if (event.type === "touchstart" || event.type === "touchmove")
            return true
          return false
        })
        .on("zoom", (event) => {
          const canvasEl = d3.select(svgRef.current!).select("g.canvas")
          canvasEl.attr("transform", event.transform.toString())
          setZoomLevel(Math.round(event.transform.k * 100))
        })

      svg.call(zoom)
      zoomRef.current = zoom

      svg.on("contextmenu", (event: Event) => event.preventDefault())
      svg.on("mousedown", (event: MouseEvent) => {
        if (event.button === 0) event.preventDefault()
      })
    }

    return () => {
      if (svgRef.current) {
        d3.select(svgRef.current).remove()
        svgRef.current = null
        zoomRef.current = null
      }
    }
  }, [])

  // Redraw when state changes
  useEffect(() => {
    if (!svgRef.current) return
    const { positions, heights, yOffsets, classifications } = layoutRef.current

    // No data — clear the canvas (e.g. task switch resets to EMPTY_DATA)
    if (positions.length === 0) {
      const svg = d3.select(svgRef.current)
      svg.on("mousemove.hoverlinks", null).on("mouseleave.hoverlinks", null)
      svg.select("g.canvas").selectAll("*").remove()
      return
    }

    drawVisualization(svgRef.current, data, positions, heights, yOffsets, {
      currentGeneration,
      selectedNodeIds,
      classifications,
      activeFilters,
      quality: effectiveQuality,
      onNodeClick: handleNodeClick,
      onBlankClick: handleBlankClick,
      labels: {
        globalBest: t("evolution.globalBest"),
        globalGenBest: t("evolution.globalGenBest"),
        islandBest: t("evolution.islandBest"),
        generationBest: t("evolution.generationBest"),
        eliteRetained: t("evolution.eliteRetained"),
      },
    })

    // Re-apply zoom transform
    if (zoomRef.current) {
      const currentTransform = d3.zoomTransform(svgRef.current)
      d3.select(svgRef.current)
        .select("g.canvas")
        .attr("transform", currentTransform.toString())
    }
  }, [
    currentGeneration,
    selectedNodeIds,
    handleNodeClick,
    handleBlankClick,
    data,
    activeFilters,
    effectiveQuality,
    t,
  ])

  const toggleFilter = useCallback((key: ClassificationFilter) => {
    setActiveFilters((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const handleZoomIn = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return
    const svg = d3.select(svgRef.current)
    zoomRef.current.scaleBy(svg.transition().duration(300), 1.3)
  }, [])

  const handleZoomOut = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return
    const svg = d3.select(svgRef.current)
    zoomRef.current.scaleBy(svg.transition().duration(300), 0.77)
  }, [])

  const handleZoomReset = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return
    const svg = d3.select(svgRef.current)
    zoomRef.current.transform(svg.transition().duration(300), d3.zoomIdentity)
  }, [])

  // One-shot: respond to focus requests by centering the camera on the node
  // and highlighting it. The selection is replaced only when the request's
  // `select` flag is set (otherwise it is a pure "locate").
  useEffect(() => {
    if (!focusNodeRequest) return
    const svgEl = svgRef.current
    const zoom = zoomRef.current
    if (!svgEl || !zoom) return
    const pos = layoutRef.current.positions.find(
      (p) => p.node.id === focusNodeRequest.id,
    )
    if (!pos) return

    const { node, x, y } = pos
    // Only replace the selection when the request asks for it; a pure "locate"
    // pans/highlights the camera without changing what is selected.
    if (focusNodeRequest.select) {
      setSelectedNodes([
        {
          id: node.id,
          generation: node.generation,
          island: node.island,
          islandId: node.islandId,
          name: node.name,
          fileName: node.fileName,
          score: node.score,
          rawScore: node.rawScore,
          parentIds: node.parentIds,
        },
      ])
    }

    const { width: pxW, height: pxH } = svgEl.getBoundingClientRect()
    // zoom.transform operates in the SVG viewBox coordinate space, not
    // pixels, so center using viewBox dimensions when present.
    const vb = svgEl.viewBox.baseVal
    const vw = vb && vb.width > 0 ? vb.width : pxW
    const vh = vb && vb.height > 0 ? vb.height : pxH
    const k = Math.max(1, d3.zoomTransform(svgEl).k)
    const tx = vw / 2 - x * k
    const ty = vh / 2 - y * k
    const target = d3.zoomIdentity.translate(tx, ty).scale(k)
    const svg = d3.select(svgEl)
    zoom.transform(svg.transition().duration(400), target)

    // Performance tier: keep the camera-center + selection but skip the ~3s
    // ripple animation (recurring transitions are costly on large canvases).
    if (effectiveQualityRef.current === "performance") {
      return () => {
        d3.select(svgEl).select("g.canvas").select("g.focus-effect").remove()
      }
    }

    // setSelectedNodes triggers a redraw that wipes the canvas, so we defer
    // attaching the ~3s ripple highlight until that redraw has committed.
    const HIGHLIGHT_MS = 3000
    let endTimer: number | undefined
    const attachTimer = window.setTimeout(() => {
      const canvas = svg.select<SVGGElement>("g.canvas")
      if (canvas.empty()) return
      canvas.select("g.focus-effect").remove()
      const fxG = canvas
        .append("g")
        .attr("class", "focus-effect")
        .attr("transform", `translate(${x},${y})`)
        .style("pointer-events", "none")
      const primaryRGB = getPrimaryRGB()
      const stroke = `rgba(${primaryRGB},0.9)`
      const fill = `rgba(${primaryRGB},0.18)`

      const halo = fxG
        .append("circle")
        .attr("r", NODE_RADIUS + 4)
        .attr("fill", fill)
        .attr("stroke", stroke)
        .attr("stroke-width", 1.2)
        .attr("opacity", 0.9)
      const pulse = () => {
        halo
          .transition()
          .duration(700)
          .ease(d3.easeSinInOut)
          .attr("r", NODE_RADIUS + 9)
          .attr("opacity", 0.45)
          .transition()
          .duration(700)
          .ease(d3.easeSinInOut)
          .attr("r", NODE_RADIUS + 4)
          .attr("opacity", 0.9)
          .on("end", pulse)
      }
      pulse()

      for (let i = 0; i < 3; i++) {
        fxG
          .append("circle")
          .attr("r", NODE_RADIUS)
          .attr("fill", "none")
          .attr("stroke", stroke)
          .attr("stroke-width", 2)
          .attr("opacity", 0.85)
          .transition()
          .delay(i * 600)
          .duration(1400)
          .ease(d3.easeCubicOut)
          .attr("r", NODE_RADIUS + 36)
          .attr("opacity", 0)
          .attr("stroke-width", 0.4)
          .remove()
      }

      endTimer = window.setTimeout(() => {
        fxG.transition().duration(250).style("opacity", 0).remove()
      }, HIGHLIGHT_MS)
    }, 80)

    return () => {
      window.clearTimeout(attachTimer)
      if (endTimer !== undefined) window.clearTimeout(endTimer)
      d3.select(svgEl).select("g.canvas").select("g.focus-effect").remove()
    }
  }, [focusNodeRequest, setSelectedNodes])

  return (
    <div className="relative w-full h-full overflow-hidden">
      <div ref={containerRef} className="w-full h-full" />

      {isEmpty && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 z-5">
          <div
            className="p-5 rounded-full"
            style={{
              background:
                "radial-gradient(circle, color-mix(in srgb, var(--primary) 8%, transparent) 0%, transparent 70%)",
            }}
          >
            <Network className="size-14 text-border" />
          </div>
          <p className="text-sm text-muted-foreground">
            {t("evolution.visualization.emptyTitle")}
          </p>
          <p className="text-xs text-muted-foreground/70 max-w-[280px] text-center leading-relaxed">
            {t("evolution.visualization.emptyDesc")}
          </p>
        </div>
      )}

      {/* Zoom controls - top right (offset to leave room for fullscreen toggle) */}
      <div className="absolute top-2 right-10 flex flex-row items-center gap-1 z-10">
        <RenderQualitySelector
          mode={qualityMode}
          effective={effectiveQuality}
          onChange={handleQualityChange}
        />
        <button
          type="button"
          onClick={handleZoomIn}
          className="p-1.5 rounded bg-card/90 border border-border/50
            text-muted-foreground hover:text-primary hover:border-primary/30
            transition-all duration-200"
          title={t("evolution.visualization.zoomIn")}
        >
          <Plus className="size-3.5" />
        </button>

        <button
          type="button"
          onClick={handleZoomReset}
          className="px-2 py-1 rounded bg-card/90 border border-border/50
            text-[10px] font-mono text-primary hover:bg-primary/10
            transition-all duration-200 min-w-[48px] text-center"
          title={t("evolution.visualization.resetZoom")}
        >
          {zoomLevel}%
        </button>

        <button
          type="button"
          onClick={handleZoomOut}
          className="p-1.5 rounded bg-card/90 border border-border/50
            text-muted-foreground hover:text-primary hover:border-primary/30
            transition-all duration-200"
          title={t("evolution.visualization.zoomOut")}
        >
          <Minus className="size-3.5" />
        </button>
      </div>

      {/* Legend */}
      <div
        className="absolute bottom-2 left-2 flex flex-col gap-1.5 px-3 py-1.5
        rounded bg-card/90 border border-border/30 z-10"
      >
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <div className="w-5 h-px bg-primary/50" />
            <span className="text-[9px] text-muted-foreground">
              {t("evolution.visualization.sameIslandConnection")}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-5 h-px border-t border-dashed border-[#ffa500]/50" />
            <span className="text-[9px] text-muted-foreground">
              {t("evolution.visualization.crossIslandMigration")}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3 pt-1 border-t border-border/20">
          <button
            type="button"
            onClick={() => toggleFilter("isElite")}
            className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded transition-all duration-200
              ${
                activeFilters.has("isElite")
                  ? "bg-primary/15 ring-1 ring-primary/40"
                  : "hover:bg-accent/30"
              }`}
            title={t("evolution.visualization.filterElite")}
          >
            <div className="size-2 rotate-45 border border-primary/60 bg-primary/20" />
            <span
              className={`text-[9px] ${activeFilters.has("isElite") ? "text-foreground" : "text-muted-foreground"}`}
            >
              {t("evolution.eliteRetained")}
            </span>
          </button>
          <button
            type="button"
            onClick={() => toggleFilter("isIslandGenBest")}
            className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded transition-all duration-200
              ${
                activeFilters.has("isIslandGenBest")
                  ? "bg-primary/15 ring-1 ring-primary/40"
                  : "hover:bg-accent/30"
              }`}
            title={t("evolution.visualization.filterGenBest")}
          >
            <div className="size-2.5 rounded-full border border-primary/60" />
            <span
              className={`text-[9px] ${activeFilters.has("isIslandGenBest") ? "text-foreground" : "text-muted-foreground"}`}
            >
              {t("evolution.generationBest")}
            </span>
          </button>
          <button
            type="button"
            onClick={() => toggleFilter("isIslandOverallBest")}
            className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded transition-all duration-200
              ${
                activeFilters.has("isIslandOverallBest")
                  ? "bg-primary/15 ring-1 ring-primary/40"
                  : "hover:bg-accent/30"
              }`}
            title={t("evolution.visualization.filterIslandBest")}
          >
            <svg
              width="10"
              height="10"
              viewBox="-6 -6 12 12"
              className="shrink-0"
              aria-hidden="true"
            >
              <path
                d="M0,-5 L4.33,2.5 L-4.33,2.5 Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinejoin="round"
                className="text-primary/70"
              />
            </svg>
            <span
              className={`text-[9px] ${activeFilters.has("isIslandOverallBest") ? "text-foreground" : "text-muted-foreground"}`}
            >
              {t("evolution.islandBest")}
            </span>
          </button>
          <button
            type="button"
            onClick={() => toggleFilter("isGlobalGenBest")}
            className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded transition-all duration-200
              ${
                activeFilters.has("isGlobalGenBest")
                  ? "bg-primary/15 ring-1 ring-primary/40"
                  : "hover:bg-accent/30"
              }`}
            title={t("evolution.visualization.filterGlobalGenBest")}
          >
            <svg
              width="10"
              height="10"
              viewBox="-6 -6 12 12"
              className="shrink-0"
              aria-hidden="true"
            >
              <path
                d="M0,5 L4.33,-2.5 L-4.33,-2.5 Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinejoin="round"
                className="text-primary/70"
              />
            </svg>
            <span
              className={`text-[9px] ${activeFilters.has("isGlobalGenBest") ? "text-foreground" : "text-muted-foreground"}`}
            >
              {t("evolution.globalGenBest")}
            </span>
          </button>
          <button
            type="button"
            onClick={() => toggleFilter("isGlobalBest")}
            className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded transition-all duration-200
              ${
                activeFilters.has("isGlobalBest")
                  ? "bg-primary/15 ring-1 ring-primary/40"
                  : "hover:bg-accent/30"
              }`}
            title={t("evolution.visualization.filterGlobalBest")}
          >
            <svg
              width="11"
              height="11"
              viewBox="-6 -6 12 12"
              className="shrink-0"
              aria-hidden="true"
            >
              <path
                d="M-4.5,3 L-4.5,-2 L-2.2,0.8 L0,-3.6 L2.2,0.8 L4.5,-2 L4.5,3 Z"
                fill="currentColor"
                className="text-primary/70"
              />
            </svg>
            <span
              className={`text-[9px] ${activeFilters.has("isGlobalBest") ? "text-foreground" : "text-muted-foreground"}`}
            >
              {t("evolution.globalBest")}
            </span>
          </button>
        </div>
      </div>
    </div>
  )
}
