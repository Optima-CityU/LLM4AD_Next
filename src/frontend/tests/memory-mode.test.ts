import { expect, test } from "bun:test"

import {
  createTaskMemoryOnboardingPresentation,
  downgradeUnavailableLongTermMemory,
} from "../src/components/Evolution/TaskDetail/steps/memoryMode"

test("downgrades unavailable long-term memory to temporary memory", () => {
  expect(
    downgradeUnavailableLongTermMemory({
      enabled: true,
      type: "mindmemos_cloud",
    }),
  ).toEqual({ enabled: true, type: "local_yaml" })
})

test("preserves an explicit no-memory choice when long-term memory is unavailable", () => {
  expect(
    downgradeUnavailableLongTermMemory({
      enabled: false,
      type: "local_yaml",
    }),
  ).toEqual({ enabled: false, type: "local_yaml" })
})

test("builds a read-only long-term-memory tour presentation without mutating the task draft", () => {
  const draft = { enabled: true, type: "local_yaml", task_memory_limit: 2 }

  const presentation = createTaskMemoryOnboardingPresentation(draft, "manual")

  expect(presentation).toMatchObject({
    enabled: true,
    type: "mindmemos_cloud",
    retrieval_mode: "manual",
    task_injection_mode: "weight",
    mindmemos_rerank: true,
    mindmemos_score_threshold: 0.65,
  })
  expect(draft).toEqual({ enabled: true, type: "local_yaml", task_memory_limit: 2 })
})
