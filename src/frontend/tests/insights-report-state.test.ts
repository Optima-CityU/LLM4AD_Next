import { expect, test } from "bun:test"

import { resolveCurrentReport } from "../src/components/Evolution/TaskDetail/insights-report-state"

test("uses the dedicated report query instead of a stale task snapshot", () => {
  const taskReport = { status: "cancelled" }
  const queriedReport = { status: "generating" }

  expect(resolveCurrentReport(taskReport, queriedReport)).toBe(queriedReport)
})
