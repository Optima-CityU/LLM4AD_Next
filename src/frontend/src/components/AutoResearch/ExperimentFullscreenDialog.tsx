import { Maximize2, X } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import type { ExperimentTab } from "./ExperimentPanel"
import ExperimentSimulation from "./ExperimentSimulation"
import ExperimentTrend from "./ExperimentTrend"

interface Props {
  sessionId: string
  running?: boolean
}

/**
 * 实验区全屏弹框：全屏展示演化仿真和趋势分析的完整版本。
 * 左侧演化仿真，右侧趋势分析，功能和样式完全对齐 evolution 页面。
 */
export default function ExperimentFullscreenDialog({
  sessionId,
  running,
}: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<ExperimentTab>("simulation")

  return (
    <>
      {/* 触发按钮：全屏图标 */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={t("autoResearch.experiment.fullscreen")}
        className="grid place-items-center size-6 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
      >
        <Maximize2 className="size-3.5" />
      </button>

      {/* 全屏弹框 */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          showCloseButton={false}
          className="max-w-none w-screen h-screen sm:max-w-none translate-x-0 translate-y-0 top-0 left-0 rounded-none border-0 p-0 gap-0 grid-rows-[auto_minmax(0,1fr)] bg-background/95 backdrop-blur"
        >
          {/* 顶栏：标题 + 视图切换 + 关闭按钮 */}
          <DialogHeader className="flex flex-row items-center justify-between gap-4 h-14 px-5 border-b border-border/60 space-y-0 text-left">
            <DialogTitle className="flex items-center gap-2 text-base">
              {t("autoResearch.experiment.title")}
            </DialogTitle>

            {/* 视图切换（居中） */}
            <div className="flex items-center gap-1 rounded-md border border-border/60 bg-card/60 p-0.5">
              <button
                type="button"
                onClick={() => setActiveTab("simulation")}
                aria-pressed={activeTab === "simulation"}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors",
                  activeTab === "simulation"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground/80",
                )}
              >
                {t("autoResearch.experiment.simulation")}
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("trend")}
                aria-pressed={activeTab === "trend"}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors",
                  activeTab === "trend"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground/80",
                )}
              >
                {t("autoResearch.experiment.trend")}
              </button>
            </div>

            <DialogClose asChild>
              <button
                type="button"
                aria-label={t("common.close")}
                className="grid place-items-center size-8 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
              >
                <X className="size-4" />
              </button>
            </DialogClose>
          </DialogHeader>

          {/* 内容区：根据 tab 切换显示，居中最大宽度 */}
          <div className="min-h-0 overflow-hidden flex items-center justify-center p-8">
            <div className="w-full h-full max-w-7xl">
              {activeTab === "simulation" ? (
                <ExperimentSimulation sessionId={sessionId} running={running} />
              ) : (
                <ExperimentTrend sessionId={sessionId} running={running} />
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
