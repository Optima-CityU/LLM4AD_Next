import ReactECharts from "echarts-for-react"
import { Loader2, RefreshCw, ScatterChart } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import "echarts-gl"
import type { TaskResponse } from "@/client"
import { Llm4AdTasksService } from "@/client"
import { useTheme } from "@/components/theme-provider"
import { useEvolution } from "@/hooks/useEvolution"
import FullscreenToggle from "./FullscreenToggle"
import IslandGAVisualization from "./IslandGAVisualization"

interface RenderSplitViewProps {
  task: TaskResponse
}

const MIN_PCT = 25
const MAX_PCT = 75

const RENDER_TABS = ["trajectory"] as const
type RenderTab = (typeof RENDER_TABS)[number]

function reviveFunctions(obj: unknown): unknown {
  if (obj === null || obj === undefined) return obj
  if (typeof obj === "string" && obj.trimStart().startsWith("function")) {
    try {
      return new Function(`return (${obj})`)()
    } catch {
      return obj
    }
  }
  if (Array.isArray(obj)) return obj.map(reviveFunctions)
  if (typeof obj === "object") {
    const result: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      result[key] = reviveFunctions(value)
    }
    return result
  }
  return obj
}

export default function RenderSplitView({ task }: RenderSplitViewProps) {
  const { t, i18n } = useTranslation()
  const { resolvedTheme } = useTheme()
  const { effectiveTaskId } = useEvolution()
  const containerRef = useRef<HTMLDivElement>(null)
  const [leftPct, setLeftPct] = useState(50)
  const [isResizing, setIsResizing] = useState(false)
  const [activeTab, setActiveTab] = useState<RenderTab>("trajectory")

  const [trajectoryOptions, setTrajectoryOptions] = useState<Record<
    string,
    unknown
  > | null>(null)
  const [trajectoryLoading, setTrajectoryLoading] = useState(false)
  const [trajectoryError, setTrajectoryError] = useState<string | null>(null)
  const [trajectoryErrorCode, setTrajectoryErrorCode] = useState<string | null>(
    null,
  )

  const taskId = effectiveTaskId || task.id
  const isTerminal = task.status === "completed" || task.status === "failed"

  useEffect(() => {
    setTrajectoryOptions(null)
    setTrajectoryError(null)
    setTrajectoryErrorCode(null)
    setTrajectoryLoading(false)
  }, [])

  const fetchTrajectory = useCallback(
    (force = false) => {
      setTrajectoryLoading(true)
      setTrajectoryError(null)
      setTrajectoryErrorCode(null)
      setTrajectoryOptions(null)

      const lang = i18n.language?.startsWith("zh") ? "zh" : "en"
      const theme = resolvedTheme === "dark" ? "dark" : "light"

      Llm4AdTasksService.generateResultRender({
        taskId,
        requestBody: {
          result_type: "trajectory",
          language: lang as "zh" | "en",
          theme: theme as "light" | "dark",
          force,
        },
      })
        .then((res) => {
          if (res.status === "completed" && res.data) {
            const raw = res.data
            const parsed = typeof raw === "string" ? JSON.parse(raw) : raw
            let options: Record<string, unknown>
            if (
              parsed &&
              typeof parsed === "object" &&
              !Array.isArray(parsed)
            ) {
              const keys = Object.keys(parsed as Record<string, unknown>)
              if (keys.length === 1) {
                options = (parsed as Record<string, unknown>)[
                  keys[0]
                ] as Record<string, unknown>
              } else {
                options = parsed as Record<string, unknown>
              }
            } else {
              options = parsed as Record<string, unknown>
            }
            setTrajectoryOptions(
              reviveFunctions(options) as Record<string, unknown>,
            )
          } else {
            setTrajectoryErrorCode(res.error_code ?? null)
            setTrajectoryError(
              res.message || t("evolution.render.trajectory.error"),
            )
          }
        })
        .catch((err) => {
          setTrajectoryErrorCode(null)
          setTrajectoryError(
            err?.message || t("evolution.render.trajectory.error"),
          )
        })
        .finally(() => {
          setTrajectoryLoading(false)
        })
    },
    [taskId, i18n.language, resolvedTheme, t],
  )

  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally re-fetch only on taskId or theme change
  useEffect(() => {
    if (activeTab !== "trajectory") return
    if (!isTerminal) return
    fetchTrajectory()
  }, [taskId, resolvedTheme, isTerminal])

  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsResizing(true)
    const onMove = (ev: MouseEvent) => {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect || rect.width === 0) return
      const pct = ((ev.clientX - rect.left) / rect.width) * 100
      setLeftPct(Math.max(MIN_PCT, Math.min(MAX_PCT, pct)))
    }
    const onUp = () => {
      setIsResizing(false)
      document.removeEventListener("mousemove", onMove)
      document.removeEventListener("mouseup", onUp)
      document.body.style.userSelect = ""
      document.body.style.cursor = ""
    }
    document.addEventListener("mousemove", onMove)
    document.addEventListener("mouseup", onUp)
    document.body.style.userSelect = "none"
    document.body.style.cursor = "ew-resize"
  }, [])

  return (
    <div
      ref={containerRef}
      className="flex h-full w-full overflow-hidden rounded-lg border border-dashed bg-card/50"
    >
      {/* Left: D3 evolution visualization */}
      <div
        className={`min-w-0 overflow-hidden relative ${
          isResizing ? "" : "transition-[width] duration-150 ease-out"
        }`}
        style={{ width: `${leftPct}%` }}
      >
        <FullscreenToggle>
          <IslandGAVisualization />
        </FullscreenToggle>
      </div>

      {/* Drag handle */}
      <div
        role="slider"
        tabIndex={-1}
        aria-label={t("evolution.dragToResize", {
          defaultValue: "Drag to resize",
        })}
        aria-valuemin={MIN_PCT}
        aria-valuemax={MAX_PCT}
        aria-valuenow={Math.round(leftPct)}
        onMouseDown={startResize}
        className="w-1 shrink-0 cursor-ew-resize bg-border/40 hover:bg-primary/40 active:bg-primary/60 transition-colors"
      />

      {/* Right: Render result panel */}
      <div className="flex-1 min-w-0 overflow-hidden flex flex-col bg-card/50">
        <FullscreenToggle>
          <div className="h-full w-full flex flex-col">
            {/* Tab bar + node selector */}
            <div className="flex items-center gap-1 px-3 py-2 border-b border-border/60 bg-card/80 shrink-0 flex-wrap">
              {RENDER_TABS.map((tab) => {
                const isActive = activeTab === tab
                return (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setActiveTab(tab)}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                      isActive
                        ? "bg-primary/15 text-primary shadow-sm"
                        : "text-muted-foreground hover:text-foreground hover:bg-accent/60"
                    }`}
                  >
                    {t(`evolution.render.${tab}`)}
                  </button>
                )
              })}
            </div>

            {/* Tab content */}
            <div className="flex-1 min-w-0 overflow-hidden">
              <TrajectoryPanel
                options={trajectoryOptions}
                loading={trajectoryLoading}
                error={trajectoryError}
                errorCode={trajectoryErrorCode}
                theme={resolvedTheme}
                isTerminal={isTerminal}
                onRegenerate={() => fetchTrajectory(true)}
              />
            </div>
          </div>
        </FullscreenToggle>
      </div>
    </div>
  )
}

function TrajectoryPanel({
  options,
  loading,
  error,
  errorCode,
  theme,
  isTerminal,
  onRegenerate,
}: {
  options: Record<string, unknown> | null
  loading: boolean
  error: string | null
  errorCode: string | null
  theme: string | undefined
  isTerminal: boolean
  onRegenerate: () => void
}) {
  const { t } = useTranslation()
  const isEmbeddingUnsupported =
    errorCode === "embedding_disabled" ||
    errorCode === "embedding_not_configured"

  if (loading) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 text-muted-foreground">
        <Loader2 className="size-8 animate-spin opacity-50" />
        <p className="text-sm">{t("evolution.render.trajectory.loading")}</p>
      </div>
    )
  }

  if (error) {
    if (isEmbeddingUnsupported) {
      return (
        <div className="h-full flex flex-col items-center justify-center gap-3 text-muted-foreground px-6">
          <ScatterChart className="size-12 opacity-20" />
          <p className="text-sm font-medium">
            {t("evolution.render.trajectory.unsupported")}
          </p>
          <p className="text-xs opacity-70 text-center">
            {t("evolution.render.trajectory.configureEmbeddingHint")}
          </p>
          <button
            type="button"
            onClick={onRegenerate}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
              bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sm mt-1"
          >
            <RefreshCw className="size-3.5" />
            {t("evolution.render.trajectory.regenerate")}
          </button>
        </div>
      )
    }

    return (
      <div className="h-full flex flex-col items-center justify-center gap-4 text-muted-foreground">
        <p className="text-sm text-destructive max-w-[80%] text-center">
          {error}
        </p>
        <button
          type="button"
          onClick={onRegenerate}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
            bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sm"
        >
          <RefreshCw className="size-3.5" />
          {t("evolution.render.trajectory.regenerate")}
        </button>
      </div>
    )
  }

  if (!options) {
    const titleKey = isTerminal
      ? "evolution.render.trajectory.empty"
      : "evolution.render.trajectory.notReady"
    const hintKey = isTerminal
      ? "evolution.render.trajectory.emptyHint"
      : "evolution.render.trajectory.notReadyHint"
    const btnKey = isTerminal
      ? "evolution.render.trajectory.regenerate"
      : "evolution.render.trajectory.generate"
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 text-muted-foreground px-6">
        <ScatterChart className="size-12 opacity-20" />
        <p className="text-sm font-medium">{t(titleKey)}</p>
        <p className="text-xs opacity-70 text-center">{t(hintKey)}</p>
        <button
          type="button"
          onClick={onRegenerate}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
            bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sm mt-1"
        >
          <RefreshCw className="size-3.5" />
          {t(btnKey)}
        </button>
      </div>
    )
  }

  return (
    <div className="h-full w-full flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 shrink-0">
        <h3 className="text-sm font-semibold text-foreground">
          {t("evolution.render.trajectory.title")}
        </h3>
        <button
          type="button"
          onClick={onRegenerate}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
            bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sm"
        >
          <RefreshCw className="size-3.5" />
          {t("evolution.render.trajectory.regenerate")}
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <ReactECharts
          option={options}
          theme={theme === "dark" ? "dark" : undefined}
          style={{ height: "100%", width: "100%" }}
          opts={{ renderer: "canvas" }}
          notMerge
        />
      </div>
    </div>
  )
}
