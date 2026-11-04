import { useQuery } from "@tanstack/react-query"
import { ArrowLeft, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { Llm4AdTasksService } from "@/client"
import AutoDesignPanel from "@/components/AutoDesign/AutoDesignPanel"
import { Button } from "@/components/ui/button"
import { buildDemoTask, isDemoTaskId } from "@/data/demoFixtures"
import { useDemoState } from "@/hooks/useDemoMode"
import { useEvolution } from "@/hooks/useEvolution"
import { rememberAgentVersion, taskViewPanelId } from "@/lib/autoDesign"
import { cn } from "@/lib/utils"

import ChatTuneView from "./ChatTuneView"
import DemoBuildView from "./DemoBuildView"
import InitializedView from "./InitializedView"
import UninitializedView from "./UninitializedView"

interface TaskDetailProps {
  taskId: string
}

export default function TaskDetail({ taskId }: TaskDetailProps) {
  const { t } = useTranslation()
  const {
    taskView,
    taskAgentAvailable,
    setTaskView,
    projectId,
    setSelectedTask,
    setSelectedChildTaskId,
    setIsViewingParams,
    setIsConfiguring,
  } = useEvolution()
  const agentLocked = !taskAgentAvailable
  const visibleView = agentLocked ? "workbench" : taskView
  useEffect(() => {
    if (agentLocked && taskView === "agent") setTaskView("workbench")
  }, [agentLocked, taskView, setTaskView])
  const [openedVersions, setOpenedVersions] = useState<string[]>([])
  useEffect(() => {
    if (taskView === "agent" && !isDemoTaskId(taskId)) {
      setOpenedVersions((opened) => rememberAgentVersion(opened, taskId))
    }
  }, [taskView, taskId])
  if (isDemoTaskId(taskId)) return <TaskExecutionView taskId={taskId} />
  const versions =
    taskView === "agent"
      ? rememberAgentVersion(openedVersions, taskId)
      : openedVersions
  return (
    <div className="flex h-full min-h-0 flex-col">
      <section
        id={taskViewPanelId(taskId, "workbench")}
        aria-label={t("autodesign.workbench")}
        hidden={visibleView !== "workbench"}
        className={cn(
          "min-h-0 flex-1 overflow-auto",
          visibleView !== "workbench" && "hidden",
        )}
      >
        <TaskExecutionView taskId={taskId} />
      </section>
      <section
        id={taskViewPanelId(taskId, "agent")}
        aria-label={t("autodesign.agent")}
        hidden={visibleView !== "agent"}
        className={cn("min-h-0 flex-1", visibleView !== "agent" && "hidden")}
      >
        {!agentLocked &&
          versions.map((versionId) => (
            <div
              key={versionId}
              hidden={versionId !== taskId}
              className={cn("h-full min-h-0", versionId !== taskId && "hidden")}
            >
              <AutoDesignPanel
                taskId={versionId}
                projectId={projectId}
                headerAction={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setTaskView("workbench")}
                  >
                    <ArrowLeft data-icon="inline-start" />
                    {t("autodesign.parameterActions.returnToParameters")}
                  </Button>
                }
                onOpenTask={(task) => {
                  setSelectedTask(task)
                  setSelectedChildTaskId(task.id)
                  setIsViewingParams(false)
                  setIsConfiguring(true)
                  setTaskView("workbench")
                }}
              />
            </div>
          ))}
      </section>
    </div>
  )
}

function TaskExecutionView({ taskId }: TaskDetailProps) {
  const { t } = useTranslation()
  const {
    isConfiguring,
    isViewingParams,
    isViewingAiBuildHistory,
    taskAgentAvailable,
    setTaskView,
  } = useEvolution()
  const demoState = useDemoState()
  const { data: task, isLoading } = useQuery({
    queryKey: ["getTask", taskId],
    queryFn: () =>
      isDemoTaskId(taskId)
        ? Promise.resolve(buildDemoTask(demoState.phase))
        : Llm4AdTasksService.getTask({ taskId }),
    enabled: !!taskId,
  })

  if (isDemoTaskId(taskId)) {
    if (
      demoState.phase === "uninitialized" ||
      demoState.phase === "configuring" ||
      demoState.phase === "building"
    ) {
      return <DemoBuildView />
    }
    return <InitializedView task={buildDemoTask(demoState.phase)} />
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!task) {
    return (
      <div className="flex items-center justify-center py-32">
        <p className="text-muted-foreground">{t("evolution.cannotLoadTask")}</p>
      </div>
    )
  }

  // Legacy records remain readable; new turns use the native AutoDesign Agent.
  if (isViewingAiBuildHistory) {
    return <ChatTuneView key={task.id} task={task} readOnly />
  }

  if (isViewingParams || (isConfiguring && task.status === "uninitialized")) {
    return (
      <UninitializedView
        task={task}
        readOnly={isViewingParams}
        onSwitchToChat={
          taskAgentAvailable ? () => setTaskView("agent") : undefined
        }
      />
    )
  }

  return <InitializedView task={task} />
}
