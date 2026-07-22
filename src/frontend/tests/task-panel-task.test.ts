import { expect, test } from "bun:test"

import type { TaskResponse } from "../src/client"
import { resolvePanelTask } from "../src/components/Evolution/task-panel-task"
import { memoryConfigSchema } from "../src/components/Evolution/TaskDetail/config-form/appConfigSchema"

const task = (id: string, retrievalMode: "auto" | "manual") =>
  ({
    id,
    input_args: {
      memory: {
        enabled: true,
        type: "mindmemos_cloud",
        retrieval_mode: retrievalMode,
      },
    },
  }) as unknown as TaskResponse

test("uses the effective task instead of the selected root task", () => {
  const selectedRoot = task("root", "manual")
  const effectiveChild = task("child", "auto")

  expect(resolvePanelTask(selectedRoot, effectiveChild)).toBe(effectiveChild)
})

test("retains manual pinned-memory fields when task config is submitted", () => {
  const memory = memoryConfigSchema.parse({
    enabled: true,
    type: "mindmemos_cloud",
    retrieval_mode: "manual",
    pinned_card_ids: ["user-card", "project-card"],
    task_injection_mode: "weight",
    task_injection_lambda: 0.35,
  })

  expect(memory.retrieval_mode).toBe("manual")
  expect(memory.pinned_card_ids).toEqual(["user-card", "project-card"])
  expect(memory.task_injection_mode).toBe("weight")
  expect(memory.task_injection_lambda).toBe(0.35)
})
