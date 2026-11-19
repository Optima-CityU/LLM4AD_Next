import assert from "node:assert/strict"
import { test } from "node:test"
import type { PaperRegion, PaperRegionRequest } from "../src/client"
import {
  canApplyPaperRegionRequest,
  findPaperRegion,
  paperRegionDiff,
  paperRegionEvidenceStatus,
  paperRegionSection,
  paperRegionSelection,
} from "../src/lib/paperRegions.ts"

const region = {
  id: "dynamic/server:key",
  kind: "reviewer_response",
  review_id: "review-a",
  title: "Response",
  content: "Keep before. Old claim. Keep after.",
  revision: "revision-one",
  source_refs: [],
} satisfies PaperRegion
const request = {
  id: "request-a",
  region_id: region.id,
  state: "candidate",
  instruction: "Narrow the claim",
  base_revision: region.revision,
  original_text: region.content,
  replacement_text: "Keep before. New claim. Keep after.",
  prompt: "Locate dynamic/server:key",
  created_at: "2026-11-17",
} satisfies PaperRegionRequest

test("matching requires a unique actual region and never guesses by identifier", () => {
  assert.equal(
    findPaperRegion([region], {
      reviewId: "review-a",
      content: region.content,
    }),
    region,
  )
  assert.equal(
    findPaperRegion([region], {
      reviewId: "review-b",
      content: region.content,
    }),
    undefined,
  )
  assert.equal(
    findPaperRegion([region, { ...region, id: "another" }], {
      content: region.content,
    }),
    undefined,
  )
  assert.equal(findPaperRegion([region], { content: "missing" }), undefined)
})

test("candidate approval is limited to the intended region and current revision", () => {
  assert.equal(canApplyPaperRegionRequest(region, request), true)
  assert.equal(
    canApplyPaperRegionRequest({ ...region, revision: "new" }, request),
    false,
  )
  assert.equal(
    canApplyPaperRegionRequest({ ...region, id: "neighbor" }, request),
    false,
  )
  assert.equal(
    canApplyPaperRegionRequest(region, { ...request, state: "queued" }),
    false,
  )
  assert.equal(
    canApplyPaperRegionRequest(region, { ...request, replacement_text: "" }),
    true,
  )
})

test("focused diffs retain unchanged context and support insertions and deletions", () => {
  assert.deepEqual(paperRegionDiff(region.content, request.replacement_text), {
    prefix: "Keep before. ",
    removed: "Old",
    added: "New",
    suffix: " claim. Keep after.",
  })
  assert.equal(paperRegionDiff("", "added").added, "added")
  assert.equal(paperRegionDiff("removed", "").removed, "removed")
  assert.equal(paperRegionDiff("same", "same").removed, "")
})

test("selection offsets preserve UTF-16 emoji and exact whitespace", () => {
  const content = "😀 before\n  selected **text**\nend"
  const start = content.indexOf("  selected")
  const end = content.indexOf("\nend")
  assert.deepEqual(paperRegionSelection(content, start, end), {
    start,
    end,
    original_text: "  selected **text**",
  })
  assert.equal(paperRegionSelection(content, start, start), undefined)
})

test("verification requires an explicit status and inspectable evidence", () => {
  assert.equal(paperRegionEvidenceStatus({ status: "verified" }), "unverified")
  assert.equal(paperRegionEvidenceStatus({ path: "legacy.txt" }), "unverified")
  assert.equal(
    paperRegionEvidenceStatus({ quote: "claim", review_id: "review-a" }),
    "unverified",
  )
  assert.equal(
    paperRegionEvidenceStatus({
      status: "verified",
      quote: "claim",
      review_id: "review-a",
    }),
    "verified",
  )
  assert.equal(
    paperRegionEvidenceStatus({
      status: "historical",
      version: "v1",
      quote: "old claim",
    }),
    "historical",
  )
})

test("semantic grouping recognizes original, analysis, author, shared, and chair sections", () => {
  for (const [kind, section] of Object.entries({
    original_review: "review",
    reviewer_analysis: "analysis",
    reviewer_response: "reply",
    global_response: "global",
    chair_message: "chair",
    author_needs: "author",
    proposal_methods: "proposal",
  })) {
    assert.equal(paperRegionSection({ kind }), section)
  }
})
