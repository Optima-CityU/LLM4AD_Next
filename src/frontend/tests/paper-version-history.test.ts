import assert from "node:assert/strict"
import { test } from "node:test"
import type { PaperHistoryResponse } from "../src/client"
import {
  buildHistoryTree,
  initialHistoryPair,
  parseRevisionDiff,
  resolveHistoryImagePath,
} from "../src/components/Paper/history/historyHelpers"

const revision = (
  id: string,
  time: string,
  current = false,
): PaperHistoryResponse["revisions"][number] => ({
  id,
  label: id,
  created_time: time,
  origin: "manual",
  summary: "",
  source_version_id: null,
  state_hash: "a".repeat(64),
  is_current: current,
})

test("default comparison selects the immediately previous revision even with unsorted newer records", () => {
  const history: PaperHistoryResponse = {
    current_revision_id: "current",
    current_state_hash: "a".repeat(64),
    revisions: [
      revision("older", "2026-11-15"),
      revision("current", "2026-11-17", true),
      revision("newer", "2026-11-18"),
      revision("previous", "2026-11-16"),
    ],
  }
  assert.deepEqual(initialHistoryPair(history), {
    baseId: "previous",
    targetId: "current",
  })
  assert.deepEqual(
    initialHistoryPair({ ...history, revisions: [history.revisions[1]] }),
    { baseId: "current", targetId: "current" },
  )
  assert.deepEqual(initialHistoryPair({ ...history, revisions: [] }), {
    baseId: "",
    targetId: "",
  })
})

test("the shared tree preserves all output namespaces and real directory nodes", () => {
  const tree = buildHistoryTree([
    { path: "source/sections/methods.typ" },
    { path: "source/figure.png" },
    { path: "outputs/rebuttal/reviewer.md" },
    { path: "outputs/ac-summary.md" },
  ])
  assert.equal(tree.length, 2)
  assert.equal(tree[0].type, "directory")
  assert.equal(
    tree[0].children?.[0].children?.[0].path,
    "source/sections/methods.typ",
  )
  assert.equal(tree[1].children?.[1].path, "outputs/ac-summary.md")
  assert.equal(tree[0].children?.[1].path, "source/figure.png")
})

test("candidate decoding preserves Unicode, missing sides and binary byte counts", () => {
  const encode = (value: string) => Buffer.from(value).toString("base64")
  const patch = parseRevisionDiff(
    JSON.stringify({
      schema_version: "paper-source-diff-v1",
      source_version_id: "current",
      content_hash: "hash",
      files: [
        {
          path: "reply.md",
          original: encode("原始"),
          replacement: encode("Revised"),
          original_text: "原始",
        },
        { path: "new.md", original: null, replacement: encode("new") },
        { path: "gone.md", original: encode("gone"), replacement: null },
        {
          path: "figure.png",
          original: Buffer.from([0, 255]).toString("base64"),
          replacement: Buffer.from([0, 254, 1]).toString("base64"),
        },
      ],
    }),
  )
  assert.equal(patch.files[0].original, "原始")
  assert.equal(patch.files[1].original, null)
  assert.equal(patch.files[2].replacement, null)
  assert.equal(patch.files[3].binary, true)
  assert.equal(patch.files[3].originalBytes, 2)
  assert.equal(patch.files[3].replacementBytes, 3)
  assert.equal(patch.sourceVersionId, "current")
})

test("legacy fragment suggestions remain readable and malformed/duplicate patches fail closed", () => {
  assert.equal(
    parseRevisionDiff(
      JSON.stringify({
        source_path: "paper.typ",
        original_text: "before",
        replacement_text: "after",
      }),
    ).files[0].replacement,
    "after",
  )
  assert.throws(() => parseRevisionDiff("not JSON"))
  assert.throws(() =>
    parseRevisionDiff(
      JSON.stringify({ schema_version: "paper-source-diff-v1", files: [] }),
    ),
  )
  assert.throws(
    () =>
      parseRevisionDiff(
        JSON.stringify({
          schema_version: "paper-source-diff-v1",
          files: [
            { path: "same", original: null, replacement: "YQ==" },
            { path: "same", original: null, replacement: "Yg==" },
          ],
        }),
      ),
    /Invalid file path/,
  )
})

test("Markdown image paths stay within the selected revision project root", () => {
  assert.equal(
    resolveHistoryImagePath("source/docs/paper.md", "../figures/a.png"),
    "source/figures/a.png",
  )
  assert.equal(
    resolveHistoryImagePath(
      "source/docs/paper.md",
      "./plot%20one.png?raw=1#preview",
    ),
    "source/docs/plot one.png",
  )
  assert.equal(
    resolveHistoryImagePath("source/docs/paper.md", "..%2Ffigure.png"),
    "source/figure.png",
  )
  for (const source of [
    "../../secret.png",
    "../../../secret.png",
    "%2e%2e/%2e%2e/secret.png",
    "%252e%252e/secret.png",
    "/private/a.png",
    "//foreign.invalid/a.png",
    "https://foreign.invalid/a.png",
    "../bad%00.png",
    "..%5Csecret.png",
    "%broken",
  ]) {
    assert.equal(
      resolveHistoryImagePath("source/docs/paper.md", source),
      null,
      source,
    )
  }
  assert.equal(
    resolveHistoryImagePath("outputs/rebuttal/a.md", "../ac-summary.png"),
    "outputs/ac-summary.png",
  )
})
