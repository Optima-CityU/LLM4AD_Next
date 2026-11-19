import { expect, test } from "bun:test"

import {
  linkedTaskVersionOverride,
  taskListItemChanged,
} from "../src/lib/task-version"

const root = {
  id: "root",
  name: "Evolution",
  status: "failed" as const,
  active_child_id: "latest",
  active_status: "running" as const,
  parent_id: null,
  group_id: null,
}

test("root task links follow the active version instead of forcing version zero", () => {
  const override = linkedTaskVersionOverride(root)
  expect(override).toBeNull()
  expect(override ?? root.active_child_id ?? root.id).toBe("latest")
})

test("explicit child links retain the requested historical version", () => {
  expect(
    linkedTaskVersionOverride({
      ...root,
      id: "history",
      parent_id: "root",
      group_id: "root",
    }),
  ).toBe("history")
})

test("standalone tasks and root versions remain selectable", () => {
  const standalone = { ...root, active_child_id: null }
  expect(
    linkedTaskVersionOverride(standalone) ??
      standalone.active_child_id ??
      standalone.id,
  ).toBe("root")
})

test("a legacy root group does not make its link an explicit child override", () => {
  expect(linkedTaskVersionOverride({ ...root, group_id: "root" })).toBeNull()
  expect(
    linkedTaskVersionOverride({ ...root, id: "child", group_id: "root" }),
  ).toBe("child")
})

test("list refresh picks up active version changes without a root status or name change", () => {
  expect(taskListItemChanged(root, { ...root, active_child_id: "newer" })).toBe(
    true,
  )
  expect(
    taskListItemChanged(root, { ...root, active_status: "completed" }),
  ).toBe(true)
  expect(taskListItemChanged(root, { ...root })).toBe(false)
})
