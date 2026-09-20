import assert from "node:assert/strict"
import test from "node:test"

import {
  appendParseActivity,
  groupParseActivities,
  parseActivityFromEvent,
} from "./progress.ts"

test("keeps parallel tool steps distinct and updates each step in place", () => {
  const read = parseActivityFromEvent({
    type: "step",
    progress: 24,
    stage: "analyzing",
    message: "reading",
    step_id: "read-1",
    step_kind: "tool",
    step_status: "running",
    tool_name: "Read",
  })
  const grep = parseActivityFromEvent({
    type: "step",
    progress: 28,
    stage: "analyzing",
    message: "searching",
    step_id: "grep-1",
    step_kind: "tool",
    step_status: "running",
    tool_name: "Grep",
  })
  assert.ok(read && grep)

  let events = appendParseActivity([], read)
  events = appendParseActivity(events, grep)
  events = appendParseActivity(events, { ...read, step_status: "success" })

  assert.equal(events.length, 2)
  assert.equal(events[0].step_status, "success")
  assert.equal(groupParseActivities(events)[0].steps.length, 2)
})

test("drops hidden model output and untrusted extra fields", () => {
  assert.equal(
    parseActivityFromEvent({
      type: "output",
      message: "raw model content",
      content: "secret",
    }),
    null,
  )
  const activity = parseActivityFromEvent({
    type: "step",
    progress: 18,
    stage: "analyzing",
    message: "waiting",
    step_id: "model-response",
    step_kind: "model",
    step_status: "running",
    content: "secret",
  })
  assert.ok(activity)
  assert.equal("content" in activity, false)
})

test("keeps context compaction steps visible", () => {
  const activity = parseActivityFromEvent({
    type: "step",
    progress: 46,
    stage: "compacting",
    message: "compacting",
    step_id: "context-compaction-1",
    step_kind: "context",
    step_status: "running",
  })

  assert.equal(activity?.step_kind, "context")
  assert.equal(activity?.step_id, "context-compaction-1")
})
