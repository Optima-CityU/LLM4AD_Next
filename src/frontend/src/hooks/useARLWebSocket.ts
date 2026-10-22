import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import type { ResearchSessionItem } from "@/client"

/** The two containers an ARL session talks to. */
export type ARLRole = "worker" | "validator"

export const ARL_ROLES: ARLRole[] = ["worker", "validator"]

/** Turn lifecycle as reported by the Paseo daemon. */
export type ARLTurnStatus =
  | "idle"
  | "running"
  | "completed"
  | "failed"
  | "canceled"

/** A timeline item ready to be rendered by the pane. */
export type ARLStreamItem =
  | {
      kind: "user_message"
      id: string
      text: string
      messageId?: string
      clientMessageId?: string
      timestamp: string
    }
  | {
      kind: "assistant_message"
      id: string
      text: string
      messageId?: string
      /** True while the agent is still streaming chunks into this item. */
      streaming: boolean
      timestamp: string
    }
  | {
      kind: "thought"
      id: string
      text: string
      streaming: boolean
      timestamp: string
    }
  | {
      kind: "tool_call"
      id: string
      callId: string
      turnId?: string
      name: string
      detail: unknown
      status: string
      error?: string | null
      timestamp: string
    }
  | { kind: "todo_list"; id: string; items: unknown[]; timestamp: string }
  | {
      kind: "notification"
      id: string
      level: string
      message: string
      timestamp: string
    }
  | { kind: "compaction"; id: string; timestamp: string }
  | { kind: "system"; id: string; message: string; timestamp: string }

/** Pending permission request raised by a container. */
export interface ARLPermissionRequest {
  id: string
  name: string
  kind: string
  title?: string
  description?: string
  input?: Record<string, unknown>
  detail?: unknown
  /** Provider-supplied buttons; when absent the pane renders allow/deny. */
  actions?: ARLPermissionAction[]
}

/** One button offered by a permission request. */
export interface ARLPermissionAction {
  id: string
  label: string
  behavior: "allow" | "deny"
  variant?: string
}

/**
 * A permission mode reported by the container's agent provider.
 *
 * Claude exposes `plan` / `default` / `acceptEdits` / `auto` /
 * `bypassPermissions`; Codex exposes `auto` / `auto-review` / `full-access`.
 */
export interface ARLAgentMode {
  id: string
  label: string
  description?: string
}

/**
 * Mode ids that run tools without asking (Paseo marks them `isUnattended`,
 * but that flag is stripped from the wire schema, so keep our own list).
 */
const UNATTENDED_MODE_IDS = new Set([
  "bypassPermissions",
  "full-access",
  "allow-all",
  "full",
])

/** True when the given mode never prompts for permission. */
export function isUnattendedMode(modeId: string | null | undefined): boolean {
  return !!modeId && UNATTENDED_MODE_IDS.has(modeId)
}

/** Everything the left/right pane needs for one container. */
export interface ARLRoleState {
  agentId: string | null
  /** Agent status from `agent_update` (e.g. idle / running). */
  agentStatus: string | null
  turnStatus: ARLTurnStatus
  /** True between pressing stop and the daemon confirming the cancel. */
  canceling: boolean
  /** Permission mode currently active in this container. */
  modeId: string | null
  /** Modes this container's provider offers. */
  modes: ARLAgentMode[]
  items: ARLStreamItem[]
  permissions: ARLPermissionRequest[]
  /** True once the daemon history backfill for this role has landed. */
  historyLoaded: boolean
}

/** Frame pushed by the backend, one per role. */
interface ARLBackendFrame {
  type: string
  role?: ARLRole
  data: any
}

function emptyRoleState(): ARLRoleState {
  return {
    agentId: null,
    agentStatus: null,
    turnStatus: "idle",
    canceling: false,
    modeId: null,
    modes: [],
    items: [],
    permissions: [],
    historyLoaded: false,
  }
}

function emptyPanes(): Record<ARLRole, ARLRoleState> {
  return { worker: emptyRoleState(), validator: emptyRoleState() }
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

let itemSeq = 0
function nextItemId(prefix: string): string {
  itemSeq += 1
  return `${prefix}-${itemSeq}`
}

/** Monotonic tool-call status ranking, mirroring the Paseo renderer. */
const TOOL_STATUS_RANK: Record<string, number> = {
  running: 0,
  completed: 1,
  canceled: 2,
  failed: 3,
}

function mergeToolStatus(current: string, incoming: string): string {
  const a = TOOL_STATUS_RANK[current] ?? 0
  const b = TOOL_STATUS_RANK[incoming] ?? 0
  return b >= a ? incoming : current
}

/**
 * Append (or merge) a single timeline item into a role's list.
 *
 * The daemon streams assistant text and reasoning as *delta chunks*, so both
 * kinds are concatenated onto the previous item when it belongs to the same
 * message. Tool calls are merged in place by call identity, with a monotonic
 * status so a late `running` frame cannot resurrect a finished call.
 */
function appendItem(
  items: ARLStreamItem[],
  item: ARLStreamItem,
  opts: { streaming?: boolean } = {},
): ARLStreamItem[] {
  const streaming = opts.streaming ?? false

  switch (item.kind) {
    case "assistant_message":
    case "thought": {
      const last = items[items.length - 1]
      const sameKind = last?.kind === item.kind
      const messageIdMatches =
        item.kind === "assistant_message" && last?.kind === "assistant_message"
          ? item.messageId === undefined ||
            last.messageId === undefined ||
            last.messageId === item.messageId
          : true

      if (sameKind && messageIdMatches) {
        const prev = last as Extract<
          ARLStreamItem,
          { kind: "assistant_message" | "thought" }
        >
        // Whitespace-only chunk with nothing to extend: drop it, otherwise
        // the pane shows a stray empty paragraph.
        if (!item.text.trim() && !prev.text) return items
        const text = prev.text + item.text
        const stillStreaming = prev.streaming || streaming
        const merged: ARLStreamItem = {
          ...prev,
          text,
          streaming: stillStreaming,
        }
        return [...items.slice(0, -1), merged]
      }

      // A whitespace-only chunk starting a fresh item carries no content.
      if (!item.text.trim()) return items
      return [...items, { ...item, streaming } as ARLStreamItem]
    }

    case "user_message": {
      // Reconcile with the optimistic echo the composer pushed locally.
      const optimisticIndex = items.findIndex(
        (candidate) =>
          candidate.kind === "user_message" &&
          candidate.id.startsWith("optimistic-") &&
          (item.clientMessageId
            ? candidate.clientMessageId === item.clientMessageId
            : candidate.text === item.text),
      )
      if (optimisticIndex >= 0) {
        const next = [...items]
        next[optimisticIndex] = {
          ...item,
          id: next[optimisticIndex].id,
        }
        return next
      }
      if (
        item.messageId &&
        items.some(
          (candidate) =>
            candidate.kind === "user_message" &&
            candidate.messageId === item.messageId,
        )
      ) {
        return items
      }
      return [...items, item]
    }

    case "tool_call": {
      const index = items.findIndex(
        (candidate) =>
          candidate.kind === "tool_call" &&
          candidate.callId === item.callId &&
          (item.turnId === undefined || candidate.turnId === item.turnId),
      )
      if (index < 0) return [...items, item]

      const prev = items[index] as Extract<ARLStreamItem, { kind: "tool_call" }>
      const merged: ARLStreamItem = {
        ...prev,
        name: item.name || prev.name,
        // Shallow-merge same-shape details so running -> completed patches in
        // `output` / `exitCode` without losing the earlier payload.
        detail: item.detail ?? prev.detail,
        status: mergeToolStatus(prev.status, item.status),
        error: item.error ?? prev.error,
        timestamp: item.timestamp || prev.timestamp,
      }
      const next = [...items]
      next[index] = merged
      return next
    }

    default:
      return [...items, item]
  }
}

/** Mark every in-flight streamable item as finished. */
function finalizeStreaming(items: ARLStreamItem[]): ARLStreamItem[] {
  let changed = false
  const next = items.map((item) => {
    if (
      (item.kind === "assistant_message" || item.kind === "thought") &&
      item.streaming
    ) {
      changed = true
      return { ...item, streaming: false }
    }
    return item
  })
  return changed ? next : items
}

/** Normalize a raw Paseo timeline item into our renderer shape. */
function normalizeItem(raw: any, timestamp: string): ARLStreamItem | null {
  const type = raw?.type
  const ts = raw?.timestamp || timestamp
  switch (type) {
    case "user_message":
      return {
        kind: "user_message",
        id: raw.messageId || nextItemId("user"),
        text: raw.text ?? "",
        messageId: raw.messageId,
        clientMessageId: raw.clientMessageId,
        timestamp: ts,
      }
    case "assistant_message":
      return {
        kind: "assistant_message",
        id: raw.messageId || nextItemId("assistant"),
        text: raw.text ?? "",
        messageId: raw.messageId,
        streaming: false,
        timestamp: ts,
      }
    case "reasoning":
      return {
        kind: "thought",
        id: nextItemId("thought"),
        text: raw.text ?? "",
        streaming: false,
        timestamp: ts,
      }
    case "tool_call":
      return {
        kind: "tool_call",
        id: nextItemId("tool"),
        callId: raw.callId ?? nextItemId("call"),
        name: raw.name ?? "tool",
        detail: raw.detail ?? null,
        status: raw.status ?? "running",
        error: raw.error ?? null,
        timestamp: ts,
      }
    case "todo":
      return {
        kind: "todo_list",
        id: nextItemId("todo"),
        items: raw.items ?? [],
        timestamp: ts,
      }
    case "error":
      return {
        kind: "notification",
        id: nextItemId("notification"),
        level: "error",
        message: raw.message ?? "",
        timestamp: ts,
      }
    case "notification":
      return {
        kind: "notification",
        id: nextItemId("notification"),
        level: raw.level ?? "info",
        message: raw.message ?? "",
        timestamp: ts,
      }
    case "compaction":
      return { kind: "compaction", id: nextItemId("compaction"), timestamp: ts }
    default:
      return null
  }
}

interface UseARLWebSocketOptions {
  session: ResearchSessionItem | null
}

/** Normalize a raw Paseo permission request into our renderer shape. */
function normalizePermission(raw: any): ARLPermissionRequest {
  const actions = Array.isArray(raw?.actions)
    ? raw.actions
        .filter(
          (action: any) => action?.id && action?.label && action?.behavior,
        )
        .map((action: any) => ({
          id: String(action.id),
          label: String(action.label),
          behavior: action.behavior === "deny" ? "deny" : "allow",
          variant: action.variant,
        }))
    : undefined

  return {
    id: raw?.id ?? nextItemId("permission"),
    name: raw?.name ?? "permission",
    kind: raw?.kind ?? "other",
    title: raw?.title,
    description: raw?.description,
    input: raw?.input,
    detail: raw?.detail,
    ...(actions && actions.length > 0 ? { actions } : {}),
  }
}

/** Add a permission request, replacing any earlier copy of the same id. */
function upsertPermission(
  state: ARLRoleState,
  raw: any,
): ARLPermissionRequest[] {
  const request = normalizePermission(raw)
  return [...state.permissions.filter((p) => p.id !== request.id), request]
}

/** Fold an agent snapshot (from `agents_ready` / `agent_update`) into a pane. */
function applyAgentSnapshot(state: ARLRoleState, agent: any): ARLRoleState {
  const next: ARLRoleState = {
    ...state,
    agentId: agent?.id ?? state.agentId,
    agentStatus: agent?.status ?? state.agentStatus,
    modeId: agent?.currentModeId ?? state.modeId,
    modes: Array.isArray(agent?.availableModes)
      ? agent.availableModes
          .filter((mode: any) => mode?.id)
          .map((mode: any) => ({
            id: String(mode.id),
            label: String(mode.label ?? mode.id),
            description: mode.description,
          }))
      : state.modes,
  }

  // `activeTurn` is the authoritative "is a turn in flight" signal (same one
  // the Paseo composer uses). It is optional on the wire, so only trust it
  // when the daemon actually sent the key — otherwise a refresh mid-turn
  // would leave the pane stuck on "working" with no way to stop it.
  if (agent && "activeTurn" in agent) {
    if (agent.activeTurn) {
      next.turnStatus = "running"
    } else if (state.turnStatus === "running") {
      next.turnStatus = "completed"
      next.canceling = false
      next.items = finalizeStreaming(next.items)
    }
  }

  return next
}

/**
 * ARL WebSocket connector.
 *
 * One socket carries both containers; every backend frame is tagged with a
 * `role` so events can be dispatched into the matching pane. Live events
 * arrive as Paseo `agent_stream` frames carrying delta chunks, which this
 * hook reduces into renderable items per role (see {@link appendItem}).
 *
 * @param options.session - The ARL research session to attach to.
 * @returns Connection state plus per-role conversation state and actions.
 */
export function useARLWebSocket({ session }: UseARLWebSocketOptions) {
  const [connected, setConnected] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [panes, setPanes] = useState<Record<ARLRole, ARLRoleState>>(emptyPanes)
  const [systemMessage, setSystemMessage] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const closedByUs = useRef(false)
  const attemptsRef = useRef(0)

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

      if (type === "agent_stream") {
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
        if (payload.kind === "upsert") {
          updateRole(role, (state) => applyAgentSnapshot(state, payload.agent))
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
        // The daemon re-based the timeline for this agent: drop local items.
        updateRole(role, (state) => ({ ...state, items: [] }))
        return
      }

      if (type === "agent_deleted" || type === "agent_archived") {
        updateRole(role, (state) => ({ ...state, agentStatus: "deleted" }))
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

        case "agents_ready": {
          // Per-role snapshot taken when the backend attached: agent id,
          // permission mode + the modes on offer, and any approval the
          // container is still waiting on (restores state after a refresh).
          setPanes((prev) => {
            const next = { ...prev }
            for (const role of ARL_ROLES) {
              const info = frame.data?.[role]
              if (!info) continue
              let state = applyAgentSnapshot(next[role], {
                id: info.agent_id,
                status: info.status,
                activeTurn: info.active_turn ?? null,
                currentModeId: info.mode_id,
                availableModes: info.modes,
              })
              for (const request of info.pending_permissions ?? []) {
                state = {
                  ...state,
                  permissions: upsertPermission(state, request),
                }
              }
              next[role] = state
            }
            return next
          })
          return
        }

        case "timeline_history": {
          const role = frame.role
          if (!role) return
          const entries: any[] = frame.data?.entries ?? []
          const items: ARLStreamItem[] = []
          for (const entry of entries) {
            const item = normalizeItem(entry?.item, entry?.timestamp)
            if (item) items.push(item)
          }
          updateRole(role, (state) => ({
            ...state,
            // History is authoritative for everything already committed:
            // replace, don't append, so a reconnect cannot duplicate rows.
            items,
            historyLoaded: true,
          }))
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

        case "error":
          toast.error(frame.data?.message || "ARL error")
          return

        default:
          return
      }
    },
    [handleDaemonMessage, updateRole],
  )

  // The generated client type does not pin `session_type` to the ARL literal,
  // so narrow it once and reuse the flag for connect + auto-connect.
  const isArlSession =
    (session as { session_type?: string } | null)?.session_type === "arl"
  const sessionId = session?.id

  const connect = useCallback(() => {
    if (!sessionId || !isArlSession) return

    const token = localStorage.getItem("access_token")
    if (!token) {
      toast.error("Not signed in")
      return
    }

    closedByUs.current = false
    setConnecting(true)

    const ws = new WebSocket(buildArlWsUrl(String(sessionId), token))
    wsRef.current = ws

    ws.onopen = () => {
      attemptsRef.current = 0
      setConnected(true)
      setConnecting(false)
    }

    ws.onmessage = (event) => {
      try {
        handleFrame(JSON.parse(event.data) as ARLBackendFrame)
      } catch (error) {
        console.error("Failed to parse ARL frame:", error)
      }
    }

    ws.onerror = () => {
      setConnecting(false)
      setConnected(false)
    }

    ws.onclose = () => {
      setConnecting(false)
      setConnected(false)
      wsRef.current = null
      if (closedByUs.current) return

      // Containers can take a while to boot; retry with a capped backoff so a
      // transient drop does not force a manual refresh.
      attemptsRef.current += 1
      if (attemptsRef.current > 5) return
      const delay = Math.min(1000 * 2 ** (attemptsRef.current - 1), 10000)
      reconnectTimer.current = setTimeout(() => connect(), delay)
    }
  }, [handleFrame, sessionId, isArlSession])

  const disconnect = useCallback(() => {
    closedByUs.current = true
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current)
      reconnectTimer.current = null
    }
    wsRef.current?.close()
    wsRef.current = null
  }, [])

  const sendRaw = useCallback((message: Record<string, unknown>) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      toast.error("Not connected to the ARL session")
      return false
    }
    ws.send(JSON.stringify(message))
    return true
  }, [])

  /** Send a chat message to one of the containers. */
  const sendMessage = useCallback(
    (role: ARLRole, content: string) => {
      const text = content.trim()
      if (!text) return
      if (!sendRaw({ type: "agent_send", role, payload: { content: text } }))
        return

      // Optimistic echo: the daemon will send back a canonical user_message
      // which replaces this row (matched by text).
      updateRole(role, (state) => ({
        ...state,
        turnStatus: "running",
        items: [
          ...state.items,
          {
            kind: "user_message",
            id: nextItemId("optimistic"),
            text,
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

  /** Switch a container's permission mode (e.g. into an auto/bypass mode). */
  const setMode = useCallback(
    (role: ARLRole, modeId: string) => {
      sendRaw({ type: "set_agent_mode", role, payload: { mode_id: modeId } })
    },
    [sendRaw],
  )

  /** Answer a permission request raised by a container. */
  const respondPermission = useCallback(
    (role: ARLRole, requestId: string, response: Record<string, unknown>) => {
      sendRaw({
        type: "permission_response",
        role,
        payload: { request_id: requestId, response },
      })
      updateRole(role, (state) => ({
        ...state,
        permissions: state.permissions.filter((p) => p.id !== requestId),
      }))
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
      for (const requestId of requestIds) {
        sendRaw({
          type: "permission_response",
          role,
          payload: { request_id: requestId, response },
        })
      }
      updateRole(role, (state) => ({
        ...state,
        permissions: state.permissions.filter(
          (p) => !requestIds.includes(p.id),
        ),
      }))
    },
    [sendRaw, updateRole],
  )

  /** Read a file inside a container workspace. */
  const readFile = useCallback(
    (role: ARLRole, path: string) =>
      sendRaw({ type: "file_read", role, payload: { path } }),
    [sendRaw],
  )

  /** Write a file inside a container workspace. */
  const writeFile = useCallback(
    (role: ARLRole, path: string, content: string) =>
      sendRaw({ type: "file_write", role, payload: { path, content } }),
    [sendRaw],
  )

  /** List a directory inside a container workspace. */
  const listFiles = useCallback(
    (role: ARLRole, path = "") =>
      sendRaw({ type: "file_list", role, payload: { path } }),
    [sendRaw],
  )

  // Connect once per session and tear the socket down when the session
  // changes or the panel unmounts. `connect` is stable (its own deps are the
  // session identity plus memoized reducers), so it does not re-fire here.
  useEffect(() => {
    if (sessionId && isArlSession) {
      setPanes(emptyPanes())
      connect()
    }
    return () => {
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      closedByUs.current = true
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [sessionId, isArlSession, connect])

  return {
    connected,
    connecting,
    panes,
    systemMessage,
    sendMessage,
    cancelTurn,
    setMode,
    respondPermission,
    respondPermissions,
    readFile,
    writeFile,
    listFiles,
    connect,
    disconnect,
  }
}
