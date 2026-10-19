import assert from "node:assert/strict"
import { test } from "node:test"

import {
  appendItem,
  applyAgentSnapshot,
  applyAgentsReady,
  emptyPanes,
  emptyRoleState,
  normalizeItem,
  type ARLStreamItem,
} from "../src/components/AutoResearch/arlStream.ts"

const TS = "2026-01-01T00:00:00.000Z"

/** Live delta chunk, as produced by an `agent_stream` timeline event. */
const chunk = (
  kind: "assistant_message" | "thought",
  text: string,
  messageId?: string,
): ARLStreamItem =>
  kind === "assistant_message"
    ? {
        kind,
        id: `a-${text}`,
        text,
        messageId,
        streaming: false,
        timestamp: TS,
      }
    : { kind, id: `t-${text}`, text, streaming: false, timestamp: TS }

const feed = (items: ARLStreamItem[], next: ARLStreamItem) =>
  appendItem(items, next, { streaming: true })

test("streamed assistant chunks concatenate into one row", () => {
  let items: ARLStreamItem[] = []
  items = feed(items, chunk("assistant_message", "Hello ", "m1"))
  items = feed(items, chunk("assistant_message", "world", "m1"))

  assert.equal(items.length, 1)
  assert.equal(
    items[0].kind === "assistant_message" && items[0].text,
    "Hello world",
  )
})

test("a finished row never absorbs a later chunk", () => {
  // Reproduces the fused-reasoning bug: `thought` carries no message id, so
  // before the streaming gate two unrelated blocks merged into one row.
  const settled: ARLStreamItem[] = [
    {
      kind: "thought",
      id: "t1",
      text: "first block",
      streaming: false,
      timestamp: TS,
    },
  ]
  const items = feed(settled, chunk("thought", "second block"))

  assert.equal(items.length, 2)
  assert.equal(items[1].kind === "thought" && items[1].text, "second block")
})

test("item ids stay unique when a tool call splits one message", () => {
  // The daemon can interleave a tool call between chunks of the same message.
  // Deriving ids from `messageId` emitted two rows sharing a React key.
  let items: ARLStreamItem[] = []
  items = feed(
    items,
    normalizeItem(
      { type: "assistant_message", text: "part 1", messageId: "m1" },
      TS,
    )!,
  )
  items = feed(
    items,
    normalizeItem({ type: "tool_call", callId: "c1", name: "Read" }, TS)!,
  )
  items = feed(
    items,
    normalizeItem(
      { type: "assistant_message", text: "part 2", messageId: "m1" },
      TS,
    )!,
  )

  assert.equal(items.length, 3)
  assert.equal(new Set(items.map((i) => i.id)).size, 3)
})

test("the daemon echo reconciles the optimistic row, not duplicates it", () => {
  const optimistic: ARLStreamItem = {
    kind: "user_message",
    id: "optimistic-1",
    text: "run the benchmark",
    pending: true,
    timestamp: TS,
  }
  const items = appendItem(
    [optimistic],
    normalizeItem(
      { type: "user_message", text: "run the benchmark", messageId: "srv-1" },
      TS,
    )!,
  )

  assert.equal(items.length, 1)
  assert.equal(items[0].kind === "user_message" && items[0].messageId, "srv-1")
  assert.equal(items[0].kind === "user_message" && items[0].pending, false)
})

test("sending identical text twice keeps both rows", () => {
  // Previously the second echo re-matched the already-reconciled row (its id
  // kept the `optimistic-` prefix forever), silently losing a message.
  let items: ARLStreamItem[] = [
    {
      kind: "user_message",
      id: "optimistic-1",
      text: "again",
      pending: true,
      timestamp: TS,
    },
  ]
  items = appendItem(
    items,
    normalizeItem(
      { type: "user_message", text: "again", messageId: "srv-1" },
      TS,
    )!,
  )
  items = appendItem(items, {
    kind: "user_message",
    id: "optimistic-2",
    text: "again",
    pending: true,
    timestamp: TS,
  })
  items = appendItem(
    items,
    normalizeItem(
      { type: "user_message", text: "again", messageId: "srv-2" },
      TS,
    )!,
  )

  assert.equal(items.length, 2)
  assert.deepEqual(
    items.map((i) => (i.kind === "user_message" ? i.messageId : null)),
    ["srv-1", "srv-2"],
  )
})

test("a repeated frame for a known message is ignored", () => {
  const echo = normalizeItem(
    { type: "user_message", text: "hi", messageId: "srv-1" },
    TS,
  )!
  const items = appendItem(appendItem([], echo), { ...echo, id: "other" })

  assert.equal(items.length, 1)
})

test("a fresh pane is not ready", () => {
  // An open socket is not enough: the backend only begins reading frontend
  // frames after it emits `agents_ready`, so input stays locked until then.
  assert.equal(emptyRoleState().ready, false)
})

test("agents_ready unlocks both panes", () => {
  const panes = applyAgentsReady(emptyPanes(), {
    worker: { agent_id: "w1", status: "idle", mode_id: "default" },
    validator: { agent_id: "v1", status: "idle", mode_id: "auto" },
  })

  assert.equal(panes.worker.ready, true)
  assert.equal(panes.validator.ready, true)
  assert.equal(panes.worker.agentId, "w1")
  assert.equal(panes.validator.modeId, "auto")
  // History is settled by this point, so the spinner must not keep running.
  assert.equal(panes.worker.historyLoaded, true)
})

test("a role missing from agents_ready stays locked", () => {
  // Only one container came up: that pane must not start accepting input on
  // the strength of the other one's readiness.
  const panes = applyAgentsReady(emptyPanes(), {
    worker: { agent_id: "w1", status: "idle" },
  })

  assert.equal(panes.worker.ready, true)
  assert.equal(panes.validator.ready, false)
})

test("agents_ready restores an in-flight turn and its pending approvals", () => {
  // After a refresh mid-turn the pane has to come back with the stop button
  // live and the outstanding permission cards on screen.
  const panes = applyAgentsReady(emptyPanes(), {
    worker: {
      agent_id: "w1",
      status: "running",
      active_turn: { id: "t1" },
      pending_permissions: [{ id: "p1", name: "Bash", kind: "shell" }],
    },
  })

  assert.equal(panes.worker.turnStatus, "running")
  assert.equal(panes.worker.canceling, false)
  assert.deepEqual(
    panes.worker.permissions.map((p) => p.id),
    ["p1"],
  )
})

test("a re-attach clears a stale canceling flag", () => {
  // `canceling` belonged to the previous socket; after reconnecting we cannot
  // know whether that cancel ever landed, so the button must be released.
  const before = emptyPanes()
  before.worker = {
    ...before.worker,
    turnStatus: "running",
    canceling: true,
  }
  const panes = applyAgentsReady(before, {
    worker: { agent_id: "w1", status: "running", active_turn: { id: "t1" } },
  })

  assert.equal(panes.worker.canceling, false)
})

test("a snapshot with no active turn releases the stop button", () => {
  // A turn can finish while we are disconnected, so no `turn_*` event ever
  // arrives; without this the pane stayed stuck on "stopping".
  const stuck = {
    ...emptyRoleState(),
    turnStatus: "running" as const,
    canceling: true,
  }
  const next = applyAgentSnapshot(stuck, {
    id: "a1",
    status: "idle",
    activeTurn: null,
  })

  assert.equal(next.canceling, false)
  assert.equal(next.turnStatus, "completed")
})

test("tool calls merge by call id with a monotonic status", () => {
  let items: ARLStreamItem[] = []
  items = feed(
    items,
    normalizeItem(
      { type: "tool_call", callId: "c1", name: "Bash", status: "running" },
      TS,
    )!,
  )
  items = feed(
    items,
    normalizeItem(
      { type: "tool_call", callId: "c1", name: "Bash", status: "completed" },
      TS,
    )!,
  )
  // A late `running` frame must not resurrect a finished call.
  items = feed(
    items,
    normalizeItem(
      { type: "tool_call", callId: "c1", name: "Bash", status: "running" },
      TS,
    )!,
  )

  assert.equal(items.length, 1)
  assert.equal(items[0].kind === "tool_call" && items[0].status, "completed")
})

test("compaction loading and completed fold into one row", () => {
  const loading = normalizeItem({ type: "compaction", status: "loading" }, TS)
  const done = normalizeItem(
    {
      type: "compaction",
      status: "completed",
      trigger: "auto",
      preTokens: 168192,
    },
    TS,
  )
  assert.ok(loading && done)
  let items = appendItem([], loading)
  items = appendItem(items, loading)
  assert.equal(items.length, 1)
  items = appendItem(items, done)
  assert.equal(items.length, 1)
  assert.deepEqual(
    { ...items[0], id: "" },
    {
      kind: "compaction",
      id: "",
      status: "completed",
      trigger: "auto",
      preTokens: 168192,
      timestamp: TS,
    },
  )
  // A later compaction starts its own row rather than reusing the finished one.
  items = appendItem(items, loading)
  assert.equal(items.length, 2)
  assert.equal(items[1].kind === "compaction" && items[1].status, "loading")
})

test("compaction without a status is treated as completed", () => {
  const item = normalizeItem({ type: "compaction" }, TS)
  assert.equal(item?.kind === "compaction" && item.status, "completed")
})
