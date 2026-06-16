import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdTasksService } from "@/client"
import { useEvolution } from "@/hooks/useEvolution"

import ChatTuneView from "./ChatTuneView"
import InitializedView from "./InitializedView"
import UninitializedView from "./UninitializedView"

interface TaskDetailProps {
  taskId: string
}

type TuneMode = "chat" | "stepper"

export default function TaskDetail({ taskId }: TaskDetailProps) {
  const { t } = useTranslation()
  const {
    isConfiguring,
    isViewingParams,
    isViewingAiBuildHistory,
    forceManualConfigTaskId,
  } = useEvolution()
  const [tuneMode, setTuneMode] = useState<TuneMode>("chat")
  // Reset to AI chat panel whenever the active task changes — otherwise a prior
  // task's "switched to stepper" state leaks into a freshly created AI task.
  useEffect(() => {
    setTuneMode("chat")
  }, [])
  const { data: task, isLoading } = useQuery({
    queryKey: ["getTask", taskId],
    queryFn: () => Llm4AdTasksService.getTask({ taskId }),
    enabled: !!taskId,
  })

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

  // AI build history → reuse the AI panel. Running/pending tasks are read-only
  // (cannot mutate state while a turn is in flight); other statuses allow the
  // user to keep chatting with the assistant on the existing session.
  if (isViewingAiBuildHistory) {
    const liveLocked = task.status === "running" || task.status === "pending"
    return <ChatTuneView key={task.id} task={task} readOnly={liveLocked} />
  }

  // View / adjust params → always the manual (stepper) panel. The AI build panel
  // is never reachable from these entry points.
  if (isViewingParams || isConfiguring) {
    // The AI build panel is the default only for a fresh uninitialized ai_built
    // task — not for a re-tune copy (tagged via forceManualConfigTaskId) and not
    // for read-only param viewing.
    const aiBuildDefault =
      !isViewingParams &&
      task.status === "uninitialized" &&
      !!task.ai_built &&
      forceManualConfigTaskId !== task.id

    if (aiBuildDefault) {
      if (tuneMode === "chat") {
        return (
          <ChatTuneView
            key={task.id}
            task={task}
            onSwitchToStepper={() => setTuneMode("stepper")}
          />
        )
      }
      return (
        <UninitializedView
          task={task}
          onSwitchToChat={() => setTuneMode("chat")}
        />
      )
    }

    return <UninitializedView task={task} readOnly={isViewingParams} />
  }

  return <InitializedView task={task} />
}
