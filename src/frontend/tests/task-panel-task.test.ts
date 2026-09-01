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
    mindmemos_context_char_budget: 12000,
    mindmemos_elite_code_slots: 2,
    mindmemos_elite_code_char_budget: 9000,
  })

  expect(memory.retrieval_mode).toBe("manual")
  expect(memory.pinned_card_ids).toEqual(["user-card", "project-card"])
  expect(memory.task_injection_mode).toBe("weight")
  expect(memory.task_injection_lambda).toBe(0.35)
  expect(memory.mindmemos_context_char_budget).toBe(12000)
  expect(memory.mindmemos_elite_code_slots).toBe(2)
  expect(memory.mindmemos_elite_code_char_budget).toBe(9000)
})

test("uses a bounded default for injected memory context", () => {
  const memory = memoryConfigSchema.parse({})

  expect(memory.mindmemos_context_char_budget).toBe(20000)
  expect(memory.mindmemos_elite_code_slots).toBe(1)
  expect(memory.mindmemos_elite_code_char_budget).toBe(12000)
})
