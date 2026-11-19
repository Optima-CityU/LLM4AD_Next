import assert from "node:assert/strict"
import { test } from "node:test"
import {
  evaluationFileTree,
  evaluationNeedsPolling,
  evaluationPreviewKind,
  formatEvaluationBytes,
} from "../src/lib/evaluationArtifacts.ts"

const file = (name: string, media_type = "application/octet-stream") => ({
  id: name,
  name,
  media_type,
  size: 1024,
  sha256: "a".repeat(64),
})

test("logical artifact names preserve nested files and file/directory name collisions", () => {
  const tree = evaluationFileTree([
    file("report"),
    file("report/measurements.json"),
    file("report/readme.md"),
  ])
  assert.equal(tree.length, 2)
  assert.equal(tree[0].type, "file")
  assert.equal(tree[1].path, "report/")
  assert.deepEqual(
    tree[1].children?.map((node) => node.path),
    ["report/measurements.json", "report/readme.md"],
  )
})

test("only common safe formats receive automatic previews", () => {
  assert.equal(evaluationPreviewKind(file("measurements.json")), "text")
  assert.equal(evaluationPreviewKind(file("REPORT.MD")), "text")
  assert.equal(evaluationPreviewKind(file("capture", "image/png")), "image")
  assert.equal(evaluationPreviewKind(file("trace.pftrace")), null)
  assert.equal(evaluationPreviewKind(file("image.svg", "image/svg+xml")), null)
  assert.equal(evaluationPreviewKind(file("report.html", "text/html")), "text")
  assert.equal(formatEvaluationBytes(1024), "1.0 KiB")
})

test("remote finalization keeps polling after the parent task stops", () => {
  for (const state of [
    "queued",
    "accepted",
    "running",
    "cancel_requested",
    "recovery_required",
    "reconnecting",
  ]) {
    assert.equal(evaluationNeedsPolling({ state }), true, state)
  }
  for (const state of ["completed", "failed", "cancelled"]) {
    assert.equal(evaluationNeedsPolling({ state }), false, state)
    assert.equal(
      evaluationNeedsPolling({ state, transfer_pending: true }),
      true,
      state,
    )
  }
  assert.equal(evaluationNeedsPolling(undefined), false)
})
