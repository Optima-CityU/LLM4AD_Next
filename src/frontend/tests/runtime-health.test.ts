import { expect, test } from "bun:test"

import { applyRuntimeHealthFrame } from "../src/hooks/useRuntimeEvents"

test("gateway retry events show the next attempt and clear after recovery", () => {
  const state = applyRuntimeHealthFrame(null, "runtime", {
    type: "proxy_error",
    reason: "timeout",
    count: 1,
    retrying: true,
    attempt: 2,
    max_attempts: 3,
    retry_in: 1,
  })
  expect(state).toMatchObject({
    reason: "timeout",
    retrying: true,
    attempt: 2,
    maxAttempts: 3,
    retryIn: 1,
  })
  expect(
    applyRuntimeHealthFrame(state, "runtime", { type: "proxy_recovered" }),
  ).toBeNull()
})

test("a healthy reconnect clears a failure even if the recovery event was missed", () => {
  const state = applyRuntimeHealthFrame(null, "runtime", {
    type: "proxy_error",
    reason: "auth",
    status: 401,
  })
  expect(
    applyRuntimeHealthFrame(state, "connected", { state: { state: "ok" } }),
  ).toBeNull()
})

test("stopping or starting a turn clears a previous gateway failure", () => {
  const state = applyRuntimeHealthFrame(null, "runtime", {
    type: "proxy_error",
    count: 9,
    reason: "timeout",
  })
  for (const type of ["proxy_cancelled", "proxy_started"])
    expect(applyRuntimeHealthFrame(state, "runtime", { type })).toBeNull()
})

test("refreshing a retrying workspace restores the same attempt progress", () => {
  expect(
    applyRuntimeHealthFrame(null, "connected", {
      state: {
        state: "failing",
        failures: 2,
        reason: "timeout",
        retrying: true,
        attempt: 3,
        max_attempts: 3,
        retry_in: 2,
      },
    }),
  ).toMatchObject({
    count: 2,
    attempt: 3,
    maxAttempts: 3,
    retrying: true,
    retryIn: 2,
  })
})
