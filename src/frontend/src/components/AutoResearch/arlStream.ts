/**
 * Pure state reducers behind the ARL dual-agent panes.
 *
 * Kept free of React and of the socket itself so the merge/reconcile rules
 * that the live panes depend on can be exercised directly in tests; the
 * transport lives in `useARLWebSocket`.
 */

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
      /**
       * True while this row is only a local echo. Cleared once the daemon's
       * canonical `user_message` reconciles it, which is what stops a later
       * identical message from matching (and overwriting) this same row.
       */
      pending?: boolean
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
  | {
      kind: "compaction"
      id: string
      /** `loading` while the provider is summarizing, then `completed`. */
      status: "loading" | "completed"
      /** `auto` when the context window filled up, `manual` for `/compact`. */
      trigger?: string
      /** Context size in tokens right before compaction. */
      preTokens?: number
      timestamp: string
    }
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
  /**
   * True once `agents_ready` has arrived for this container.
   *
   * An open socket is not enough to accept input: the backend only starts
   * reading frontend frames *after* it has resolved credentials, booted the
   * container and created the agent — the same step that emits `agents_ready`.
   * Anything typed before that has nobody on the other end to read it.
   */
  ready: boolean
}

/**
 * State of the validation service the user brings up inside the Validator.
 *
 * The ARL flow is Validator-first: the user iterates with the Validator until
 * it starts a validation service and emits a `task.md`. Only then does the
 * Worker need the service address and that same `task.md`.
 *
 * Nothing about this is observable through the daemon — the agent starts the
 * service by itself — so the backend polls the bind-mounted files and pushes
 * this state whenever it changes.
 */
export interface ARLValidationState {
  /** True once the service address *and* its token are both readable. */
  configured: boolean
  /** Worker-reachable address, `http://<validator container>:<port>`. */
  url: string | null
  /**
   * Whether a token was found. The token itself never crosses to the browser:
   * the caller is the agent inside the container, which receives it through a
   * generated `CLAUDE.md`, and WS frames end up in the devtools console.
   */
  hasToken: boolean
  /** Whether the Validator has written `task.md` yet. */
  hasTask: boolean
  /** Why it is not usable yet, phrased for display. */
  reason: string | null
  /** True between pressing sync and the backend confirming it. */
  syncing: boolean
  /** Set once a sync has completed, so the UI can show it already ran. */
  syncedAt: string | null
}

export function emptyValidationState(): ARLValidationState {
  return {
    configured: false,
    url: null,
    hasToken: false,
    hasTask: false,
    reason: null,
    syncing: false,
    syncedAt: null,
  }
}

/**
 * Fold a `validation_status` frame into local state.
 *
 * `syncing` and `syncedAt` are client-only and must survive the merge: status
 * frames arrive every time the poller sees a change, and dropping `syncing`
 * here would release the button's spinner before the sync result lands.
 */
export function applyValidationStatus(
  prev: ARLValidationState,
  data: any,
): ARLValidationState {
  return {
    ...prev,
    configured: Boolean(data?.configured),
    url: data?.url ?? null,
    hasToken: Boolean(data?.has_token),
    hasTask: Boolean(data?.has_task),
    reason: data?.reason ?? null,
  }
}

export function emptyRoleState(): ARLRoleState {
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
    ready: false,
  }
}

export function emptyPanes(): Record<ARLRole, ARLRoleState> {
  return { worker: emptyRoleState(), validator: emptyRoleState() }
}

let itemSeq = 0

/**
 * Mint a unique id for a timeline row.
 *
 * Ids are synthetic rather than derived from the daemon's `messageId`, so two
 * rows can never collide as React keys even when they continue one logical
 * message. Merge identity is carried separately (see {@link appendItem}).
 */
export function nextItemId(prefix: string): string {
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
export function appendItem(
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
      // Only an item that is *still streaming* may absorb a new chunk. Once a
      // turn finalizes (or history lands), the tail is closed: a fresh chunk
      // starts its own row instead of being appended to finished text. Without
      // this, two unrelated reasoning blocks fuse into one, because `thought`
      // carries no message id to tell them apart.
      const tailOpen =
        (last?.kind === "assistant_message" || last?.kind === "thought") &&
        last.streaming
      const messageIdMatches =
        item.kind === "assistant_message" && last?.kind === "assistant_message"
          ? item.messageId === undefined ||
            last.messageId === undefined ||
            last.messageId === item.messageId
          : true

      if (sameKind && tailOpen && messageIdMatches) {
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
      // Already have this exact message from the daemon: ignore the repeat.
      // Checked before reconciliation so a duplicated frame cannot consume a
      // *different* pending echo that merely happens to share its text.
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

      // Reconcile with the optimistic echo the composer pushed locally. Only
      // rows still flagged `pending` are candidates, and the match is taken
      // from the front, so sending the same text twice reconciles the first
      // echo and then the second — instead of overwriting one row twice.
      const optimisticIndex = items.findIndex(
        (candidate) =>
          candidate.kind === "user_message" &&
          candidate.pending === true &&
          (item.clientMessageId
            ? candidate.clientMessageId === item.clientMessageId
            : candidate.text === item.text),
      )
      if (optimisticIndex >= 0) {
        const next = [...items]
        // Adopt the daemon's identity wholesale (including a fresh unique id)
        // and drop `pending`, retiring this row from future echo matching.
        next[optimisticIndex] = { ...item, pending: false }
        return next
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

    case "compaction": {
      // The daemon emits a `loading` item when compaction starts and a
      // `completed` one when it ends. Fold the pair into one row: the
      // completed item takes over the newest still-loading row in place.
      let open = -1
      for (let i = items.length - 1; i >= 0; i--) {
        const candidate = items[i]
        if (candidate.kind === "compaction" && candidate.status === "loading") {
          open = i
          break
        }
      }
      if (open < 0) return [...items, item]
      // A repeated `loading` frame adds nothing to the open row.
      if (item.status === "loading") return items
      const prev = items[open] as Extract<ARLStreamItem, { kind: "compaction" }>
      const next = [...items]
      next[open] = {
        ...prev,
        status: "completed",
        trigger: item.trigger ?? prev.trigger,
        preTokens: item.preTokens ?? prev.preTokens,
        timestamp: item.timestamp || prev.timestamp,
      }
      return next
    }

    default:
      return [...items, item]
  }
}

/** Mark every in-flight streamable item as finished. */
export function finalizeStreaming(items: ARLStreamItem[]): ARLStreamItem[] {
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

/**
 * Normalize a raw Paseo timeline item into our renderer shape.
 *
 * Item ids are always freshly minted and never derived from `messageId`: the
 * daemon can split one assistant message into several chunks separated by a
 * tool call, and reusing the message id would then emit two rows sharing a
 * React key. Identity for *merging* lives in `messageId` instead (see
 * {@link appendItem}), which keeps keys unique without losing continuation.
 */
export function normalizeItem(
  raw: any,
  timestamp: string,
): ARLStreamItem | null {
  const type = raw?.type
  const ts = raw?.timestamp || timestamp
  switch (type) {
    case "user_message":
      return {
        kind: "user_message",
        id: nextItemId("user"),
        text: raw.text ?? "",
        messageId: raw.messageId,
        clientMessageId: raw.clientMessageId,
        pending: false,
        timestamp: ts,
      }
    case "assistant_message":
      return {
        kind: "assistant_message",
        id: nextItemId("assistant"),
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
      return {
        kind: "compaction",
        id: nextItemId("compaction"),
        status: raw.status === "loading" ? "loading" : "completed",
        trigger: typeof raw.trigger === "string" ? raw.trigger : undefined,
        preTokens:
          typeof raw.preTokens === "number" ? raw.preTokens : undefined,
        timestamp: ts,
      }
    default:
      return null
  }
}
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
export function upsertPermission(
  state: ARLRoleState,
  raw: any,
): ARLPermissionRequest[] {
  const request = normalizePermission(raw)
  return [...state.permissions.filter((p) => p.id !== request.id), request]
}

/**
 * Fold an `agents_ready` frame into both panes.
 *
 * This frame is the readiness gate for the whole surface. The backend sends it
 * only after it has resolved credentials, booted each container and created
 * each agent — and it is the same point at which it starts reading frontend
 * frames. So this is where a pane becomes writable; an open socket on its own
 * means nothing is listening yet.
 *
 * The payload is also a full snapshot (agent id, permission mode and the modes
 * on offer, plus any approval still outstanding), which is what restores the
 * panes after a refresh or a reconnect.
 *
 * @param panes Current per-role state.
 * @param data The frame's `data`, keyed by role.
 * @returns The updated panes; roles absent from the payload are left as-is.
 */
export function applyAgentsReady(
  panes: Record<ARLRole, ARLRoleState>,
  data: any,
): Record<ARLRole, ARLRoleState> {
  const next = { ...panes }
  for (const role of ARL_ROLES) {
    const info = data?.[role]
    if (!info) continue
    let state = applyAgentSnapshot(next[role], {
      id: info.agent_id,
      status: info.status,
      activeTurn: info.active_turn ?? null,
      currentModeId: info.mode_id,
      availableModes: info.modes,
    })
    // The snapshot is authoritative: requests resolved or expired while the
    // socket was down never send `agent_permission_resolved`, so replace the
    // list instead of merging into it.
    if (Array.isArray(info.pending_permissions)) {
      state = { ...state, permissions: [] }
      for (const request of info.pending_permissions) {
        state = { ...state, permissions: upsertPermission(state, request) }
      }
    }
    next[role] = {
      ...state,
      // The container is attached and the backend is now reading frontend
      // frames, so this pane can accept input.
      ready: true,
      // `canceling` belongs to the socket that sent the cancel. On a fresh
      // attach we cannot know whether it landed, so drop it rather than leave
      // the stop button spinning forever.
      canceling: false,
      // The backend pushes `timeline_history` *before* this frame (see
      // `_backfill_history`), but it skips the push when the tail fetch fails.
      // By the time we are here, history either arrived or never will — so
      // stop waiting on it either way. A late frame simply replaces `items`.
      historyLoaded: true,
    }
  }
  return next
}

/** Fold an agent snapshot (`agents_ready` / `agent_update`) into a pane. */
export function applyAgentSnapshot(
  state: ARLRoleState,
  agent: any,
): ARLRoleState {
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
    } else {
      // No turn in flight. Anything we were waiting on is settled, so release
      // the stop button here too: a `turn_*` event may never arrive, since a
      // turn can finish while we are disconnected.
      next.canceling = false
      if (state.turnStatus === "running") {
        next.turnStatus = "completed"
        next.items = finalizeStreaming(next.items)
      }
    }
  }

  return next
}
