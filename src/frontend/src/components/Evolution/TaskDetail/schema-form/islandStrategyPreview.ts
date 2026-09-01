export type IslandMemoryPolicy = "success_only" | "corrective" | "none"

export interface IslandStrategyPreviewInput {
  numIslands: number
  strategyStrength: number
  restartRatio: number
}

export interface IslandStrategyPreview {
  islandId: number
  position: number
  exploration: number
  exploitation: number
  memoryPolicy: IslandMemoryPolicy
  successMemoryRatio: number
  errorMemoryRatio: number
  restartProbability: number
}

export type IslandPreviewRole =
  | "reuse"
  | "corrective"
  | "exploitation"
  | "balanced"
  | "open"
  | "independent"

const clamp = (value: number, minimum: number, maximum: number) =>
  Math.min(maximum, Math.max(minimum, value))

const rounded = (value: number) => Number(value.toFixed(6))

export function buildIslandStrategyPreview({
  numIslands,
  strategyStrength,
  restartRatio,
}: IslandStrategyPreviewInput): IslandStrategyPreview[] {
  const count = Math.max(1, Math.floor(numIslands))
  const strength = clamp(strategyStrength, 0, 1)
  const boundedRestartRatio = clamp(restartRatio, 0, 1)

  return Array.from({ length: count }, (_, islandId) => {
    const rawPosition = count === 1 ? 0.5 : islandId / (count - 1)
    const position = 0.5 + (rawPosition - 0.5) * strength
    const exploration = position
    const exploitation = 1 - exploration
    let errorMemoryRatio = Math.min(0.4, 0.8 * position)
    let successMemoryRatio = 1 - errorMemoryRatio
    const explorationAffinity = Math.max(0, 2 * position - 1)
    const restartProbability = explorationAffinity * boundedRestartRatio
    let memoryPolicy: IslandMemoryPolicy

    if (position > 0.5) {
      memoryPolicy = "none"
      successMemoryRatio = 0
      errorMemoryRatio = 0
    } else if (errorMemoryRatio <= 0) {
      memoryPolicy = "success_only"
    } else {
      memoryPolicy = "corrective"
    }

    return {
      islandId,
      position: rounded(position),
      exploration: rounded(exploration),
      exploitation: rounded(exploitation),
      memoryPolicy,
      successMemoryRatio: rounded(successMemoryRatio),
      errorMemoryRatio: rounded(errorMemoryRatio),
      restartProbability: rounded(restartProbability),
    }
  })
}

const finiteNumber = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback

export function getDiverseIslandPreviewConfig(
  value: Record<string, unknown> | undefined,
): IslandStrategyPreviewInput {
  return {
    numIslands: Math.max(1, Math.floor(finiteNumber(value?.num_islands, 3))),
    strategyStrength: clamp(
      finiteNumber(value?.island_strategy_strength, 1),
      0,
      1,
    ),
    restartRatio: clamp(
      finiteNumber(value?.exploration_restart_ratio, 0.3),
      0,
      1,
    ),
  }
}

export function isMemoryEnabled(
  memory: Record<string, unknown> | undefined,
): boolean {
  return memory?.enabled !== false
}

export function getIslandPreviewRole(
  profile: IslandStrategyPreview,
  memoryEnabled: boolean,
): IslandPreviewRole {
  if (!memoryEnabled) {
    if (profile.position < 0.35) return "exploitation"
    if (profile.position <= 0.5) return "balanced"
  } else {
    if (profile.memoryPolicy === "success_only") return "reuse"
    if (profile.memoryPolicy === "corrective") return "corrective"
  }
  return profile.restartProbability >= 0.2 ? "independent" : "open"
}
