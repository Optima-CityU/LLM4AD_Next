import {
  Activity,
  Code,
  Download,
  Eye,
  Lightbulb,
  Loader2,
  RefreshCw,
} from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
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
import { DEMO_BEST_CODE, isDemoTaskId } from "@/data/demoFixtures"
import { useEvolution } from "@/hooks/useEvolution"
import { cn } from "@/lib/utils"
import { authFetch } from "@/utils/auth"
import InsightsSplitView from "./InsightsSplitView"
import MultiPanelLayout from "./MultiPanelLayout"
import RenderSplitView from "./RenderSplitView"

const REFRESH_COOLDOWN_MS = 3000

function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try {
      return decodeURIComponent(encoded)
    } catch {
      return encoded
    }
  }
  return header.match(/filename="([^"]+)"/i)?.[1] ?? null
}

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
  const [isDownloadingWorkspace, setIsDownloadingWorkspace] = useState(false)
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
    // Demo tasks render a static code block in the IDE tab; skip the real
    // code-server token fetch which would 404 against the live nginx proxy.
    if (isDemoTaskId(task.id)) return
    if (activeTab === "ide" && ideState === "idle") {
      loadCodeToken()
    }
  }, [activeTab, ideState, loadCodeToken, task.id])

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

  const handleDownloadWorkspace = async () => {
    if (isDownloadingWorkspace || isDemoTaskId(task.id)) return
    setIsDownloadingWorkspace(true)
    try {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await authFetch(
        `${baseUrl}/api/v1/llm4ad/tasks/${task.id}/workspace/download`,
      )
      if (!response.ok) {
        let message = t("evolution.ideDownload.failed")
        try {
          const body = await response.json()
          message = body?.detail || message
        } catch {
          // Keep the localized fallback for non-JSON errors.
        }
        throw new Error(message)
      }

      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download =
        filenameFromContentDisposition(response.headers.get("Content-Disposition")) ??
        "LLM4AD-workspace.zip"
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("evolution.ideDownload.failed"),
      )
    } finally {
      setIsDownloadingWorkspace(false)
    }
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
      <div className="flex shrink-0 items-center gap-2">
        <TabsList
          data-tour="result-tabs"
          className="h-11 min-w-0 flex-1 overflow-x-auto p-1 rounded-lg bg-card border border-border"
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
          {!isDemoTaskId(task.id) && (
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={t("evolution.ideDownload.label")}
                    disabled={isDownloadingWorkspace}
                    onClick={(e) => {
                      e.stopPropagation()
                      void handleDownloadWorkspace()
                    }}
                    className="inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-transparent px-2 py-1 text-sm font-bold text-foreground transition-[color,box-shadow] hover:bg-primary/10 hover:text-primary disabled:pointer-events-none disabled:opacity-50 dark:text-muted-foreground"
                  >
                    {isDownloadingWorkspace ? (
                      <Loader2 className="size-3.5 shrink-0 animate-spin" />
                    ) : (
                      <Download className="size-3.5 shrink-0" />
                    )}
                    <span className="truncate">
                      {t("evolution.ideDownload.label")}
                    </span>
                  </button>
                </TooltipTrigger>
                <TooltipContent
                  side="bottom"
                  className="max-w-xs leading-relaxed"
                >
                  {t("evolution.ideDownload.tooltip")}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          <TabsTrigger
            value="ide"
            data-tour="demo-best-summary"
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
      </div>

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
        {isDemoTaskId(task.id) ? (
          <div
            data-tour="demo-ide-code"
            className="h-full rounded-lg border bg-card/60 backdrop-blur flex flex-col overflow-hidden"
          >
            <div className="shrink-0 px-4 py-2.5 border-b border-border/60 flex items-center gap-2 text-xs">
              <Code className="size-3.5 text-primary" />
              <span className="font-mono text-foreground">solve.py</span>
              <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
                {t("evolution.tabs.ide")}
              </span>
            </div>
            <pre className="flex-1 min-h-0 overflow-auto px-4 py-4 text-xs leading-relaxed font-mono text-foreground/90 bg-background/30">
              <code>{DEMO_BEST_CODE}</code>
            </pre>
          </div>
        ) : (
          <>
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
          </>
        )}
      </TabsContent>
    </Tabs>
  )
}
