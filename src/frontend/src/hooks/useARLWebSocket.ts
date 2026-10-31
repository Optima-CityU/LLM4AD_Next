import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import type { ResearchSessionItem } from "@/client"
import type { ARLCommandsState } from "@/components/AutoResearch/arlCommands"
import {
  emptyCommandsState,
  normalizeCommands,
} from "@/components/AutoResearch/arlCommands"
import {
  type ARLFsOp,
  ARLFsRequests,
  type ARLFsResult,
} from "@/components/AutoResearch/arlFiles"
import type {
  ARLConnectionStatus,
  ARLSocketLike,
} from "@/components/AutoResearch/arlSocket"
import { ARLSocketController } from "@/components/AutoResearch/arlSocket"
import type {
  ARLRole,
  ARLRoleState,
  ARLStreamItem,
  ARLTimelineCursor,
  ARLTurnStatus,
  ARLValidationState,
} from "@/components/AutoResearch/arlStream"
import {
  ARL_ROLES,
  appendItem,
  applyAgentReset,
  applyAgentSnapshot,
  applyAgentsReady,
  applyHistory,
  applyOlderPage,
  applyPeerList,
  applyPeerUpdate,
  applySubagentList,
  applySubagentUpdate,
  applyValidationStatus,
  emptyPanes,
  emptyValidationState,
  finalizeStreaming,
  hasPeerLabel,
  itemsFromEntries,
  nextItemId,
  normalizeItem,
  upsertPermission,
} from "@/components/AutoResearch/arlStream"
import { handleUnauthorized } from "@/utils/auth"

export type {
  ARLCommand,
  ARLCommandsState,
} from "@/components/AutoResearch/arlCommands"
export type { ARLConnectionStatus } from "@/components/AutoResearch/arlSocket"
export type {
  ARLAgentMode,
  ARLPeerAgent,
  ARLPermissionAction,
  ARLPermissionRequest,
  ARLRole,
  ARLRoleState,
  ARLStreamItem,
  ARLSubagent,
  ARLSubagentStatus,
  ARLTurnStatus,
  ARLValidationState,
} from "@/components/AutoResearch/arlStream"
// Re-exported so existing imports keep resolving through the hook. The pure
// reducers themselves live in `arlStream.ts`.
export {
  ARL_ROLES,
  isUnattendedMode,
} from "@/components/AutoResearch/arlStream"

/** How long to wait for a subagent transcript before giving up. */
const SUBAGENT_TIMELINE_TIMEOUT_MS = 30_000

/** A `subagent_timeline` request waiting for its reply. */
interface PendingSubagentRequest {
  resolve: (items: ARLStreamItem[]) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

/** True when a daemon message belongs to an agent other than the pane's. */
function isForeignAgent(state: ARLRoleState, agentId: unknown): boolean {
  return (
    !!state.agentId &&
    typeof agentId === "string" &&
    !!agentId &&
    agentId !== state.agentId
  )
}

function emptyCommands(): Record<ARLRole, ARLCommandsState> {
  return { worker: emptyCommandsState(), validator: emptyCommandsState() }
}

/** Frame pushed by the backend, one per role. */
interface ARLBackendFrame {
  type: string
  role?: ARLRole
  data: any
}

/**
 * Build the WebSocket URL for the ARL endpoint.
 *
 * Follows the same base-URL convention as the REST client (`VITE_API_URL`)
 * but maps it onto the ws/wss scheme. When no absolute base URL is
 * configured we fall back to the current origin, which the dev-server proxy
 * forwards to the backend (`ws: true` on `/api`).
 */
function buildArlWsUrl(sessionId: string, token: string): string {
  const raw = import.meta.env.VITE_API_URL || ""
  const base = raw
    ? new URL(raw, window.location.origin)
    : new URL(window.location.origin)
  const scheme = base.protocol === "https:" ? "wss:" : "ws:"
  const path = `/api/v1/llm4ad/research/sessions/${sessionId}/arl/ws`
  return `${scheme}//${base.host}${path}?token=${encodeURIComponent(token)}`
}

interface UseARLWebSocketOptions {
  session: ResearchSessionItem | null
}

/**
 * Whether a research session runs the dual-container ARL flow.
 *
 * `ResearchSessionItem.session_type` is typed as a plain string on the list
 * shape, so the comparison lives here once instead of being cast at each call
 * site.
 */
export function isArlResearchSession(
  session: ResearchSessionItem | null | undefined,
): boolean {
  return session?.session_type === "arl"
}

/**
 * Fingerprint of the provider/model each role is configured with.
 *
 * Only these two fields decide which credentials the containers are built
 * with; other `arl_config` keys (e.g. the persisted `mode_id`) are excluded
 * so that changing them does not force a reconnect.
 */
function arlProviderKey(session: ResearchSessionItem | null | undefined) {
  const config = (session?.arl_config ?? {}) as Record<
    string,
    { provider_id?: unknown; model_name?: unknown } | undefined
  >
  return ARL_ROLES.map((role) => {
    const cfg = config[role] ?? {}
    return `${String(cfg.provider_id ?? "")}/${String(cfg.model_name ?? "")}`
  }).join("|")
}

/**
 * ARL WebSocket connector.
 *
 * One socket carries both containers; every backend frame is tagged with a
 * `role` so events can be dispatched into the matching pane. Live events
 * arrive as Paseo `agent_stream` frames carrying delta chunks, which this
 * hook reduces into renderable items per role (see `appendItem`).
 *
 * @param options.session - The ARL research session to attach to.
 * @returns Connection state plus per-role conversation state and actions.
 */
export function useARLWebSocket({ session }: UseARLWebSocketOptions) {
  const [status, setStatus] = useState<ARLConnectionStatus>("idle")
  const [panes, setPanes] = useState<Record<ARLRole, ARLRoleState>>(emptyPanes)
  const [systemMessage, setSystemMessage] = useState<string | null>(null)
  // Session-level, not part of `ARLRoleState`: the backend tags these frames
  // `role: "worker"` because that is the pane that renders them, but the state
  // itself describes the Validator's service and is read by both sides.
  const [validation, setValidation] =
    useState<ARLValidationState>(emptyValidationState)
  // Slash-command lists, per container. Kept out of `ARLRoleState`: they are
  // fetched on demand by the composer, not reduced from the daemon stream.
  const [commands, setCommands] =
    useState<Record<ARLRole, ARLCommandsState>>(emptyCommands)
  // File-panel requests awaiting their `fs_result`. A plain object rather than
  // state: replies resolve promises held by the panel, nothing re-renders.
  const fsRequestsRef = useRef<ARLFsRequests | null>(null)
  if (!fsRequestsRef.current) fsRequestsRef.current = new ARLFsRequests()
  const fsRequests = fsRequestsRef.current
  // Cursor each in-flight "load older" request was sent from, so a reply
  // that lands after history was replaced (reconnect) can be recognized.
  const olderFromRef = useRef<Record<ARLRole, ARLTimelineCursor | null>>({
    worker: null,
    validator: null,
  })
  // Subagent transcripts, fetched on demand when the user opens one.
  const subagentRequestsRef = useRef(new Map<string, PendingSubagentRequest>())

  const updateRole = useCallback(
    (role: ARLRole, updater: (state: ARLRoleState) => ARLRoleState) => {
      setPanes((prev) => ({ ...prev, [role]: updater(prev[role]) }))
    },
    [],
  )

  /** Reduce one Paseo session message into the given role's state. */
  const handleDaemonMessage = useCallback(
    (role: ARLRole, message: any) => {
      const type = message?.type
      const payload = message?.payload ?? {}

      if (type === "agent.provider_subagents.update") {
        updateRole(role, (state) => applySubagentUpdate(state, payload))
        return
      }

      if (type === "agent_stream") {
        // Only the pane's own agent drives its transcript. The daemon also
        // streams other agents in the container (one archived by `/clear`,
        // children an agent created through MCP), which would otherwise be
        // spliced into this conversation.
        if (isForeignAgent(panesRef.current[role], payload.agentId)) return
        const event = payload.event ?? {}
        const timestamp = new Date(
          payload.timestamp ?? Date.now(),
        ).toISOString()

        if (event.type === "turn_started") {
          updateRole(role, (state) => ({ ...state, turnStatus: "running" }))
          return
        }
        if (
          event.type === "turn_completed" ||
          event.type === "turn_failed" ||
          event.type === "turn_canceled"
        ) {
          const status: ARLTurnStatus =
            event.type === "turn_completed"
              ? "completed"
              : event.type === "turn_failed"
                ? "failed"
                : "canceled"
          updateRole(role, (state) => ({
            ...state,
            turnStatus: status,
            canceling: false,
            items: finalizeStreaming(state.items),
          }))
          return
        }
        if (event.type === "timeline") {
          const item = normalizeItem(event.item, timestamp)
          if (!item) return
          updateRole(role, (state) => ({
            ...state,
            items: appendItem(state.items, item, { streaming: true }),
          }))
          return
        }
        if (event.type === "permission_requested") {
          const request = event.request ?? event.item ?? {}
          updateRole(role, (state) => ({
            ...state,
            permissions: upsertPermission(state, request),
          }))
          return
        }
        if (event.type === "permission_resolved") {
          updateRole(role, (state) => ({
            ...state,
            permissions: state.permissions.filter(
              (p) => p.id !== (event.requestId ?? event.id),
            ),
          }))
          return
        }
        return
      }

      if (type === "agent_update") {
        // Peer agents (validation runs spawned with an `aris.role` label) go
        // to the peer list. Checked by label first, so a peer can never be
        // mistaken for the main agent even before `agents_ready` sets its id.
        if (payload.kind === "remove") {
          updateRole(role, (state) => applyPeerUpdate(state, payload))
          return
        }
        if (payload.kind === "upsert") {
          updateRole(role, (state) =>
            hasPeerLabel(payload.agent) ||
            isForeignAgent(state, payload.agent?.id)
              ? applyPeerUpdate(state, payload)
              : applyAgentSnapshot(state, payload.agent),
          )
        }
        return
      }

      // The daemon mirrors permission traffic as top-level messages as well as
      // `agent_stream` events; handling both is what keeps the cards in sync
      // after a reconnect (upserts are keyed by request id, so no duplicates).
      if (type === "agent_permission_request") {
        updateRole(role, (state) => ({
          ...state,
          permissions: upsertPermission(state, payload.request ?? {}),
        }))
        return
      }

      if (type === "agent_permission_resolved") {
        updateRole(role, (state) => ({
          ...state,
          permissions: state.permissions.filter(
            (p) => p.id !== payload.requestId,
          ),
        }))
        return
      }

      if (type === "agent.timeline.replacement") {
        // The daemon re-based the timeline for this agent: drop local items,
        // and the paging cursor with them (it points into the old epoch).
        updateRole(role, (state) =>
          isForeignAgent(state, payload.agentId)
            ? state
            : {
                ...state,
                items: [],
                historyCursor: null,
                hasOlder: false,
                loadingOlder: false,
              },
        )
        return
      }

      if (type === "agent_deleted" || type === "agent_archived") {
        updateRole(role, (state) =>
          isForeignAgent(state, payload.agentId)
            ? state
            : { ...state, agentStatus: "deleted" },
        )
        return
      }

      if (type === "agent_attention_required") {
        const detail = payload.reason ?? payload.message ?? payload.kind
        updateRole(role, (state) => ({
          ...state,
          items: appendItem(state.items, {
            kind: "notification",
            id: nextItemId("attention"),
            level: "warning",
            message:
              typeof detail === "string" ? detail : "Agent needs attention",
            timestamp: new Date().toISOString(),
          }),
        }))
      }
    },
    [updateRole],
  )

  /** Reduce a backend frame into per-role state. */
  const handleFrame = useCallback(
    (frame: ARLBackendFrame) => {
      switch (frame.type) {
        case "system":
          setSystemMessage(frame.data?.message ?? null)
          return

        case "agents_ready":
          setPanes((prev) => applyAgentsReady(prev, frame.data))
          return

        case "timeline_history": {
          const role = frame.role
          if (!role) return
          olderFromRef.current[role] = null
          updateRole(role, (state) => applyHistory(state, frame.data))
          return
        }

        case "timeline_older": {
          const role = frame.role
          if (!role) return
          const requestedFrom = olderFromRef.current[role]
          olderFromRef.current[role] = null
          updateRole(role, (state) =>
            applyOlderPage(state, frame.data, requestedFrom),
          )
          if (!frame.data?.ok) {
            toast.error(frame.data?.message || "Failed to load older messages")
          }
          return
        }

        case "subagent_list": {
          const role = frame.role
          if (!role) return
          updateRole(role, (state) => applySubagentList(state, frame.data))
          return
        }

        case "peer_agent_list": {
          const role = frame.role
          if (!role) return
          updateRole(role, (state) => applyPeerList(state, frame.data))
          return
        }

        case "peer_timeline":
        case "subagent_timeline": {
          const role = frame.role
          if (!role) return
          // Peers and subagents share the pending map, keyed apart by prefix
          const key =
            frame.type === "peer_timeline"
              ? `${role}:peer:${frame.data?.peer_id ?? ""}`
              : `${role}:${frame.data?.subagent_id ?? ""}`
          const pending = subagentRequestsRef.current.get(key)
          if (!pending) return
          subagentRequestsRef.current.delete(key)
          clearTimeout(pending.timer)
          if (frame.data?.ok) {
            const rows: any[] = frame.data?.rows ?? []
            pending.resolve(itemsFromEntries(rows))
          } else {
            pending.reject(
              new Error(
                frame.data?.message || "Failed to load the subagent transcript",
              ),
            )
          }
          return
        }

        case "agent_reset_result": {
          const role = frame.role
          if (!role) return
          olderFromRef.current[role] = null
          updateRole(role, (state) => applyAgentReset(state, frame.data))
          if (!frame.data?.ok) {
            toast.error(frame.data?.message || "Failed to clear the conversation")
          }
          return
        }

        case "timeline_event": {
          const role = frame.role
          if (!role) return
          handleDaemonMessage(role, frame.data)
          return
        }

        case "agent_cancel_result": {
          const role = frame.role
          if (!role) return
          if (frame.data?.ok) return
          // The daemon refused the cancel (turn already finished, agent gone):
          // release the button instead of leaving it spinning forever.
          updateRole(role, (state) => ({ ...state, canceling: false }))
          toast.error(frame.data?.message || "Failed to stop the agent")
          return
        }

        case "agent_mode_result": {
          const role = frame.role
          if (!role) return
          if (!frame.data?.ok) {
            toast.error(
              frame.data?.message || "Failed to switch permission mode",
            )
            return
          }
          updateRole(role, (state) => ({
            ...state,
            modeId: frame.data?.mode_id ?? state.modeId,
          }))
          return
        }

        case "command_list": {
          const role = frame.role
          if (!role) return
          // Failures stay inside the menu (it shows the message) rather than
          // raising a toast: the user only asked for a completion list.
          setCommands((prev) => ({
            ...prev,
            [role]: frame.data?.ok
              ? {
                  commands: normalizeCommands(frame.data?.commands),
                  loading: false,
                  loaded: true,
                  error: null,
                }
              : {
                  ...prev[role],
                  loading: false,
                  loaded: true,
                  error:
                    frame.data?.message || "Failed to load the command list",
                },
          }))
          return
        }

        case "validation_status":
          setValidation((prev) => applyValidationStatus(prev, frame.data))
          return

        case "validation_sync_result": {
          // Always release the spinner: this frame is the only thing that ends
          // the button's loading state, success or not.
          setValidation((prev) => ({
            ...prev,
            syncing: false,
            syncedAt: frame.data?.ok ? new Date().toISOString() : prev.syncedAt,
          }))
          if (frame.data?.ok) {
            toast.success("task.md synced to the Worker workspace")
          } else {
            toast.error(frame.data?.message || "Failed to sync task.md")
          }
          return
        }

        case "fs_result":
          fsRequests.settle(frame.data)
          return

        case "error":
          toast.error(frame.data?.message || "ARL error")
          return

        default:
          return
      }
    },
    [handleDaemonMessage, updateRole, fsRequests],
  )

  const isArlSession = isArlResearchSession(session)
  const sessionId = isArlSession ? (session?.id ?? null) : null
  const providerKey = isArlSession ? arlProviderKey(session) : ""

  // The frame handler is rebuilt whenever its reducers change, but the
  // controller must outlive those renders (it owns the socket), so it reads
  // the latest handler through a ref instead of being reconstructed.
  const handleFrameRef = useRef(handleFrame)
  handleFrameRef.current = handleFrame

  // Session whose panes have already been cleared, so a StrictMode re-setup
  // does not wipe a transcript that the reclaimed socket is still filling.
  const resetSessionRef = useRef<string | null>(null)

  // Live view of `panes` for the send guards, which must read current
  // readiness rather than whatever was captured when the callback was built.
  const panesRef = useRef(panes)
  panesRef.current = panes

  const controllerRef = useRef<ARLSocketController | null>(null)
  if (!controllerRef.current) {
    controllerRef.current = new ARLSocketController({
      openSocket: (id, token) =>
        new WebSocket(buildArlWsUrl(id, token)) as unknown as ARLSocketLike,
      getToken: () => localStorage.getItem("access_token"),
      refreshToken: handleUnauthorized,
      onFrame: (frame) => handleFrameRef.current(frame as ARLBackendFrame),
      onStatus: setStatus,
      onError: (message) => toast.error(message),
    })
  }
  const controller = controllerRef.current

  const connect = useCallback(() => controller.connect(), [controller])

  const disconnect = useCallback(() => {
    controller.teardown()
    setStatus("idle")
  }, [controller])

  /** Drop the current socket and start over, clearing the backoff budget. */
  const reconnect = useCallback(() => controller.reconnect(), [controller])

  const sendRaw = useCallback(
    (message: Record<string, unknown>, opts?: { quiet?: boolean }) =>
      controller.send(message, opts),
    [controller],
  )

  const isOpen = useCallback(() => controller.isOpen(), [controller])

  /**
   * Send a chat message to one of the containers.
   *
   * Refuses until the pane is ready. The composer is disabled in that state
   * too, but the guard lives here as well so a stray caller cannot drop a
   * message into a socket the backend is not reading yet.
   */
  const sendMessage = useCallback(
    (role: ARLRole, content: string) => {
      const text = content.trim()
      if (!text) return
      if (!panesRef.current[role].ready) {
        toast.error("The ARL container is still starting up")
        return
      }
      if (!sendRaw({ type: "agent_send", role, payload: { content: text } }))
        return

      // Optimistic echo: the daemon will send back a canonical user_message
      // which reconciles this row (matched by text, since `agent_send` carries
      // no client id). `pending` marks it as the only valid match target.
      updateRole(role, (state) => ({
        ...state,
        turnStatus: "running",
        items: [
          ...state.items,
          {
            kind: "user_message",
            id: nextItemId("optimistic"),
            text,
            pending: true,
            timestamp: new Date().toISOString(),
          },
        ],
      }))
    },
    [sendRaw, updateRole],
  )

  /** Stop the turn a container is currently running. */
  const cancelTurn = useCallback(
    (role: ARLRole) => {
      if (!sendRaw({ type: "agent_cancel", role, payload: {} })) return
      updateRole(role, (state) => ({ ...state, canceling: true }))
    },
    [sendRaw, updateRole],
  )

  /**
   * Load one page of history older than what the pane holds.
   *
   * No-op while a page is in flight or when the daemon has nothing older.
   * Sent quietly: the top-of-list spinner is the feedback, and a dropped
   * socket is already reported by the connection strip.
   *
   * @returns Whether a request went out.
   */
  const loadOlder = useCallback(
    (role: ARLRole) => {
      const state = panesRef.current[role]
      const cursor = state.historyCursor
      if (!state.ready || !state.hasOlder || state.loadingOlder || !cursor)
        return false
      if (
        !sendRaw(
          { type: "timeline_fetch_older", role, payload: { cursor } },
          { quiet: true },
        )
      )
        return false
      olderFromRef.current[role] = cursor
      updateRole(role, (prev) => ({ ...prev, loadingOlder: true }))
      return true
    },
    [sendRaw, updateRole],
  )

  /**
   * Fetch the transcript of one subagent (opened from the subagent strip).
   *
   * Resolves with renderable rows, rejects with the daemon's message, a
   * timeout, or a dropped socket. A second request for the same subagent
   * supersedes the first.
   */
  const requestTranscript = useCallback(
    (
      key: string,
      frame: { type: string; role: ARLRole; payload: Record<string, unknown> },
    ): Promise<ARLStreamItem[]> => {
      const requests = subagentRequestsRef.current
      const previous = requests.get(key)
      if (previous) {
        clearTimeout(previous.timer)
        requests.delete(key)
        previous.reject(new Error("Superseded"))
      }
      return new Promise<ARLStreamItem[]>((resolve, reject) => {
        const sent = sendRaw(frame, { quiet: true })
        if (!sent) {
          reject(new Error("Not connected to the ARL session"))
          return
        }
        const timer = setTimeout(() => {
          if (requests.get(key)?.timer !== timer) return
          requests.delete(key)
          reject(new Error("Request timed out"))
        }, SUBAGENT_TIMELINE_TIMEOUT_MS)
        requests.set(key, { resolve, reject, timer })
      })
    },
    [sendRaw],
  )

  const fetchSubagentTimeline = useCallback(
    (role: ARLRole, subagentId: string) =>
      requestTranscript(`${role}:${subagentId}`, {
        type: "subagent_timeline",
        role,
        payload: { subagent_id: subagentId },
      }),
    [requestTranscript],
  )

  /**
   * Fetch the transcript of one peer agent (opened from the subagent strip).
   *
   * Same contract as {@link fetchSubagentTimeline}. The backend refuses
   * `closed` peers so that viewing one never resumes its provider process.
   */
  const fetchPeerTimeline = useCallback(
    (role: ARLRole, peerId: string) =>
      requestTranscript(`${role}:peer:${peerId}`, {
        type: "peer_timeline",
        role,
        payload: { peer_id: peerId },
      }),
    [requestTranscript],
  )

  /**
   * `/clear`: archive the container's agent and start a fresh one.
   *
   * Mirrors the native client command. The workspace is left alone; only the
   * conversation context starts over.
   *
   * @returns Whether the request made it onto the wire.
   */
  const resetAgent = useCallback(
    (role: ARLRole) => {
      const state = panesRef.current[role]
      if (!state.ready || state.resetting) return false
      if (!sendRaw({ type: "agent_reset", role, payload: {} })) return false
      updateRole(role, (prev) => ({ ...prev, resetting: true }))
      return true
    },
    [sendRaw, updateRole],
  )

  /**
   * Switch a container's permission mode (e.g. into an auto/bypass mode).
   *
   * @returns Whether the request made it onto the wire.
   */
  const setMode = useCallback(
    (role: ARLRole, modeId: string) =>
      sendRaw({ type: "set_agent_mode", role, payload: { mode_id: modeId } }),
    [sendRaw],
  )

  /**
   * Answer a permission request raised by a container.
   *
   * The card is only removed once the answer is actually sent. Dropping it
   * optimistically would tell the user they had decided while the container
   * stayed blocked on a reply that never left the browser.
   */
  const respondPermission = useCallback(
    (role: ARLRole, requestId: string, response: Record<string, unknown>) => {
      if (
        !sendRaw({
          type: "permission_response",
          role,
          payload: { request_id: requestId, response },
        })
      ) {
        return false
      }
      updateRole(role, (state) => ({
        ...state,
        permissions: state.permissions.filter((p) => p.id !== requestId),
      }))
      return true
    },
    [sendRaw, updateRole],
  )

  /**
   * Answer several pending requests of one container at once.
   *
   * A single turn routinely raises several requests (one per tool call), and
   * clearing them one by one is the main reason the panes feel stuck.
   */
  const respondPermissions = useCallback(
    (
      role: ARLRole,
      requestIds: string[],
      response: Record<string, unknown>,
    ) => {
      if (!requestIds.length) return false
      // One readiness check for the whole batch: the socket cannot flip to
      // closed between synchronous sends, and checking per request would fire
      // one toast per pending card.
      if (!isOpen()) {
        toast.error("Not connected to the ARL session")
        return false
      }
      const sent = requestIds.filter((requestId) =>
        sendRaw(
          {
            type: "permission_response",
            role,
            payload: { request_id: requestId, response },
          },
          { quiet: true },
        ),
      )
      if (!sent.length) return false
      updateRole(role, (state) => ({
        ...state,
        permissions: state.permissions.filter((p) => !sent.includes(p.id)),
      }))
      return true
    },
    [isOpen, sendRaw, updateRole],
  )

  /**
   * Copy the Validator's `task.md` and service credentials to the Worker.
   *
   * The backend re-probes before writing anything, so this does not pass the
   * cached status along — by the time the click lands the Validator may have
   * stopped its service, and a stale address written into `CLAUDE.md` would
   * send the Worker through a whole turn against an unreachable endpoint.
   *
   * @returns Whether the request made it onto the wire.
   */
  const syncValidation = useCallback(() => {
    if (!sendRaw({ type: "sync_validation", role: "worker", payload: {} })) {
      return false
    }
    setValidation((prev) => ({ ...prev, syncing: true }))
    return true
  }, [sendRaw])

  /**
   * Fetch the slash commands a container's agent offers.
   *
   * Sent quietly: the composer calls this as the user types `/`, and a
   * dropped socket is already reported by the connection strip. The previous
   * list stays visible while the refresh is in flight.
   *
   * @returns Whether the request made it onto the wire.
   */
  const listCommands = useCallback(
    (role: ARLRole) => {
      if (!panesRef.current[role].ready) return false
      if (
        !sendRaw({ type: "list_commands", role, payload: {} }, { quiet: true })
      )
        return false
      setCommands((prev) => ({
        ...prev,
        [role]: { ...prev[role], loading: true, error: null },
      }))
      return true
    },
    [sendRaw],
  )

  /**
   * Run one file-panel operation inside a container workspace.
   *
   * Resolves with the `fs_result` data, rejects with the daemon's message.
   * Sent quietly: the panel shows failures inline, and a dropped socket is
   * already reported by the connection strip.
   */
  const fsRequest = useCallback(
    (
      role: ARLRole,
      op: ARLFsOp,
      params: Record<string, unknown> = {},
    ): Promise<ARLFsResult> => {
      const { id, promise } = fsRequests.begin()
      const sent = sendRaw(
        {
          type: "fs_request",
          role,
          payload: { ...params, op, request_id: id },
        },
        { quiet: true },
      )
      if (!sent) fsRequests.fail(id, "Not connected to the ARL session")
      return promise
    },
    [sendRaw, fsRequests],
  )

  // Readiness belongs to one attach, not to the session: a drop means the
  // backend is no longer reading our frames, so both panes go back to "not
  // ready" until a new `agents_ready` arrives. Items and permissions are left
  // alone so the transcript stays on screen while reconnecting.
  useEffect(() => {
    if (status === "connected") return
    setPanes((prev) => {
      const stale = (role: ARLRole) =>
        prev[role].ready || prev[role].loadingOlder || prev[role].resetting
      if (!ARL_ROLES.some(stale)) return prev
      const next = { ...prev }
      for (const role of ARL_ROLES) {
        // Replies to in-flight requests cannot arrive on a dead socket.
        if (stale(role))
          next[role] = {
            ...next[role],
            ready: false,
            loadingOlder: false,
            resetting: false,
          }
      }
      return next
    })
    olderFromRef.current = { worker: null, validator: null }
    for (const pending of subagentRequestsRef.current.values()) {
      clearTimeout(pending.timer)
      pending.reject(new Error("Disconnected from the ARL session"))
    }
    subagentRequestsRef.current.clear()
    // Same for file-panel requests: reject them now instead of at timeout.
    fsRequests.failAll("Disconnected from the ARL session")
    // A `list_commands` reply cannot arrive on a dead socket; release the
    // menu spinner so the next open simply asks again.
    setCommands((prev) => {
      if (!ARL_ROLES.some((role) => prev[role].loading)) return prev
      const next = { ...prev }
      for (const role of ARL_ROLES) {
        if (next[role].loading) next[role] = { ...next[role], loading: false }
      }
      return next
    })
    // A `validation_sync_result` cannot arrive on a dead socket either.
    setValidation((prev) => (prev.syncing ? { ...prev, syncing: false } : prev))
  }, [status, fsRequests])

  // Connect once per session; release on session change or unmount. Panes and
  // the retry budget are per session, so both reset here rather than on every
  // reconnect (a reconnect keeps the transcript on screen until
  // `timeline_history` replaces it).
  //
  // Both halves are written to be idempotent, because React runs this pair
  // twice for one mount under StrictMode: the reset is keyed on the session
  // actually changing, and the release is deferred so the second setup can
  // reclaim the same socket (see `scheduleTeardown`).
  useEffect(() => {
    controller.setSession(sessionId)
    if (!sessionId) {
      controller.teardown()
      resetSessionRef.current = null
      setStatus("idle")
      return
    }
    if (resetSessionRef.current !== sessionId) {
      resetSessionRef.current = sessionId
      setPanes(emptyPanes())
      setSystemMessage(null)
      // Per session: the validation service lives inside this session's
      // Validator container, so carrying the previous session's address over
      // would show a sync button pointing at a container that is gone.
      setValidation(emptyValidationState())
      setCommands(emptyCommands())
      controller.resetAttempts()
    }
    controller.connect()
    return () => {
      controller.scheduleTeardown()
    }
  }, [sessionId, controller])

  // Containers are only recreated with new credentials when the backend
  // attaches, so after the user edits a role's provider/model the live socket
  // has to be dropped and reopened. Keyed per session: the first value seen
  // for a session (initial load, or switching sessions) never reconnects, and
  // a `mode_id` write-back does not touch the key.
  const providerKeyRef = useRef<{ sessionId: string; key: string } | null>(
    null,
  )
  useEffect(() => {
    if (!sessionId) {
      providerKeyRef.current = null
      return
    }
    const prev = providerKeyRef.current
    providerKeyRef.current = { sessionId, key: providerKey }
    if (prev && prev.sessionId === sessionId && prev.key !== providerKey) {
      controller.reconnect()
    }
  }, [sessionId, providerKey, controller])

  return {
    status,
    /** Socket is open and both containers are reachable. */
    connected: status === "connected",
    /** A connection attempt is in flight (first try or a retry). */
    connecting: status === "connecting" || status === "reconnecting",
    /** Automatic retries are exhausted; only `reconnect()` will help. */
    offline: status === "offline",
    panes,
    systemMessage,
    /** State of the validation service the Validator exposes to the Worker. */
    validation,
    syncValidation,
    /** Slash commands per container, fetched on demand via `listCommands`. */
    commands,
    listCommands,
    sendMessage,
    cancelTurn,
    setMode,
    respondPermission,
    respondPermissions,
    /** Page further back through a pane's history. */
    loadOlder,
    /** On-demand transcript of one subagent. */
    fetchSubagentTimeline,
    /** On-demand transcript of one peer agent. */
    fetchPeerTimeline,
    /** `/clear`: archive the agent and start a fresh conversation. */
    resetAgent,
    /** File-panel operations, paired with their `fs_result` by request id. */
    fsRequest,
    connect,
    reconnect,
    disconnect,
  }
}
