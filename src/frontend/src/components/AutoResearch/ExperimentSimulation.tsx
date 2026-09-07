import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import IslandGAVisualization from "@/components/Evolution/TaskDetail/IslandGAVisualization"
import { useResearchGenerated } from "@/hooks/useAutoResearch"
import { EvolutionProvider } from "./EvolutionProvider"
import { convertToEvolutionData } from "./evolutionDataAdapter"

interface Props {
  sessionId: string
  running?: boolean
}

/**
 * 演化仿真完整版：使用 evolution 页面的 IslandGAVisualization 组件。
 * 包含完整的 D3.js 交互、zoom、hover、节点选择、分类筛选等功能。
 */
export default function ExperimentSimulation({ sessionId, running }: Props) {
  const { t } = useTranslation()
  const genQ = useResearchGenerated(sessionId, running)

  const groups = useMemo(
    () => (genQ.data?.groups ?? []).filter((g) => (g.items?.length ?? 0) > 0),
    [genQ.data],
  )

  const [stage, setStage] = useState<number | null>(null)
  useEffect(() => {
    if (groups.length === 0) return
    const stages = groups.map((g) => g.stage ?? -1)
    if (stage == null || !stages.includes(stage)) {
      setStage(stages[stages.length - 1])
    }
  }, [groups, stage])

  const activeGroup =
    groups.find((g) => (g.stage ?? -1) === stage) ?? groups[groups.length - 1]

  // 转换为 evolution 数据格式
  const evolutionData = useMemo(() => {
    return convertToEvolutionData(activeGroup?.items ?? [])
  }, [activeGroup?.items])

  if (genQ.isLoading) {
    return (
      <div className="flex items-center justify-center h-full gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("autoResearch.artifacts.loading")}
      </div>
    )
  }

  if (groups.length === 0) {
    return (
      <p className="flex items-center justify-center h-full text-center text-sm text-muted-foreground/60">
        {t("autoResearch.experiment.empty")}
      </p>
    )
  }

  if (
    evolutionData.nodes.length === 0 &&
    evolutionData.unscoredNodes.length === 0
  ) {
    return (
      <p className="flex items-center justify-center h-full text-center text-sm text-muted-foreground/60">
        {t("autoResearch.experiment.empty")}
      </p>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* stage 选择 */}
      {groups.length > 1 && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border/40">
          <span className="text-xs text-muted-foreground">
            {t("autoResearch.stages.title")}
          </span>
          <select
            value={stage ?? ""}
            onChange={(e) => setStage(Number(e.target.value))}
            className="h-7 rounded border border-border/60 bg-background/60 px-2 text-xs focus:border-primary/50 focus:outline-none"
          >
            {groups.map((g) => (
              <option key={g.stage ?? -1} value={g.stage ?? -1}>
                #{g.stage ?? "?"}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* 演化可视化：使用 evolution 的完整组件 */}
      <div className="flex-1 min-h-0">
        <EvolutionProvider initialData={evolutionData}>
          <IslandGAVisualization />
        </EvolutionProvider>
      </div>
    </div>
  )
}
