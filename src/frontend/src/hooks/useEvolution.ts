import { createContext, useContext } from "react"
import type { ReportType, TaskResponse, TaskStatus } from "@/client"
import type { IslandGAData } from "@/components/Evolution/TaskDetail/island-ga-mock-data"
import { EMPTY_DATA } from "@/components/Evolution/TaskDetail/island-ga-mock-data"
import type { LogEntry } from "@/hooks/useTaskLogs"

export interface SelectedNodeInfo {
  id: string
  generation: number
  island: number
  islandId: string
  name: string
  fileName: string
  score: number
  rawScore: number
  parentIds: string[]
}

export interface TaskMemoryCreatedSignal {
  event: Record<string, unknown>
  nonce: number
}

export interface TaskMemoryInjectedSignal {
  event: Record<string, unknown>
  nonce: number
}

interface EvolutionContextValue {
  projectId: string | undefined
  projectValid: boolean
  selectedTask: TaskResponse | null
  setSelectedTask: (task: TaskResponse | null) => void

  // Child task selection within a version tree
  selectedChildTaskId: string | null
  setSelectedChildTaskId: (id: string | null) => void
  effectiveTaskId: string | null

  // The resolved status of the currently selected version (effectiveTask)
  effectiveStatus: TaskStatus

  // Generation progress
  currentGeneration: number
  maxGeneration: number
  setCurrentGeneration: (gen: number) => void
  setMaxGeneration: (max: number) => void

  // Playback
  isPlaying: boolean
  setIsPlaying: (playing: boolean) => void

  // Selected visualization nodes (multi-select)
  selectedNodes: SelectedNodeInfo[]
  setSelectedNodes: (nodes: SelectedNodeInfo[]) => void
  toggleSelectedNode: (node: SelectedNodeInfo) => void

  // Evolution data (real nodes from stream / REST)
  evolutionData: IslandGAData

  // Log entries from SSE / REST (shared via context)
  logEntries: LogEntry[]
  logLoading: boolean
  logError: string | null

  // Active tab in InitializedView
  activeTab: string
  setActiveTab: (tab: string) => void

  // Configuration mode for uninitialized tasks
  isConfiguring: boolean
  setIsConfiguring: (v: boolean) => void

  // Read-only parameter viewing mode (for running/pending tasks)
  isViewingParams: boolean
  setIsViewingParams: (v: boolean) => void

  // Read-only AI build history viewing mode
  isViewingAiBuildHistory: boolean
  setIsViewingAiBuildHistory: (v: boolean) => void

  // Task id forced to open in the manual (stepper) config panel even when it is
  // an uninitialized ai_built task — set when "调整参数" copies a child so the
  // re-tune flow stays in the manual panel instead of the AI build panel.
  forceManualConfigTaskId: string | null
  setForceManualConfigTaskId: (id: string | null) => void

  // Insight navigation signals (nullable one-shot)
  insightActiveType: ReportType | null
  setInsightActiveType: (type: ReportType | null) => void
  insightSelectedNodeIds: string[] | null
  setInsightSelectedNodeIds: (ids: string[] | null) => void
  insightSelectedBestNodeId: string | null
  setInsightSelectedBestNodeId: (id: string | null) => void

  // One-shot signal: focus the visualization camera on this node id. When
  // `select` is true the node also becomes the single selection; when false
  // the camera only pans/highlights without changing the selection. Consumed
  // by IslandGAVisualization.
  focusNodeRequest: { id: string; nonce: number; select: boolean } | null
  requestFocusNode: (id: string, select?: boolean) => void

  // Left panel collapse state
  leftCollapsed: boolean
  setLeftCollapsed: (v: boolean) => void

  // SSE stream callbacks
  resetTaskData: () => void
  updateTaskStatus: (status: string) => void

  // Latest task memory event from the task SSE stream
  taskMemoryCreatedSignal: TaskMemoryCreatedSignal | null

  // Latest MindMemOS injection stats from the task SSE stream
  taskMemoryInjectedSignal: TaskMemoryInjectedSignal | null
}

export const EvolutionContext = createContext<EvolutionContextValue>({
  projectId: undefined,
  projectValid: false,
  selectedTask: null,
  setSelectedTask: () => {},
  selectedChildTaskId: null,
  setSelectedChildTaskId: () => {},
  effectiveTaskId: null,
  effectiveStatus: "uninitialized",
  currentGeneration: 0,
  maxGeneration: 9,
  setCurrentGeneration: () => {},
  setMaxGeneration: () => {},
  isPlaying: false,
  setIsPlaying: () => {},
  selectedNodes: [],
  setSelectedNodes: () => {},
  toggleSelectedNode: () => {},
  evolutionData: EMPTY_DATA,
  logEntries: [],
  logLoading: false,
  logError: null,
  activeTab: "overview",
  setActiveTab: () => {},
  isConfiguring: false,
  setIsConfiguring: () => {},
  isViewingParams: false,
  setIsViewingParams: () => {},
  isViewingAiBuildHistory: false,
  setIsViewingAiBuildHistory: () => {},
  forceManualConfigTaskId: null,
  setForceManualConfigTaskId: () => {},
  insightActiveType: null,
  setInsightActiveType: () => {},
  insightSelectedNodeIds: null,
  setInsightSelectedNodeIds: () => {},
  insightSelectedBestNodeId: null,
  setInsightSelectedBestNodeId: () => {},
  focusNodeRequest: null,
  requestFocusNode: () => {},
  leftCollapsed: false,
  setLeftCollapsed: () => {},
  resetTaskData: () => {},
  updateTaskStatus: () => {},
  taskMemoryCreatedSignal: null,
  taskMemoryInjectedSignal: null,
})

export function useEvolution() {
  return useContext(EvolutionContext)
}
