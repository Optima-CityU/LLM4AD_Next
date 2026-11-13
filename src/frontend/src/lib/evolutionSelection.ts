import type { GANode } from "@/components/Evolution/TaskDetail/island-ga-mock-data"
import type { SelectedNodeInfo } from "@/hooks/useEvolution"

type NodeFitness = Pick<SelectedNodeInfo, "rawScore" | "unscored">

export function hasNodeFitness(node?: NodeFitness): boolean {
  return !!node && !node.unscored && Number.isFinite(node.rawScore)
}

export function toSelectedNodeInfo(node: GANode): SelectedNodeInfo {
  return {
    id: node.id,
    generation: node.generation,
    island: node.island,
    islandId: node.islandId,
    name: node.name,
    fileName: node.fileName,
    score: node.score,
    rawScore: node.rawScore,
    parentIds: node.parentIds,
    unscored: node.unscored,
  }
}

/** Gray nodes can open diagnostics once visible in the playback. */
export function visibleNodeSelection(node: GANode, generation: number) {
  return node.generation <= generation ? toSelectedNodeInfo(node) : null
}

/** Place scored nodes first without treating an unscored zero as fitness. */
export function compareNodeFitness(a?: NodeFitness, b?: NodeFitness): number {
  const aScored = hasNodeFitness(a)
  const bScored = hasNodeFitness(b)
  if (aScored && bScored) return b!.rawScore - a!.rawScore
  return Number(bScored) - Number(aScored)
}

export function selectionScoreStats(nodes: NodeFitness[]) {
  const scores = nodes.filter(hasNodeFitness).map((node) => node.rawScore)
  if (scores.length === 0) return null
  return {
    max: Math.max(...scores),
    min: Math.min(...scores),
    avg: scores.reduce((sum, score) => sum + score, 0) / scores.length,
  }
}
