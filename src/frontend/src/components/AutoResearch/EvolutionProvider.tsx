import type React from "react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { IslandGAData } from "@/components/Evolution/TaskDetail/island-ga-mock-data"
import type { SelectedNodeInfo } from "@/hooks/useEvolution"
import { EvolutionContext } from "@/hooks/useEvolution"

interface EvolutionProviderProps {
  initialData: IslandGAData
  children: React.ReactNode
}

/**
 * 为 autoresearch 提供 EvolutionContext 的独立 Provider。
 * 提供 IslandGAVisualization 和 TrendPanel 所需的最小上下文。
 */
export function EvolutionProvider({
  initialData,
  children,
}: EvolutionProviderProps) {
  const [currentGeneration, setCurrentGeneration] = useState(
    initialData.maxGeneration,
  )
  const [selectedNodes, setSelectedNodes] = useState<SelectedNodeInfo[]>([])
  const [focusNodeRequest, setFocusNodeRequest] = useState<{
    id: string
    nonce: number
    select: boolean
  } | null>(null)
  const [activeTab, setActiveTab] = useState("overview")

  // 当数据变化时更新当前代数
  useEffect(() => {
    setCurrentGeneration(initialData.maxGeneration)
  }, [initialData.maxGeneration])

  const toggleSelectedNode = useCallback((node: SelectedNodeInfo) => {
    setSelectedNodes((prev) => {
      const exists = prev.find((n) => n.id === node.id)
      if (exists) {
        return prev.filter((n) => n.id !== node.id)
      }
      return [...prev, node]
    })
  }, [])

  const requestFocusNode = useCallback((id: string, select = false) => {
    setFocusNodeRequest({ id, nonce: Date.now(), select })
    // 清除请求，让下次聚焦同一节点也能触发
    setTimeout(() => setFocusNodeRequest(null), 100)
  }, [])

  const contextValue = useMemo(
    () => ({
      // 项目和任务相关（autoresearch 不需要，提供空值）
      projectId: undefined,
      projectValid: true,
      selectedTask: null,
      setSelectedTask: () => {},
      selectedChildTaskId: null,
      setSelectedChildTaskId: () => {},
      effectiveTaskId: null,
      effectiveStatus: "completed" as const,

      // 代数进度
      currentGeneration,
      maxGeneration: initialData.maxGeneration,
      setCurrentGeneration,
      setMaxGeneration: () => {},

      // 播放控制（autoresearch 不需要）
      isPlaying: false,
      setIsPlaying: () => {},

      // 节点选择
      selectedNodes,
      setSelectedNodes,
      toggleSelectedNode,

      // 演化数据
      evolutionData: initialData,

      // 日志（autoresearch 不需要）
      logEntries: [],
      logLoading: false,
      logError: null,

      // 激活的 tab
      activeTab,
      setActiveTab,

      // 配置模式（autoresearch 不需要）
      isConfiguring: false,
      setIsConfiguring: () => {},
      isViewingParams: false,
      setIsViewingParams: () => {},
      isViewingAiBuildHistory: false,
      setIsViewingAiBuildHistory: () => {},
      forceManualConfigTaskId: null,
      setForceManualConfigTaskId: () => {},

      // Insight 导航（autoresearch 不需要）
      insightActiveType: null,
      setInsightActiveType: () => {},
      insightSelectedNodeIds: null,
      setInsightSelectedNodeIds: () => {},
      insightSelectedBestNodeId: null,
      setInsightSelectedBestNodeId: () => {},

      // 节点聚焦
      focusNodeRequest,
      requestFocusNode,

      // 左侧面板折叠（autoresearch 不需要）
      leftCollapsed: false,
      setLeftCollapsed: () => {},

      // SSE 回调（autoresearch 不需要）
      resetTaskData: () => {},
      updateTaskStatus: () => {},
      taskMemoryCreatedSignal: null,
      taskMemoryInjectedSignal: null,
    }),
    [
      currentGeneration,
      initialData,
      selectedNodes,
      activeTab,
      focusNodeRequest,
      toggleSelectedNode,
      requestFocusNode,
    ],
  )

  return (
    <EvolutionContext.Provider value={contextValue}>
      {children}
    </EvolutionContext.Provider>
  )
}
