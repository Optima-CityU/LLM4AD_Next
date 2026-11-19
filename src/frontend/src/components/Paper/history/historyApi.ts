import { authFetch } from "@/utils/auth"

export class HistoryError extends Error {
  constructor(public status: number) {
    super(`HTTP ${status}`)
  }
}

export function paperHistoryUrl(workspaceId: string, suffix = "") {
  return `${import.meta.env.VITE_API_URL || ""}/api/v1/llm4ad/papers/workspaces/${encodeURIComponent(workspaceId)}/history${suffix}`
}

/** Keep history reads and guarded decisions on the platform authentication transport. */
export async function historyRequest<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await authFetch(url, init)
  if (!response.ok) throw new HistoryError(response.status)
  if (response.status === 204) return undefined as T
  return response.json()
}

export function revisionFileUrl(
  workspaceId: string,
  revisionId: string,
  path: string,
  download = false,
) {
  return paperHistoryUrl(
    workspaceId,
    `/${encodeURIComponent(revisionId)}/${download ? "download" : "file"}?${new URLSearchParams({ path })}`,
  )
}

/** Download authenticated bytes without exposing bearer credentials in a URL. */
export async function downloadHistoryFile(
  workspaceId: string,
  revisionId: string,
  path: string,
) {
  const response = await authFetch(
    revisionFileUrl(workspaceId, revisionId, path, true),
  )
  if (!response.ok) throw new HistoryError(response.status)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = path.split("/").pop() || "download"
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
