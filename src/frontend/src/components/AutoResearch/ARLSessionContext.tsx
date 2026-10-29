import { createContext, type ReactNode, useContext, useMemo } from "react"

import type { ResearchSessionItem } from "@/client"
import {
  type ARLRole,
  type ARLTurnStatus,
  useARLWebSocket,
} from "@/hooks/useARLWebSocket"

import type { ARLFsOp, ARLFsResult } from "./arlFiles"

type ARLSession = ReturnType<typeof useARLWebSocket>

/** What the file panel needs: the request channel plus per-role readiness. */
export interface ARLFilesApi {
  /** Active ARL session id, for the HTTP workspace download. */
  sessionId: string | null
  request: (
    role: ARLRole,
    op: ARLFsOp,
    params?: Record<string, unknown>,
  ) => Promise<ARLFsResult>
  ready: Record<ARLRole, boolean>
  turnStatus: Record<ARLRole, ARLTurnStatus>
}

const SessionContext = createContext<ARLSession | null>(null)
const FilesContext = createContext<ARLFilesApi | null>(null)

/**
 * Owns the ARL socket for the page so the chat panes and the right-hand file
 * panel share one connection.
 *
 * The file slice is published through its own, memoized context: streaming
 * deltas rewrite `panes` on every chunk, and the file tree should not
 * re-render for those — only when readiness or a turn status changes.
 *
 * @param session - Active session; pass null for non-ARL sessions so the
 *   hook stays idle.
 */
export function ARLSessionProvider({
  session,
  children,
}: {
  session: ResearchSessionItem | null
  children: ReactNode
}) {
  const arl = useARLWebSocket({ session })
  const { fsRequest, panes } = arl
  const workerReady = panes.worker.ready
  const validatorReady = panes.validator.ready
  const workerTurn = panes.worker.turnStatus
  const validatorTurn = panes.validator.turnStatus
  const sessionId = session?.id ?? null

  const files = useMemo<ARLFilesApi>(
    () => ({
      sessionId,
      request: fsRequest,
      ready: { worker: workerReady, validator: validatorReady },
      turnStatus: { worker: workerTurn, validator: validatorTurn },
    }),
    [
      sessionId,
      fsRequest,
      workerReady,
      validatorReady,
      workerTurn,
      validatorTurn,
    ],
  )

  return (
    <SessionContext.Provider value={arl}>
      <FilesContext.Provider value={files}>{children}</FilesContext.Provider>
    </SessionContext.Provider>
  )
}

/** Full ARL session state; must be rendered under {@link ARLSessionProvider}. */
export function useARLSession(): ARLSession {
  const value = useContext(SessionContext)
  if (!value) throw new Error("useARLSession requires ARLSessionProvider")
  return value
}

/** File-panel slice of the ARL session. */
export function useARLFiles(): ARLFilesApi {
  const value = useContext(FilesContext)
  if (!value) throw new Error("useARLFiles requires ARLSessionProvider")
  return value
}
