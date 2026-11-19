export interface GANode {
  id: string
  generation: number
  island: number
  /** Original island_id from backend */
  islandId: string
  name: string
  fileName: string
  /** Normalized score in [0, 1] for visualization color / bar width */
  score: number
  /** Original score from evaluation (may be any number) */
  rawScore: number
  evaluation?: Record<string, unknown>
  parentIds: string[]
  /** True when this node has no evaluation score; rendered gray and selectable for diagnostics. */
  unscored?: boolean
}

export interface IslandGAData {
  nodes: GANode[]
  /** Nodes available for diagnostic selection; population statistics, classification and normalization use `nodes` only. */
  unscoredNodes: GANode[]
  maxGeneration: number
  islandCount: number
  /** Ordered island_id list, index corresponds to numeric island index */
  islandIds: string[]
}

export const EMPTY_DATA: IslandGAData = {
  nodes: [],
  unscoredNodes: [],
  maxGeneration: 0,
  islandCount: 1,
  islandIds: [],
}

export const ISLAND_COLORS = [
  "#00d4ff", // cyan
  "#ff6b6b", // coral
  "#ffd93d", // gold
  "#6bcb77", // green
  "#a66cff", // purple
  "#ff8c42", // orange
  "#e84393", // pink
  "#00b894", // teal
  "#fd79a8", // rose
  "#74b9ff", // sky blue
]
