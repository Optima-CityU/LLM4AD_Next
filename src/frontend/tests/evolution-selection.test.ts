import assert from "node:assert/strict"
import { test } from "node:test"
import type { GANode } from "../src/components/Evolution/TaskDetail/island-ga-mock-data.ts"
import { computeNodeClassifications } from "../src/components/Evolution/TaskDetail/node-classification.ts"
import {
  compareNodeFitness,
  hasNodeFitness,
  selectionScoreStats,
  toSelectedNodeInfo,
  visibleNodeSelection,
} from "../src/lib/evolutionSelection.ts"

const node = (id: string, rawScore: number, unscored = false): GANode => ({
  id,
  rawScore,
  unscored,
  score: 0,
  generation: 2,
  island: 0,
  islandId: "0",
  name: id,
  fileName: `${id}.json`,
  parentIds: [],
})

test("visible failed nodes can be selected without losing their unscored flag", () => {
  const failed = node("failed", 0, true)
  assert.deepEqual(visibleNodeSelection(failed, 2), toSelectedNodeInfo(failed))
  assert.equal(visibleNodeSelection(failed, 2)?.unscored, true)
  assert.equal(hasNodeFitness(visibleNodeSelection(failed, 2)!), false)
  assert.equal(visibleNodeSelection(failed, 1), null)
  assert.equal(visibleNodeSelection(node("scored", -5), 1), null)
  assert.equal(visibleNodeSelection(node("scored", -5), 2)?.rawScore, -5)
})

test("failed-node zero placeholders never outrank negative scores or enter score statistics", () => {
  const nodes = [node("failed", 0, true), node("low", -10), node("high", -2)]
  assert.deepEqual(
    [...nodes].sort(compareNodeFitness).map((n) => n.id),
    ["high", "low", "failed"],
  )
  assert.deepEqual(selectionScoreStats(nodes), { max: -2, min: -10, avg: -6 })
  assert.equal(selectionScoreStats([nodes[0]]), null)
  assert.equal(selectionScoreStats([node("invalid", Infinity)]), null)
  const classes = computeNodeClassifications({
    nodes: nodes.slice(1),
    unscoredNodes: [nodes[0]],
    maxGeneration: 2,
    islandCount: 1,
    islandIds: ["0"],
  })
  assert.equal(classes.has("failed"), false)
  assert.equal(classes.get("high")?.isGlobalBest, true)
})
