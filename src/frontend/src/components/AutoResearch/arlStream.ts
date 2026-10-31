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

/** Position in a daemon timeline, used to page further back. */
export interface ARLTimelineCursor {
  epoch: string
  seq: number
}

/** Lifecycle of a provider-owned subagent (e.g. a Claude `Task`). */
export type ARLSubagentStatus = "running" | "completed" | "failed" | "canceled"

/** A subagent spawned by a container's main agent. */
export interface ARLSubagent {
  id: string
  title: string | null
  description: string | null
  /** Provider-formatted context line (model, usage...); display only. */
  subtitle: string | null
  status: ARLSubagentStatus
  createdAt: string
  updatedAt: string
}

/**
 * A top-level agent in the same container that the validation flow spawned
 * (`paseo run --label aris.role=...`). Unlike a subagent it has no parent, so
 * it only shows up in the agent directory.
 */
export interface ARLPeerAgent {
  id: string
  title: string | null
  /** `aris.role` label, e.g. `validator` / `leak-review`. */
  peerRole: string
  /** `aris.submission` label, e.g. `s001`. */
  submission: string | null
  /** Daemon lifecycle status: initializing / idle / running / error / closed. */
  status: string
  createdAt: string
  updatedAt: string
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
  /** Cursor of the oldest loaded row; `null` until history lands. */
  historyCursor: ARLTimelineCursor | null
  /** The daemon has rows older than `historyCursor`. */
  hasOlder: boolean
  /** A `timeline_fetch_older` request is in flight. */
  loadingOlder: boolean
  /** Subagents of the current main agent, oldest first. */
  subagents: ARLSubagent[]
  /** Peer agents the validation flow spawned in this container. */
  peers: ARLPeerAgent[]
  /** True between confirming `/clear` and the backend's reset result. */
  resetting: boolean
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
    historyCursor: null,
    hasOlder: false,
    loadingOlder: false,
    subagents: [],
    peers: [],
    resetting: false,
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

/**
 * Turn daemon timeline entries (history backfill or an older page) into rows.
 *
 * History keeps both halves of a compaction (loading, then completed); they
 * are folded into one row like the live stream does.
 */
export function itemsFromEntries(entries: unknown): ARLStreamItem[] {
  if (!Array.isArray(entries)) return []
  let items: ARLStreamItem[] = []
  for (const entry of entries) {
    const item = normalizeItem(entry?.item, entry?.timestamp)
    if (!item) continue
    if (item.kind === "compaction") items = appendItem(items, item)
    else items.push(item)
  }
  return items
}

/** Validate a cursor coming off the wire. */
function parseCursor(raw: any): ARLTimelineCursor | null {
  if (!raw || typeof raw.epoch !== "string" || !raw.epoch) return null
  if (typeof raw.seq !== "number" || raw.seq < 0) return null
  return { epoch: raw.epoch, seq: raw.seq }
}

/**
 * Fold a `timeline_history` frame (the tail backfill) into a pane.
 *
 * History is authoritative for everything already committed: replace, do not
 * append, so a reconnect cannot duplicate rows. The cursor of its first row
 * is what the next "load older" request pages back from.
 *
 * The backend also re-sends history mid-session when the live stream has a
 * gap. If a turn is still in flight, the newest text row is reopened for
 * streaming: the next live chunk continues that same message, and appending
 * it to a closed row would split one paragraph into two.
 */
export function applyHistory(state: ARLRoleState, data: any): ARLRoleState {
  const historyCursor = parseCursor(data?.start_cursor)
  let items = itemsFromEntries(data?.entries)
  const last = items[items.length - 1]
  if (
    data?.active_turn &&
    (last?.kind === "assistant_message" || last?.kind === "thought")
  ) {
    items = [...items.slice(0, -1), { ...last, streaming: true }]
  }
  return {
    ...state,
    items,
    historyLoaded: true,
    historyCursor,
    hasOlder: Boolean(data?.has_older) && historyCursor !== null,
    loadingOlder: false,
  }
}

/**
 * Fold a `timeline_older` frame (one page before the oldest loaded row) into
 * a pane by prepending it.
 *
 * A page is dropped when the daemon flags the cursor as stale (the timeline
 * was rebuilt) or when it was requested from a cursor that is no longer ours
 * (a reconnect replaced history in the meantime): splicing either in would
 * duplicate or reorder rows. Paging stops in both cases; the next tail
 * backfill re-arms it.
 *
 * @param state - Current pane state.
 * @param data - The frame data.
 * @param requestedFrom - Cursor the request was sent with.
 */
export function applyOlderPage(
  state: ARLRoleState,
  data: any,
  requestedFrom: ARLTimelineCursor | null,
): ARLRoleState {
  const released = { ...state, loadingOlder: false }
  if (!data?.ok) return released
  const current = state.historyCursor
  const sameOrigin =
    !!current &&
    !!requestedFrom &&
    current.epoch === requestedFrom.epoch &&
    current.seq === requestedFrom.seq
  if (data.stale || !sameOrigin) return { ...released, hasOlder: false }
  const older = itemsFromEntries(data.entries)
  const historyCursor = parseCursor(data.start_cursor)
  return {
    ...released,
    items: older.length ? [...older, ...state.items] : state.items,
    historyCursor: historyCursor ?? current,
    // An empty page means there is nothing further back, whatever the flag.
    hasOlder:
      Boolean(data.has_older) && older.length > 0 && historyCursor !== null,
  }
}

const SUBAGENT_STATUSES = new Set<string>([
  "running",
  "completed",
  "failed",
  "canceled",
])

/** Coerce a daemon subagent descriptor; `null` when it has no id. */
export function normalizeSubagent(raw: any): ARLSubagent | null {
  if (!raw || typeof raw.id !== "string" || !raw.id) return null
  const str = (value: unknown) =>
    typeof value === "string" && value.trim() ? value : null
  return {
    id: raw.id,
    title: str(raw.title),
    description: str(raw.description),
    subtitle: str(raw.subtitle),
    status: SUBAGENT_STATUSES.has(raw.status)
      ? (raw.status as ARLSubagentStatus)
      : "running",
    createdAt: typeof raw.createdAt === "string" ? raw.createdAt : "",
    updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : "",
  }
}

/** Replace a pane's subagent list with a `subagent_list` snapshot. */
export function applySubagentList(
  state: ARLRoleState,
  data: any,
): ARLRoleState {
  // A snapshot for an agent we have already replaced (`/clear`) is stale.
  if (state.agentId && data?.agent_id && data.agent_id !== state.agentId)
    return state
  const raw: unknown[] = Array.isArray(data?.subagents) ? data.subagents : []
  const subagents = raw
    .map(normalizeSubagent)
    .filter((subagent): subagent is ARLSubagent => subagent !== null)
  return { ...state, subagents }
}

/**
 * Fold an `agent.provider_subagents.update` upsert/remove into a pane.
 *
 * Updates for another parent (an archived agent, a user-created one) are
 * ignored. `timeline` updates are not part of the pane state; the subagent
 * detail view fetches its own transcript.
 */
export function applySubagentUpdate(
  state: ARLRoleState,
  payload: any,
): ARLRoleState {
  if (payload?.kind === "upsert") {
    const parent = payload.subagent?.parentAgentId
    if (state.agentId && parent && parent !== state.agentId) return state
    const subagent = normalizeSubagent(payload.subagent)
    if (!subagent) return state
    const index = state.subagents.findIndex((s) => s.id === subagent.id)
    const subagents =
      index < 0
        ? [...state.subagents, subagent]
        : state.subagents.map((s, i) => (i === index ? subagent : s))
    return { ...state, subagents }
  }
  if (payload?.kind === "remove") {
    if (state.agentId && payload.parentAgentId !== state.agentId) return state
    const subagents = state.subagents.filter((s) => s.id !== payload.subagentId)
    return subagents.length === state.subagents.length
      ? state
      : { ...state, subagents }
  }
  return state
}

/** Label the validation flow puts on the peer agents it spawns. */
export const PEER_AGENT_LABEL = "aris.role"

/** True when a daemon agent snapshot carries the peer label. */
export function hasPeerLabel(raw: any): boolean {
  const value = raw?.labels?.[PEER_AGENT_LABEL]
  return typeof value === "string" && value.length > 0
}

/**
 * Coerce a daemon agent snapshot into a peer agent.
 *
 * @param raw - Full snapshot (`agent_update`) or backend summary (`peer_agent_list`).
 * @param mainAgentId - The pane's main agent, which is never a peer.
 * @returns The peer, or `null` when the agent is not one (no label, the main
 *   agent, archived, or daemon-internal).
 */
export function normalizePeer(
  raw: any,
  mainAgentId: string | null,
): ARLPeerAgent | null {
  if (!raw || typeof raw.id !== "string" || !raw.id) return null
  if (raw.id === mainAgentId || raw.archivedAt || raw.internal) return null
  const labels = raw.labels && typeof raw.labels === "object" ? raw.labels : {}
  const peerRole = labels[PEER_AGENT_LABEL]
  if (typeof peerRole !== "string" || !peerRole) return null
  const submission = labels["aris.submission"]
  return {
    id: raw.id,
    title: typeof raw.title === "string" && raw.title.trim() ? raw.title : null,
    peerRole,
    submission:
      typeof submission === "string" && submission ? submission : null,
    status: typeof raw.status === "string" ? raw.status : "idle",
    createdAt: typeof raw.createdAt === "string" ? raw.createdAt : "",
    updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : "",
  }
}

/** Replace a pane's peer list with a `peer_agent_list` snapshot. */
export function applyPeerList(state: ARLRoleState, data: any): ARLRoleState {
  const raw: unknown[] = Array.isArray(data?.peers) ? data.peers : []
  const peers = raw
    .map((peer) => normalizePeer(peer, state.agentId))
    .filter((peer): peer is ARLPeerAgent => peer !== null)
  return { ...state, peers }
}

/**
 * Fold an `agent_update` for an agent other than the pane's main one.
 *
 * Upserts of a peer replace or add it; an upsert that no longer qualifies
 * (archived) or a `remove` drops it. Updates for unrelated agents leave the
 * state untouched, so callers can route every foreign update here.
 */
export function applyPeerUpdate(
  state: ARLRoleState,
  payload: any,
): ARLRoleState {
  const drop = (id: unknown) => {
    const peers = state.peers.filter((peer) => peer.id !== id)
    return peers.length === state.peers.length ? state : { ...state, peers }
  }
  if (payload?.kind === "remove") return drop(payload.agentId)
  if (payload?.kind !== "upsert") return state
  const peer = normalizePeer(payload.agent, state.agentId)
  if (!peer) return drop(payload.agent?.id)
  const index = state.peers.findIndex((p) => p.id === peer.id)
  const peers =
    index < 0
      ? [...state.peers, peer]
      : state.peers.map((p, i) => (i === index ? peer : p))
  return { ...state, peers }
}

/**
 * Map a peer's daemon status onto the strip's status icons.
 *
 * `idle` means its turn finished, which for a one-shot validation run is
 * "completed". `closed` (not loaded after a container restart) shows as
 * canceled; the strip also greys it out.
 */
export function peerDisplayStatus(peer: ARLPeerAgent): ARLSubagentStatus {
  switch (peer.status) {
    case "running":
    case "initializing":
      return "running"
    case "error":
      return "failed"
    case "closed":
      return "canceled"
    default:
      return "completed"
  }
}

/** Number of subagents still running in a pane. */
export function runningSubagents(state: ARLRoleState): number {
  return state.subagents.filter((s) => s.status === "running").length
}

/**
 * Fold an `agent_reset_result` frame (`/clear`) into a pane.
 *
 * On success the pane starts over on the new agent: transcript, approvals,
 * subagents and paging all belonged to the archived one. Readiness is kept,
 * since the backend is still reading our frames.
 */
export function applyAgentReset(state: ARLRoleState, data: any): ARLRoleState {
  if (!data?.ok) return { ...state, resetting: false }
  const info = data.agent ?? {}
  const fresh = applyAgentSnapshot(
    { ...emptyRoleState(), modes: state.modes, modeId: state.modeId },
    {
      id: info.agent_id,
      status: info.status,
      currentModeId: info.mode_id,
      availableModes: info.modes,
    },
  )
  // Peers belong to the container, not to the archived agent: keep them.
  return {
    ...fresh,
    historyLoaded: true,
    ready: state.ready,
    peers: state.peers,
  }
}
