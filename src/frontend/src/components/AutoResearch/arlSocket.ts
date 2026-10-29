/**
 * Socket lifecycle for the ARL dual-agent panes.
 *
 * Deliberately free of React: the connect/retry/teardown ordering is where the
 * duplicate-connection bugs lived, so it is expressed as a plain object whose
 * dependencies (socket constructor, timers, token access) are injected and can
 * therefore be driven directly in tests. `useARLWebSocket` binds it to state.
 */

/** Lifecycle of the single socket that carries both containers. */
export type ARLConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "offline"

/** Automatic retries before the caller should offer a manual retry. */
export const MAX_RECONNECT_ATTEMPTS = 5

/**
 * How long a released socket stays reclaimable.
 *
 * Only has to outlast React's own teardown/setup round trip, which is
 * synchronous — so this is deliberately short: long enough to absorb a
 * remount, short enough that a real navigation frees the backend's container
 * attach promptly.
 */
const LINGER_MS = 150

/** Backoff for attempt N (1-based), capped so retries stay responsive. */
export function backoffDelay(attempt: number): number {
  return Math.min(1000 * 2 ** (attempt - 1), 10000)
}

/** The slice of `WebSocket` this controller relies on. */
export interface ARLSocketLike {
  readyState: number
  close(): void
  send(data: string): void
  onopen: ((event: any) => void) | null
  onmessage: ((event: { data: any }) => void) | null
  onerror: ((event: any) => void) | null
  onclose: ((event: { code?: number }) => void) | null
}

export interface ARLSocketDeps {
  /** Open a socket for this session. */
  openSocket: (sessionId: string, token: string) => ARLSocketLike
  /** Current access token, or null when signed out. */
  getToken: () => string | null
  /** Refresh the access token; rejects when the session is unrecoverable. */
  refreshToken: () => Promise<unknown>
  /** Decoded frame from the backend. */
  onFrame: (frame: unknown) => void
  /** Connection status transition. */
  onStatus: (status: ARLConnectionStatus) => void
  /** Surface a user-facing problem (signed out, send on a dead socket). */
  onError: (message: string) => void
  setTimer?: (fn: () => void, ms: number) => any
  clearTimer?: (handle: any) => void
}

/** `WebSocket.OPEN` / `CONNECTING`, named locally so fakes need no globals. */
const OPEN = 1
const CONNECTING = 0

/**
 * Owns at most one live socket for one session.
 *
 * The invariant that fixes the duplicate connections: every socket handler
 * captures the *generation* it was opened under and returns early once that
 * generation is retired. `close()` settles asynchronously, so a socket torn
 * down by a session switch — or by StrictMode's double mount — fires its
 * `onclose`
 * only after its replacement is already live. Keying on a shared mutable flag
 * let that late handler null out the live socket and schedule a reconnect on
 * top of it, leaving two sockets feeding one pane (which is what doubled every
 * streamed chunk). Generations make the stale handler a no-op.
 */
export class ARLSocketController {
  private deps: ARLSocketDeps
  private sessionId: string | null = null
  private socket: ARLSocketLike | null = null
  private generation = 0
  private attempts = 0
  private refreshed = false
  private timer: any = null
  /** Session the live socket was opened for, to decide if it can be reused. */
  private socketSessionId: string | null = null
  /** Armed by {@link scheduleTeardown}; cancelled by a same-session connect. */
  private teardownTimer: any = null
  /** Status last reported, replayed when a lingering socket is reclaimed. */
  private lastStatus: ARLConnectionStatus = "idle"

  constructor(deps: ARLSocketDeps) {
    this.deps = deps
  }

  /** The socket currently owned, for assertions and send-readiness checks. */
  get current(): ARLSocketLike | null {
    return this.socket
  }

  /** True when a frame can actually be put on the wire right now. */
  isOpen(): boolean {
    return this.socket?.readyState === OPEN
  }

  /** Point the controller at a session. Does not connect on its own. */
  setSession(sessionId: string | null): void {
    this.sessionId = sessionId
  }

  /** Reset the retry budget, so the next failure gets a fresh set of tries. */
  resetAttempts(): void {
    this.attempts = 0
    this.refreshed = false
  }

  /**
   * Open a connection, unless one is already live.
   *
   * Idempotent by design: a second call while a socket is OPEN or CONNECTING
   * is a no-op rather than a second socket.
   */
  connect(): void {
    if (!this.sessionId) return

    const existing = this.socket
    const existingLive =
      existing &&
      (existing.readyState === OPEN || existing.readyState === CONNECTING)

    if (existingLive) {
      if (this.socketSessionId === this.sessionId) {
        // Same session, socket still live: reclaim it. This is what collapses
        // a remount into one connection — the pending teardown from the old
        // cleanup is cancelled instead of aborting a half-open handshake and
        // making the next mount dial again.
        this.cancelTeardown()
        this.deps.onStatus(this.lastStatus)
        return
      }
      // Different session: the live socket is for the wrong room, so close it
      // now rather than letting it linger alongside the new one.
      this.teardown()
    }

    const token = this.deps.getToken()
    if (!token) {
      this.setStatus("offline")
      this.deps.onError("Not signed in")
      return
    }

    this.clearTimer()
    this.cancelTeardown()
    const gen = ++this.generation
    this.setStatus(this.attempts > 0 ? "reconnecting" : "connecting")

    const ws = this.deps.openSocket(this.sessionId, token)
    this.socket = ws
    this.socketSessionId = this.sessionId

    /** True only while this socket is still the one we own. */
    const isCurrent = () => this.generation === gen && this.socket === ws

    ws.onopen = () => {
      if (!isCurrent()) return
      this.attempts = 0
      this.refreshed = false
      this.setStatus("connected")
    }

    ws.onmessage = (event) => {
      if (!isCurrent()) return
      try {
        this.deps.onFrame(JSON.parse(String(event.data)))
      } catch (error) {
        console.error("Failed to parse ARL frame:", error)
      }
    }

    // `onerror` always precedes `onclose`; leave the transition to the close
    // handler so retry bookkeeping happens exactly once per connection.
    ws.onerror = () => {}

    ws.onclose = (event) => {
      if (!isCurrent()) return
      this.socket = null

      // 1008 is the backend's auth rejection. The token rides in the URL and
      // never passes through `authFetch`, so nothing refreshes it for us:
      // refresh once by hand, then retry with the new one.
      if (event?.code === 1008 && !this.refreshed) {
        this.refreshed = true
        this.setStatus("reconnecting")
        this.deps
          .refreshToken()
          .then(() => {
            // The await window is wide enough for a session switch to land.
            if (this.generation !== gen) return
            this.connect()
          })
          .catch(() => {
            if (this.generation !== gen) return
            this.setStatus("offline")
          })
        return
      }

      // Containers can take a while to boot, so retry with a capped backoff
      // rather than making a transient drop require a page refresh.
      this.attempts += 1
      if (this.attempts > MAX_RECONNECT_ATTEMPTS) {
        // Give up automatically but stay reconnectable: the pane shows a retry
        // button instead of stranding the user on a dead panel.
        this.setStatus("offline")
        return
      }
      this.setStatus("reconnecting")
      this.timer = this.setTimer(() => {
        this.timer = null
        if (this.generation !== gen) return
        this.connect()
      }, backoffDelay(this.attempts))
    }
  }

  /**
   * Abandon the current connection immediately.
   *
   * Bumping the generation retires every handler of the outgoing socket in one
   * step, so its pending `onclose` cannot touch whatever replaces it.
   */
  teardown(): void {
    this.generation += 1
    this.clearTimer()
    this.cancelTeardown()
    const ws = this.socket
    this.socket = null
    this.socketSessionId = null
    if (ws && (ws.readyState === OPEN || ws.readyState === CONNECTING)) {
      ws.close()
    }
  }

  /**
   * Release the connection, but give an imminent re-`connect()` a chance to
   * reclaim it first.
   *
   * React tears an effect down and sets it up again for reasons that have
   * nothing to do with the session: StrictMode double-invokes every mount in
   * dev, and the panel genuinely remounts whenever the session detail query
   * has to load before `activeSession` becomes non-null. Closing on the spot
   * aborted a socket that was still mid-handshake (the browser's "WebSocket is
   * closed before the connection is established" warning) and left the next
   * setup to dial a second time — two connections for one panel, and two
   * container attaches on the backend.
   *
   * Lingering briefly makes that sequence idempotent: if the next `connect()`
   * names the same session it simply takes the socket back, and if it never
   * comes the timer closes it anyway.
   */
  scheduleTeardown(): void {
    if (!this.socket) {
      this.teardown()
      return
    }
    if (this.teardownTimer !== null) return
    this.teardownTimer = (this.deps.setTimer ?? setTimeout)(() => {
      this.teardownTimer = null
      this.teardown()
    }, LINGER_MS)
  }

  /** Abort a pending {@link scheduleTeardown}, keeping the socket alive. */
  private cancelTeardown(): void {
    if (this.teardownTimer === null) return
    ;(this.deps.clearTimer ?? clearTimeout)(this.teardownTimer)
    this.teardownTimer = null
  }

  private setStatus(status: ARLConnectionStatus): void {
    this.lastStatus = status
    this.deps.onStatus(status)
  }

  /** Drop the current socket and start over with a fresh retry budget. */
  reconnect(): void {
    this.teardown()
    this.resetAttempts()
    this.connect()
  }

  /**
   * Put one frame on the wire.
   *
   * @returns Whether the frame was actually sent. Callers use this to avoid
   *   applying an optimistic update for something that never left the browser.
   */
  send(
    message: Record<string, unknown>,
    opts: { quiet?: boolean } = {},
  ): boolean {
    const ws = this.socket
    if (!ws || ws.readyState !== OPEN) {
      if (!opts.quiet) this.deps.onError("Not connected to the ARL session")
      return false
    }
    ws.send(JSON.stringify(message))
    return true
  }

  private setTimer(fn: () => void, ms: number): any {
    return (this.deps.setTimer ?? setTimeout)(fn, ms)
  }

  private clearTimer(): void {
    if (this.timer === null) return
    ;(this.deps.clearTimer ?? clearTimeout)(this.timer)
    this.timer = null
  }
}
