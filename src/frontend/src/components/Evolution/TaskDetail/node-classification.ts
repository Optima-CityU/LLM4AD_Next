import type { GANode, IslandGAData } from "./island-ga-mock-data"

export interface NodeClassification {
  isElite: boolean
  isIslandGenBest: boolean
  isGlobalGenBest: boolean
  isIslandOverallBest: boolean
  isGlobalBest: boolean
}

export function computeNodeClassifications(
  data: IslandGAData,
): Map<string, NodeClassification> {
  const map = new Map<string, NodeClassification>()
  const nodeById = new Map<string, GANode>()

  for (const node of data.nodes) {
    nodeById.set(node.id, node)
    map.set(node.id, {
      isElite: false,
      isIslandGenBest: false,
      isGlobalGenBest: false,
      isIslandOverallBest: false,
      isGlobalBest: false,
    })
  }

  // Pass 1: Elite detection - single parent with same name
  for (const node of data.nodes) {
    if (node.parentIds.length === 1) {
      const parent = nodeById.get(node.parentIds[0])
      if (parent && parent.name === node.name) {
        map.get(node.id)!.isElite = true
      }
    }
  }

  // Pass 2: Island-generation best - highest rawScore per (island, generation)
  const genIslandGroups = new Map<string, GANode[]>()
  for (const node of data.nodes) {
    const key = `${node.generation}-${node.island}`
    if (!genIslandGroups.has(key)) genIslandGroups.set(key, [])
    genIslandGroups.get(key)!.push(node)
  }
  for (const [, group] of genIslandGroups) {
    let best = -Infinity
    for (const node of group) {
      if (node.rawScore > best) best = node.rawScore
    }
    for (const node of group) {
      if (node.rawScore === best) {
        map.get(node.id)!.isIslandGenBest = true
      }
    }
  }

  // Pass 3: Global-generation best - highest rawScore per generation across ALL islands
  const genGroups = new Map<number, GANode[]>()
  for (const node of data.nodes) {
    if (!genGroups.has(node.generation)) genGroups.set(node.generation, [])
    genGroups.get(node.generation)!.push(node)
  }
  for (const [, group] of genGroups) {
    let best = -Infinity
    for (const node of group) {
      if (node.rawScore > best) best = node.rawScore
    }
    for (const node of group) {
      if (node.rawScore === best) {
        map.get(node.id)!.isGlobalGenBest = true
      }
    }
  }

  // Pass 4: Island overall best - highest rawScore per island across all generations
  const islandBest = new Map<number, number>() // island -> best rawScore
  for (const node of data.nodes) {
    const cur = islandBest.get(node.island) ?? -Infinity
    if (node.rawScore > cur) islandBest.set(node.island, node.rawScore)
  }
  for (const node of data.nodes) {
    if (node.rawScore === islandBest.get(node.island)) {
      map.get(node.id)!.isIslandOverallBest = true
    }
  }

  // Pass 5: Global best - every node tied at the highest rawScore overall
  let globalMax = -Infinity
  for (const node of data.nodes) {
    if (node.rawScore > globalMax) globalMax = node.rawScore
  }
  for (const node of data.nodes) {
    if (node.rawScore === globalMax) {
      map.get(node.id)!.isGlobalBest = true
    }
  }

  return map
}
