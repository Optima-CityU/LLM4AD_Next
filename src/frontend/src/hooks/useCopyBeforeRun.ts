import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import type {
  PaginatedTaskResponse,
  TaskResponse,
  TaskTreeResponse,
} from "@/client"
import { Llm4AdTasksService } from "@/client"
import { getTasksInfiniteQueryKey } from "@/components/Evolution/EvolutionTaskList"
import { useEvolution } from "@/hooks/useEvolution"
import { taskKeys } from "@/lib/task-queries"

export function useCopyBeforeRun(task: TaskResponse) {
  const { setSelectedChildTaskId, projectId, selectedTask, setSelectedTask } =
    useEvolution()
  const queryClient = useQueryClient()
  const [isCopying, setIsCopying] = useState(false)

  const withCopyIfNeeded = useCallback(
    async (action: (effectiveTaskId: string) => Promise<void>) => {
      if (task.status === "uninitialized") {
        await action(task.id)
        return
      }

      setIsCopying(true)
      try {
        const child = await Llm4AdTasksService.copyTask({
          taskId: task.id,
          requestBody: { is_child: true },
        })
        queryClient.setQueryData(taskKeys.detail(child.id), child)
        setSelectedChildTaskId(child.id)

        const rootId = task.group_id || task.id

        // Optimistically reflect the new active version in caches & local
        // state so the left sidebar's active_status updates immediately,
        // mirroring TaskVersionTree.handleSelect.
        if (selectedTask && selectedTask.id === rootId) {
          setSelectedTask({
            ...selectedTask,
            active_child_id: child.id,
            active_status: child.status,
          })
        }

        if (projectId) {
          queryClient.setQueryData<{
            pages: PaginatedTaskResponse[]
            pageParams: unknown[]
          }>(getTasksInfiniteQueryKey(projectId), (data) => {
            if (!data) return data
            return {
              ...data,
              pages: data.pages.map((page) => ({
                ...page,
                items: page.items.map((item: TaskResponse) =>
                  item.id === rootId
                    ? {
                        ...item,
                        active_child_id: child.id,
                        active_status: child.status,
                      }
                    : item,
                ),
              })),
            }
          })
        }

        queryClient.setQueryData<TaskTreeResponse>(
          taskKeys.tree(rootId),
          (data) =>
            data
              ? { ...data, root: { ...data.root, active_child_id: child.id } }
              : data,
        )
        // Tree needs the new child node populated — invalidate to refetch
        // (optimistic update above keeps active_child_id stable in the meantime).
        queryClient.invalidateQueries({ queryKey: taskKeys.tree(rootId) })

        Llm4AdTasksService.setActiveChild({
          taskId: rootId,
          requestBody: { child_id: child.id },
        }).catch(() => {})

        await action(child.id)
      } finally {
        setIsCopying(false)
      }
    },
    [
      task,
      setSelectedChildTaskId,
      queryClient,
      projectId,
      selectedTask,
      setSelectedTask,
    ],
  )

  return { withCopyIfNeeded, isCopying }
}
