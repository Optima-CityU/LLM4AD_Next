import {
  AlertCircle,
  AlertTriangle,
  FileCheck2,
  Info,
  Loader2,
  RefreshCw,
  Settings2,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { PaperRuntimeSessionCreate, ProposalStageState } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { usePaperRuntimeSession } from "@/hooks/usePapers"
import { useRuntimeEvents } from "@/hooks/useRuntimeEvents"
import { cssColorToHslChannels } from "@/lib/embeddedAppearance"

type PaperWorkflowStage = PaperRuntimeSessionCreate["workflow_stage"]

type PaperNativeRuntimeProps = {
  workspaceId: string
  stage: PaperWorkflowStage
  modelReady: boolean
  prerequisiteReady: boolean
  profileKey: string
  artifactPaths: string[]
  stageState?: ProposalStageState
  modelName: string | null
  onConfigureModel: () => void
  onOpenArtifact: (path: string) => void
}

const EMBEDDED_COLOR_TOKENS = [
  "--background",
  "--foreground",
  "--card",
  "--card-foreground",
  "--popover",
  "--popover-foreground",
  "--primary",
  "--primary-foreground",
  "--secondary",
  "--secondary-foreground",
  "--muted",
  "--muted-foreground",
  "--accent",
  "--accent-foreground",
  "--destructive",
  "--destructive-foreground",
  "--border",
  "--input",
  "--ring",
] as const

export default function PaperNativeRuntime({
  workspaceId,
  stage,
  modelReady,
  prerequisiteReady,
  profileKey,
  artifactPaths,
  stageState,
  modelName,
  onConfigureModel,
  onOpenArtifact,
}: PaperNativeRuntimeProps) {
  const { t, i18n } = useTranslation()
  const [loadedSessionId, setLoadedSessionId] = useState<string | null>(null)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const session = usePaperRuntimeSession(
    workspaceId,
    stage,
    modelReady && prerequisiteReady,
    profileKey,
  )
  const frameReady = session.data?.session_id === loadedSessionId
  // 模型网关健康流：会话加载后订阅，上游失败时给出可操作提示。
  const runtimeEvents = useRuntimeEvents(
    workspaceId,
    Boolean(session.data && frameReady),
  )
  const runtimeFailure = runtimeEvents.failure
  const gatewayReasonLabel = runtimeFailure
    ? t(`paper.runtime.gatewayReason.${runtimeFailure.reason}`, {
        defaultValue: runtimeFailure.reason,
      })
    : ""
  const stageTitle = t(`paper.workflow.stage.${stage}`)
  const stageDescription = t(`paper.workflow.stage.${stage}Hint`)
  const stageExamples = useMemo(() => {
    const translated = t(`paper.workflow.stage.${stage}Examples`, {
      returnObjects: true,
    })
    return Array.isArray(translated)
      ? translated
          .filter((example): example is string => typeof example === "string")
          .slice(0, 3)
      : []
  }, [stage, t])
  const needsRevision = stageState?.status === "needs_revision"

  const syncAppearance = useCallback(() => {
    const target = frameRef.current?.contentWindow
    if (!target) return
    const root = document.documentElement
    const computed = window.getComputedStyle(root)
    const tokens = EMBEDDED_COLOR_TOKENS.reduce<Record<string, string>>(
      (result, name) => {
        const value = computed.getPropertyValue(name).trim()
        const channels = cssColorToHslChannels(value)
        if (channels) result[name] = channels
        return result
      },
      {},
    )
    tokens["--radius"] = computed.getPropertyValue("--radius").trim()
    target.postMessage(
      {
        type: "llm4ad:appearance",
        theme: root.classList.contains("dark") ? "dark" : "light",
        language: i18n.resolvedLanguage ?? i18n.language,
        fontFamily: window.getComputedStyle(document.body).fontFamily,
        stageTitle,
        stageDescription,
        stageExamples,
        tokens,
      },
      window.location.origin,
    )
  }, [
    i18n.language,
    i18n.resolvedLanguage,
    stageDescription,
    stageExamples,
    stageTitle,
  ])

  useEffect(() => {
    const observer = new MutationObserver(syncAppearance)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style"],
    })
    return () => observer.disconnect()
  }, [syncAppearance])

  if (!modelReady || !prerequisiteReady) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center p-6">
        <div className="max-w-md rounded-xl border border-dashed bg-background p-7 text-center">
          <AlertCircle className="mx-auto size-7 text-muted-foreground" />
          <h2 className="mt-3 text-sm font-semibold">
            {t(
              modelReady
                ? "paper.runtime.prerequisiteTitle"
                : "paper.runtime.modelTitle",
            )}
          </h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {t(
              modelReady
                ? "paper.runtime.prerequisiteDescription"
                : "paper.runtime.modelDescription",
            )}
          </p>
        </div>
      </div>
    )
  }

  if (session.isError) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center p-6">
        <div className="max-w-md rounded-xl border bg-background p-7 text-center shadow-sm">
          <AlertCircle className="mx-auto size-7 text-destructive" />
          <h2 className="mt-3 text-sm font-semibold">
            {t("paper.runtime.unavailableTitle")}
          </h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {t("paper.runtime.unavailableDescription")}
          </p>
          <Button
            className="mt-4"
            size="sm"
            variant="outline"
            onClick={() => void session.refetch()}
          >
            <RefreshCw className="size-3.5" />
            {t("common.retry")}
          </Button>
        </div>
      </div>
    )
  }

  const artifactSummary = artifactPaths.length
    ? t("paper.runtime.artifactCount", { count: artifactPaths.length })
    : t("paper.runtime.artifactPending")

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b bg-background/95 px-3 text-xs">
        <span className="min-w-0 truncate font-medium text-foreground">
          {stageTitle}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label={t("paper.runtime.stageHint")}
            >
              <Info className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-sm leading-5">
            {stageDescription}
          </TooltipContent>
        </Tooltip>
        <div className="ml-auto flex min-w-0 items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 max-w-48 gap-1.5 px-2 text-xs"
            onClick={onConfigureModel}
            title={modelName ?? t("paper.model.configure")}
          >
            <Settings2 className="size-3.5 shrink-0" />
            <span className="truncate">
              {modelName ?? t("paper.model.configure")}
            </span>
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 gap-1.5 px-2 text-xs"
            disabled={artifactPaths.length === 0}
            onClick={() => artifactPaths[0] && onOpenArtifact(artifactPaths[0])}
            title={artifactPaths.join("\n") || artifactSummary}
          >
            <FileCheck2 className="size-3.5" />
            {artifactSummary}
          </Button>
        </div>
      </div>
      {needsRevision && (
        <div
          role="status"
          className="flex max-h-40 shrink-0 gap-2 overflow-y-auto border-b border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-xs"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0">
            <p className="font-medium text-foreground">
              {t("paper.runtime.revisionRequired")}
            </p>
            {stageState.summary && (
              <p className="mt-1 leading-5 text-muted-foreground">
                {stageState.summary}
              </p>
            )}
            {(stageState.findings?.length ?? 0) > 0 && (
              <ul className="mt-1.5 list-disc space-y-1 pl-4 leading-5 text-muted-foreground">
                {stageState.findings?.map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
      {runtimeFailure && (
        <div
          role="status"
          className="flex shrink-0 items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs"
        >
          <AlertTriangle className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <p className="min-w-0 flex-1 leading-5 text-foreground">
            {t("paper.runtime.modelGatewayFailing", {
              count: runtimeFailure.count,
              reason: gatewayReasonLabel,
            })}
          </p>
          <Button size="sm" variant="outline" onClick={onConfigureModel}>
            <Settings2 className="size-3.5" />
            {t("paper.runtime.changeModel")}
          </Button>
        </div>
      )}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        {(!session.data || !frameReady) && (
          <div className="absolute inset-0 z-10 grid place-items-center bg-background">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
              {t("paper.runtime.starting")}
            </div>
          </div>
        )}
        {session.data && (
          <iframe
            ref={frameRef}
            key={session.data.session_id}
            src={session.data.runtime_url}
            title={t("paper.runtime.title")}
            className="h-full min-h-0 w-full border-0 bg-background"
            sandbox="allow-forms allow-same-origin allow-scripts"
            allow="clipboard-read; clipboard-write"
            onLoad={() => {
              setLoadedSessionId(session.data.session_id)
              syncAppearance()
            }}
          />
        )}
      </div>
    </div>
  )
}
