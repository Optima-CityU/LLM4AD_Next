import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { type AutoDesignSessionCreate, Llm4AdAutodesignService } from "@/client"
import { type AutoDesignFileArea, autoDesignKeys } from "@/lib/autoDesign"

export function autoDesignTaskSessionOptions(taskId: string) {
  return {
    queryKey: autoDesignKeys.taskSession(taskId),
    queryFn: () => Llm4AdAutodesignService.getTaskSession({ taskId }),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
  }
}

export function useAutoDesignSessions(projectId?: string, enabled = true) {
  return useQuery({
    queryKey: [...autoDesignKeys.sessions, projectId ?? null],
    queryFn: () => Llm4AdAutodesignService.listSessions({ projectId }),
    enabled,
  })
}

export function useCreateAutoDesignSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (requestBody: AutoDesignSessionCreate) =>
      Llm4AdAutodesignService.createSession({ requestBody }),
    onSuccess: (session) => {
      queryClient.setQueryData(autoDesignKeys.session(session.id), session)
      void queryClient.invalidateQueries({ queryKey: autoDesignKeys.sessions })
    },
  })
}

export function useAutoDesignContext(sessionId: string, taskId?: string) {
  return useQuery({
    queryKey: autoDesignKeys.context(sessionId, taskId),
    queryFn: () =>
      Llm4AdAutodesignService.getSessionContext({ sessionId, taskId }),
    refetchInterval: 5000,
  })
}

export function useAutoDesignFiles(
  sessionId: string,
  area: AutoDesignFileArea,
  taskId?: string,
) {
  return useQuery({
    queryKey: autoDesignKeys.files(sessionId, area, taskId),
    queryFn: () =>
      Llm4AdAutodesignService.listFiles({ sessionId, area, taskId }),
    refetchInterval: 5000,
  })
}

export function useAutoDesignFile(
  sessionId: string,
  area: AutoDesignFileArea,
  path: string,
  taskId?: string,
) {
  return useQuery({
    queryKey: autoDesignKeys.file(sessionId, area, path, taskId),
    queryFn: () =>
      Llm4AdAutodesignService.readFile({ sessionId, area, path, taskId }),
    enabled: Boolean(path),
    refetchInterval: 5000,
  })
}
