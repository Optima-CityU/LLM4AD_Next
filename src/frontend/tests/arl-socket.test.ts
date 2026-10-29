import assert from "node:assert/strict"
import { test } from "node:test"

import {
  ARLSocketController,
  MAX_RECONNECT_ATTEMPTS,
  type ARLConnectionStatus,
  type ARLSocketLike,
} from "../src/components/AutoResearch/arlSocket.ts"

const CONNECTING = 0
const OPEN = 1
const CLOSED = 3

/**
 * Fake socket with a manually driven lifecycle.
 *
 * `close()` only marks the socket closed — the `onclose` callback has to be
 * fired explicitly, which is what lets these tests reproduce the real browser
 * ordering where a close settles *after* its replacement is already live.
 */
class FakeSocket implements ARLSocketLike {
  readyState = CONNECTING
  sent: string[] = []
  closeCalls = 0
  onopen: ((event: any) => void) | null = null
  onmessage: ((event: { data: any }) => void) | null = null
  onerror: ((event: any) => void) | null = null
  onclose: ((event: { code?: number }) => void) | null = null

  sessionId: string
  token: string

  constructor(sessionId: string, token: string) {
    this.sessionId = sessionId
    this.token = token
  }

  close(): void {
    this.closeCalls += 1
    this.readyState = CLOSED
  }

  send(data: string): void {
    this.sent.push(data)
  }

  /** Complete the handshake. */
  open(): void {
    this.readyState = OPEN
    this.onopen?.({})
  }

  /** Deliver the deferred close event. */
  fireClose(code?: number): void {
    this.readyState = CLOSED
    this.onclose?.({ code })
  }

  get isLive(): boolean {
    return this.readyState === OPEN || this.readyState === CONNECTING
  }
}

interface Harness {
  controller: ARLSocketController
  sockets: FakeSocket[]
  statuses: ARLConnectionStatus[]
  errors: string[]
  frames: unknown[]
  /** Pending backoff timers, fired manually via `runTimers`. */
  runTimers: () => void
  token: { value: string | null }
  refreshes: number
}

function harness(opts: { refreshFails?: boolean } = {}): Harness {
  const sockets: FakeSocket[] = []
  const statuses: ARLConnectionStatus[] = []
  const errors: string[] = []
  const frames: unknown[] = []
  const token = { value: "tok-1" }
  const timers = new Map<number, () => void>()
  let nextHandle = 1
  let refreshes = 0

  const controller = new ARLSocketController({
    openSocket: (sessionId, tok) => {
      const socket = new FakeSocket(sessionId, tok)
      sockets.push(socket)
      return socket
    },
    getToken: () => token.value,
    refreshToken: () => {
      refreshes += 1
      token.value = "tok-2"
      return opts.refreshFails
        ? Promise.reject(new Error("refresh failed"))
        : Promise.resolve("tok-2")
    },
    onFrame: (frame) => frames.push(frame),
    onStatus: (status) => statuses.push(status),
    onError: (message) => errors.push(message),
    // Keyed by handle so clearing one timer leaves the others armed — the
    // linger timer and the backoff timer must not cancel each other.
    setTimer: (fn) => {
      const handle = nextHandle++
      timers.set(handle, fn)
      return handle
    },
    clearTimer: (handle) => {
      timers.delete(handle)
    },
  })

  return {
    controller,
    sockets,
    statuses,
    errors,
    frames,
    runTimers: () => {
      const pending = [...timers.values()]
      timers.clear()
      for (const fn of pending) fn()
    },
    token,
    get refreshes() {
      return refreshes
    },
  } as Harness
}

const live = (sockets: FakeSocket[]) => sockets.filter((s) => s.isLive)

test("a remount dials once and never aborts a handshake", () => {
  // The reported symptom: refreshing (or opening a not-yet-cached session)
  // produced two connections — one logging "WebSocket is closed before the
  // connection is established" and one that worked. React sets the effect up,
  // tears it down and sets it up again; closing on the spot killed socket #1
  // mid-handshake and made setup #2 dial a second time.
  const h = harness()
  h.controller.setSession("s1")

  h.controller.connect() // setup #1
  h.controller.scheduleTeardown() // teardown #1 (deferred)
  h.controller.connect() // setup #2 reclaims the lingering socket

  assert.equal(h.sockets.length, 1, "only one connection was opened")
  assert.equal(h.sockets[0].closeCalls, 0, "the handshake was never aborted")

  h.runTimers() // the linger timer must have been cancelled
  assert.equal(h.sockets[0].closeCalls, 0)
  assert.equal(h.controller.current, h.sockets[0])

  // And it still completes normally.
  h.sockets[0].open()
  assert.equal(h.statuses.at(-1), "connected")
})

test("a reclaimed socket replays its status to the new subscriber", () => {
  // The remounted pane renders from fresh state, so it has to be told the
  // connection it just inherited is already up.
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()
  assert.equal(h.statuses.at(-1), "connected")

  h.controller.scheduleTeardown()
  h.controller.connect()

  assert.equal(h.sockets.length, 1)
  assert.equal(h.statuses.at(-1), "connected")
})

test("a release with no follow-up closes once the linger expires", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()

  h.controller.scheduleTeardown()
  assert.equal(h.sockets[0].closeCalls, 0, "not closed yet")

  h.runTimers()
  assert.equal(h.sockets[0].closeCalls, 1)
  assert.equal(h.controller.current, null)
})

test("a lingering socket is not reused for a different session", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()

  // Navigating to another session: the live socket is for the wrong room.
  h.controller.scheduleTeardown()
  h.controller.setSession("s2")
  h.controller.connect()

  assert.equal(h.sockets.length, 2)
  assert.equal(h.sockets[0].closeCalls, 1, "the old room was closed at once")
  assert.equal(h.sockets[1].sessionId, "s2")
  assert.deepEqual(live(h.sockets), [h.sockets[1]])
})

test("repeated releases arm only one linger timer", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()

  h.controller.scheduleTeardown()
  h.controller.scheduleTeardown()
  h.runTimers()

  assert.equal(h.sockets[0].closeCalls, 1)
})

test("releasing before a socket exists tears down immediately", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.scheduleTeardown()

  assert.equal(h.controller.current, null)
})

test("a pending retry survives a reclaim", () => {
  // The linger timer and the backoff timer are independent: reclaiming a
  // socket must not cancel a reconnect that is already scheduled.
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].fireClose(1006) // schedules a retry, socket is gone

  h.controller.scheduleTeardown() // nothing live -> immediate teardown
  assert.equal(h.controller.current, null)
})

test("a StrictMode double mount leaves exactly one live socket", () => {
  // The original bug: cleanup closes socket #1, the remount opens #2, then #1's
  // deferred onclose nulls the live ref and schedules a reconnect, ending
  // with #2 and #3 both open and both feeding the same panes.
  const h = harness()
  h.controller.setSession("s1")

  h.controller.connect() // first mount
  h.controller.teardown() // cleanup
  h.controller.connect() // remount
  h.sockets[1].open()

  // Socket #1's close event arrives only now, after #2 is already live.
  h.sockets[0].fireClose()
  h.runTimers()

  assert.equal(h.sockets.length, 2, "no third socket was opened")
  assert.deepEqual(live(h.sockets), [h.sockets[1]])
  assert.equal(h.controller.current, h.sockets[1])
})

test("switching sessions does not let the old socket reopen", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()

  // Same instance, new session: teardown then connect, old close still pending.
  h.controller.teardown()
  h.controller.setSession("s2")
  h.controller.connect()
  h.sockets[1].open()
  h.sockets[0].fireClose()
  h.runTimers()

  assert.equal(h.sockets.length, 2)
  assert.deepEqual(live(h.sockets), [h.sockets[1]])
  assert.equal(h.sockets[1].sessionId, "s2")
})

test("a retired socket's frames are ignored", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()
  h.controller.teardown()
  h.controller.connect()
  h.sockets[1].open()

  // The orphaned socket can still receive before the browser finishes closing
  // it. Its chunks used to be appended a second time, doubling streamed text.
  h.sockets[0].onmessage?.({ data: JSON.stringify({ type: "stale" }) })
  h.sockets[1].onmessage?.({ data: JSON.stringify({ type: "fresh" }) })

  assert.deepEqual(h.frames, [{ type: "fresh" }])
})

test("connect is idempotent while a socket is live", () => {
  const h = harness()
  h.controller.setSession("s1")

  h.controller.connect()
  h.controller.connect() // still CONNECTING
  h.sockets[0].open()
  h.controller.connect() // now OPEN

  assert.equal(h.sockets.length, 1)
})

test("an unexpected drop reconnects with a capped backoff", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()

  h.sockets[0].fireClose(1006)
  assert.equal(h.statuses.at(-1), "reconnecting")
  h.runTimers()

  assert.equal(h.sockets.length, 2)
})

test("retries stop at the cap and stay manually recoverable", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()

  for (let i = 0; i <= MAX_RECONNECT_ATTEMPTS; i += 1) {
    h.sockets.at(-1)!.fireClose(1006)
    h.runTimers()
  }

  assert.equal(h.statuses.at(-1), "offline")
  assert.equal(h.sockets.length, MAX_RECONNECT_ATTEMPTS + 1)

  // `reconnect()` clears the budget, so the user is not stuck on a dead panel.
  h.controller.reconnect()
  assert.equal(h.sockets.length, MAX_RECONNECT_ATTEMPTS + 2)
})

test("a successful connection clears the retry budget", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()

  h.sockets[0].fireClose(1006)
  h.runTimers()
  h.sockets[1].open() // recovered

  // A later drop therefore gets the full set of retries again.
  h.sockets[1].fireClose(1006)
  h.runTimers()
  assert.equal(h.sockets.length, 3)
  assert.notEqual(h.statuses.at(-1), "offline")
})

test("an auth rejection refreshes the token once and retries", () => {
  // The token rides in the URL, so nothing else refreshes it for this socket.
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  assert.equal(h.sockets[0].token, "tok-1")

  h.sockets[0].fireClose(1008)

  return new Promise<void>((resolve) => {
    setImmediate(() => {
      assert.equal(h.sockets.length, 2)
      assert.equal(h.sockets[1].token, "tok-2")
      resolve()
    })
  })
})

test("a failed refresh goes offline instead of looping", () => {
  const h = harness({ refreshFails: true })
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].fireClose(1008)

  return new Promise<void>((resolve) => {
    setImmediate(() => {
      assert.equal(h.sockets.length, 1)
      assert.equal(h.statuses.at(-1), "offline")
      resolve()
    })
  })
})

test("a refresh that lands after a session switch is discarded", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].fireClose(1008)

  // The refresh is in flight; the user switches away in the meantime.
  h.controller.teardown()
  h.controller.setSession("s2")

  return new Promise<void>((resolve) => {
    setImmediate(() => {
      // The stale refresh must not resurrect a socket for the old session.
      assert.equal(h.sockets.length, 1)
      resolve()
    })
  })
})

test("signed out reports offline without opening a socket", () => {
  const h = harness()
  h.token.value = null
  h.controller.setSession("s1")
  h.controller.connect()

  assert.equal(h.sockets.length, 0)
  assert.equal(h.statuses.at(-1), "offline")
  assert.deepEqual(h.errors, ["Not signed in"])
})

test("send refuses on a socket that is not open", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()

  // Still CONNECTING: the frame would be lost, so the caller must learn it.
  assert.equal(h.controller.send({ type: "agent_send" }), false)
  assert.deepEqual(h.errors, ["Not connected to the ARL session"])

  h.sockets[0].open()
  assert.equal(h.controller.send({ type: "agent_send" }), true)
  assert.deepEqual(h.sockets[0].sent, ['{"type":"agent_send"}'])
})

test("quiet sends report failure without a toast", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()

  assert.equal(h.controller.send({ type: "x" }, { quiet: true }), false)
  assert.deepEqual(h.errors, [])
})

test("teardown closes the socket and cancels a pending retry", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()
  h.sockets[0].fireClose(1006) // schedules a retry

  h.controller.teardown()
  h.runTimers()

  assert.equal(h.sockets.length, 1)
  assert.equal(h.controller.current, null)
})

test("teardown does not close an already-closed socket twice", () => {
  const h = harness()
  h.controller.setSession("s1")
  h.controller.connect()
  h.sockets[0].open()
  h.sockets[0].fireClose(1006)
  h.runTimers()

  assert.equal(h.sockets[0].closeCalls, 0, "a closed socket is left alone")
})

test("no session means no socket", () => {
  const h = harness()
  h.controller.setSession(null)
  h.controller.connect()

  assert.equal(h.sockets.length, 0)
})
