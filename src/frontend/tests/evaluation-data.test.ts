import assert from "node:assert/strict"
import { test } from "node:test"
import {
  evaluationRecordScore,
  normalizeNodeEvaluation,
  rawEvaluationFields,
} from "../src/lib/evaluationData.ts"
import { groupEvaluationMetrics } from "../src/lib/evaluationMetrics.ts"

test("full evaluator payload overrides planner fields and retains arbitrary details", () => {
  const full = {
    score: -1.23456789,
    metrics: { latency_ms: 1, psnr_db: 40 },
    monitor_metrics: { psnr_db: 40 },
    metadata: { device: "phone", score: "metadata score" },
    evolution_feedback: { candidate_update: { patch: "retained patch" } },
    custom_field: ["anything", null, { nested: true }],
  }
  const data = {
    evaluation: { score: -2, hardware_info: { cpu: "test" }, metrics: {} },
    custom_metadata: { evaluation_result: full },
  }
  const normalized = normalizeNodeEvaluation(data)!
  assert.deepEqual(normalized, { ...data.evaluation, ...full })
  assert.deepEqual(groupEvaluationMetrics(normalized), {
    primary: [["latency_ms", 1]],
    monitoring: [["psnr_db", 40]],
  })
  assert.equal(data.evaluation.score, -2)
  assert.deepEqual(
    normalizeNodeEvaluation({
      custom_metadata: { evaluation_result: { custom: "no required fields" } },
    }),
    { custom: "no required fields" },
  )
})

test("planner-only logs and failed nodes retain their diagnostic fallback", () => {
  assert.deepEqual(
    normalizeNodeEvaluation({
      evaluation: { score: 1, metrics: {}, error: "legacy error" },
      custom_metadata: { evaluation_result: [] },
    }),
    {
      score: 1,
      metrics: {},
      error: "legacy error",
      error_message: "legacy error",
    },
  )
  assert.deepEqual(
    normalizeNodeEvaluation({
      evaluation: null,
      evaluation_failure: "compile failed",
    }),
    { error_message: "compile failed" },
  )
  assert.equal(normalizeNodeEvaluation({ evaluation: null }), undefined)
  assert.deepEqual(
    normalizeNodeEvaluation({
      evaluation: { error: "old error" },
      custom_metadata: {
        evaluation_result: { error_message: null, success: true },
      },
    }),
    { error: "old error", error_message: null, success: true },
  )
})

test("raw details retain feedback and colliding metadata keys without flattening", () => {
  const result = {
    score: 12.3456789,
    metrics: { latency_ms: 1 },
    monitor_metrics: { fps: 60 },
    error_message: "visible error",
    observation: "visible observation",
    evolution_feedback: { hint: "root feedback" },
    metadata: { evolution_feedback: { hint: "nested feedback" }, score: 999 },
    duration_ms: 42,
    custom: [1, 2],
  }
  assert.deepEqual(rawEvaluationFields(result, result.score), {
    evolution_feedback: result.evolution_feedback,
    metadata: result.metadata,
    duration_ms: 42,
    custom: [1, 2],
  })
  assert.deepEqual(
    rawEvaluationFields({
      metrics: ["unrendered"],
      observation: null,
      score: 0,
      success: false,
    }),
    {
      metrics: ["unrendered"],
      observation: null,
      score: 0,
      success: false,
    },
  )
})

test("record scores accept exact finite values and reject failed or unfinished placeholders", () => {
  for (const score of [0, -1.2345678912345, 1e-12]) {
    assert.equal(
      evaluationRecordScore({ score, success: true }, "completed"),
      score,
    )
  }
  for (const score of [null, undefined, "1", NaN, Infinity, -Infinity]) {
    assert.equal(evaluationRecordScore({ score }, "completed"), undefined)
  }
  for (const state of ["failed", "cancelled", "running", "pending", "queued"]) {
    assert.equal(evaluationRecordScore({ score: 0 }, state), undefined)
  }
  assert.equal(
    evaluationRecordScore({ score: 0, success: false }, "completed"),
    undefined,
  )
  assert.equal(evaluationRecordScore({ metrics: {} }, "completed"), undefined)
})
