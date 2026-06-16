import {
  Activity,
  Code,
  Eye,
  Lightbulb,
  Loader2,
  RefreshCw,
} from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import type { TaskResponse } from "@/client"
import { UtilsCodeServerService } from "@/client"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import { useTheme } from "@/components/theme-provider"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useEvolution } from "@/hooks/useEvolution"
import { cn } from "@/lib/utils"
import InsightsSplitView from "./InsightsSplitView"
import MultiPanelLayout from "./MultiPanelLayout"
import RenderSplitView from "./RenderSplitView"

const REFRESH_COOLDOWN_MS = 3000

interface InitializedViewProps {
  task: TaskResponse
}

export default function InitializedView({ task }: InitializedViewProps) {
  const { t } = useTranslation()
  const [ideState, setIdeState] = useState<
    "idle" | "loading" | "success" | "error"
  >("idle")
  const [ideError, setIdeError] = useState<string>("")
  const [iframeKey, setIframeKey] = useState(0)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const { activeTab, setActiveTab, selectedNodes } = useEvolution()
  const { resolvedTheme } = useTheme()

  const loadCodeToken = useCallback(() => {
    setIdeState("loading")
    return UtilsCodeServerService.getCodeToken({
      dark: resolvedTheme === "dark",
    })
      .then(() => {
        setIdeState("success")
        setIframeKey((k) => k + 1)
      })
      .catch((err) => {
        setIdeState("error")
        setIdeError(
          err?.body?.detail ||
            err?.message ||
            t("evolution.getCodeTokenFailed"),
        )
      })
  }, [resolvedTheme, t])

  const handleTabChange = (value: string) => {
    setActiveTab(value)
  }

  useEffect(() => {
    if (activeTab === "ide" && ideState === "idle") {
      loadCodeToken()
    }
  }, [activeTab, ideState, loadCodeToken])

  const handleRefreshIDE = () => {
    if (isRefreshing) return
    setIsRefreshing(true)
    if (activeTab !== "ide") {
      setActiveTab("ide")
    }
    loadCodeToken().finally(() => {
      setTimeout(() => setIsRefreshing(false), REFRESH_COOLDOWN_MS)
    })
  }

  return (
    <Tabs
      value={activeTab}
      className="h-full flex flex-col gap-4"
      onValueChange={handleTabChange}
    >
      <OnboardingTour
        tourId="evolution-results"
        startDelay={600}
        steps={[
          {
            selector: '[data-tour="result-tabs"]',
            title: t("tour.results.tabsTitle"),
            content: t("tour.results.tabsContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="result-canvas"]',
            title: t("tour.results.canvasTitle"),
            content: t("tour.results.canvasContent"),
          },
          {
            selector: '[data-tour="task-actions"]',
            title: t("tour.results.actionsTitle"),
            content: t("tour.results.actionsContent"),
            placement: "left",
          },
        ]}
      />
      <TabsList
        data-tour="result-tabs"
        className="w-full h-11 p-1 rounded-lg shrink-0 bg-card border border-border"
      >
        <TabsTrigger
          value="overview"
          className="font-bold text-sm gap-1.5 data-[state=active]:bg-primary/15 data-[state=active]:text-primary data-[state=active]:shadow-[0_0_10px] data-[state=active]:shadow-primary/15"
        >
          <Activity className="size-3.5" />
          {t("evolution.tabs.overview")}
        </TabsTrigger>
        <TabsTrigger
          value="render"
          className="font-bold text-sm gap-1.5 data-[state=active]:bg-primary/15 data-[state=active]:text-primary data-[state=active]:shadow-[0_0_10px] data-[state=active]:shadow-primary/15"
        >
          <Eye className="size-3.5" />
          {t("evolution.tabs.render")}
        </TabsTrigger>
        <TabsTrigger
          value="insights"
          className="font-bold text-sm gap-1.5 data-[state=active]:bg-primary/15 data-[state=active]:text-primary data-[state=active]:shadow-[0_0_10px] data-[state=active]:shadow-primary/15"
        >
          <Lightbulb className="size-3.5" />
          {t("evolution.tabs.insights")}
        </TabsTrigger>
        <TabsTrigger
          value="ide"
          className="font-bold text-sm gap-1.5 data-[state=active]:bg-primary/15 data-[state=active]:text-primary data-[state=active]:shadow-[0_0_10px] data-[state=active]:shadow-primary/15"
        >
          <Code className="size-3.5" />
          {t("evolution.tabs.ide")}
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={t("evolution.ideRefresh.label")}
                  aria-disabled={isRefreshing}
                  onClick={(e) => {
                    e.stopPropagation()
                    handleRefreshIDE()
                  }}
                  className={cn(
                    "ml-1 inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                    isRefreshing && "cursor-not-allowed",
                  )}
                >
                  <RefreshCw
                    className={cn("size-3", isRefreshing && "animate-spin")}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent
                side="bottom"
                className="max-w-xs leading-relaxed"
              >
                {t("evolution.ideRefresh.tooltip")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </TabsTrigger>
      </TabsList>

      <TabsContent value="overview" className="mt-0 flex-1 min-h-0">
        <div
          data-tour="result-canvas"
          className="relative overflow-hidden rounded-lg border border-dashed bg-card/50 h-full"
        >
          <MultiPanelLayout task={task} />
        </div>
      </TabsContent>

      <TabsContent
        value="render"
        className="flex-1 min-h-0 data-[state=inactive]:hidden"
        forceMount
      >
        <RenderSplitView task={task} />
      </TabsContent>

      <TabsContent
        value="insights"
        className="flex-1 min-h-0 data-[state=inactive]:hidden"
        forceMount
      >
        <InsightsSplitView task={task} />
      </TabsContent>

      <TabsContent value="ide" className="flex-1 min-h-0">
        {ideState === "loading" && (
          <div className="flex items-center justify-center rounded-lg border border-dashed bg-card/50 p-16">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            <span className="text-muted-foreground">
              {t("evolution.startingIDE")}
            </span>
          </div>
        )}
        {ideState === "error" && (
          <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive bg-card/50 p-16">
            <p className="text-destructive">{ideError}</p>
            <button
              type="button"
              className="text-sm text-primary underline"
              onClick={() => {
                setIdeState("idle")
                handleTabChange("ide")
              }}
            >
              {t("common.retry")}
            </button>
          </div>
        )}
        {ideState === "success" && (
          <div className="h-full">
            <iframe
              key={iframeKey}
              id="vscodeFrame"
              className="w-full h-full border rounded-lg"
              src={`${import.meta.env.VITE_CODE_SERVER_URL || "/code_ide"}/?folder=/data/project_home/${task.id}/${selectedNodes.length === 1 ? `llm4ad/run/generated/` : ""}`}
              title={t("evolution.vsCodeTitle")}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
              sandbox="allow-same-origin allow-scripts allow-popups allow-forms allow-modals allow-downloads allow-presentation"
              loading="lazy"
            />
          </div>
        )}
      </TabsContent>
    </Tabs>
  )
}
