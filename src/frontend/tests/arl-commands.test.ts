import assert from "node:assert/strict"
import { test } from "node:test"

import {
  type ARLCommand,
  applyCommand,
  filterCommands,
  normalizeCommands,
  slashQuery,
} from "../src/components/AutoResearch/arlCommands.ts"

const cmd = (name: string, kind = "command"): ARLCommand => ({
  name,
  description: "",
  argumentHint: "",
  kind,
})

test("slashQuery opens only while a bare command name is typed", () => {
  assert.equal(slashQuery("/"), "")
  assert.equal(slashQuery("/go"), "go")
  assert.equal(slashQuery("/goal "), null)
  assert.equal(slashQuery("/goal pause"), null)
  assert.equal(slashQuery("hello /goal"), null)
  assert.equal(slashQuery(" /goal"), null)
  assert.equal(slashQuery("/a\nb"), null)
  assert.equal(slashQuery(""), null)
})

test("filterCommands puts prefix matches before substring matches", () => {
  const list = [cmd("review-agent"), cmd("compact"), cmd("validation-review")]
  assert.deepEqual(
    filterCommands(list, "rev").map((c) => c.name),
    ["review-agent", "validation-review"],
  )
  assert.deepEqual(
    filterCommands(list, "REVIEW").map((c) => c.name),
    ["review-agent", "validation-review"],
  )
  assert.equal(filterCommands(list, "").length, 3)
  assert.equal(filterCommands(list, "zzz").length, 0)
})

test("applyCommand writes the command with a trailing space", () => {
  assert.equal(applyCommand(cmd("goal")), "/goal ")
})

test("normalizeCommands drops junk, strips slashes and de-duplicates", () => {
  const out = normalizeCommands([
    {
      name: "compact",
      description: "Summarize",
      argumentHint: "",
      kind: "command",
    },
    { name: "/goal", argumentHint: "[<objective>]" },
    { name: "compact", description: "dup" },
    { name: "" },
    null,
    "nope",
  ])
  assert.deepEqual(out, [
    {
      name: "compact",
      description: "Summarize",
      argumentHint: "",
      kind: "command",
    },
    {
      name: "goal",
      description: "",
      argumentHint: "[<objective>]",
      kind: "command",
    },
  ])
  assert.deepEqual(normalizeCommands(undefined), [])
})
