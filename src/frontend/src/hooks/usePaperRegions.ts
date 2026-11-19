import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  PaperRegion,
  PaperRegionRequest,
  PaperRegionSnapshot,
} from "@/client"
import { paperWorkspaceKeys } from "@/hooks/usePapers"
import type { PaperRegionSelection } from "@/lib/paperRegions"
import { authFetch } from "@/utils/auth"

export const paperRegionKeys = {
  snapshot: (workspaceId: string | undefined) =>
    ["paper-regions", workspaceId] as const,
}

export class PaperRegionError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function regionRequest<T>(
  workspaceId: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const root = `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/papers/workspaces/${encodeURIComponent(workspaceId)}`
  const response = await authFetch(`${root}/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  })
  if (!response.ok)
    throw new PaperRegionError(response.status, `HTTP ${response.status}`)
  if (response.status === 204) return undefined as T
  return response.json()
}

export function usePaperRegions(workspaceId?: string) {
  return useQuery({
    queryKey: paperRegionKeys.snapshot(workspaceId),
    queryFn: ({ signal }) =>
      regionRequest<PaperRegionSnapshot>(workspaceId!, "regions", { signal }),
    enabled: Boolean(workspaceId),
    refetchInterval: 3000,
    retry: 1,
  })
}

/** Persist regional actions and refresh both regional and canonical paper views. */
export function usePaperRegionActions(workspaceId?: string) {
  const client = useQueryClient()
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({
        queryKey: paperRegionKeys.snapshot(workspaceId),
      }),
      client.invalidateQueries({
        queryKey: paperWorkspaceKeys.detail(workspaceId ?? null),
      }),
    ])
  const save = useMutation({
    mutationFn: (body: { region: PaperRegion; content: string }) =>
      regionRequest(
        workspaceId!,
        `regions/${encodeURIComponent(body.region.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            base_revision: body.region.revision,
            content: body.content,
          }),
        },
      ),
    onSuccess: refresh,
    onError: refresh,
  })
  const createRequest = useMutation({
    mutationFn: (body: {
      region: PaperRegion
      instruction: string
      answer?: string
      selection?: PaperRegionSelection
      idempotencyKey: string
    }) =>
      regionRequest<PaperRegionRequest>(
        workspaceId!,
        `regions/${encodeURIComponent(body.region.id)}/requests`,
        {
          method: "POST",
          body: JSON.stringify({
            base_revision: body.region.revision,
            instruction: body.instruction,
            answer: body.answer,
            selection: body.selection,
            idempotency_key: body.idempotencyKey,
          }),
        },
      ),
    onError: refresh,
  })
  const decideRequest = useMutation({
    mutationFn: (body: {
      request: PaperRegionRequest
      action: "apply" | "reject"
      revision: PaperRegion["revision"]
    }) =>
      regionRequest(
        workspaceId!,
        `region-requests/${encodeURIComponent(body.request.id)}/${body.action}`,
        {
          method: "POST",
          body: JSON.stringify({ base_revision: body.revision }),
        },
      ),
    onSuccess: refresh,
    onError: refresh,
  })
  return { save, createRequest, decideRequest, refresh }
}
