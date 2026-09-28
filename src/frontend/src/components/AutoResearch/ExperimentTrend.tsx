import { ListStart, Loader2 } from "lucide-react"
import { useMemo } from "react"
import { useTranslation } from "react-i18next"
import TrendPanel from "@/components/Evolution/TaskDetail/TrendPanel"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { EvolutionProvider } from "./EvolutionProvider"
import { convertToEvolutionData } from "./evolutionDataAdapter"
import { algorithmKey, useExperimentAlgorithm } from "./useExperimentAlgorithm"

interface Props {
  sessionId: string
  running?: boolean
  /** 当前选中的算法名（与右侧面板共享同一份状态）。 */
  algorithm?: string | null
  onAlgorithmChange?: (algo: string) => void
}

/**
 * 趋势分析完整版：使用 evolution 页面的 TrendPanel 组件。
 * 包含完整的交互、tooltip、视图切换（global/instance）等功能。
 */
export default function ExperimentTrend({
  sessionId,
  running,
  algorithm,
  onAlgorithmChange,
}: Props) {
  const { t } = useTranslation()
  const { genQ, groups, selected, onSelect } = useExperimentAlgorithm(
    sessionId,
    running,
  )

  const stage = algorithm ?? selected
  const handleSelect = onAlgorithmChange ?? onSelect

  const activeGroup = useMemo(
    () =>
      groups.find((g) => algorithmKey(g.stage) === stage) ??
      groups[groups.length - 1],
    [groups, stage],
  )

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

  if (evolutionData.nodes.length === 0) {
    return (
      <p className="flex items-center justify-center h-full text-center text-sm text-muted-foreground/60">
        {t("autoResearch.experiment.noScored")}
      </p>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* 算法分组选择：单个分组也渲染，用作「当前算法名」的展示位。
          控制行常显（不再随分组数隐藏），与右侧面板的排布保持一致。 */}
      <div className="mb-2 flex items-center gap-1 px-4 py-2 border-b border-border/40">
        <Select value={stage ?? ""} onValueChange={handleSelect}>
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
                key={algorithmKey(g.stage)}
                value={algorithmKey(g.stage)}
                className="text-xs"
              >
                {g.stage ?? "?"}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* 趋势分析：使用 evolution 的完整组件 */}
      <div className="flex-1 min-h-0 p-4">
        <EvolutionProvider initialData={evolutionData}>
          <TrendPanel />
        </EvolutionProvider>
      </div>
    </div>
  )
}
