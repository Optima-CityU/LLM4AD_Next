import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Code,
  Crosshair,
  Database,
  Eye,
  FileText,
  GitBranch,
  History,
  Info,
  Loader2,
  MoreHorizontal,
  MousePointerClick,
  Network,
  PanelRightOpen,
  Play,
  Search,
  Settings2,
  Square,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import type {
  MemoryContributionSummary,
  MemoryInjectionSummary,
  TaskResponse,
} from "@/client"
import { Llm4AdMemoryService, Llm4AdTasksService, UtilsService } from "@/client"
import PanelErrorBoundary from "@/components/Common/PanelErrorBoundary"
import MemoryCardManager from "@/components/Memory/MemoryCardManager"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useCopyBeforeRun } from "@/hooks/useCopyBeforeRun"
import { useEvolution } from "@/hooks/useEvolution"
import { useTaskLogsList } from "@/hooks/useTaskLogs"
import { INPUT_LIMITS } from "@/lib/inputLimits"
import {
  resetTaskCaches,
  setTaskStatusInCache,
  taskKeys,
} from "@/lib/task-queries"
import { cn, formatDateTime, formatScore } from "@/lib/utils"
import { type GANode, ISLAND_COLORS } from "./TaskDetail/island-ga-mock-data"
import {
  LOG_LEVELS,
  type LogLevel,
  matchesLogLevels,
  renderEntry,
} from "./TaskDetail/log-renderers"

import { computeNodeClassifications } from "./TaskDetail/node-classification"
import TaskVersionTree from "./TaskVersionTree"

function getStatusMap(t: (key: string) => string) {
  return {
    uninitialized: {
      label: t("evolution.taskStatus.uninitialized"),
      color: "#6b7280",
    },
    pending: { label: t("evolution.taskStatus.pending"), color: "#f59e0b" },
    running: { label: t("evolution.taskStatus.running"), color: "#00d4ff" },
    completed: { label: t("evolution.taskStatus.completed"), color: "#10b981" },
    failed: { label: t("evolution.taskStatus.failed"), color: "#ef4444" },
  } as Record<string, { label: string; color: string }>
}

function SectionHeader({
  icon: Icon,
  title,
  isOpen,
  onToggle,
  summary,
}: {
  icon: typeof Info
  title: string
  isOpen: boolean
  onToggle: () => void
  summary?: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex items-center gap-2 w-full h-10 px-4 text-left shrink-0
        border-b border-border/60 hover:bg-accent/40 transition-colors"
    >
      <Icon className="size-3.5 text-primary shrink-0" />
      <span className="text-xs font-semibold text-primary tracking-wider uppercase leading-none flex-1 truncate">
        {title}
      </span>
      {!isOpen && summary && (
        <span className="shrink-0 max-w-[55%] inline-flex items-center truncate leading-none">
          {summary}
        </span>
      )}
      {isOpen ? (
        <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />
      ) : (
        <ChevronRight className="size-3.5 text-muted-foreground shrink-0" />
      )}
    </button>
  )
}

type ConfirmAction = "run" | "copyRun" | "copyConfig" | "adjustChoice" | null

/**
 * Shared task action logic (mutations, derived status flags, confirm-dialog
 * state and handlers) for a task. Consumed by both the in-panel
 * `TaskInfoSection` and the collapsed-panel `CollapsedRightPanelActions` so the
 * two surfaces stay behaviorally identical.
 */
function useTaskActions(task: TaskResponse) {
  const {
    projectId,
    effectiveTaskId,
    resetTaskData,
    isConfiguring,
    setIsConfiguring,
    setIsViewingParams,
    setIsViewingAiBuildHistory,
    setForceManualConfigTaskId,
    updateTaskStatus,
  } = useEvolution()
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const statusMap = getStatusMap(t)

  const { data: effectiveTask } = useQuery({
    queryKey: taskKeys.detail(effectiveTaskId!),
    queryFn: () => Llm4AdTasksService.getTask({ taskId: effectiveTaskId! }),
    enabled: !!effectiveTaskId,
  })

  const displayTask = effectiveTask ?? task

  const status = statusMap[displayTask.status] ?? {
    label: displayTask.status,
    color: "#6b7280",
  }

  const { withCopyIfNeeded, isCopying } = useCopyBeforeRun(displayTask)

  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: ["taskDataTree", displayTask.id],
    queryFn: () =>
      Llm4AdTasksService.getTaskDataTree({ taskId: displayTask.id }),
    enabled: !!displayTask.id,
  })
  const hasFile = (
    nodes: { type: string; children?: unknown[] | null }[],
  ): boolean =>
    nodes.some(
      (n) =>
        n.type === "file" ||
        (n.children && hasFile(n.children as typeof nodes)),
    )
  const dataEmpty = !treeLoading && !hasFile(treeData?.tree ?? [])

  const refreshTask = () => {
    resetTaskCaches(queryClient, displayTask.id, projectId)
  }

  const stopMutation = useMutation({
    mutationFn: () => Llm4AdTasksService.stopTask({ taskId: displayTask.id }),
    onSuccess: refreshTask,
  })

  const runMutation = useMutation({
    mutationFn: () => Llm4AdTasksService.runTask({ taskId: displayTask.id }),
    onSuccess: (result) => {
      const nextStatus = result.status ?? "pending"
      setTaskStatusInCache(queryClient, displayTask.id, nextStatus, projectId)
      updateTaskStatus(nextStatus)
      setIsConfiguring(false)
      setIsViewingParams(false)
      resetTaskData()
    },
  })

  const copyAndRunMutation = useMutation({
    mutationFn: () =>
      withCopyIfNeeded(async (effectiveId) => {
        const result = await Llm4AdTasksService.runTask({ taskId: effectiveId })
        const nextStatus = result.status ?? "pending"
        setTaskStatusInCache(queryClient, effectiveId, nextStatus, projectId)
        updateTaskStatus(nextStatus)
        setIsConfiguring(false)
        setIsViewingParams(false)
        resetTaskData()
      }),
  })

  const copyAndConfigMutation = useMutation({
    mutationFn: () =>
      withCopyIfNeeded(async (effectiveId) => {
        // Re-tuning a built task must stay in the manual panel even though the
        // copied child inherits ai_built — tag it so TaskDetail routes to manual.
        setForceManualConfigTaskId(effectiveId)
        setIsViewingParams(false)
        setIsConfiguring(true)
      }),
  })

  const isRunning =
    displayTask.status === "running" || displayTask.status === "pending"
  const isUninitialized = displayTask.status === "uninitialized"
  const canRerun =
    displayTask.status === "completed" || displayTask.status === "failed"
  const isActionPending =
    runMutation.isPending ||
    copyAndRunMutation.isPending ||
    copyAndConfigMutation.isPending ||
    isCopying

  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null)

  const handleConfirm = () => {
    if (confirmAction === "run") runMutation.mutate()
    else if (confirmAction === "copyRun") copyAndRunMutation.mutate()
    else if (confirmAction === "copyConfig") copyAndConfigMutation.mutate()
    setConfirmAction(null)
  }

  const confirmMessages: Record<string, { title: string; desc: string }> = {
    run: {
      title: t("evolution.confirm.runTitle"),
      desc: t("evolution.confirm.runDesc"),
    },
    copyRun: {
      title: t("evolution.confirm.copyRunTitle"),
      desc: t("evolution.confirm.copyRunDesc"),
    },
    copyConfig: {
      title: t("evolution.confirm.copyConfigTitle"),
      desc: t("evolution.confirm.copyConfigDesc"),
    },
  }

  // Which secondary actions apply. Adjust: uninitialized → edit directly;
  // otherwise prompt edit-current vs. clone-child. Running tasks expose "view".
  const showAdjust = (isUninitialized || canRerun) && !isConfiguring
  const showViewParams = !isConfiguring
  const showAiBuildHistory = !!displayTask.ai_built && !isConfiguring

  const viewParams = () => {
    setIsConfiguring(false)
    setIsViewingParams(true)
  }
  const adjustParams = () => {
    if (isUninitialized) {
      setIsViewingParams(false)
      setIsConfiguring(true)
    } else {
      setConfirmAction("adjustChoice")
    }
  }
  const openAiBuildHistory = () => {
    setIsConfiguring(false)
    setIsViewingParams(false)
    setIsViewingAiBuildHistory(true)
  }
  const adjustCurrent = () => {
    setConfirmAction(null)
    setIsViewingParams(false)
    setIsConfiguring(true)
  }
  const adjustChild = () => {
    setConfirmAction(null)
    copyAndConfigMutation.mutate()
  }

  return {
    displayTask,
    status,
    isCopying,
    dataEmpty,
    isConfiguring,
    stopMutation,
    runMutation,
    copyAndRunMutation,
    copyAndConfigMutation,
    isRunning,
    isUninitialized,
    canRerun,
    isActionPending,
    confirmAction,
    setConfirmAction,
    handleConfirm,
    confirmMessages,
    showAdjust,
    showViewParams,
    showAiBuildHistory,
    viewParams,
    adjustParams,
    openAiBuildHistory,
    adjustCurrent,
    adjustChild,
  }
}

type TaskActions = ReturnType<typeof useTaskActions>

/** Confirm dialogs shared by the in-panel and collapsed action surfaces. */
function TaskActionDialogs({ actions }: { actions: TaskActions }) {
  const { t } = useTranslation()
  const {
    confirmAction,
    setConfirmAction,
    handleConfirm,
    confirmMessages,
    isActionPending,
    copyAndConfigMutation,
    adjustCurrent,
    adjustChild,
  } = actions

  return (
    <>
      <AlertDialog
        open={!!confirmAction && confirmAction !== "adjustChoice"}
        onOpenChange={(open) => {
          if (!open) setConfirmAction(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction && confirmMessages[confirmAction]?.title}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction && confirmMessages[confirmAction]?.desc}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirm}>
              {t("common.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Adjust params: two-choice dialog. Lets the user pick between editing
       * the current task in place vs. cloning a child task to tune. */}
      <AlertDialog
        open={confirmAction === "adjustChoice"}
        onOpenChange={(open) => {
          if (!open) setConfirmAction(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("evolution.confirm.adjustChoiceTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("evolution.confirm.adjustChoiceDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2 py-1">
            <button
              type="button"
              disabled={isActionPending}
              onClick={adjustCurrent}
              className="flex flex-col items-start gap-1 rounded-lg border-2 border-border/60 hover:border-primary/60 hover:bg-accent/40 p-3 text-left transition-colors disabled:opacity-50"
            >
              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                <Settings2 className="size-4 text-primary shrink-0" />
                {t("evolution.confirm.adjustCurrentTitle")}
              </span>
              <p className="text-xs text-muted-foreground leading-relaxed">
                {t("evolution.confirm.adjustCurrentDesc")}
              </p>
            </button>
            <button
              type="button"
              disabled={isActionPending}
              onClick={adjustChild}
              className="flex flex-col items-start gap-1 rounded-lg border-2 border-border/60 hover:border-primary/60 hover:bg-accent/40 p-3 text-left transition-colors disabled:opacity-50"
            >
              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                {copyAndConfigMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin text-primary shrink-0" />
                ) : (
                  <GitBranch className="size-4 text-primary shrink-0" />
                )}
                {t("evolution.confirm.adjustChildTitle")}
              </span>
              <p className="text-xs text-muted-foreground leading-relaxed">
                {t("evolution.confirm.adjustChildDesc")}
              </p>
            </button>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function TaskInfoSection({
  task,
  isOpen,
  onToggle,
}: {
  task: TaskResponse
  isOpen: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const actions = useTaskActions(task)
  const {
    displayTask,
    status,
    isCopying,
    dataEmpty,
    isConfiguring,
    stopMutation,
    runMutation,
    copyAndRunMutation,
    copyAndConfigMutation,
    isRunning,
    isUninitialized,
    canRerun,
    isActionPending,
    setConfirmAction,
    showAdjust,
    showViewParams,
    showAiBuildHistory,
    viewParams,
    adjustParams,
    openAiBuildHistory,
  } = actions

  return (
    <div className="flex flex-col min-h-0 shrink-0">
      <SectionHeader
        icon={Info}
        title={t("evolution.taskInfo")}
        isOpen={isOpen}
        onToggle={onToggle}
        summary={
          <span
            className="inline-flex items-center gap-1 text-[10px] font-medium"
            style={{ color: status.color }}
          >
            <span
              className="size-1.5 rounded-full shrink-0"
              style={{ backgroundColor: status.color }}
            />
            <span className="truncate">{status.label}</span>
          </span>
        }
      />
      {isOpen && (
        <div className="px-4 py-3 space-y-3 border-b border-border/30 overflow-y-auto max-h-[60vh]">
          {/* Status — first thing user wants to know */}
          <div className="flex items-center gap-2">
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border"
              style={{
                color: status.color,
                borderColor: `${status.color}40`,
                backgroundColor: `${status.color}15`,
              }}
            >
              <span
                className="size-1.5 rounded-full"
                style={{ backgroundColor: status.color }}
              />
              {status.label}
            </span>
            {displayTask.ai_built && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex items-center justify-center size-5 rounded border border-primary/30 bg-primary/10 text-primary shrink-0">
                    <Bot className="size-3" />
                  </span>
                </TooltipTrigger>
                <TooltipContent side="bottom" sideOffset={4}>
                  {t("evolution.buildModeAi")}
                </TooltipContent>
              </Tooltip>
            )}
            <span
              className="text-[10px] text-muted-foreground/70 ml-auto truncate"
              title={`${t("evolution.taskInfoLabel.createdTime")}: ${formatDateTime(displayTask.created_time)}\n${t("evolution.taskInfoLabel.updatedTime")}: ${formatDateTime(displayTask.updated_time)}`}
            >
              {formatDateTime(displayTask.updated_time)}
            </span>
          </div>

          {/* Version tree — visually grouped */}
          <div className="rounded-md border border-border/30 bg-muted/20 p-2">
            <TaskVersionTree />
          </div>

          {displayTask.description && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
                {t("evolution.taskInfoLabel.description")}
              </span>
              <span className="text-xs text-foreground/80 break-words whitespace-pre-wrap">
                {displayTask.description}
              </span>
            </div>
          )}

          {/* Action buttons */}
          <div data-tour="task-actions" className="flex flex-col gap-2 pt-1">
            {/* Secondary config-access actions (placed first for quick access) */}
            {(() => {
              const count = Number(showAdjust) + Number(showViewParams)
              if (count === 0 && !showAiBuildHistory) return null
              return (
                <>
                  {count > 0 && (
                    <div
                      className={cn(
                        "grid gap-2",
                        count === 1 ? "grid-cols-1" : "grid-cols-2",
                      )}
                    >
                      {showViewParams && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1.5 text-xs"
                          onClick={viewParams}
                        >
                          <Eye className="size-3.5" />
                          {t("evolution.viewParams")}
                        </Button>
                      )}
                      {showAdjust && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1.5 text-xs"
                          disabled={isActionPending}
                          onClick={adjustParams}
                        >
                          {copyAndConfigMutation.isPending ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Settings2 className="size-3.5" />
                          )}
                          {t("evolution.adjustParams")}
                        </Button>
                      )}
                    </div>
                  )}
                  {showAiBuildHistory && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full gap-1.5 text-xs border-primary/30 text-primary bg-primary/5
                        hover:bg-primary/10 hover:border-primary/50"
                      onClick={openAiBuildHistory}
                    >
                      <History className="size-3.5" />
                      {t("evolution.aiBuildHistory")}
                    </Button>
                  )}
                </>
              )
            })()}

            {/* Primary action (full width, prominent) */}
            {isRunning && (
              <Button
                variant="destructive"
                size="sm"
                className="w-full gap-1.5 text-xs"
                disabled={stopMutation.isPending}
                onClick={() => stopMutation.mutate()}
              >
                {stopMutation.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Square className="size-3.5" />
                )}
                {t("evolution.stopTask")}
              </Button>
            )}
            {canRerun && (
              <Button
                size="sm"
                className="w-full gap-1.5 text-xs"
                disabled={isActionPending}
                onClick={() => setConfirmAction("copyRun")}
              >
                {copyAndRunMutation.isPending || isCopying ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Play className="size-3.5" />
                )}
                {isCopying
                  ? t("evolution.versionTree.copyingAsChild")
                  : t("evolution.startTask")}
              </Button>
            )}
            {isUninitialized && !isConfiguring && (
              <Button
                size="sm"
                className="w-full gap-1.5 text-xs"
                disabled={runMutation.isPending || dataEmpty}
                onClick={() => runMutation.mutate()}
              >
                {runMutation.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Play className="size-3.5" />
                )}
                {t("evolution.startTask")}
              </Button>
            )}
          </div>
        </div>
      )}

      <TaskActionDialogs actions={actions} />
    </div>
  )
}

function TaskMemorySection({
  task,
  isOpen,
  onToggle,
}: {
  task: TaskResponse
  isOpen: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const {
    projectId,
    taskMemoryCreatedSignal,
    taskMemoryInjectedSignal,
  } = useEvolution()
  const [memoryCount, setMemoryCount] = useState<number | null>(null)
  const [pendingCount, setPendingCount] = useState(0)
  const [usageStatsOpen, setUsageStatsOpen] = useState(false)
  const [contributionOpen, setContributionOpen] = useState(false)
  const taskScopeId = task.group_id ?? task.id

  useEffect(() => {
    setMemoryCount(null)
    setPendingCount(0)
    setUsageStatsOpen(false)
    setContributionOpen(false)
  }, [taskScopeId])

  const { data: featureFlags } = useQuery({
    queryKey: ["featureFlags"],
    queryFn: () => UtilsService.featureFlags(),
    staleTime: 5 * 60 * 1000,
  })
  const featureEnabled = featureFlags?.mindmemos_memory_enabled ?? false

  const configQuery = useQuery({
    queryKey: ["memory", "projectConfig", projectId],
    queryFn: () => Llm4AdMemoryService.getProjectMemoryConfig({ projectId: projectId! }),
    enabled: isOpen && featureEnabled && Boolean(projectId),
    staleTime: 60 * 1000,
  })

  const memoryEvent = taskMemoryCreatedSignal?.event
  const memoryEventMatches =
    memoryEvent?.type === "memory_card_created" &&
    memoryEvent?.scope === "task" &&
    String(memoryEvent?.task_id ?? "") === taskScopeId
  const refreshSignal = memoryEventMatches ? taskMemoryCreatedSignal?.nonce : null
  const injectionEvent = taskMemoryInjectedSignal?.event
  const injectionEventMatches =
    injectionEvent?.type === "mindmemos_memory_injected" &&
    String(injectionEvent?.task_id ?? "") === taskScopeId
  const observabilityQuery = useQuery({
    queryKey: ["memory", "taskObservability", taskScopeId],
    queryFn: () =>
      Llm4AdTasksService.getTaskMemoryObservability({ taskId: taskScopeId }),
    enabled: isOpen && isMindMemOSTask(task),
    staleTime: 15 * 1000,
    refetchOnWindowFocus: false,
  })
  const observability = observabilityQuery.data
  const latestInjection = injectionEventMatches
    ? eventToInjectionSummary(injectionEvent ?? {})
    : observability?.latest_injection
  const latestScopeHits = latestInjection?.scope_hits
  const injectedSummary = latestScopeHits
    ? t("evolution.taskMemory.injectedSummary", {
        task: Number(latestScopeHits.task ?? 0),
        project: Number(latestScopeHits.project ?? 0),
        user: Number(latestScopeHits.user ?? 0),
        chars: Number(latestInjection?.injected_chars ?? 0),
      })
    : null

  useEffect(() => {
    if (!memoryEventMatches || !taskMemoryCreatedSignal) return
    if (isOpen) {
      setPendingCount(0)
      return
    }
    setPendingCount((current) => current + 1)
  }, [isOpen, memoryEventMatches, taskMemoryCreatedSignal])

  useEffect(() => {
    if (isOpen) setPendingCount(0)
  }, [isOpen])

  useEffect(() => {
    if (!isOpen || !injectionEventMatches) return
    observabilityQuery.refetch()
  }, [isOpen, injectionEventMatches, taskMemoryInjectedSignal, observabilityQuery.refetch])

  const systemReady = configQuery.data?.system_runtime_available === true
  const bindingReady = Boolean(configQuery.data?.mindmemos_binding_id)
  const memoryReady =
    featureEnabled &&
    !configQuery.isError &&
    (!isOpen || (systemReady && bindingReady))
  const disabledReason = !featureEnabled
    ? t("evolution.taskMemory.unavailable.featureDisabled")
    : configQuery.isLoading
      ? t("evolution.taskMemory.unavailable.checking")
      : configQuery.isError
        ? t("evolution.taskMemory.unavailable.checkFailed")
        : !systemReady
          ? t("evolution.taskMemory.unavailable.serviceUnavailable")
          : !bindingReady
            ? t("evolution.taskMemory.unavailable.modelUnbound")
            : undefined
  const canLoadCards = isOpen && featureEnabled && systemReady && bindingReady
  const injectionCalls = observability?.injection_calls ?? 0
  const dedupedHitsTotal = observability?.deduped_hits_total ?? 0
  const elapsedMsAvg = observability?.elapsed_ms_avg ?? 0
  const createdTaskMemoryCount = observability?.created_task_memory_count ?? 0
  const scopeHitsTotal = observability?.scope_hits_total ?? {}
  const contribution = (
    observability as
      | { contribution?: MemoryContributionSummary }
      | undefined
  )?.contribution
  const contributionAssociated = Number(contribution?.associated_generations ?? 0)
  const contributionPositive = Number(contribution?.positive_results ?? 0)
  const contributionSummary = contributionAssociated
    ? t("evolution.taskMemory.observability.contributionSummary", {
        associated: contributionAssociated,
        positive: contributionPositive,
      })
    : t("evolution.taskMemory.observability.contributionEmpty")
  const summary = pendingCount > 0
    ? t("evolution.taskMemory.newCount", { count: pendingCount })
    : injectionCalls
      ? t("evolution.taskMemory.observability.summary", {
          calls: injectionCalls,
          hits: dedupedHitsTotal,
        })
    : memoryCount !== null
      ? t("evolution.taskMemory.count", { count: memoryCount })
      : featureEnabled
        ? t("evolution.taskMemory.ready")
        : t("evolution.taskMemory.unavailable.short")

  return (
    <div className="flex min-h-0 flex-col shrink-0">
      <SectionHeader
        icon={Database}
        title={t("evolution.taskMemory.title")}
        isOpen={isOpen}
        onToggle={onToggle}
        summary={
          <span
            className={cn(
              "inline-flex max-w-full truncate text-[10px]",
              pendingCount > 0 ? "text-primary" : "text-muted-foreground/70",
            )}
          >
            {summary}
          </span>
        }
      />
      {isOpen && (
        <div className="max-h-[56vh] overflow-y-auto border-b border-border/30 px-3 py-3">
          <TaskMemoryFoldout
            open={usageStatsOpen}
            title={t("evolution.taskMemory.observability.title")}
            summary={injectedSummary ?? t("evolution.taskMemory.injectedEmpty")}
            onToggle={() => setUsageStatsOpen((current) => !current)}
          >
            {observabilityQuery.isLoading ? (
              <div className="grid grid-cols-3 gap-1.5">
                {[0, 1, 2].map((item) => (
                  <div key={item} className="h-10 animate-pulse rounded bg-muted/70" />
                ))}
              </div>
            ) : observabilityQuery.isError ? (
              <p className="text-[11px] leading-4 text-destructive">
                {t("evolution.taskMemory.observability.loadFailed")}
              </p>
            ) : injectionCalls ? (
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.calls")}
                  value={String(injectionCalls)}
                />
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.hits")}
                  value={String(dedupedHitsTotal)}
                />
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.taskCalls")}
                  value={String(scopeHitsTotal.task ?? 0)}
                />
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.projectCalls")}
                  value={String(scopeHitsTotal.project ?? 0)}
                />
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.userCalls")}
                  value={String(scopeHitsTotal.user ?? 0)}
                />
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.created")}
                  value={String(createdTaskMemoryCount)}
                />
                <TaskMemoryMetric
                  label={t("evolution.taskMemory.observability.avgLatency")}
                  value={t("evolution.taskMemory.observability.ms", {
                    value: elapsedMsAvg,
                  })}
                />
              </div>
            ) : (
              <p className="text-[11px] leading-4 text-muted-foreground">
                {t("evolution.taskMemory.observability.empty")}
              </p>
            )}
            <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
              {t("evolution.taskMemory.injectedHint")}
            </p>
          </TaskMemoryFoldout>
          <TaskMemoryFoldout
            open={contributionOpen}
            title={t("evolution.taskMemory.observability.contributionTitle")}
            summary={contributionSummary}
            onToggle={() => setContributionOpen((current) => !current)}
          >
            {observabilityQuery.isLoading ? (
              <div className="grid grid-cols-3 gap-1.5">
                {[0, 1, 2].map((item) => (
                  <div key={item} className="h-10 animate-pulse rounded bg-muted/70" />
                ))}
              </div>
            ) : observabilityQuery.isError ? (
              <p className="text-[11px] leading-4 text-destructive">
                {t("evolution.taskMemory.observability.loadFailed")}
              </p>
            ) : contributionAssociated ? (
              <>
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                  <TaskMemoryMetric
                    label={t("evolution.taskMemory.observability.associated")}
                    value={String(contributionAssociated)}
                  />
                  <TaskMemoryMetric
                    label={t("evolution.taskMemory.observability.scored")}
                    value={String(contribution?.scored_generations ?? 0)}
                  />
                  <TaskMemoryMetric
                    label={t("evolution.taskMemory.observability.positive")}
                    value={String(contributionPositive)}
                  />
                  <TaskMemoryMetric
                    label={t("evolution.taskMemory.observability.bestDelta")}
                    value={formatContributionDelta(contribution?.best_delta)}
                  />
                  <TaskMemoryMetric
                    label={t("evolution.taskMemory.observability.averageDelta")}
                    value={formatContributionDelta(contribution?.average_delta)}
                  />
                </div>
                <div className="mt-2 grid grid-cols-3 gap-1.5">
                  {(["task", "project", "user"] as const).map((scope) => (
                    <TaskMemoryMetric
                      key={scope}
                      label={t(`evolution.taskMemory.observability.${scope}Contribution`)}
                      value={t("evolution.taskMemory.observability.scopeContributionValue", {
                        calls: contribution?.by_scope?.[scope]?.calls ?? 0,
                        positive: contribution?.by_scope?.[scope]?.positive_results ?? 0,
                      })}
                    />
                  ))}
                </div>
                <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
                  {t("evolution.taskMemory.observability.contributionHint")}
                </p>
              </>
            ) : (
              <p className="text-[11px] leading-4 text-muted-foreground">
                {t("evolution.taskMemory.observability.noContribution")}
              </p>
            )}
          </TaskMemoryFoldout>
          <div className="mb-2">
            <div className="text-xs font-medium text-foreground">
              {t("evolution.taskMemory.storedTitle")}
            </div>
          </div>
          {!memoryReady && (
            <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              {disabledReason}
            </div>
          )}
          {canLoadCards && (
            <MemoryCardManager
              scope="task"
              taskId={taskScopeId}
              title={t("evolution.taskMemory.title")}
              description={t("evolution.taskMemory.description")}
              embedded
              loadEnabled={canLoadCards}
              refreshSignal={refreshSignal}
              onCountChange={setMemoryCount}
              defaultExtractionPromptLanguage={taskMemoryExtractionPromptLanguage(task)}
            />
          )}
        </div>
      )}
    </div>
  )
}

function TaskMemoryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border/40 bg-muted/30 px-2 py-1.5">
      <div className="truncate text-[10px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 truncate text-xs font-medium text-foreground">{value}</div>
    </div>
  )
}

function TaskMemoryFoldout({
  open,
  title,
  summary,
  onToggle,
  children,
}: {
  open: boolean
  title: string
  summary: string
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <div className="mb-2 rounded-md border border-border/60 bg-background/70">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={onToggle}
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          {title}
        </span>
        <span className="max-w-[62%] truncate rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
          {summary}
        </span>
      </button>
      {open && <div className="border-t border-border/50 px-3 py-2">{children}</div>}
    </div>
  )
}

function formatContributionDelta(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-"
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(3)}`
}

function eventToInjectionSummary(event: Record<string, unknown>): MemoryInjectionSummary {
  const rawScopeHits = event.scope_hits
  const scopeHits = typeof rawScopeHits === "object" && rawScopeHits !== null
    ? (rawScopeHits as Record<string, number>)
    : {}
  return {
    sampler: String(event.sampler ?? "unknown"),
    strategy: String(event.strategy ?? ""),
    scope_hits: scopeHits,
    deduped_hits: Number(event.deduped_hits ?? 0),
    injected_chars: Number(event.injected_chars ?? 0),
    elapsed_ms: Number(event.elapsed_ms ?? 0),
  }
}

/**
 * Compact task actions for the top bar, shown while the right panel is
 * collapsed. Mirrors the panel's primary action (run/stop) as a visible
 * button, tucks the secondary actions into a "more" menu, and offers an
 * explicit expand affordance.
 */
function CollapsedTaskActionButtons({
  task,
  onExpand,
}: {
  task: TaskResponse
  onExpand: () => void
}) {
  const { t } = useTranslation()
  const actions = useTaskActions(task)
  const {
    isCopying,
    dataEmpty,
    stopMutation,
    runMutation,
    copyAndRunMutation,
    isRunning,
    isUninitialized,
    canRerun,
    isActionPending,
    setConfirmAction,
    showAdjust,
    showViewParams,
    showAiBuildHistory,
    viewParams,
    adjustParams,
    openAiBuildHistory,
  } = actions

  const hasMore = showViewParams || showAdjust || showAiBuildHistory

  return (
    <div className="flex items-center gap-1.5">
      {/* Primary action */}
      {isRunning && (
        <Button
          variant="destructive"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={stopMutation.isPending}
          onClick={() => stopMutation.mutate()}
        >
          {stopMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Square className="size-3.5" />
          )}
          <span className="hidden lg:inline">{t("evolution.stopTask")}</span>
        </Button>
      )}
      {canRerun && (
        <Button
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={isActionPending}
          onClick={() => setConfirmAction("copyRun")}
        >
          {copyAndRunMutation.isPending || isCopying ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Play className="size-3.5" />
          )}
          <span className="hidden lg:inline">
            {isCopying
              ? t("evolution.versionTree.copyingAsChild")
              : t("evolution.startTask")}
          </span>
        </Button>
      )}
      {isUninitialized && (
        <Button
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={runMutation.isPending || dataEmpty}
          onClick={() => runMutation.mutate()}
        >
          {runMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Play className="size-3.5" />
          )}
          <span className="hidden lg:inline">{t("evolution.startTask")}</span>
        </Button>
      )}

      {/* Overflow menu — secondary actions + expand */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 text-muted-foreground hover:text-primary"
            aria-label={t("evolution.moreActions", {
              defaultValue: "More actions",
            })}
          >
            <MoreHorizontal className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[180px]">
          {showViewParams && (
            <DropdownMenuItem onClick={viewParams}>
              <Eye className="size-3.5" />
              {t("evolution.viewParams")}
            </DropdownMenuItem>
          )}
          {showAdjust && (
            <DropdownMenuItem onClick={adjustParams} disabled={isActionPending}>
              <Settings2 className="size-3.5" />
              {t("evolution.adjustParams")}
            </DropdownMenuItem>
          )}
          {showAiBuildHistory && (
            <DropdownMenuItem onClick={openAiBuildHistory}>
              <History className="size-3.5" />
              {t("evolution.aiBuildHistory")}
            </DropdownMenuItem>
          )}
          {hasMore && <DropdownMenuSeparator />}
          <DropdownMenuItem onClick={onExpand}>
            <PanelRightOpen className="size-3.5" />
            {t("evolution.expandPanel")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <TaskActionDialogs actions={actions} />
    </div>
  )
}

function taskMemoryExtractionPromptLanguage(task: TaskResponse): "auto" | "ZH" | "EN" {
  const memory = taskMemoryConfig(task)
  if (!memory) return "auto"
  const value = String(
    memory.mindmemos_extraction_prompt_language ?? "auto",
  )
  return value === "ZH" || value === "EN" ? value : "auto"
}

function taskMemoryConfig(task: TaskResponse): Record<string, unknown> | null {
  const memory = task.input_args?.memory
  if (!memory || typeof memory !== "object" || Array.isArray(memory)) return null
  return memory as Record<string, unknown>
}

function isMindMemOSTask(task: TaskResponse): boolean {
  const memory = taskMemoryConfig(task)
  return memory?.enabled !== false && memory?.type === "mindmemos_cloud"
}

export function CollapsedRightPanelActions({
  task,
  onExpand,
}: {
  task: TaskResponse | null
  onExpand: () => void
}) {
  const { t } = useTranslation()

  // Without a task there are no actions — still offer the expand affordance.
  if (!task) {
    return (
      <Button
        variant="ghost"
        size="icon"
        className="size-8 text-muted-foreground hover:text-primary"
        onClick={onExpand}
        aria-label={t("evolution.expandPanel")}
        title={t("evolution.expandPanel")}
      >
        <PanelRightOpen className="size-4" />
      </Button>
    )
  }

  return <CollapsedTaskActionButtons task={task} onExpand={onExpand} />
}

function NodeLink({
  nodeId,
  currentIsland,
  nodeMap,
  onClick,
  onLocate,
  crossIslandLabel,
  locateLabel,
}: {
  nodeId: string
  currentIsland: number
  nodeMap: Map<string, GANode>
  onClick: () => void
  onLocate: () => void
  crossIslandLabel: string
  locateLabel: string
}) {
  const node = nodeMap.get(nodeId)
  const nodeIsland = node?.island ?? -1
  const isCrossIsland = nodeIsland !== currentIsland && nodeIsland >= 0
  const ariaLabel = node
    ? `${node.name} (${formatScore(node.rawScore)})`
    : nodeId

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onClick()
        }
      }}
      aria-label={ariaLabel}
      className="flex items-center gap-2 w-full px-2 py-1.5 rounded cursor-pointer
        bg-muted/60 border border-border/30
        hover:border-primary/30 hover:bg-accent/60
        transition-all duration-200 text-left group"
    >
      {nodeIsland >= 0 && (
        <span
          className="size-1.5 rounded-full shrink-0"
          style={{
            backgroundColor: ISLAND_COLORS[nodeIsland % ISLAND_COLORS.length],
          }}
        />
      )}
      <span className="font-mono text-[10px] text-muted-foreground group-hover:text-primary transition-colors flex-1 truncate">
        {node ? node.name : nodeId}
      </span>
      {node && (
        <span className="font-mono text-[9px] text-muted-foreground/70 shrink-0">
          {formatScore(node.rawScore)}
        </span>
      )}
      <span className="font-mono text-[9px] text-muted-foreground/50 shrink-0">
        {nodeId.slice(0, 8)}
      </span>
      {isCrossIsland && (
        <span
          className="text-[9px] px-1.5 py-0.5 rounded border shrink-0"
          style={{
            color: "#ffa500",
            borderColor: "rgba(255,165,0,0.3)",
            backgroundColor: "rgba(255,165,0,0.08)",
          }}
        >
          {crossIslandLabel}
        </span>
      )}
      {node && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onLocate()
          }}
          className="shrink-0 p-0.5 rounded text-muted-foreground/50
            opacity-0 group-hover:opacity-100
            hover:text-primary hover:bg-primary/10 transition-all"
          title={locateLabel}
          aria-label={locateLabel}
        >
          <Crosshair className="size-3" />
        </button>
      )}
    </div>
  )
}

function NodeInfoSection({
  isOpen,
  onToggle,
}: {
  isOpen: boolean
  onToggle: () => void
}) {
  const {
    selectedNodes,
    setSelectedNodes,
    evolutionData,
    setActiveTab,
    activeTab,
    requestFocusNode,
  } = useEvolution()
  const { t } = useTranslation()
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const nodeMap = useMemo(() => {
    const m = new Map<string, GANode>()
    for (const n of evolutionData.nodes) m.set(n.id, n)
    return m
  }, [evolutionData])

  const childrenMap = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const n of evolutionData.nodes) {
      for (const pid of n.parentIds) {
        if (!m.has(pid)) m.set(pid, [])
        m.get(pid)!.push(n.id)
      }
    }
    return m
  }, [evolutionData])

  const classificationMap = useMemo(
    () => computeNodeClassifications(evolutionData),
    [evolutionData],
  )

  const selectedNode = selectedNodes.length === 1 ? selectedNodes[0] : null

  // Sort children by score desc — most relevant first
  const sortedChildIds = useMemo(() => {
    if (!selectedNode) return []
    const ids = childrenMap.get(selectedNode.id) || []
    return [...ids].sort((a, b) => {
      const na = nodeMap.get(a)
      const nb = nodeMap.get(b)
      return (nb?.rawScore ?? 0) - (na?.rawScore ?? 0)
    })
  }, [selectedNode, childrenMap, nodeMap])

  // Sort parents by score desc
  const sortedParentIds = useMemo(() => {
    if (!selectedNode) return []
    return [...selectedNode.parentIds].sort((a, b) => {
      const na = nodeMap.get(a)
      const nb = nodeMap.get(b)
      return (nb?.rawScore ?? 0) - (na?.rawScore ?? 0)
    })
  }, [selectedNode, nodeMap])

  // Show-more toggles for parent / children lists
  const PREVIEW_COUNT = 5
  const [showAllParents, setShowAllParents] = useState(false)
  const [showAllChildren, setShowAllChildren] = useState(false)

  // Scroll to top + reset show-all when selection changes
  // biome-ignore lint/correctness/useExhaustiveDependencies: react to node id change
  useEffect(() => {
    setShowAllParents(false)
    setShowAllChildren(false)
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = 0
    }
  }, [selectedNode?.id, selectedNodes.length])

  const navigateToNode = (nodeId: string) => {
    const node = nodeMap.get(nodeId)
    if (node) {
      setSelectedNodes([
        {
          id: node.id,
          generation: node.generation,
          island: node.island,
          islandId: node.islandId,
          name: node.name,
          fileName: node.fileName,
          score: node.score,
          rawScore: node.rawScore,
          parentIds: node.parentIds,
        },
      ])
    }
  }

  const removeNode = (nodeId: string) => {
    setSelectedNodes(selectedNodes.filter((n) => n.id !== nodeId))
  }

  // Multi-select stats
  const multiStats = useMemo(() => {
    if (selectedNodes.length < 2) return null
    const scores = selectedNodes.map((n) => n.rawScore)
    const gens = selectedNodes.map((n) => n.generation)
    const islands = new Set(selectedNodes.map((n) => n.island))
    const max = Math.max(...scores)
    const min = Math.min(...scores)
    const avg = scores.reduce((s, v) => s + v, 0) / scores.length
    return {
      max,
      min,
      avg,
      genMin: Math.min(...gens),
      genMax: Math.max(...gens),
      islandCount: islands.size,
    }
  }, [selectedNodes])

  // Sort multi-select list by score desc
  const sortedMultiNodes = useMemo(() => {
    return [...selectedNodes].sort((a, b) => b.rawScore - a.rawScore)
  }, [selectedNodes])

  // Collapsed summary
  const summary = (() => {
    if (selectedNodes.length === 0) return null
    if (selectedNodes.length === 1 && selectedNode) {
      return (
        <span className="text-[10px] text-muted-foreground truncate">
          {selectedNode.name}
        </span>
      )
    }
    return (
      <span className="text-[10px] text-primary">
        {t("evolution.selectedNodesCount", { count: selectedNodes.length })}
      </span>
    )
  })()

  return (
    <div className={`flex flex-col min-h-0 ${isOpen ? "flex-1" : "shrink-0"}`}>
      <SectionHeader
        icon={Network}
        title={t("evolution.nodeInfo")}
        isOpen={isOpen}
        onToggle={onToggle}
        summary={summary}
      />
      {isOpen && (
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-y-auto px-4 py-3 border-b border-border/30"
        >
          {selectedNodes.length === 0 ? (
            /* ===== No selection — minimal hint ===== */
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <div
                className="p-4 rounded-full"
                style={{
                  background:
                    "radial-gradient(circle, color-mix(in srgb, var(--primary) 6%, transparent) 0%, transparent 70%)",
                }}
              >
                <MousePointerClick className="size-7 text-muted-foreground/50" />
              </div>
              <div className="text-center space-y-1">
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {t("evolution.clickNodeHint")}
                </p>
                <p className="text-[10px] text-muted-foreground/50">
                  {t("evolution.multiSelectHint")}
                </p>
              </div>
            </div>
          ) : selectedNodes.length === 1 && selectedNode ? (
            /* ===== Single node selected ===== */
            <div className="space-y-3">
              {/* Node header */}
              <div
                className="px-3 py-2 rounded-lg border"
                style={{
                  borderColor: `${ISLAND_COLORS[selectedNode.island % ISLAND_COLORS.length]}30`,
                  backgroundColor: `${ISLAND_COLORS[selectedNode.island % ISLAND_COLORS.length]}08`,
                }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <div
                    className="size-2.5 rounded-full shrink-0"
                    style={{
                      backgroundColor:
                        ISLAND_COLORS[
                          selectedNode.island % ISLAND_COLORS.length
                        ],
                      boxShadow: `0 0 6px ${ISLAND_COLORS[selectedNode.island % ISLAND_COLORS.length]}60`,
                    }}
                  />
                  <span className="text-sm font-semibold text-foreground truncate flex-1">
                    {selectedNode.name}
                  </span>
                  {activeTab !== "ide" && (
                    <button
                      type="button"
                      onClick={() => setActiveTab("ide")}
                      title={t("evolution.nodeActions.openIDE")}
                      aria-label={t("evolution.nodeActions.openIDE")}
                      className="shrink-0 inline-flex items-center justify-center size-6 rounded
                        text-muted-foreground hover:text-primary hover:bg-primary/10
                        border border-border/40 hover:border-primary/40 transition-colors"
                    >
                      <Code className="size-3.5" />
                    </button>
                  )}
                </div>
                <span className="text-[10px] font-mono text-muted-foreground">
                  {selectedNode.id}
                </span>
              </div>

              {/* Classification badges */}
              {(() => {
                const cls = classificationMap.get(selectedNode.id)
                if (!cls) return null
                const islandColor =
                  ISLAND_COLORS[selectedNode.island % ISLAND_COLORS.length]
                const badges: { label: string; color: string }[] = []
                if (cls.isGlobalBest)
                  badges.push({
                    label: t("evolution.globalBest"),
                    color: "#ffbf00",
                  })
                if (cls.isGlobalGenBest)
                  badges.push({
                    label: t("evolution.globalGenBest"),
                    color: islandColor,
                  })
                if (cls.isIslandOverallBest)
                  badges.push({
                    label: t("evolution.islandBest"),
                    color: islandColor,
                  })
                if (cls.isIslandGenBest)
                  badges.push({
                    label: t("evolution.generationBest"),
                    color: islandColor,
                  })
                if (cls.isElite)
                  badges.push({
                    label: t("evolution.eliteRetained"),
                    color: "#8ca0bc",
                  })
                if (badges.length === 0) return null
                return (
                  <div className="flex flex-wrap gap-1.5">
                    {badges.map((b) => (
                      <span
                        key={b.label}
                        className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border"
                        style={{
                          color: b.color,
                          borderColor: `${b.color}40`,
                          backgroundColor: `${b.color}15`,
                        }}
                      >
                        {b.label}
                      </span>
                    ))}
                  </div>
                )
              })()}

              {/* Score */}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
                    {t("evolution.fitnessScore")}
                  </span>
                  <span
                    className="text-[9px] text-muted-foreground/60"
                    title={t("evolution.scoreNormalizedHint", {
                      defaultValue:
                        "Bar shows score normalized within current population",
                    })}
                  >
                    {(selectedNode.score * 100).toFixed(4)}%
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${selectedNode.score * 100}%`,
                        background: `linear-gradient(90deg, #004466, ${ISLAND_COLORS[selectedNode.island % ISLAND_COLORS.length]})`,
                        boxShadow: `0 0 6px ${ISLAND_COLORS[selectedNode.island % ISLAND_COLORS.length]}40`,
                      }}
                    />
                  </div>
                  <span className="text-sm font-mono font-bold text-primary min-w-[48px] text-right">
                    {formatScore(selectedNode.rawScore)}
                  </span>
                </div>
              </div>

              <InfoRow
                label={t("evolution.generation")}
                value={t("evolution.generationValue", {
                  gen: selectedNode.generation,
                })}
              />
              <InfoRow label={t("evolution.belongIsland")}>
                <span className="flex items-center gap-1.5">
                  <span
                    className="size-2 rounded-full"
                    style={{
                      backgroundColor:
                        ISLAND_COLORS[
                          selectedNode.island % ISLAND_COLORS.length
                        ],
                    }}
                  />
                  <span
                    className="text-xs font-medium"
                    style={{
                      color:
                        ISLAND_COLORS[
                          selectedNode.island % ISLAND_COLORS.length
                        ],
                    }}
                  >
                    {selectedNode.islandId || `Island ${selectedNode.island}`}
                  </span>
                </span>
              </InfoRow>

              {/* Parents — sorted by score, truncated */}
              <div className="space-y-1">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
                  {t("evolution.parentNodes")} ({sortedParentIds.length})
                </span>
                {sortedParentIds.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    {t("evolution.initialPopulation")}
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {(showAllParents
                      ? sortedParentIds
                      : sortedParentIds.slice(0, PREVIEW_COUNT)
                    ).map((pid) => (
                      <NodeLink
                        key={pid}
                        nodeId={pid}
                        currentIsland={selectedNode.island}
                        nodeMap={nodeMap}
                        onClick={() => navigateToNode(pid)}
                        onLocate={() => requestFocusNode(pid, false)}
                        crossIslandLabel={t("evolution.crossIsland")}
                        locateLabel={t("evolution.locateNode")}
                      />
                    ))}
                    {sortedParentIds.length > PREVIEW_COUNT && (
                      <button
                        type="button"
                        onClick={() => setShowAllParents((v) => !v)}
                        className="text-[10px] text-muted-foreground hover:text-primary transition-colors"
                      >
                        {showAllParents
                          ? t("evolution.showLess", {
                              defaultValue: "收起",
                            })
                          : t("evolution.showAllCount", {
                              defaultValue: "查看全部 ({{count}})",
                              count: sortedParentIds.length,
                            })}
                      </button>
                    )}
                  </div>
                )}
              </div>

              {/* Children — sorted by score, truncated */}
              <div className="space-y-1">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
                  {t("evolution.childNodes")} ({sortedChildIds.length})
                </span>
                {sortedChildIds.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    {selectedNode.generation === evolutionData.maxGeneration
                      ? t("evolution.finalGeneration")
                      : t("evolution.noChildNodes")}
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {(showAllChildren
                      ? sortedChildIds
                      : sortedChildIds.slice(0, PREVIEW_COUNT)
                    ).map((cid) => (
                      <NodeLink
                        key={cid}
                        nodeId={cid}
                        currentIsland={selectedNode.island}
                        nodeMap={nodeMap}
                        onClick={() => navigateToNode(cid)}
                        onLocate={() => requestFocusNode(cid, false)}
                        crossIslandLabel={t("evolution.crossIsland")}
                        locateLabel={t("evolution.locateNode")}
                      />
                    ))}
                    {sortedChildIds.length > PREVIEW_COUNT && (
                      <button
                        type="button"
                        onClick={() => setShowAllChildren((v) => !v)}
                        className="text-[10px] text-muted-foreground hover:text-primary transition-colors"
                      >
                        {showAllChildren
                          ? t("evolution.showLess", {
                              defaultValue: "收起",
                            })
                          : t("evolution.showAllCount", {
                              defaultValue: "查看全部 ({{count}})",
                              count: sortedChildIds.length,
                            })}
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* ===== Multiple nodes selected ===== */
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-foreground">
                  {t("evolution.selectedNodesCount", {
                    count: selectedNodes.length,
                  })}
                </span>
                <button
                  type="button"
                  onClick={() => setSelectedNodes([])}
                  className="text-[10px] text-muted-foreground hover:text-primary transition-colors"
                >
                  {t("evolution.nodeActions.clearSelection")}
                </button>
              </div>

              {/* Stats */}
              {multiStats && (
                <div className="grid grid-cols-2 gap-1.5 text-[10px]">
                  <div className="px-2 py-1.5 rounded border border-border/30 bg-muted/40">
                    <div className="text-muted-foreground/70 uppercase tracking-wider">
                      {t("evolution.multi.maxScore", {
                        defaultValue: "Max",
                      })}
                    </div>
                    <div className="font-mono text-primary font-medium">
                      {formatScore(multiStats.max)}
                    </div>
                  </div>
                  <div className="px-2 py-1.5 rounded border border-border/30 bg-muted/40">
                    <div className="text-muted-foreground/70 uppercase tracking-wider">
                      {t("evolution.multi.avgScore", {
                        defaultValue: "Avg",
                      })}
                    </div>
                    <div className="font-mono text-foreground/80 font-medium">
                      {formatScore(multiStats.avg)}
                    </div>
                  </div>
                  <div className="px-2 py-1.5 rounded border border-border/30 bg-muted/40">
                    <div className="text-muted-foreground/70 uppercase tracking-wider">
                      {t("evolution.multi.minScore", {
                        defaultValue: "Min",
                      })}
                    </div>
                    <div className="font-mono text-foreground/80 font-medium">
                      {formatScore(multiStats.min)}
                    </div>
                  </div>
                  <div className="px-2 py-1.5 rounded border border-border/30 bg-muted/40">
                    <div className="text-muted-foreground/70 uppercase tracking-wider">
                      {t("evolution.multi.genRange", {
                        defaultValue: "Gen",
                      })}
                    </div>
                    <div className="font-mono text-foreground/80 font-medium">
                      {multiStats.genMin === multiStats.genMax
                        ? multiStats.genMin
                        : `${multiStats.genMin}–${multiStats.genMax}`}
                    </div>
                  </div>
                  <div className="col-span-2 px-2 py-1.5 rounded border border-border/30 bg-muted/40 flex items-center justify-between">
                    <span className="text-muted-foreground/70 uppercase tracking-wider">
                      {t("evolution.multi.islandCount", {
                        defaultValue: "Islands",
                      })}
                    </span>
                    <span className="font-mono text-foreground/80 font-medium">
                      {multiStats.islandCount}
                    </span>
                  </div>
                </div>
              )}

              {/* Compact node list — sorted by score desc */}
              <div className="space-y-1.5 max-h-[240px] overflow-y-auto">
                {sortedMultiNodes.map((node) => (
                  <div
                    key={node.id}
                    className="flex items-center gap-2 px-2 py-1.5 rounded
                      bg-muted/60 border border-border/30 group cursor-pointer
                      hover:border-primary/30 hover:bg-accent/60 transition-colors"
                    onClick={() => navigateToNode(node.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        navigateToNode(node.id)
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={`${node.name} (${formatScore(node.rawScore)})`}
                  >
                    <span
                      className="size-2 rounded-full shrink-0"
                      style={{
                        backgroundColor:
                          ISLAND_COLORS[node.island % ISLAND_COLORS.length],
                      }}
                    />
                    <span className="text-xs text-foreground/80 truncate flex-1">
                      {node.name}
                    </span>
                    <span className="text-[10px] font-mono text-muted-foreground/70 shrink-0">
                      {formatScore(node.rawScore)}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        removeNode(node.id)
                      }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity
                        text-muted-foreground hover:text-destructive p-0.5"
                      title={t("evolution.nodeActions.removeNode")}
                      aria-label={t("evolution.nodeActions.removeNode")}
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function RunLogsSection({
  isOpen,
  onToggle,
}: {
  isOpen: boolean
  onToggle: () => void
}) {
  const { effectiveTaskId, effectiveStatus, logEntries, logLoading } =
    useEvolution()
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)

  const isActive =
    effectiveStatus === "pending" || effectiveStatus === "running"

  const [searchQuery, setSearchQuery] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [levelFilter, setLevelFilter] = useState<Set<LogLevel>>(new Set())

  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on task switch
  useEffect(() => {
    setSearchQuery("")
    setSearchInput("")
    setLevelFilter(new Set())
    isNearBottomRef.current = true
  }, [effectiveTaskId])

  // Shared REST query (terminal mode only) — multiple subscribers dedupe.
  // Active streaming uses logEntries from SSE, so disable here.
  const {
    entries: restEntries,
    isLoading: isInitialLoading,
    isFetchingMore: isLoadingMore,
    hasMore,
    loadMore,
  } = useTaskLogsList({
    taskId: effectiveTaskId,
    searchQuery,
    levelFilter,
    enabled: !isActive && !!effectiveTaskId && isOpen,
  })

  const handleSearch = useCallback(() => {
    setSearchQuery(searchInput.trim())
  }, [searchInput])

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") handleSearch()
    },
    [handleSearch],
  )

  const clearSearch = useCallback(() => {
    setSearchInput("")
    setSearchQuery("")
  }, [])

  const rawEntries = isActive ? logEntries : restEntries

  // Streaming mode filters client-side; terminal mode filters are baked into
  // the query key (server-side) so rawEntries is already final.
  const displayEntries = useMemo(() => {
    if (!isActive) return rawEntries
    let filtered = rawEntries
    if (levelFilter.size > 0) {
      filtered = filtered.filter((e) => matchesLogLevels(e, levelFilter))
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter((e) => {
        const msg = String(e.message ?? e.msg ?? "").toLowerCase()
        return msg.includes(q)
      })
    }
    return filtered
  }, [rawEntries, levelFilter, isActive, searchQuery])

  const isLoading = isActive ? logLoading : isInitialLoading

  const virtualizer = useVirtualizer({
    count: displayEntries.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 20,
    overscan: 30,
  })

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const threshold = 40
    isNearBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  }, [])

  // biome-ignore lint/correctness/useExhaustiveDependencies: effectiveTaskId triggers scroll on task switch
  useEffect(() => {
    if (displayEntries.length > 0 && isNearBottomRef.current) {
      virtualizer.scrollToIndex(displayEntries.length - 1, { align: "end" })
    }
  }, [displayEntries.length, virtualizer, effectiveTaskId])

  const summary =
    rawEntries.length > 0 ? (
      <span className="text-[10px] text-muted-foreground">
        {displayEntries.length}
        {levelFilter.size > 0 && ` / ${rawEntries.length}`}
      </span>
    ) : null

  return (
    <div className={`flex flex-col min-h-0 ${isOpen ? "flex-1" : "shrink-0"}`}>
      <SectionHeader
        icon={FileText}
        title={t("evolution.runLogs")}
        isOpen={isOpen}
        onToggle={onToggle}
        summary={summary}
      />
      {isOpen && (
        <div className="flex-1 overflow-hidden border-b border-border/30 flex flex-col">
          {/* Toolbar: search + level filter */}
          <div className="shrink-0 border-b border-border/30">
            {/* Search bar (always shown; filters client-side when streaming) */}
            <div className="flex items-center gap-1.5 px-3 py-2">
              <Search className="size-3.5 text-muted-foreground shrink-0" />
              <input
                type="text"
                value={searchInput}
                maxLength={INPUT_LIMITS.search}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={handleSearchKeyDown}
                placeholder={t("evolution.logs.searchPlaceholder", {
                  defaultValue: "搜索日志...",
                })}
                aria-label={t("evolution.logs.searchPlaceholder", {
                  defaultValue: "搜索日志",
                })}
                className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/50"
              />
              {searchInput && (
                <button
                  type="button"
                  onClick={clearSearch}
                  aria-label={t("common.cancel")}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="size-3" />
                </button>
              )}
              <span className="text-[10px] text-muted-foreground/60">
                {t("evolution.logs.searchHint", {
                  defaultValue: "Enter 搜索",
                })}
              </span>
            </div>

            {/* Level filter */}
            <div className="flex items-center gap-0.5 px-2 py-1.5">
              <span className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mr-1">
                {t("evolution.logs.level", { defaultValue: "Level" })}
              </span>
              {LOG_LEVELS.map((lv) => {
                const isActiveLv = levelFilter.has(lv)
                return (
                  <button
                    key={lv}
                    type="button"
                    onClick={() =>
                      setLevelFilter((prev) => {
                        const next = new Set(prev)
                        if (next.has(lv)) next.delete(lv)
                        else next.add(lv)
                        return next
                      })
                    }
                    className={cn(
                      "px-1.5 py-0.5 text-[10px] rounded font-medium transition-colors",
                      isActiveLv
                        ? "text-primary bg-primary/10 border border-primary/30"
                        : "text-muted-foreground hover:text-foreground border border-transparent hover:border-border/40",
                    )}
                  >
                    {lv}
                  </button>
                )
              })}
              {levelFilter.size > 0 && (
                <button
                  type="button"
                  onClick={() => setLevelFilter(new Set())}
                  className="ml-0.5 p-0.5 rounded text-muted-foreground/60 hover:text-foreground hover:bg-accent/60 transition-colors"
                  title={t("evolution.logs.clearLevel", {
                    defaultValue: "Clear filter",
                  })}
                >
                  <X className="size-3" />
                </button>
              )}
            </div>
          </div>

          {isLoading ? (
            <div className="px-4 py-3 text-xs font-mono bg-card">
              <div className="text-muted-foreground py-4">
                {t("evolution.logs.loading")}
              </div>
            </div>
          ) : displayEntries.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-3 px-4">
              <div
                className="p-4 rounded-full"
                style={{
                  background:
                    "radial-gradient(circle, color-mix(in srgb, var(--primary) 6%, transparent) 0%, transparent 70%)",
                }}
              >
                <FileText className="size-7 text-muted-foreground/50" />
              </div>
              <div className="text-center space-y-1">
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {searchQuery
                    ? t("evolution.logs.noSearchResults", {
                        defaultValue: "未找到匹配的日志",
                      })
                    : levelFilter.size > 0 && rawEntries.length > 0
                      ? t("evolution.logs.noLevelMatch", {
                          defaultValue: "当前等级无日志",
                        })
                      : t("evolution.runLogsEmpty")}
                </p>
                <p className="text-[10px] text-muted-foreground/50">
                  {searchQuery
                    ? t("evolution.logs.tryDifferentSearch", {
                        defaultValue: "尝试其他搜索关键词",
                      })
                    : t("evolution.runLogsEmptyHint")}
                </p>
              </div>
            </div>
          ) : (
            <div
              ref={containerRef}
              onScroll={handleScroll}
              className="flex-1 w-full overflow-y-auto px-4 py-3 text-xs font-mono bg-card"
            >
              {/* Load more button (terminal mode only) */}
              {!isActive && hasMore && (
                <div className="flex justify-center pb-2">
                  <button
                    type="button"
                    onClick={loadMore}
                    disabled={isLoadingMore}
                    className="flex items-center gap-1.5 px-3 py-1 rounded text-[10px] font-medium
                      border border-border/60 text-muted-foreground
                      hover:text-primary hover:border-primary/40 transition-colors
                      disabled:opacity-50"
                  >
                    {isLoadingMore ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <ChevronUp className="size-3" />
                    )}
                    {t("evolution.logs.loadMore", { defaultValue: "加载更多" })}
                  </button>
                </div>
              )}
              <div
                style={{
                  height: virtualizer.getTotalSize(),
                  width: "100%",
                  position: "relative",
                }}
              >
                {virtualizer.getVirtualItems().map((virtualRow) => (
                  <div
                    key={virtualRow.key}
                    ref={virtualizer.measureElement}
                    data-index={virtualRow.index}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "max-content",
                      minWidth: "100%",
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    {renderEntry(displayEntries[virtualRow.index])}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function InfoRow({
  label,
  value,
  children,
}: {
  label: string
  value?: string
  children?: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
        {label}
      </span>
      {children ?? (
        <span className="text-xs text-foreground/80 break-all">{value}</span>
      )}
    </div>
  )
}

interface EvolutionRightPanelProps {
  task: TaskResponse | null
}

export default function EvolutionRightPanel({
  task,
}: EvolutionRightPanelProps) {
  const {
    isConfiguring,
    isViewingParams,
    isViewingAiBuildHistory,
    selectedNodes,
    effectiveTaskId,
  } = useEvolution()
  const { t } = useTranslation()

  const isTuning = isConfiguring || isViewingParams || isViewingAiBuildHistory
  const [infoOpen, setInfoOpen] = useState(true)
  const [memoryOpen, setMemoryOpen] = useState(true)
  const [nodeOpen, setNodeOpen] = useState(!isTuning)
  const [logsOpen, setLogsOpen] = useState(true)
  const showTaskMemory = task ? isMindMemOSTask(task) : false

  useEffect(() => {
    if (isTuning) {
      setLogsOpen(true)
    } else {
      setNodeOpen(true)
    }
  }, [isTuning])

  useEffect(() => {
    if (showTaskMemory) setMemoryOpen(true)
  }, [task?.id, effectiveTaskId, showTaskMemory])

  if (!task) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-xs text-muted-foreground/50">
          {t("evolution.selectTask")}
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <PanelErrorBoundary resetKey={task.id}>
        <TaskInfoSection
          task={task}
          isOpen={infoOpen}
          onToggle={() => setInfoOpen(!infoOpen)}
        />
      </PanelErrorBoundary>
      {showTaskMemory && (
        <PanelErrorBoundary resetKey={task.id}>
          <TaskMemorySection
            task={task}
            isOpen={memoryOpen}
            onToggle={() => setMemoryOpen(!memoryOpen)}
          />
        </PanelErrorBoundary>
      )}
      {isTuning ? (
        <PanelErrorBoundary resetKey={task.id}>
          <RunLogsSection
            isOpen={logsOpen}
            onToggle={() => setLogsOpen(!logsOpen)}
          />
        </PanelErrorBoundary>
      ) : (
        selectedNodes.length > 0 && (
          <PanelErrorBoundary resetKey={task.id}>
            <NodeInfoSection
              isOpen={nodeOpen}
              onToggle={() => setNodeOpen(!nodeOpen)}
            />
          </PanelErrorBoundary>
        )
      )}
    </div>
  )
}
