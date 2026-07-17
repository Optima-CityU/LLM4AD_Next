import { expect, test } from "bun:test"

import { downgradeUnavailableLongTermMemory } from "../src/components/Evolution/TaskDetail/steps/memoryMode"

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
