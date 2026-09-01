import { useQuery } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { TaskStatus } from "@/client"
import { Llm4AdTasksService } from "@/client"
import type {
  GANode,
  IslandGAData,
} from "@/components/Evolution/TaskDetail/island-ga-mock-data"
import { EMPTY_DATA } from "@/components/Evolution/TaskDetail/island-ga-mock-data"
import { buildDemoEvolutionData, isDemoTaskId } from "@/data/demoFixtures"
import {
  collectMigrationAliases,
  resolveMigrationParentIds,
} from "@/hooks/evolutionMigration"
import { useDemoState } from "@/hooks/useDemoMode"
import { taskKeys } from "@/lib/task-queries"

/* ── Raw generated entry from SSE / REST ── */

interface RawGenerated {
  type: "generated"
  file_name: string
  data: {
    id: string
    name?: string
    generation?: number
    island?: number
    island_id?: string | number
    parent_ids?: string[]
    scores?: Record<string, number>
    evaluation?: { score?: number; [key: string]: unknown }
    [key: string]: unknown
  }
}

/* ── Helpers ── */

function hasValidScore(data: Record<string, unknown>): boolean {
  const evaluation = data.evaluation as Record<string, unknown> | undefined
  return evaluation?.score != null
}

function parseGeneration(fileName: string): number {
  const m = fileName.match(/gen_(\d+)_/)
  return m ? parseInt(m[1], 10) : 0
}

function meanScore(scores?: Record<string, number>): number {
  if (!scores) return 0
  const vals = Object.values(scores)
  if (vals.length === 0) return 0
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

function toRawGANode(
  entry: RawGenerated,
  migrationAliases: ReadonlyMap<string, string> = new Map(),
): GANode {
  const d = entry.data
  const raw = d.evaluation?.score ?? meanScore(d.scores)
  const islandId =
    d.island_id != null
      ? String(d.island_id)
      : d.island != null
        ? String(d.island)
        : "0"
  // Backend may emit duplicate parent_ids (e.g. same parent referenced twice in
  // a crossover). Deduplicate at the source so all downstream consumers — the
  // right-panel parent/children lists and counts, the elite classifier in
  // node-classification.ts (which relies on parentIds.length === 1), and the
  // edge renderer — see a clean unique list.
  const parentIds = resolveMigrationParentIds(
    d.parent_ids ?? [],
    migrationAliases,
  )
  return {
    id: d.id,
    generation: d.generation ?? parseGeneration(entry.file_name),
    island: 0,
    islandId,
    name: d.name ?? d.id,
    fileName: entry.file_name,
    score: 0,
    rawScore: raw,
    parentIds,
  }
}

/** Min-max normalize scores to [0, 1]. */
function normalizeScores(nodes: GANode[]): void {
  if (nodes.length === 0) return
  let min = Infinity
  let max = -Infinity
  for (const n of nodes) {
    if (n.rawScore < min) min = n.rawScore
    if (n.rawScore > max) max = n.rawScore
  }
  const range = max - min
  for (const n of nodes) {
    n.score = range > 0 ? (n.rawScore - min) / range : 0.5
  }
}

function buildData(scored: GANode[], unscored: GANode[]): IslandGAData {
  if (scored.length === 0 && unscored.length === 0) return EMPTY_DATA

  const all =
    scored.length === 0
      ? unscored
      : unscored.length === 0
        ? scored
        : [...scored, ...unscored]

  const islandIdSet = new Map<string, number>()
  for (const n of all) {
    if (!islandIdSet.has(n.islandId)) {
      islandIdSet.set(n.islandId, islandIdSet.size)
    }
  }
  const islandIds = [...islandIdSet.keys()]

  for (const n of all) {
    n.island = islandIdSet.get(n.islandId) ?? 0
  }

  normalizeScores(scored)
  return {
    nodes: scored,
    unscoredNodes: unscored,
    maxGeneration: Math.max(...all.map((n) => n.generation)),
    islandCount: islandIds.length,
    islandIds,
  }
}

/* ── Hook ── */

export function useEvolutionNodes(
  taskId: string | undefined,
  taskStatus: TaskStatus | undefined,
  enabled: boolean,
): {
  data: IslandGAData
  isLoading: boolean
  feedGenerated: (entry: Record<string, unknown>) => void
  feedMigration: (entry: Record<string, unknown>) => void
} {
  const isDemo = isDemoTaskId(taskId)
  const demoState = useDemoState()
  const isTerminal = taskStatus === "completed" || taskStatus === "failed"
  const isActive = taskStatus === "pending" || taskStatus === "running"
  // Only fetch `generated` history for terminal tasks. Uninitialized tasks show
  // the parameter-tuning UI by default and have no generated nodes; running
  // tasks stream nodes via SSE.
  const shouldRestFetch = isTerminal

  // ── REST path: fetch graph nodes and migration lineage ──
  const generatedQuery = useQuery({
    queryKey: [...taskKeys.logs(taskId!), "graph-events-all"],
    queryFn: () =>
      Llm4AdTasksService.getTaskLogs({
        taskId: taskId!,
        logType: "generated,migration",
        limit: 0,
      }),
    enabled: enabled && !!taskId && !isDemo && shouldRestFetch,
  })

  const restData = useMemo<IslandGAData>(() => {
    if (!generatedQuery.data?.entries) return EMPTY_DATA
    const entries = generatedQuery.data.entries as Record<string, unknown>[]
    const aliases = new Map<string, string>()
    for (const entry of entries) {
      if (entry.type !== "migration") continue
      for (const [cloneId, sourceId] of collectMigrationAliases(entry)) {
        aliases.set(cloneId, sourceId)
      }
    }
    const generated = entries.filter(
      (e) =>
        e.type === "generated" && !!(e.data as Record<string, unknown>)?.id,
    ) as unknown as RawGenerated[]
    // Dedup by id: once an id has a valid score we ignore later unscored entries
    // for the same id, and a later scored entry replaces any earlier unscored one.
    const scoredMap = new Map<string, GANode>()
    const unscoredMap = new Map<string, GANode>()
    for (const entry of generated) {
      const node = toRawGANode(entry, aliases)
      if (hasValidScore(entry.data as Record<string, unknown>)) {
        scoredMap.set(node.id, node)
        unscoredMap.delete(node.id)
      } else if (!scoredMap.has(node.id)) {
        node.unscored = true
        node.rawScore = 0
        unscoredMap.set(node.id, node)
      }
    }
    return buildData([...scoredMap.values()], [...unscoredMap.values()])
  }, [generatedQuery.data])

  // ── Active task: fed by useTaskLogs via feedGenerated ──
  const [streamData, setStreamData] = useState<IslandGAData>(EMPTY_DATA)
  const nodesMap = useRef<Map<string, GANode>>(new Map())
  const migrationAliases = useRef<Map<string, string>>(new Map())
  const rafId = useRef<number>(0)

  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional reset on taskId or active transition
  useEffect(() => {
    setStreamData(EMPTY_DATA)
    nodesMap.current = new Map()
    migrationAliases.current = new Map()
    cancelAnimationFrame(rafId.current)
  }, [taskId, isActive])

  const flush = useCallback(() => {
    const all = [...nodesMap.current.values()].map((n) => ({ ...n }))
    const scored: GANode[] = []
    const unscored: GANode[] = []
    for (const n of all) {
      if (n.unscored) unscored.push(n)
      else scored.push(n)
    }
    setStreamData(buildData(scored, unscored))
  }, [])

  const feedGenerated = useCallback(
    (entry: Record<string, unknown>) => {
      const data = entry.data as Record<string, unknown> | undefined
      if (!data?.id) return
      const node = toRawGANode(
        entry as unknown as RawGenerated,
        migrationAliases.current,
      )
      if (!hasValidScore(data)) {
        node.unscored = true
        node.rawScore = 0
      }
      nodesMap.current.set(data.id as string, node)
      cancelAnimationFrame(rafId.current)
      rafId.current = requestAnimationFrame(flush)
    },
    [flush],
  )

  const feedMigration = useCallback(
    (entry: Record<string, unknown>) => {
      const incoming = collectMigrationAliases(entry)
      if (incoming.size === 0) return

      for (const [cloneId, sourceId] of incoming) {
        migrationAliases.current.set(cloneId, sourceId)
      }

      // Normally migration arrives before its descendants. Re-resolve existing
      // nodes as well so an SSE reconnect or reordered history remains correct.
      let changed = false
      for (const [nodeId, node] of nodesMap.current) {
        const parentIds = resolveMigrationParentIds(
          node.parentIds,
          migrationAliases.current,
        )
        if (
          parentIds.length !== node.parentIds.length ||
          parentIds.some(
            (parentId, index) => parentId !== node.parentIds[index],
          )
        ) {
          nodesMap.current.set(nodeId, { ...node, parentIds })
          changed = true
        }
      }
      if (changed) {
        cancelAnimationFrame(rafId.current)
        rafId.current = requestAnimationFrame(flush)
      }
    },
    [flush],
  )

  if (!taskId)
    return {
      data: EMPTY_DATA,
      isLoading: false,
      feedGenerated,
      feedMigration,
    }
  if (isDemo) {
    // Demo phases gate visible generations: running streams nodes one
    // generation at a time (via setDemoGeneration), completed shows the full
    // graph, earlier phases hide the canvas.
    const visibleGen =
      demoState.phase === "completed"
        ? 12
        : demoState.phase === "running"
          ? demoState.generation
          : -1
    return {
      data: visibleGen >= 0 ? buildDemoEvolutionData(visibleGen) : EMPTY_DATA,
      isLoading: false,
      feedGenerated,
      feedMigration,
    }
  }
  if (shouldRestFetch)
    return {
      data: restData,
      isLoading: generatedQuery.isLoading,
      feedGenerated,
      feedMigration,
    }
  return {
    data: streamData,
    isLoading: false,
    feedGenerated,
    feedMigration,
  }
}
