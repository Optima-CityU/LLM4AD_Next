import type { ResearchGeneratedItem } from "@/client"
import type {
  GANode,
  IslandGAData,
} from "@/components/Evolution/TaskDetail/island-ga-mock-data"

/**
 * 将 research generated 数据转换为 evolution IslandGAData 格式。
 *
 * research generated 数据结构：
 * - items[].data.id, generation, island_id, name
 * - items[].data.evaluation.score/objective/fitness
 * - items[].data.parent_ids
 *
 * evolution IslandGAData 结构：
 * - nodes: 有分数的节点数组
 * - unscoredNodes: 无分数的节点数组
 * - maxGeneration, islandCount, islandIds
 */
export function convertToEvolutionData(
  items: ResearchGeneratedItem[],
): IslandGAData {
  const nodes: GANode[] = []
  const unscoredNodes: GANode[] = []

  let maxGeneration = 0
  const islandSet = new Set<number>()

  // 第一遍：提取所有节点数据
  for (const it of items) {
    const d = it.data as Record<string, unknown> | null
    if (!d) continue

    const evaluation = d.evaluation as
      | Record<string, unknown>
      | null
      | undefined
    const rawScore =
      (evaluation?.score as number | undefined) ??
      (evaluation?.objective as number | undefined) ??
      (evaluation?.fitness as number | undefined)

    const generation = Number(d.generation ?? 0)
    const island = Number(d.island_id ?? 0)
    const id = String(d.id ?? it.name)
    const name = String(d.name ?? d.id ?? it.name)
    const parentIds = Array.isArray(d.parent_ids)
      ? (d.parent_ids as unknown[]).map(String)
      : []

    if (generation > maxGeneration) maxGeneration = generation
    islandSet.add(island)

    const baseNode = {
      id,
      generation,
      island,
      islandId: String(island),
      name,
      fileName: name,
      parentIds,
    }

    if (typeof rawScore === "number") {
      nodes.push({
        ...baseNode,
        rawScore,
        score: 0, // 稍后归一化
      })
    } else {
      unscoredNodes.push({
        ...baseNode,
        rawScore: 0,
        score: 0,
        unscored: true,
      })
    }
  }

  // 第二遍：min-max 归一化分数
  if (nodes.length > 0) {
    let min = Number.POSITIVE_INFINITY
    let max = Number.NEGATIVE_INFINITY
    for (const n of nodes) {
      if (n.rawScore < min) min = n.rawScore
      if (n.rawScore > max) max = n.rawScore
    }
    const range = max - min
    for (const n of nodes) {
      n.score = range > 0 ? (n.rawScore - min) / range : 0.5
    }
  }

  const islandCount = Math.max(1, islandSet.size)
  const islandIds = Array.from(islandSet)
    .sort((a, b) => a - b)
    .map(String)

  return {
    nodes,
    unscoredNodes,
    maxGeneration,
    islandCount,
    islandIds,
  }
}
