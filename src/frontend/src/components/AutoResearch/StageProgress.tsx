import { useMemo, useState } from "react"

import type { ResearchStageSnapshot } from "@/client"
import { cn } from "@/lib/utils"

import StageDetailDrawer from "./StageDetailDrawer"
import StageGroupRail from "./StageGroupRail"
import { buildStageRoadmap, type StageCell } from "./tech"

interface BarProps {
  sessionId: string
  stages: ResearchStageSnapshot[]
  activeStage: number | null
  className?: string
  /** 是否允许从某一步运行（仅终态且非运行中）。 */
  canRunFromStage?: boolean
  /** 允许作为起点的阶段集合（仅这些阶段显示运行按钮）。 */
  runnableStages?: Set<number>
  /** 从指定阶段运行（等价于设好起始阶段再点运行）。 */
  onRunFromStage?: (stage: number) => void
  /** ml_vision 画像下隐藏 9-13 阶段的 LLM4AD 标识（徽章 + 来源提示）。 */
  hideLlm4ad?: boolean
}

/**
 * 顶部常驻进度带：把 23 阶段折叠成 8 大步骤的发光步骤条（唯一视图）。
 *
 * 单一视图、常驻展示——不再有折叠 / 视图切换按钮。点某个步骤悬停展开其子阶段，
 * 点子阶段打开注入引导抽屉（HITL）。
 */
export function StageProgressBar({
  sessionId,
  stages,
  activeStage,
  className,
  canRunFromStage,
  runnableStages,
  onRunFromStage,
  hideLlm4ad,
}: BarProps) {
  const [guideStage, setGuideStage] = useState<StageCell | null>(null)

  // 补齐成完整 23 阶段路线图：后端已返回的保留真实态，其余合成为 pending。
  // stages 为空（新建 / 尚未运行）时也常显——合成全 pending 的 23 阶段轮廓。
  const roadmap = useMemo(() => buildStageRoadmap(stages), [stages])

  return (
    // 进度轨作为对话卡片顶部的「状态带」：浅色给浅灰底 + 分隔线，从下方白色
    // 对话流里分出来；深色沿用主色内发光。两种主题都与对话区清晰分层。
    <div
      className={cn(
        "relative shrink-0 border-b border-border/60 bg-muted/40 dark:bg-background/50",
        className,
      )}
      style={{
        boxShadow:
          "inset 0 0 15px color-mix(in srgb, var(--primary) 3%, transparent), 0 0 8px color-mix(in srgb, var(--primary) 5%, transparent)",
      }}
    >
      <StageGroupRail
        cells={roadmap}
        activeStage={activeStage}
        onSelect={setGuideStage}
        canRunFromStage={canRunFromStage}
        runnableStages={runnableStages}
        onRunFromStage={onRunFromStage}
        hideLlm4ad={hideLlm4ad}
      />

      <StageDetailDrawer
        sessionId={sessionId}
        cell={guideStage}
        stages={stages}
        hideLlm4ad={hideLlm4ad}
        onClose={() => setGuideStage(null)}
      />
    </div>
  )
}
