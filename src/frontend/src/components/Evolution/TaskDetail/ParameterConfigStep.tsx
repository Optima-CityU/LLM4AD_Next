import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { TaskResponse } from "@/client"
import { Llm4AdTasksService } from "@/client"
import { Button } from "@/components/ui/button"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { useEvolution } from "@/hooks/useEvolution"
import { setTaskStatusInCache } from "@/lib/task-queries"
import { handleError } from "@/utils"
import AppConfigForm from "./config-form/AppConfigForm"
import type { AppConfig } from "./config-form/appConfigSchema"

interface ParameterConfigStepProps {
  task: TaskResponse
  onBack: () => void
}

export default function ParameterConfigStep({
  task,
  onBack,
}: ParameterConfigStepProps) {
  const {
    projectId,
    resetTaskData,
    setSelectedTask,
    setActiveTab,
    setIsConfiguring,
    setIsViewingParams,
    updateTaskStatus,
  } = useEvolution()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: async (data: AppConfig) => {
      await Llm4AdTasksService.updateTask({
        taskId: task.id,
        requestBody: { input_args: data as unknown as Record<string, unknown> },
      })
      const result = await Llm4AdTasksService.runTask({ taskId: task.id })
      const nextStatus = result.status ?? "pending"
      setTaskStatusInCache(queryClient, task.id, nextStatus, projectId)
      updateTaskStatus(nextStatus)
    },
    onSuccess: () => {
      showSuccessToast("任务已提交运行")
      setIsConfiguring(false)
      setIsViewingParams(false)
      setActiveTab("overview")
      setSelectedTask({
        ...task,
        status: "pending" as TaskResponse["status"],
      })
      resetTaskData()
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">参数配置</h3>
        <p className="text-sm text-muted-foreground mt-1">配置任务的运行参数</p>
      </div>

      <AppConfigForm
        defaultValues={task.input_args}
        onSubmit={(data) => mutation.mutate(data)}
        footer={
          <div className="flex justify-between pt-4 border-t">
            <Button type="button" variant="outline" onClick={onBack}>
              上一步
            </Button>
            <LoadingButton type="submit" loading={mutation.isPending}>
              确认
            </LoadingButton>
          </div>
        }
      />
    </div>
  )
}
