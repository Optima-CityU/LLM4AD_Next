import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  Llm4AdPapersService,
  type PaperAlgorithmProposalUpdate,
  type PaperJudgeBindingsUpdate,
  type PaperMetricSelectionRequest,
  type PaperModelBindingUpdate,
  type PaperOptimizationTargetUpdate,
  type PaperProposalTaskCreateRequest,
  type PaperReviewCreate,
  type PaperReviewUpdate,
  type PaperRevisionCandidateCreate,
  type PaperRuntimeSessionCreate,
  type PaperSourceFileUpdateRequest,
  type PaperSourcePathDeleteRequest,
  type PaperWorkspaceCreate,
} from "@/client"

export const paperWorkspaceKeys = {
  all: ["paper-workspaces"] as const,
  list: (search: string) => ["paper-workspaces", "list", search] as const,
  detail: (workspaceId: string | null) =>
    ["paper-workspaces", "detail", workspaceId] as const,
}

export function usePaperWorkspaces(search = "") {
  return useQuery({
    queryKey: paperWorkspaceKeys.list(search),
    queryFn: () =>
      Llm4AdPapersService.listWorkspaces({
        skip: 0,
        limit: 100,
        search: search.trim() || undefined,
      }),
  })
}

export function usePaperWorkspace(workspaceId: string | null) {
  return useQuery({
    queryKey: paperWorkspaceKeys.detail(workspaceId),
    queryFn: () =>
      Llm4AdPapersService.getWorkspace({ workspaceId: workspaceId! }),
    enabled: Boolean(workspaceId),
    refetchInterval: (query) =>
      query.state.data?.workflow_available ? 5000 : false,
  })
}

export function usePaperRuntimeSession(
  workspaceId: string,
  workflowStage: PaperRuntimeSessionCreate["workflow_stage"],
  enabled: boolean,
  profileKey: string,
) {
  return useQuery({
    queryKey: ["paper-runtime-session", workspaceId, workflowStage, profileKey],
    queryFn: () =>
      Llm4AdPapersService.createRuntimeSession({
        workspaceId,
        requestBody: { workflow_stage: workflowStage },
      }),
    enabled,
    retry: 1,
    refetchOnWindowFocus: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
}

function useRefreshPapers(workspaceId?: string | null) {
  const queryClient = useQueryClient()
  return async () => {
    await queryClient.invalidateQueries({ queryKey: paperWorkspaceKeys.all })
    if (workspaceId) {
      await queryClient.invalidateQueries({
        queryKey: paperWorkspaceKeys.detail(workspaceId),
      })
    }
  }
}

export function useCreatePaperWorkspace() {
  const refresh = useRefreshPapers()
  return useMutation({
    mutationFn: (body: PaperWorkspaceCreate) =>
      Llm4AdPapersService.createWorkspace({ requestBody: body }),
    onSuccess: refresh,
  })
}

export function useDeletePaperWorkspace() {
  const refresh = useRefreshPapers()
  return useMutation({
    mutationFn: (workspaceId: string) =>
      Llm4AdPapersService.deleteWorkspace({ workspaceId }),
    onSuccess: refresh,
  })
}

export function useUploadPaperSource(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (items: Array<File | { file: File; relativePath: string }>) => {
      const uploads = items.map((item) =>
        item instanceof File
          ? {
              file: item,
              relativePath: item.webkitRelativePath || item.name,
            }
          : item,
      )
      return Llm4AdPapersService.uploadSource({
        workspaceId: workspaceId!,
        formData: {
          files: uploads.map((item) => item.file),
          relative_paths: uploads.map((item) => item.relativePath),
        },
      })
    },
    onSuccess: refresh,
  })
}

export function useUpdatePaperModelBinding(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (body: PaperModelBindingUpdate) =>
      Llm4AdPapersService.updateModelBinding({
        workspaceId: workspaceId!,
        requestBody: body,
      }),
    onSuccess: refresh,
  })
}

export function usePaperSourceFile(
  sourceVersionId: string | null,
  path: string | null,
  contentHash?: string | null,
) {
  return useQuery({
    queryKey: ["paper-source-file", sourceVersionId, path, contentHash],
    queryFn: () =>
      Llm4AdPapersService.getSourceFile({
        sourceVersionId: sourceVersionId!,
        path: path!,
      }),
    enabled: Boolean(sourceVersionId && path),
  })
}

export function useDeletePaperSourcePath(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      sourceVersionId: string
      path: string
      workflowStage?: PaperSourcePathDeleteRequest["workflow_stage"]
    }) =>
      Llm4AdPapersService.deleteSourcePath({
        sourceVersionId: variables.sourceVersionId,
        requestBody: {
          path: variables.path,
          workflow_stage: variables.workflowStage,
        },
      }),
    onSuccess: refresh,
  })
}

export function useUpdatePaperSourceFile(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      sourceVersionId: string
      body: PaperSourceFileUpdateRequest
    }) =>
      Llm4AdPapersService.updateSourceFile({
        sourceVersionId: variables.sourceVersionId,
        requestBody: variables.body,
      }),
    onSuccess: refresh,
  })
}

export function useAttachReviewerFeedback(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      sourceVersionId: string
      body: PaperReviewCreate
    }) =>
      Llm4AdPapersService.attachReviewerFeedback({
        sourceVersionId: variables.sourceVersionId,
        requestBody: variables.body,
      }),
    onSuccess: refresh,
  })
}

export function usePaperReviewContent(reviewId: string | null) {
  return useQuery({
    queryKey: ["paper-review-content", reviewId],
    queryFn: () =>
      Llm4AdPapersService.getReviewerFeedback({ reviewId: reviewId! }),
    enabled: Boolean(reviewId),
  })
}

export function useUpdateReviewerFeedback(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (variables: { reviewId: string; body: PaperReviewUpdate }) =>
      Llm4AdPapersService.updateReviewerFeedback({
        reviewId: variables.reviewId,
        requestBody: variables.body,
      }),
    onSuccess: async (_data, variables) => {
      await queryClient.invalidateQueries({
        queryKey: ["paper-review-content", variables.reviewId],
      })
      await refresh()
    },
  })
}

export function useDeleteReviewerFeedback(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (reviewId: string) =>
      Llm4AdPapersService.deleteReviewerFeedback({ reviewId }),
    onSuccess: async (_data, reviewId) => {
      queryClient.removeQueries({
        queryKey: ["paper-review-content", reviewId],
      })
      await refresh()
    },
  })
}

export function useUpdatePaperJudgeBindings(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (body: PaperJudgeBindingsUpdate) =>
      Llm4AdPapersService.updateJudgeBindings({
        workspaceId: workspaceId!,
        requestBody: body,
      }),
    onSuccess: refresh,
  })
}

export function useUpdatePaperTarget(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      targetId: string
      body: PaperOptimizationTargetUpdate
    }) =>
      Llm4AdPapersService.updateOptimizationTarget({
        targetId: variables.targetId,
        requestBody: variables.body,
      }),
    onSuccess: refresh,
  })
}

export function useAcceptPaperRevision(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (candidateId: string) =>
      Llm4AdPapersService.acceptRevisionCandidate({ candidateId }),
    onSuccess: refresh,
  })
}

export function useCreatePaperRevisionCandidate(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      targetId: string
      body: PaperRevisionCandidateCreate
    }) =>
      Llm4AdPapersService.createRevisionCandidate({
        targetId: variables.targetId,
        requestBody: variables.body,
      }),
    onSuccess: refresh,
  })
}

export function useExportPaperSource() {
  return useMutation({
    mutationFn: (sourceVersionId: string) =>
      Llm4AdPapersService.exportSourceVersion({ sourceVersionId }),
  })
}

export function useSelectPaperMetrics(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      sourceVersionId: string
      body: PaperMetricSelectionRequest
    }) =>
      Llm4AdPapersService.selectMetrics({
        sourceVersionId: variables.sourceVersionId,
        requestBody: variables.body,
      }),
    onSuccess: refresh,
  })
}

export function useUpdatePaperProposal(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (variables: {
      proposalId: string
      body: PaperAlgorithmProposalUpdate
    }) =>
      Llm4AdPapersService.updateProposal({
        proposalId: variables.proposalId,
        requestBody: variables.body,
      }),
    onSuccess: refresh,
  })
}

export function useCreatePaperProposalTasks(workspaceId: string | null) {
  const refresh = useRefreshPapers(workspaceId)
  return useMutation({
    mutationFn: (body: PaperProposalTaskCreateRequest) =>
      Llm4AdPapersService.createProposalTasks({
        workspaceId: workspaceId!,
        requestBody: body,
      }),
    onSuccess: refresh,
  })
}
