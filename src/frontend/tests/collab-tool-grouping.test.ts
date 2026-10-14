import assert from "node:assert/strict"
import { test } from "node:test"

import { groupConsecutiveCollabTools } from "../src/components/AutoResearch/collabToolGrouping.ts"

type Item = { name: string | null; id: string }

const group = (items: Item[]) =>
  groupConsecutiveCollabTools(items, (item) => item.name)

test("adjacent calls to the same tool form one expandable group", () => {
  const items = [
    { name: "Read", id: "1" },
    { name: "Read", id: "2" },
    { name: "Read", id: "3" },
  ]

  assert.deepEqual(group(items), [{ kind: "group", name: "Read", items }])
})

test("different tools and non-tool messages break a run", () => {
  const items = [
    { name: "Read", id: "1" },
    { name: "Read", id: "2" },
    { name: "Write", id: "3" },
    { name: null, id: "assistant" },
    { name: "Read", id: "4" },
    { name: "Read", id: "5" },
  ]

  assert.deepEqual(group(items), [
    { kind: "group", name: "Read", items: items.slice(0, 2) },
    { kind: "single", item: items[2] },
    { kind: "single", item: items[3] },
    { kind: "group", name: "Read", items: items.slice(4) },
  ])
})

test("one call stays visible as a single row", () => {
  const item = { name: "Read", id: "1" }
  assert.deepEqual(group([item]), [{ kind: "single", item }])
})
