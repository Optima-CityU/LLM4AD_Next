import { useMemo, useState } from "react"
import type { ResearchGeneratedItem } from "@/client"
import { useResearchGenerated } from "@/hooks/useAutoResearch"

/** `listGenerated` 返回的分组（字段名叫 `stage`，实际承载的是**算法名**）。 */
type GeneratedGroup = { stage?: string | null; items?: ResearchGeneratedItem[] }

export const algorithmKey = (a: string | null | undefined) => a ?? "?"

/**
 * 实验区的「算法分组」选择：查询 + 过滤空分组 + 当前选中项，供右侧面板与全屏弹框
 * 共享。
 *
 * 后端 `list_generated_solutions` 按**算法名**分组（放在响应字段 `stage` 里，见
 * artifacts.py 的 `_GENERATED_PATH_RE` / `ResearchGeneratedStageGroup`），所以这里的
 * `groups[i].stage` 就是算法名。
 *
 * 为什么把状态放在这里而不是各组件内部：右侧面板的「实验」区和全屏弹框里的演化仿真 /
 * 趋势分析原本各持一份 `useState`，切一个不会影响另一个。现在由共同祖先
 * （ArtifactsPanel）调一次本 hook，把 `selected` / `onSelect` 逐层下发，四处就是同一份
 * 状态。query 仍按 key 去重（`researchKeys.generated`），不会产生额外请求。
 *
 * @param sessionId 会话 ID，为空时不拉取。
 * @param running 会话是否在跑；跑动时 hook 内部按 15s 轮询刷新。
 * @param enabled 为 false 时完全禁用（如 ml_vision 画像不接演化引擎）。
 * @returns `groups` 有数据的分组、`selected` 当前算法名（未选时取最后一个分组）、
 *   `onSelect` 选择回调、以及透传的 query 状态。
 */
export function useExperimentAlgorithm(
  sessionId: string | null,
  running = false,
  enabled = true,
) {
  const genQ = useResearchGenerated(sessionId, running, enabled)
  const [picked, setPicked] = useState<string | null>(null)

  // 只保留有个体的分组：空分组没有可视化数据，列出来只会是死选项。
  const groups = useMemo<GeneratedGroup[]>(
    () => (genQ.data?.groups ?? []).filter((g) => (g.items?.length ?? 0) > 0),
    [genQ.data],
  )

  // 缺省取最后一个分组（列表按算法名排序，最后一个通常是最近写入的那个）；只有当用户
  // 选过的算法在当前数据里不存在时（切会话、跑完一轮换了算法）才回落到缺省。不在
  // effect 里 setState，避免多一次渲染和「手动选的被覆盖」这类竞态。
  const selected = useMemo<string | null>(() => {
    const keys = groups.map((g) => algorithmKey(g.stage))
    if (picked != null && keys.includes(picked)) return picked
    return keys.length > 0 ? keys[keys.length - 1] : null
  }, [groups, picked])

  return { genQ, groups, selected, onSelect: setPicked }
}
