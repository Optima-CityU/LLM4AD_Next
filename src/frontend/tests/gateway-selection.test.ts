import assert from "node:assert/strict"
import { test } from "node:test"
import { evaluatorConfigSchema } from "../src/components/Evolution/TaskDetail/config-form/appConfigSchema.ts"
import {
  gatewaySelection,
  selectedGateway,
} from "../src/lib/remoteEvaluation.ts"

const first = "881af4bf-3e66-4e9a-b452-55b9498b29e9"
const second = "3ccdfe33-a764-4456-9369-6108cee41b0f"
const contract = { evaluator_id: "static", version: "1" }

test("a new remote draft has no gateway before explicit selection", () => {
  const draft = {
    type: "remote",
    timeout: 240.5,
    candidate_paths: ["shader.comp"],
  }
  assert.equal(selectedGateway(draft), undefined)
  assert.equal(evaluatorConfigSchema.safeParse(draft).success, false)
  const parsed = evaluatorConfigSchema.parse(gatewaySelection(draft, first))
  assert.equal(parsed.gateway_id, first)
  assert.equal(parsed.timeout, 240.5)
  assert.equal(parsed.contract, undefined)
})

test("legacy explicit single targets remain valid, ambiguous targets require a selection", () => {
  const evaluator = { type: "remote", gateway_ids: [first], contract }
  assert.equal(evaluatorConfigSchema.parse(evaluator).gateway_id, first)
  assert.deepEqual(evaluatorConfigSchema.parse(evaluator).contract, contract)
  assert.equal(selectedGateway({ ...evaluator, gateway_id: second }), undefined)
  for (const version of [
    { ...evaluator, selection_mode: "auto" },
    { ...evaluator, gateway_ids: [first, second] },
  ]) {
    assert.equal(evaluatorConfigSchema.safeParse(version).success, false)
  }
})

test("selecting a gateway clears previous registration metadata and retains candidate inputs", () => {
  const evaluator = gatewaySelection(
    {
      type: "remote",
      gateway_ids: [first],
      selection_mode: "auto",
      max_attempts: 3,
      lease_timeout: 60,
      contract,
      parameters: { detail: true },
      candidate_paths: ["shader.comp"],
    },
    second,
  )
  assert.deepEqual(evaluator, {
    type: "remote",
    gateway_id: second,
    parameters: { detail: true },
    candidate_paths: ["shader.comp"],
  })
})
