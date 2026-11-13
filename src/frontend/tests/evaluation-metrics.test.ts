import assert from "node:assert/strict"
import { test } from "node:test"
import {
  describeEvaluationMetric,
  groupEvaluationMetrics,
} from "../src/lib/evaluationMetrics.ts"

test("monitoring values appear once without losing distinct values or all-shared objectives", () => {
  assert.deepEqual(
    groupEvaluationMetrics({
      metrics: { score: 3, latency_ms: 12 },
      monitor_metrics: { latency_ms: 12, fps: 60 },
    }),
    {
      primary: [["score", 3]],
      monitoring: [
        ["latency_ms", 12],
        ["fps", 60],
      ],
    },
  )
  assert.deepEqual(
    groupEvaluationMetrics({
      metrics: { latency_ms: 12 },
      monitor_metrics: { latency_ms: 12 },
    }),
    { primary: [["latency_ms", 12]], monitoring: [] },
  )
  assert.deepEqual(
    groupEvaluationMetrics({
      metrics: { latency_ms: 12 },
      monitor_metrics: { latency_ms: 14 },
    }),
    { primary: [["latency_ms", 12]], monitoring: [["latency_ms", 14]] },
  )
})

test("compact precision retains tiny values, negative improvements and the exact original", () => {
  const tiny = describeEvaluationMetric("error", 0.000000123456789, "en")
  assert.equal(tiny.display, "1.235E-7")
  assert.equal(tiny.exact, "1.23456789e-7")
  const regression = describeEvaluationMetric(
    "operator_improvement_pct",
    -12.3456789,
    "en",
  )
  assert.equal(regression.display, "-12.35")
  assert.equal(regression.exact, "-12.3456789")
  assert.equal(regression.unit, "%")
  assert.equal(describeEvaluationMetric("error", null, "en").display, "—")
  assert.equal(describeEvaluationMetric("error", Infinity, "en").display, "—")
  assert.equal(describeEvaluationMetric("error", -0, "en").display, "0")
})

test("units and labels preserve ratios and custom metric meaning", () => {
  assert.equal(
    describeEvaluationMetric("operator_latency_ms", 1.25, "en").unit,
    "ms",
  )
  assert.equal(describeEvaluationMetric("psnr_db", 35.5, "en").label, "PSNR")
  assert.equal(
    describeEvaluationMetric("gpu_time_us", 1.25, "en").label,
    "GPU time",
  )
  assert.equal(describeEvaluationMetric("accuracy", 0.95, "en").display, "0.95")
  assert.equal(describeEvaluationMetric("custom_ratio", 0.5, "en").unit, "")
  assert.equal(
    describeEvaluationMetric("latency_ms", "unavailable", "en").unit,
    "",
  )
})
