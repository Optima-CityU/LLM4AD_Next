import assert from "node:assert/strict"
import { test } from "node:test"
import {
  evaluationDownloadName,
  evaluationFileTree,
  evaluationNeedsPolling,
  evaluationPreviewKind,
  formatEvaluationBytes,
  visibleEvaluationFiles,
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

test("gallery hides only byte-identical behavior image aliases", () => {
  const camera = file("screenshot_main", "image/png")
  const alias = file("behavior/0-0.png", "image/png")
  const different = {
    ...file("behavior/0-1.png", "image/png"),
    sha256: "b".repeat(64),
  }
  const secondCamera = file("screenshot_wall", "image/png")
  assert.deepEqual(
    visibleEvaluationFiles([alias, camera, different, secondCamera, camera]),
    [camera, different, secondCamera],
  )
  assert.deepEqual(visibleEvaluationFiles([alias]), [alias])
  const noHash = { ...camera, sha256: "" }
  const noHashAlias = { ...alias, sha256: "" }
  assert.deepEqual(visibleEvaluationFiles([noHash, noHashAlias]), [
    noHash,
    noHashAlias,
  ])
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

test("download names add image extensions without renaming existing files", () => {
  for (const [mime, extension] of [
    ["image/png", ".png"],
    ["image/jpeg", ".jpg"],
    ["image/webp", ".webp"],
    ["image/gif", ".gif"],
  ]) {
    assert.equal(
      evaluationDownloadName(file("figures/screenshot", mime)),
      `screenshot${extension}`,
    )
  }
  assert.equal(
    evaluationDownloadName(file("figures\\screenshot", "image/png")),
    "screenshot.png",
  )
  assert.equal(
    evaluationDownloadName(file("frame.PNG", "image/png")),
    "frame.PNG",
  )
  assert.equal(
    evaluationDownloadName(file("frame.custom", "image/png")),
    "frame.custom",
  )
  assert.equal(evaluationDownloadName(file(".hidden", "image/png")), ".hidden")
  assert.equal(
    evaluationDownloadName(file("frame", "IMAGE/PNG; charset=binary")),
    "frame.png",
  )
  assert.equal(evaluationDownloadName(file("data")), "data")
})
