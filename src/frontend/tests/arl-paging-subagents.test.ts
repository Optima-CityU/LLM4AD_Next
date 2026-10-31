import assert from "node:assert/strict"
import { test } from "node:test"

import {
  resolveClientCommand,
  withClientCommands,
} from "../src/components/AutoResearch/arlCommands.ts"
import {
  applyAgentReset,
  applyHistory,
  applyOlderPage,
  applyPeerList,
  applyPeerUpdate,
  applySubagentList,
  applySubagentUpdate,
  emptyRoleState,
  normalizePeer,
  peerDisplayStatus,
  runningSubagents,
} from "../src/components/AutoResearch/arlStream.ts"

const TS = "2026-01-01T00:00:00.000Z"

const entry = (text: string, seq: number) => ({
  item: { type: "user_message", text },
  timestamp: TS,
  seqStart: seq,
})

const texts = (state: ReturnType<typeof emptyRoleState>) =>
  state.items.map((item) =>
    item.kind === "user_message" ? item.text : item.kind,
  )

// ---- history paging ----

test("tail history arms paging from its first row", () => {
  const state = applyHistory(emptyRoleState(), {
    entries: [entry("c", 30), entry("d", 31)],
    has_older: true,
    start_cursor: { epoch: "e1", seq: 30 },
  })

  assert.deepEqual(texts(state), ["c", "d"])
  assert.deepEqual(state.historyCursor, { epoch: "e1", seq: 30 })
  assert.equal(state.hasOlder, true)
  assert.equal(state.historyLoaded, true)
})

test("tail history without a cursor cannot page", () => {
  const state = applyHistory(emptyRoleState(), {
    entries: [entry("a", 1)],
    has_older: true,
  })
  assert.equal(state.hasOlder, false)
})

test("an older page is prepended and moves the cursor back", () => {
  const cursor = { epoch: "e1", seq: 30 }
  const base = {
    ...applyHistory(emptyRoleState(), {
      entries: [entry("c", 30)],
      has_older: true,
      start_cursor: cursor,
    }),
    loadingOlder: true,
  }

  const next = applyOlderPage(
    base,
    {
      ok: true,
      entries: [entry("a", 10), entry("b", 11)],
      has_older: false,
      start_cursor: { epoch: "e1", seq: 10 },
    },
    cursor,
  )

  assert.deepEqual(texts(next), ["a", "b", "c"])
  assert.deepEqual(next.historyCursor, { epoch: "e1", seq: 10 })
  assert.equal(next.hasOlder, false)
  assert.equal(next.loadingOlder, false)
})

test("a page requested from a replaced cursor is dropped", () => {
  // A reconnect replaced the history (new cursor) while the request was out.
  const base = applyHistory(emptyRoleState(), {
    entries: [entry("x", 50)],
    has_older: true,
    start_cursor: { epoch: "e2", seq: 50 },
  })

  const next = applyOlderPage(
    { ...base, loadingOlder: true },
    {
      ok: true,
      entries: [entry("old", 1)],
      has_older: true,
      start_cursor: { epoch: "e1", seq: 1 },
    },
    { epoch: "e1", seq: 30 },
  )

  assert.deepEqual(texts(next), ["x"])
  assert.equal(next.loadingOlder, false)
})

test("a stale page stops paging without touching the transcript", () => {
  const cursor = { epoch: "e1", seq: 30 }
  const base = applyHistory(emptyRoleState(), {
    entries: [entry("c", 30)],
    has_older: true,
    start_cursor: cursor,
  })

  const next = applyOlderPage(
    base,
    { ok: true, stale: true, entries: [entry("a", 1)] },
    cursor,
  )

  assert.deepEqual(texts(next), ["c"])
  assert.equal(next.hasOlder, false)
})

test("a failed page only releases the spinner", () => {
  const cursor = { epoch: "e1", seq: 30 }
  const base = applyHistory(emptyRoleState(), {
    entries: [entry("c", 30)],
    has_older: true,
    start_cursor: cursor,
  })

  const next = applyOlderPage(
    { ...base, loadingOlder: true },
    { ok: false },
    cursor,
  )

  assert.equal(next.loadingOlder, false)
  // Still pageable: the user can retry.
  assert.equal(next.hasOlder, true)
})

test("an empty page ends paging even if the flag says otherwise", () => {
  const cursor = { epoch: "e1", seq: 30 }
  const base = applyHistory(emptyRoleState(), {
    entries: [entry("c", 30)],
    has_older: true,
    start_cursor: cursor,
  })

  const next = applyOlderPage(
    base,
    { ok: true, entries: [], has_older: true, start_cursor: null },
    cursor,
  )

  assert.equal(next.hasOlder, false)
  assert.deepEqual(next.historyCursor, cursor)
})

// ---- subagents ----

const sub = (id: string, status: string, parentAgentId = "a1") => ({
  id,
  parentAgentId,
  provider: "claude",
  title: null,
  description: `task ${id}`,
  status,
  createdAt: TS,
  updatedAt: TS,
  toolCallId: null,
})

test("subagent upserts add, update and ignore foreign parents", () => {
  let state = { ...emptyRoleState(), agentId: "a1" }
  state = applySubagentUpdate(state, {
    kind: "upsert",
    subagent: sub("s1", "running"),
  })
  state = applySubagentUpdate(state, {
    kind: "upsert",
    subagent: sub("s2", "running"),
  })
  state = applySubagentUpdate(state, {
    kind: "upsert",
    subagent: sub("s1", "completed"),
  })
  // Child of an archived agent: must not show up in this pane.
  state = applySubagentUpdate(state, {
    kind: "upsert",
    subagent: sub("x", "running", "old"),
  })

  assert.deepEqual(
    state.subagents.map((s) => [s.id, s.status]),
    [
      ["s1", "completed"],
      ["s2", "running"],
    ],
  )
  assert.equal(runningSubagents(state), 1)
})

test("subagent remove drops only the matching row", () => {
  let state = applySubagentList(
    { ...emptyRoleState(), agentId: "a1" },
    { agent_id: "a1", subagents: [sub("s1", "running"), sub("s2", "failed")] },
  )
  state = applySubagentUpdate(state, {
    kind: "remove",
    parentAgentId: "a1",
    subagentId: "s1",
  })

  assert.deepEqual(
    state.subagents.map((s) => s.id),
    ["s2"],
  )
})

test("a subagent snapshot for another agent is ignored", () => {
  const state = applySubagentList(
    { ...emptyRoleState(), agentId: "a2" },
    { agent_id: "a1", subagents: [sub("s1", "running")] },
  )
  assert.equal(state.subagents.length, 0)
})

test("unknown subagent status falls back to running", () => {
  const state = applySubagentList(
    { ...emptyRoleState(), agentId: "a1" },
    { agent_id: "a1", subagents: [sub("s1", "weird"), { title: "no id" }] },
  )
  assert.deepEqual(
    state.subagents.map((s) => s.status),
    ["running"],
  )
})

// ---- /clear ----

test("a successful reset starts the pane over on the new agent", () => {
  const before = {
    ...applyHistory(emptyRoleState(), {
      entries: [entry("old", 1)],
      has_older: true,
      start_cursor: { epoch: "e1", seq: 1 },
    }),
    agentId: "a1",
    ready: true,
    resetting: true,
    modeId: "bypassPermissions",
    subagents: [],
  }

  const after = applyAgentReset(before, {
    ok: true,
    agent: { agent_id: "a2", status: "idle", mode_id: "bypassPermissions" },
  })

  assert.equal(after.agentId, "a2")
  assert.equal(after.items.length, 0)
  assert.equal(after.hasOlder, false)
  assert.equal(after.historyCursor, null)
  assert.equal(after.resetting, false)
  assert.equal(after.ready, true)
  assert.equal(after.historyLoaded, true)
})

test("a failed reset keeps the conversation", () => {
  const before = {
    ...applyHistory(emptyRoleState(), { entries: [entry("keep", 1)] }),
    agentId: "a1",
    resetting: true,
  }
  const after = applyAgentReset(before, { ok: false, message: "boom" })

  assert.equal(after.agentId, "a1")
  assert.deepEqual(texts(after), ["keep"])
  assert.equal(after.resetting, false)
})

test("/clear and /new resolve to the client command; arguments do not", () => {
  assert.equal(resolveClientCommand("/clear")?.name, "clear")
  assert.equal(resolveClientCommand("  /new  ")?.name, "clear")
  assert.equal(resolveClientCommand("/clear now"), null)
  assert.equal(resolveClientCommand("/compact"), null)
  assert.equal(resolveClientCommand("clear"), null)
})

test("client commands lead the list and shadow a daemon duplicate", () => {
  const merged = withClientCommands([
    { name: "compact", description: "", argumentHint: "", kind: "command" },
    { name: "clear", description: "daemon", argumentHint: "", kind: "command" },
  ])
  assert.deepEqual(
    merged.map((c) => [c.name, c.kind]),
    [
      ["clear", "client"],
      ["compact", "command"],
    ],
  )
})

test("history re-sent mid-turn reopens the newest text row for streaming", () => {
  const data = {
    entries: [
      { item: { type: "user_message", text: "go" }, timestamp: TS },
      { item: { type: "assistant_message", text: "partial" }, timestamp: TS },
    ],
    active_turn: true,
  }
  const live = applyHistory(emptyRoleState(), data)
  const last = live.items[live.items.length - 1]
  assert.equal(last.kind === "assistant_message" && last.streaming, true)

  const idle = applyHistory(emptyRoleState(), { ...data, active_turn: false })
  const idleLast = idle.items[idle.items.length - 1]
  assert.equal(idleLast.kind === "assistant_message" && idleLast.streaming, false)
})

const peer = (id: string, status = "running", extra: object = {}) => ({
  id,
  title: `ARIS validation ${id}`,
  status,
  labels: { "aris.role": "validator", "aris.submission": id },
  createdAt: TS,
  updatedAt: TS,
  ...extra,
})

test("peer list keeps only labelled, live agents other than the main one", () => {
  const state = { ...emptyRoleState(), agentId: "main" }
  const next = applyPeerList(state, {
    peers: [
      peer("s001"),
      peer("main"),
      { ...peer("old"), archivedAt: TS },
      { id: "plain", title: "user agent", status: "idle", labels: {} },
    ],
  })
  assert.deepEqual(
    next.peers.map((p) => [p.id, p.submission]),
    [["s001", "s001"]],
  )
})

test("peer updates upsert, drop on archive and on remove", () => {
  let state = { ...emptyRoleState(), agentId: "main" }
  state = applyPeerUpdate(state, { kind: "upsert", agent: peer("s001") })
  state = applyPeerUpdate(state, { kind: "upsert", agent: peer("s002") })
  state = applyPeerUpdate(state, {
    kind: "upsert",
    agent: peer("s001", "idle"),
  })
  assert.deepEqual(
    state.peers.map((p) => [p.id, p.status]),
    [
      ["s001", "idle"],
      ["s002", "running"],
    ],
  )
  state = applyPeerUpdate(state, {
    kind: "upsert",
    agent: peer("s002", "idle", { archivedAt: TS }),
  })
  assert.deepEqual(
    state.peers.map((p) => p.id),
    ["s001"],
  )
  state = applyPeerUpdate(state, { kind: "remove", agentId: "s001" })
  assert.equal(state.peers.length, 0)
})

test("unrelated agent updates leave the peer list untouched", () => {
  const state = { ...emptyRoleState(), agentId: "main", peers: [] }
  const next = applyPeerUpdate(state, {
    kind: "upsert",
    agent: { id: "plain", status: "idle", labels: {} },
  })
  assert.equal(next, state)
})

test("peer status maps onto strip icons; closed reads as canceled", () => {
  const status = (s: string) =>
    peerDisplayStatus(normalizePeer(peer("x", s), null)!)
  assert.equal(status("running"), "running")
  assert.equal(status("initializing"), "running")
  assert.equal(status("idle"), "completed")
  assert.equal(status("error"), "failed")
  assert.equal(status("closed"), "canceled")
})

test("clearing the main agent keeps the container's peers", () => {
  const state = {
    ...emptyRoleState(),
    agentId: "main",
    peers: [normalizePeer(peer("s001"), "main")!],
  }
  const next = applyAgentReset(state, { ok: true, agent: { agent_id: "new" } })
  assert.deepEqual(
    next.peers.map((p) => p.id),
    ["s001"],
  )
})
