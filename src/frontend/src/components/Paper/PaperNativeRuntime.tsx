import {
  AlertCircle,
  AlertTriangle,
  FileCheck2,
  Loader2,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Upload,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { PaperRuntimeSessionCreate, ProposalStageState } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  useInvalidatePaperWorkflowStage,
  usePaperRuntimeSession,
} from "@/hooks/usePapers"
import { useRuntimeEvents } from "@/hooks/useRuntimeEvents"
import { cssColorToHslChannels } from "@/lib/embeddedAppearance"
import { cn } from "@/lib/utils"

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
  onConfigurePreferences: () => void
  showProjectContextPrompt?: boolean
  onUploadProjectContext?: () => void
  onSkipProjectContext?: () => void
  onOpenArtifact: (path: string) => void
}

function ProjectContextPrompt({
  className,
  onUpload,
  onSkip,
}: {
  className: string
  onUpload: () => void
  onSkip: () => void
}) {
  const { t } = useTranslation()
  return (
    <div
      className={cn(
        "flex flex-col gap-3 sm:flex-row sm:items-center",
        className,
      )}
    >
      <div className="min-w-0 flex-1 text-left">
        <p className="text-sm font-semibold text-foreground">
          {t("paper.source.projectContext.title")}
        </p>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {t("paper.source.projectContext.description")}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="sm" variant="outline" onClick={onUpload}>
          <Upload data-icon="inline-start" />
          {t("paper.source.projectContext.upload")}
        </Button>
        <Button size="sm" variant="ghost" onClick={onSkip}>
          {t("paper.source.projectContext.skip")}
        </Button>
      </div>
    </div>
  )
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
  onConfigurePreferences,
  showProjectContextPrompt = false,
  onUploadProjectContext,
  onSkipProjectContext,
  onOpenArtifact,
}: PaperNativeRuntimeProps) {
  const { t, i18n } = useTranslation()
  const [loadedSessionId, setLoadedSessionId] = useState<string | null>(null)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const { mutate: invalidateStage } =
    useInvalidatePaperWorkflowStage(workspaceId)
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
  const showRevisionSummary = needsRevision && stage !== "autorebuttal"
  const revisionTitle = t(
    stage === "rebuttal_baseline"
      ? "paper.runtime.rebuttalBaselineRevisionRequired"
      : "paper.runtime.revisionRequired",
  )
  const revisionFindingCount = stageState?.findings?.length ?? 0

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
        stage,
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
    stage,
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

  useEffect(() => {
    const handleConversationRewind = (event: MessageEvent) => {
      if (
        event.origin !== window.location.origin ||
        event.source !== frameRef.current?.contentWindow ||
        event.data?.type !== "llm4ad:conversation-rewound"
      ) {
        return
      }
      invalidateStage({
        workflow_stage: stage,
        expected_iteration: stageState?.iteration,
      })
    }
    window.addEventListener("message", handleConversationRewind)
    return () => window.removeEventListener("message", handleConversationRewind)
  }, [invalidateStage, stage, stageState?.iteration])

  if (!modelReady || !prerequisiteReady) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center p-8">
        <div className="max-w-lg border-y bg-background/70 px-8 py-10 text-center shadow-sm">
          <AlertCircle className="mx-auto size-8 text-muted-foreground" />
          <h2 className="mt-4 font-serif text-xl font-semibold text-pretty">
            {t(
              modelReady
                ? "paper.runtime.prerequisiteTitle"
                : "paper.runtime.modelTitle",
            )}
          </h2>
          <p className="mt-3 text-[15px] leading-7 text-muted-foreground">
            {t(
              modelReady
                ? "paper.runtime.prerequisiteDescription"
                : "paper.runtime.modelDescription",
            )}
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            {!modelReady && (
              <Button onClick={onConfigureModel}>
                <Settings2 className="size-3.5" />
                {t("paper.model.configure")}
              </Button>
            )}
            <Button variant="outline" onClick={onConfigurePreferences}>
              <SlidersHorizontal className="size-3.5" />
              {t("paper.preferences.configure")}
            </Button>
          </div>
          {showProjectContextPrompt &&
            onUploadProjectContext &&
            onSkipProjectContext && (
              <ProjectContextPrompt
                className="mt-6 border-t pt-5"
                onUpload={onUploadProjectContext}
                onSkip={onSkipProjectContext}
              />
            )}
        </div>
      </div>
    )
  }

  if (session.isError) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center p-8">
        <div className="max-w-lg border-y bg-background/70 px-8 py-10 text-center shadow-sm">
          <AlertCircle className="mx-auto size-8 text-destructive" />
          <h2 className="mt-4 font-serif text-xl font-semibold">
            {t("paper.runtime.unavailableTitle")}
          </h2>
          <p className="mt-3 text-[15px] leading-7 text-muted-foreground">
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
      <header className="flex shrink-0 flex-wrap items-start gap-4 border-b bg-card/40 px-5 py-3.5">
        <div className="min-w-0 flex-1 border-l-2 border-primary/65 pl-3.5">
          <h2 className="font-serif text-lg font-semibold tracking-tight text-pretty">
            {stageTitle}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-5 text-muted-foreground">
            {stageDescription}
          </p>
        </div>
        <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-2">
          {showRevisionSummary && (
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-9 max-w-56 gap-2 border-amber-500/40 bg-amber-500/10 px-3 text-sm text-amber-800 hover:bg-amber-500/15 hover:text-amber-900 dark:text-amber-200 dark:hover:text-amber-100"
                  aria-label={`${revisionTitle}，${t("paper.runtime.revisionDetails")}`}
                >
                  <AlertTriangle className="size-3.5 shrink-0" />
                  <span className="truncate">{revisionTitle}</span>
                  {revisionFindingCount > 0 && (
                    <span className="grid min-w-5 shrink-0 place-items-center rounded-full bg-amber-500/20 px-1.5 text-xs font-semibold tabular-nums">
                      {revisionFindingCount}
                    </span>
                  )}
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                sideOffset={8}
                className="max-h-[min(32rem,75vh)] w-[min(34rem,calc(100vw-2rem))] overflow-y-auto p-0"
              >
                <div className="sticky top-0 flex items-start gap-2.5 border-b bg-popover/95 px-4 py-3 backdrop-blur">
                  <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300">
                    <AlertTriangle className="size-4" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold leading-5">
                      {revisionTitle}
                    </p>
                    <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                      {t("paper.runtime.revisionDetailsDescription")}
                    </p>
                  </div>
                </div>
                <div className="space-y-4 px-4 py-4 text-sm">
                  {stageState.summary && (
                    <p className="whitespace-pre-wrap break-words leading-5 text-foreground/90">
                      {stageState.summary}
                    </p>
                  )}
                  {revisionFindingCount > 0 && (
                    <ul className="space-y-3 border-t pt-4 text-muted-foreground">
                      {stageState.findings?.map((finding, index) => (
                        <li
                          key={`${index}-${finding}`}
                          className="flex gap-2.5 leading-6"
                        >
                          <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
                          <span className="min-w-0 whitespace-pre-wrap break-words">
                            {finding}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </PopoverContent>
            </Popover>
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-9 max-w-56 gap-2 px-3 text-sm"
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
            variant="outline"
            className="h-9 gap-2 px-3 text-sm"
            onClick={onConfigurePreferences}
          >
            <SlidersHorizontal className="size-3.5" />
            {t("paper.preferences.configure")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-9 gap-2 px-3 text-sm"
            disabled={artifactPaths.length === 0}
            onClick={() => artifactPaths[0] && onOpenArtifact(artifactPaths[0])}
            title={artifactPaths.join("\n") || artifactSummary}
          >
            <FileCheck2 className="size-3.5" />
            {artifactSummary}
          </Button>
        </div>
      </header>
      {showProjectContextPrompt &&
        onUploadProjectContext &&
        onSkipProjectContext && (
          <ProjectContextPrompt
            className="shrink-0 border-b bg-muted/10 px-5 py-3"
            onUpload={onUploadProjectContext}
            onSkip={onSkipProjectContext}
          />
        )}
      {runtimeFailure && (
        <div
          role="status"
          className="flex shrink-0 items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-5 py-3 text-sm"
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
            <div className="flex items-center gap-3 font-serif text-base text-muted-foreground">
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
