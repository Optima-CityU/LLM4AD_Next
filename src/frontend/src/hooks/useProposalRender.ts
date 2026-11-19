import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import type { PaperRenderRequest, PaperRenderResponse } from "@/client"
import {
  type ProposalRenderRevision,
  proposalRenderKey,
} from "@/lib/proposalDocument"
import { authFetch } from "@/utils/auth"

export type ProposalRenderResult = PaperRenderResponse

type Attempt = {
  workspaceId: string
  id: number
  startedAt: number
  status: "pending" | "ready" | "failed"
  result?: ProposalRenderResult
}

function renderUrl(revision: ProposalRenderRevision) {
  const query = new URLSearchParams({ entry_path: revision.entryPaths[0] })
  for (const path of revision.entryPaths) query.append("entry_paths", path)
  return `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/papers/source-versions/${encodeURIComponent(revision.sourceVersionId)}/render?${query}`
}

function validateRender(
  result: ProposalRenderResult,
  revision: ProposalRenderRevision,
) {
  const paths = result.entry_paths?.length
    ? result.entry_paths
    : [result.entry_path]
  if (
    result.source_version_id !== revision.sourceVersionId ||
    result.source_hash !== revision.sourceHash ||
    JSON.stringify(paths) !== JSON.stringify(revision.entryPaths)
  )
    throw new Error("Render revision changed")
  return result
}

/** Read persisted builds; only an explicit action may save and compile. */
export function useProposalRender({
  workspaceId,
  revision,
  enabled,
  onBeforeRender,
}: {
  workspaceId: string
  revision: ProposalRenderRevision
  enabled: boolean
  onBeforeRender: () => Promise<ProposalRenderRevision>
}) {
  const client = useQueryClient()
  const activeWorkspace = useRef(workspaceId)
  activeWorkspace.current = workspaceId
  const sequence = useRef(0)
  const flight = useRef<{ workspaceId: string; id: number } | null>(null)
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [retained, setRetained] = useState<
    Record<string, ProposalRenderResult>
  >({})
  const stored = useQuery({
    queryKey: proposalRenderKey(workspaceId, revision),
    queryFn: async ({ signal }): Promise<ProposalRenderResult | null> => {
      const response = await authFetch(renderUrl(revision), { signal })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const result = await response.json()
      return result ? validateRender(result, revision) : null
    },
    enabled: enabled && Boolean(revision.sourceVersionId),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    const result = stored.data
    if (result?.status === "ready" && activeWorkspace.current === workspaceId)
      setRetained((previous) => ({ ...previous, [workspaceId]: result }))
  }, [stored.data, workspaceId])

  const confirm = async () => {
    if (flight.current?.workspaceId === workspaceId) return
    const identity = { workspaceId, id: ++sequence.current }
    flight.current = identity
    const startedAt = Date.now()
    const current = () =>
      activeWorkspace.current === workspaceId && flight.current === identity
    setAttempt({ ...identity, startedAt, status: "pending" })
    try {
      const saved = await onBeforeRender()
      if (!current()) return
      const response = await authFetch(renderUrl(saved), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entry_paths: saved.entryPaths,
          expected_source_hash: saved.sourceHash,
        } satisfies PaperRenderRequest),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const result = validateRender(await response.json(), saved)
      if (!current()) return
      await client.cancelQueries({
        queryKey: proposalRenderKey(workspaceId, saved),
        exact: true,
      })
      if (!current()) return
      client.setQueryData(proposalRenderKey(workspaceId, saved), result)
      if (result.status === "ready")
        setRetained((previous) => ({ ...previous, [workspaceId]: result }))
      setAttempt({ ...identity, startedAt, status: result.status, result })
    } catch {
      if (current()) setAttempt({ ...identity, startedAt, status: "failed" })
    } finally {
      if (flight.current === identity) flight.current = null
    }
  }
  const ownAttempt = attempt?.workspaceId === workspaceId ? attempt : null
  const result =
    stored.data?.status === "ready"
      ? stored.data
      : (retained[workspaceId] ?? null)
  const paths = result?.entry_paths?.length
    ? result.entry_paths
    : result
      ? [result.entry_path]
      : []
  const stale = Boolean(
    result &&
      (result.source_version_id !== revision.sourceVersionId ||
        result.source_hash !== revision.sourceHash ||
        JSON.stringify(paths) !== JSON.stringify(revision.entryPaths)),
  )
  const recovered =
    stored.data?.status === "ready" &&
    stored.dataUpdatedAt > (ownAttempt?.startedAt ?? 0)
  return {
    confirm,
    result,
    stale,
    pending: ownAttempt?.status === "pending",
    failed:
      !recovered &&
      (ownAttempt?.status === "failed" || stored.data?.status === "failed"),
    diagnostics:
      stored.data?.diagnostics ?? ownAttempt?.result?.diagnostics ?? [],
    available: Boolean(stored.data || retained[workspaceId] || ownAttempt),
  }
}
