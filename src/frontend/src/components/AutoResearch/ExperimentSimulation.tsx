import { ListStart, Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import IslandGAVisualization from "@/components/Evolution/TaskDetail/IslandGAVisualization"
import { useResearchGenerated } from "@/hooks/useAutoResearch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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

  const [stage, setStage] = useState<string | null>(null)
  const stageKey = (a: string | null | undefined) => a ?? "?"
  useEffect(() => {
    if (groups.length === 0) return
    const stages = groups.map((g) => stageKey(g.stage))
    if (stage == null || !stages.includes(stage)) {
      setStage(stages[stages.length - 1])
    }
  }, [groups, stage])

  const activeGroup =
    groups.find((g) => stageKey(g.stage) === stage) ?? groups[groups.length - 1]

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
      {/* 算法分组选择：对齐底部输入框的「选择算法」Select 样式（ListStart + 紧凑透明 trigger） */}
      {groups.length > 1 && (
        <div className="mb-2 flex items-center gap-1 px-4 py-2 border-b border-border/40">
          <Select value={stage ?? ""} onValueChange={setStage}>
            <SelectTrigger
              size="sm"
              aria-label={t("autoResearch.experiment.selectAlgorithm")}
              className="h-6 w-auto gap-1 rounded-md border-0 bg-transparent dark:bg-transparent dark:hover:bg-transparent px-1.5 py-0 text-[11px] font-medium text-muted-foreground shadow-none hover:text-foreground focus-visible:ring-0 [&>svg:last-child]:size-3 [&>svg:last-child]:opacity-60 shrink-0"
            >
              <ListStart className="size-3 shrink-0" />
              <SelectValue placeholder={t("autoResearch.experiment.selectAlgorithm")} />
            </SelectTrigger>
            <SelectContent>
              {groups.map((g) => (
                <SelectItem
                  key={stageKey(g.stage)}
                  value={stageKey(g.stage)}
                  className="text-xs"
                >
                  {g.stage ?? "?"}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
