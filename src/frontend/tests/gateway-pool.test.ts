import assert from "node:assert/strict"
import { test } from "node:test"
import type { GatewayView } from "../src/client/types.gen.ts"
import {
  appConfigSchema,
  evaluatorConfigSchema,
} from "../src/components/Evolution/TaskDetail/config-form/appConfigSchema.ts"
import {
  type JsonSchema,
  mergeSchemaDefaults,
} from "../src/components/Evolution/TaskDetail/schema-form/resolveSchema.ts"
import {
  gatewayPoolState,
  normalizeGatewayPool,
  type PoolGateway,
  type PoolJob,
  poolSelectionMode,
  remoteAttemptHistory,
  remoteFailureCategory,
  selectPoolContract,
  selectPoolEvaluator,
  setPoolSelectionMode,
  togglePoolGateway,
} from "../src/lib/gatewayPool.ts"

const contract = {
  evaluator_id: "hgamey-genshin-sr-static",
  version: "1",
  data_version: "images-1",
  metrics: { psnr: { direction: "maximize", unit: "dB" } },
}
const gateway = (
  id: string,
  overrides: Partial<GatewayView> = {},
): GatewayView => ({
  id,
  name: id,
  contract,
  contract_key: "static-v1",
  capacity: 1,
  running: 0,
  queued: 0,
  online: true,
  last_seen: "2026-11-13",
  ...overrides,
})
const first = gateway("static-a")
const second = gateway("static-b")
const game = gateway("game", {
  contract: { ...contract, evaluator_id: "hgamey-genshin-sr-game" },
  contract_key: "game-v1",
})

test("evaluator groups include independent static, game and arbitrary custom evaluators without auto-selection", () => {
  const custom = gateway("custom", {
    contract: { ...contract, evaluator_id: "research/custom.v2" },
  })
  const pool = gatewayPoolState({}, [first, game, custom])
  assert.equal(pool.group, undefined)
  assert.deepEqual(pool.ids, [])
  assert.deepEqual(pool.groups, [
    "hgamey-genshin-sr-game",
    "hgamey-genshin-sr-static",
    "research/custom.v2",
  ])
  assert.equal(pool.compatible(first), false)
  assert.equal(
    gatewayPoolState({}, [first], contract.evaluator_id).compatible(first),
    true,
  )
})

test("configured contracts remain pinned when the original gateway disappears or every gateway is removed", () => {
  const value = { gateway_ids: [first.id], contract, timeout: 10 }
  const changed = gateway("changed", {
    contract: { ...contract, data_version: "images-2" },
    contract_key: "static-v2",
  })
  const pool = gatewayPoolState(value, [second, changed])
  assert.equal(pool.compatible(second), true)
  assert.equal(pool.compatible(changed), false)
  assert.deepEqual(pool.unavailable, [first.id])
  assert.equal(pool.group, contract.evaluator_id)
  const removed = togglePoolGateway(value, [first], pool.group, first, false)
  assert.deepEqual(removed, {
    gateway_ids: [],
    contract,
    timeout: 10,
    selection_mode: "selected",
    max_attempts: 3,
    lease_timeout: 60,
  })
  assert.equal(gatewayPoolState(removed, [changed]).compatible(changed), false)
  assert.deepEqual(gatewayPoolState(value, []).contract, contract)
})

test("all contract fields and the selected pool key must match, independent of object key order", () => {
  const value = { gateway_ids: [first.id], contract }
  for (const change of [
    { evaluator_id: "other" },
    { version: "2" },
    { data_version: "images-2" },
    { metrics: { psnr: { direction: "minimize", unit: "dB" } } },
  ]) {
    const other = gateway("other", { contract: { ...contract, ...change } })
    assert.equal(
      gatewayPoolState(value, [first, other]).compatible(other),
      false,
    )
    assert.equal(
      togglePoolGateway(
        value,
        [first, other],
        contract.evaluator_id,
        other,
        true,
      ),
      value,
    )
  }
  const reordered = gateway("reordered", {
    contract: {
      metrics: { psnr: { unit: "dB", direction: "maximize" } },
      data_version: "images-1",
      version: "1",
      evaluator_id: contract.evaluator_id,
    },
  })
  assert.equal(
    gatewayPoolState(value, [first, reordered]).compatible(reordered),
    true,
  )
  const conflictingKey = gateway("conflict", { contract_key: "different-key" })
  assert.equal(
    gatewayPoolState(value, [first, conflictingKey]).compatible(conflictingKey),
    false,
  )
})

test("legacy selections keep their contract and unknown stale IDs cannot acquire a new contract", () => {
  const legacy = { gateway_ids: [first.id] }
  assert.deepEqual(
    togglePoolGateway(legacy, [first], contract.evaluator_id, first, false),
    {
      gateway_ids: [],
      contract,
      selection_mode: "selected",
      max_attempts: 3,
      lease_timeout: 60,
    },
  )
  const unknown = { gateway_ids: ["unavailable"] }
  assert.equal(
    gatewayPoolState(unknown, [first], contract.evaluator_id).compatible(first),
    false,
  )
  assert.equal(
    togglePoolGateway(unknown, [first], contract.evaluator_id, first, true),
    unknown,
  )
})

test("selection tolerates malformed IDs, preserves offline gateways, and never duplicates a selection", () => {
  const offline = gateway("offline", { online: false })
  const value = { gateway_ids: [offline.id, offline.id, 42], contract }
  const pool = gatewayPoolState(value, [offline])
  assert.deepEqual(pool.ids, [offline.id])
  assert.deepEqual(pool.unavailable, [])
  assert.equal(pool.compatible(offline), true)
  assert.deepEqual(
    togglePoolGateway(value, [offline], pool.group, offline, true).gateway_ids,
    [offline.id],
  )
  assert.deepEqual(gatewayPoolState({ gateway_ids: "bad" }, []).ids, [])
})

const metricContract = {
  ...contract,
  metrics: [
    { name: "psnr", type: "maximize", weight: 1, description: "Image quality" },
    {
      name: "latency",
      type: "minimize",
      weight: 2,
      description: "GPU latency",
    },
  ],
}

test("metric array permutations match backend contract keys without changing the pinned contract or metric order", () => {
  const original = gateway("original", { contract: metricContract })
  const reversedMetrics = [...metricContract.metrics].reverse()
  const reversed = gateway("reversed", {
    contract: { ...metricContract, metrics: reversedMetrics },
  })
  const value = { gateway_ids: [original.id], contract: metricContract }
  const before = JSON.stringify(value)
  for (const ids of [[original.id], []]) {
    const pool = gatewayPoolState({ ...value, gateway_ids: ids }, [
      original,
      reversed,
    ])
    assert.equal(pool.compatible(reversed), true)
    assert.equal(pool.compatible(original), true)
  }
  const next = togglePoolGateway(
    value,
    [original, reversed],
    contract.evaluator_id,
    reversed,
    true,
  )
  assert.deepEqual(next.gateway_ids, [original.id, reversed.id])
  assert.equal(next.contract, metricContract)
  assert.equal(JSON.stringify(value), before)
  assert.deepEqual(
    reversedMetrics.map((metric) => metric.name),
    ["latency", "psnr"],
  )
  assert.deepEqual(
    metricContract.metrics.map((metric) => metric.name),
    ["psnr", "latency"],
  )
})

test("metric ordering never hides changes to any contract or metric field", () => {
  const value = { gateway_ids: [], contract: metricContract }
  const changes = [
    { evaluator_id: "other" },
    { version: "2" },
    { data_version: "other-data" },
    { extra_contract_field: true },
    ...[
      { name: "different" },
      { type: "minimize" },
      { weight: 0.5 },
      { description: "Different meaning" },
      { extra_metric_field: true },
    ].map((change) => ({
      metrics: [
        metricContract.metrics[1],
        { ...metricContract.metrics[0], ...change },
      ],
    })),
    { metrics: [metricContract.metrics[1]] },
    { metrics: [...metricContract.metrics, metricContract.metrics[0]] },
    {
      metrics: [
        metricContract.metrics[1],
        { name: "psnr", type: "maximize", weight: 1 },
      ],
    },
  ]
  for (const change of changes) {
    const changed = gateway("changed", {
      contract: { ...metricContract, ...change },
    })
    assert.equal(
      gatewayPoolState(value, [changed]).compatible(changed),
      false,
      JSON.stringify(change),
    )
  }
})

test("only the top-level metrics array is order-insensitive and duplicate names retain stable order", () => {
  const nestedContract = {
    ...metricContract,
    parameters: { metrics: [{ name: "b" }, { name: "a" }], order: [1, 2] },
    metrics: metricContract.metrics.map((metric) => ({
      ...metric,
      thresholds: [1, 2],
    })),
  }
  const value = { gateway_ids: [], contract: nestedContract }
  const changed = [
    {
      ...nestedContract,
      parameters: { ...nestedContract.parameters, order: [2, 1] },
    },
    {
      ...nestedContract,
      parameters: {
        ...nestedContract.parameters,
        metrics: [...nestedContract.parameters.metrics].reverse(),
      },
    },
    {
      ...nestedContract,
      metrics: nestedContract.metrics.map((metric) => ({
        ...metric,
        thresholds: [2, 1],
      })),
    },
  ]
  for (const candidate of changed) {
    const other = gateway("other", { contract: candidate })
    assert.equal(gatewayPoolState(value, [other]).compatible(other), false)
  }
  const duplicates = {
    ...metricContract,
    metrics: [
      { name: "score", weight: 1 },
      { name: "score", weight: 2 },
    ],
  }
  const reversed = gateway("reversed", {
    contract: { ...duplicates, metrics: [...duplicates.metrics].reverse() },
  })
  assert.equal(
    gatewayPoolState({ contract: duplicates }, [reversed]).compatible(reversed),
    false,
  )
})

const v2 = (id: string, overrides: Partial<PoolGateway> = {}): PoolGateway => ({
  ...gateway(id),
  protocol_version: 2,
  ready: true,
  ...overrides,
})

test("new pools default to automatic selection with one shared attempt budget and lease", () => {
  const value = { type: "remote", timeout: 120, parameters: { keep: true } }
  const normalized = normalizeGatewayPool(value)
  assert.deepEqual(normalized, {
    ...value,
    selection_mode: "auto",
    gateway_ids: [],
    max_attempts: 3,
    lease_timeout: 60,
  })
  assert.equal(poolSelectionMode({ gateway_ids: [] }), "auto")
  assert.equal(poolSelectionMode({ gateway_ids: [null, 42, ""] }), "auto")
  assert.equal(normalizeGatewayPool(normalized), normalized)
  assert.deepEqual(value, {
    type: "remote",
    timeout: 120,
    parameters: { keep: true },
  })
})

test("legacy IDs infer manual mode while explicit automatic mode discards stale IDs", () => {
  const legacy = { gateway_ids: [first.id, "missing"], contract }
  assert.equal(poolSelectionMode(legacy), "selected")
  assert.equal(normalizeGatewayPool(legacy).selection_mode, "selected")
  assert.deepEqual(
    gatewayPoolState(legacy, [first, second]).members.map((item) => item.id),
    [first.id],
  )
  assert.equal(
    poolSelectionMode({ selection_mode: "selected", gateway_ids: [] }),
    "selected",
  )
  const automatic = { ...legacy, selection_mode: "auto" }
  assert.deepEqual(normalizeGatewayPool(automatic).gateway_ids, [])
  const pool = gatewayPoolState(automatic, [v2(second.id)])
  assert.deepEqual(pool.ids, [])
  assert.deepEqual(pool.unavailable, [])
  assert.deepEqual(
    pool.eligible.map((item) => item.id),
    [second.id],
  )
})

test("choosing an evaluator with a single comparable contract freezes it without pinning device IDs", () => {
  const one = v2("one")
  const two = v2("two")
  const configured = selectPoolEvaluator(
    {},
    [one, two, game],
    contract.evaluator_id,
  )
  assert.equal(configured.selection_mode, "auto")
  assert.equal(configured.contract, contract)
  assert.deepEqual(configured.gateway_ids, [])
  assert.equal(gatewayPoolState(configured, [one, two]).contracts.length, 1)
  assert.deepEqual(
    gatewayPoolState(configured, [one, two]).eligible.map((item) => item.id),
    [one.id, two.id],
  )
})

test("multiple contracts for the same evaluator require an explicit benchmark choice regardless of response order", () => {
  const variants = [
    v2("original"),
    v2("new-version", { contract: { ...contract, version: "2" } }),
    v2("other-benchmark", {
      contract: { ...contract, data_version: "images-2" },
    }),
    v2("other-scoring", {
      contract: {
        ...contract,
        metrics: { psnr: { direction: "minimize", unit: "dB" } },
      },
    }),
  ]
  for (const gateways of [variants, [...variants].reverse()]) {
    const configured = selectPoolEvaluator({}, gateways, contract.evaluator_id)
    assert.deepEqual(configured.contract, {})
    const pool = gatewayPoolState(configured, gateways, contract.evaluator_id)
    assert.equal(pool.contracts.length, 4)
    assert.deepEqual(pool.members, [])
    assert.deepEqual(pool.eligible, [])
    const chosen = selectPoolContract(configured, variants[2].contract)
    assert.deepEqual(
      gatewayPoolState(chosen, gateways).members.map((item) => item.id),
      ["other-benchmark"],
    )
    assert.equal(chosen.contract, variants[2].contract)
    assert.deepEqual(chosen.gateway_ids, [])
  }
})

test("automatic pools admit late compatible gateways and remove revoked members without rewriting their configuration", () => {
  const original = v2("original")
  const late = v2("late")
  const incompatible = v2("different-benchmark", {
    contract: { ...contract, data_version: "images-2" },
  })
  const configured = selectPoolEvaluator({}, [original], contract.evaluator_id)
  const before = JSON.stringify(configured)
  assert.deepEqual(
    gatewayPoolState(configured, [original]).eligible.map((item) => item.id),
    ["original"],
  )
  assert.deepEqual(
    gatewayPoolState(configured, [
      original,
      late,
      incompatible,
      game,
    ]).eligible.map((item) => item.id),
    ["original", "late"],
  )
  assert.deepEqual(
    gatewayPoolState(configured, [late]).eligible.map((item) => item.id),
    ["late"],
  )
  const empty = gatewayPoolState(configured, [incompatible])
  assert.deepEqual(empty.members, [])
  assert.equal(empty.contract, contract)
  assert.equal(
    empty.contracts.some((option) => option.contract === contract),
    true,
  )
  assert.equal(JSON.stringify(configured), before)
  assert.deepEqual(configured.gateway_ids, [])
})

test("automatic member status includes offline, unready and legacy gateways but only ready online protocol-v2 devices are eligible", () => {
  const gateways = [
    v2("ready"),
    v2("offline", { online: false }),
    v2("unready", { ready: false }),
    v2("legacy-ready-default", { ready: undefined }),
    v2("legacy-protocol", { protocol_version: 1 }),
    first,
  ]
  const pool = gatewayPoolState({ selection_mode: "auto", contract }, gateways)
  assert.equal(pool.members.length, 6)
  assert.deepEqual(
    pool.eligible.map((item) => item.id),
    ["ready", "legacy-ready-default"],
  )
  assert.equal(
    pool.members.find((item) => item.id === first.id)?.ready ?? true,
    true,
  )
})

test("automatic mode retains metric-order compatibility without mixing changed scoring semantics", () => {
  const original = v2("original", { contract: metricContract })
  const late = v2("late", {
    contract: {
      ...metricContract,
      metrics: [...metricContract.metrics].reverse(),
    },
  })
  const changed = v2("changed", {
    contract: {
      ...metricContract,
      metrics: metricContract.metrics.map((metric) => ({
        ...metric,
        weight: 100,
      })),
    },
  })
  const configured = selectPoolEvaluator({}, [original], contract.evaluator_id)
  const pool = gatewayPoolState(configured, [original, late, changed])
  assert.deepEqual(
    pool.eligible.map((item) => item.id),
    ["original", "late"],
  )
  assert.equal(pool.contracts.length, 2)
  assert.equal(configured.contract, metricContract)
})

test("frozen automatic contracts remain configured while busy, offline or absent devices wait without consuming attempts", () => {
  const configured = selectPoolEvaluator(
    {},
    [v2("first")],
    contract.evaluator_id,
  )
  const before = JSON.stringify(configured)
  const busy = v2("busy", { capacity: 2, running: 2 })
  const offline = v2("offline", { online: false })
  for (const gateways of [[], [busy], [offline], [busy, offline]]) {
    const pool = gatewayPoolState(configured, gateways)
    assert.equal(pool.contract, contract)
    assert.equal(pool.contracts.length, 1)
    assert.deepEqual(pool.available, [])
    assert.equal(configured.max_attempts, 3)
    assert.deepEqual(configured.gateway_ids, [])
    assert.equal(JSON.stringify(configured), before)
  }
  const released = { ...busy, running: 1 }
  assert.deepEqual(
    gatewayPoolState(configured, [released]).available.map((item) => item.id),
    ["busy"],
  )
  assert.deepEqual(
    gatewayPoolState(configured, [v2("late")]).available.map((item) => item.id),
    ["late"],
  )
})

test("switching to manual never selects the whole automatic pool and checked IDs limit membership", () => {
  const gateways = [v2(first.id), v2(second.id)]
  const automatic = selectPoolEvaluator({}, gateways, contract.evaluator_id)
  const manual = setPoolSelectionMode(automatic, gateways, "selected")
  assert.deepEqual(manual.gateway_ids, [])
  assert.equal(manual.contract, contract)
  const selected = togglePoolGateway(
    manual,
    gateways,
    contract.evaluator_id,
    gateways[0],
    true,
  )
  assert.deepEqual(selected.gateway_ids, [first.id])
  assert.deepEqual(
    gatewayPoolState(selected, gateways).members.map((item) => item.id),
    [first.id],
  )
  const back = setPoolSelectionMode(selected, gateways, "auto")
  assert.deepEqual(back.gateway_ids, [])
  assert.equal(back.contract, contract)
  assert.equal(gatewayPoolState(back, gateways).members.length, 2)
  assert.deepEqual(selected.gateway_ids, [first.id])
})

test("switching legacy manual selections to automatic preserves the inferred contract, including through outages", () => {
  const legacy = {
    gateway_ids: [first.id],
    timeout: 42,
    max_attempts: 7,
    lease_timeout: 90,
  }
  const automatic = setPoolSelectionMode(legacy, [first], "auto")
  assert.equal(automatic.contract, contract)
  assert.deepEqual(automatic.gateway_ids, [])
  assert.equal(automatic.timeout, 42)
  assert.equal(automatic.max_attempts, 7)
  assert.equal(automatic.lease_timeout, 90)
  const manual = setPoolSelectionMode(automatic, [], "selected")
  assert.equal(manual.contract, contract)
  assert.deepEqual(manual.gateway_ids, [])
  assert.equal(gatewayPoolState(manual, []).group, contract.evaluator_id)
})

test("changing evaluator clears manual IDs and explicitly chooses only a unique new contract", () => {
  const legacy = { gateway_ids: [first.id], contract, timeout: 90 }
  const next = selectPoolEvaluator(
    legacy,
    [first, game],
    String(game.contract.evaluator_id),
  )
  assert.equal(next.selection_mode, "selected")
  assert.equal(next.contract, game.contract)
  assert.deepEqual(next.gateway_ids, [])
  assert.equal(next.timeout, 90)
  const selected = togglePoolGateway(
    next,
    [first, game],
    String(game.contract.evaluator_id),
    game,
    true,
  )
  assert.deepEqual(selected.gateway_ids, [game.id])
})

test("attempt budgets and leases retain valid custom settings and reject malformed defaults", () => {
  assert.equal(normalizeGatewayPool({ max_attempts: 5 }).max_attempts, 5)
  assert.equal(normalizeGatewayPool({ lease_timeout: 0.5 }).lease_timeout, 0.5)
  for (const max_attempts of [0, -1, 1.5, NaN, Infinity, "4", null])
    assert.equal(normalizeGatewayPool({ max_attempts }).max_attempts, 3)
  for (const lease_timeout of [0, -1, NaN, Infinity, "30", null])
    assert.equal(normalizeGatewayPool({ lease_timeout }).lease_timeout, 60)
})

const manualGatewayId = "11111111-1111-4111-8111-111111111111"

test("legacy form submissions accept automatic empty-ID pools and preserve policy fields and frozen contracts", () => {
  const configured = {
    type: "remote",
    selection_mode: "auto",
    gateway_ids: [],
    contract: metricContract,
    max_attempts: 7,
    lease_timeout: 90,
  }
  const result = appConfigSchema.parse({ evaluator: configured })
  assert.equal(result.evaluator.selection_mode, "auto")
  assert.deepEqual(result.evaluator.gateway_ids, [])
  assert.equal(result.evaluator.max_attempts, 7)
  assert.equal(result.evaluator.lease_timeout, 90)
  assert.deepEqual(result.evaluator.contract, metricContract)
  assert.deepEqual(configured.gateway_ids, [])
})

test("legacy form parsing infers manual IDs before supplying automatic defaults", () => {
  const legacy = evaluatorConfigSchema.parse({
    type: "remote",
    gateway_ids: [manualGatewayId],
  })
  assert.equal(legacy.selection_mode, "selected")
  assert.deepEqual(legacy.gateway_ids, [manualGatewayId])
  const automatic = evaluatorConfigSchema.parse({
    type: "remote",
    contract: metricContract,
  })
  assert.equal(automatic.selection_mode, "auto")
  assert.deepEqual(automatic.gateway_ids, [])
  assert.equal(automatic.max_attempts, 3)
  assert.equal(automatic.lease_timeout, 60)
  const explicit = evaluatorConfigSchema.parse({
    type: "remote",
    selection_mode: "auto",
    gateway_ids: [manualGatewayId],
    contract: metricContract,
  })
  assert.deepEqual(explicit.gateway_ids, [])
})

test("form validation distinguishes an unchosen benchmark from an empty manual selection", () => {
  const automatic = evaluatorConfigSchema.safeParse({
    type: "remote",
    selection_mode: "auto",
  })
  assert.equal(automatic.success, false)
  if (!automatic.success)
    assert.deepEqual(
      automatic.error.issues.map((issue) => issue.path),
      [["contract"]],
    )
  const manual = evaluatorConfigSchema.safeParse({
    type: "remote",
    selection_mode: "selected",
    contract: metricContract,
  })
  assert.equal(manual.success, false)
  if (!manual.success)
    assert.deepEqual(
      manual.error.issues.map((issue) => issue.path),
      [["gateway_ids"]],
    )
  for (const fields of [
    { max_attempts: 0 },
    { max_attempts: 1.5 },
    { lease_timeout: 0 },
    { lease_timeout: Infinity },
    { selection_mode: "invalid" },
  ])
    assert.equal(
      evaluatorConfigSchema.safeParse({
        type: "remote",
        contract: metricContract,
        ...fields,
      }).success,
      false,
    )
})

const remoteFormSchema: JsonSchema = {
  type: "object",
  properties: {
    evaluator: {
      oneOf: [{ $ref: "#/$defs/RemoteEvaluator" }],
      discriminator: { propertyName: "type" },
    },
  },
  $defs: {
    RemoteEvaluator: {
      type: "object",
      properties: {
        type: { const: "remote", default: "remote" },
        selection_mode: {
          type: "string",
          enum: ["auto", "selected"],
          default: "auto",
        },
        gateway_ids: { type: "array", items: { type: "string" } },
        contract: { type: "object" },
        max_attempts: { type: "integer", default: 3 },
        lease_timeout: { type: "number", default: 60 },
      },
    },
  },
}

test("schema-form default merging preserves legacy manual membership through nested discriminated schemas", () => {
  const saved = {
    evaluator: {
      type: "remote",
      gateway_ids: [manualGatewayId],
      contract: metricContract,
      max_attempts: 5,
      lease_timeout: 120,
    },
  }
  const before = JSON.stringify(saved)
  const merged = mergeSchemaDefaults(
    remoteFormSchema,
    remoteFormSchema,
    saved,
  ) as typeof saved & { evaluator: { selection_mode: string } }
  assert.equal(merged.evaluator.selection_mode, "selected")
  assert.deepEqual(merged.evaluator.gateway_ids, [manualGatewayId])
  assert.deepEqual(merged.evaluator.contract, metricContract)
  assert.equal(merged.evaluator.max_attempts, 5)
  assert.equal(merged.evaluator.lease_timeout, 120)
  assert.equal(
    evaluatorConfigSchema.parse(merged.evaluator).selection_mode,
    "selected",
  )
  assert.equal(JSON.stringify(saved), before)
})

test("both form entrypoints agree on new automatic defaults and preserve explicit modes", () => {
  for (const evaluator of [
    { type: "remote", contract: metricContract },
    {
      type: "remote",
      selection_mode: "auto",
      gateway_ids: [manualGatewayId],
      contract: metricContract,
    },
    {
      type: "remote",
      selection_mode: "selected",
      gateway_ids: [manualGatewayId],
      contract: metricContract,
    },
  ]) {
    const merged = mergeSchemaDefaults(remoteFormSchema, remoteFormSchema, {
      evaluator,
    }) as { evaluator: Record<string, unknown> }
    const schemaEntry = normalizeGatewayPool(merged.evaluator)
    const legacyEntry = evaluatorConfigSchema.parse(evaluator)
    assert.equal(schemaEntry.selection_mode, legacyEntry.selection_mode)
    assert.deepEqual(schemaEntry.gateway_ids, legacyEntry.gateway_ids)
    assert.equal(schemaEntry.max_attempts, legacyEntry.max_attempts)
    assert.equal(schemaEntry.lease_timeout, legacyEntry.lease_timeout)
  }
})

test("remote default migration leaves local evaluators and unrelated schemas unchanged", () => {
  const local = evaluatorConfigSchema.parse({
    type: "custom",
    module: "my.evaluator:Evaluator",
  })
  assert.equal(local.selection_mode, undefined)
  assert.equal(local.max_attempts, undefined)
  assert.equal(local.lease_timeout, undefined)
  const schema: JsonSchema = {
    type: "object",
    properties: {
      type: { type: "string" },
      selection_mode: { type: "string", default: "auto" },
    },
  }
  assert.deepEqual(
    mergeSchemaDefaults(schema, schema, {
      type: "remote",
      gateway_ids: [manualGatewayId],
    }),
    { type: "remote", gateway_ids: [manualGatewayId], selection_mode: "auto" },
  )
})

test("attempt history extracts bounded readable fields and ignores nested payloads and invalid records", () => {
  const started = "2026-11-16T10:00:00Z"
  const finished = "2026-11-16T10:01:00Z"
  const history = remoteAttemptHistory([
    {
      attempt: 1,
      gateway_name: "Phone A",
      failure_category: "infrastructure",
      message: "Device lease expired",
      started_at: started,
      finished_at: finished,
      token: "never-render-this",
      raw: { secret: "hidden" },
    },
    {
      attempt: 2,
      gateway_id: "phone-b",
      failure_category: "candidate",
      message: "Compilation failed",
    },
    null,
    [],
    7,
    {
      attempt: -1,
      gateway_name: { raw: "wrong type" },
      failure_category: "new-category",
      message: "x".repeat(500),
      started_at: "invalid",
    },
  ])
  assert.equal(history.length, 3)
  assert.deepEqual(history[0], {
    number: 1,
    gateway: "Phone A",
    category: "infrastructure",
    state: undefined,
    message: "Device lease expired",
    startedAt: started,
    finishedAt: finished,
  })
  assert.equal(history[1].gateway, "phone-b")
  assert.equal(history[1].category, "candidate")
  assert.equal(history[2].gateway, undefined)
  assert.equal(history[2].category, "unknown")
  assert.equal(history[2].message?.length, 300)
  assert.equal(history[2].startedAt, undefined)
  assert.equal(JSON.stringify(history).includes("never-render-this"), false)
  for (const value of [undefined, null, {}, "raw JSON"])
    assert.deepEqual(remoteAttemptHistory(value), [])
})

test("failure categories display a known category or a readable fallback without exposing raw objects", () => {
  for (const value of [
    "candidate",
    "infrastructure",
    "evaluator",
    "configuration",
  ])
    assert.equal(remoteFailureCategory(value), value)
  assert.equal(remoteFailureCategory("new-server-category"), "unknown")
  for (const value of [undefined, null, "", {}, []])
    assert.equal(remoteFailureCategory(value), undefined)
})

async function renderProgress(jobs: PoolJob[], language: "en" | "zh") {
  const [
    query,
    translation,
    react,
    server,
    reactTranslation,
    component,
    en,
    zh,
  ] = await Promise.all([
    import("@tanstack/react-query"),
    import("i18next"),
    import("react"),
    import("react-dom/server"),
    import("react-i18next"),
    import("../src/components/EvaluationGateway/RemoteEvaluationProgress.tsx"),
    import("../src/i18n/locales/en.json"),
    import("../src/i18n/locales/zh.json"),
  ])
  const { QueryClient, QueryClientProvider } = query
  const { createInstance } = translation
  const { createElement } = react
  const { renderToStaticMarkup } = server
  const { I18nextProvider, initReactI18next } = reactTranslation
  const { default: RemoteEvaluationProgress } = component
  const i18n = createInstance()
  await i18n.use(initReactI18next).init({
    resources: {
      en: { translation: en.default },
      zh: { translation: zh.default },
    },
    lng: language,
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  client.setQueryData(["remoteEvaluationJobs", "task", false], jobs)
  try {
    return renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          I18nextProvider,
          { i18n },
          createElement(RemoteEvaluationProgress, {
            taskId: "task",
            active: false,
          }),
        ),
      ),
    )
  } finally {
    client.clear()
  }
}

const failedJob: PoolJob = {
  id: "failed-job",
  state: "failed",
  gateway_id: "phone-b",
  gateway_name: "Phone B",
  gateway_online: true,
  report: { message: "Attempts exhausted", score: 0 },
  updated_at: "2026-11-16T10:00:00Z",
  attempt_count: 3,
  max_attempts: 3,
  failure_category: "infrastructure",
  attempt_history: [
    {
      attempt: 1,
      gateway_name: "Phone A",
      state: "failed",
      failure_category: "infrastructure",
      message: "Lease expired <script>bad()</script>",
      started_at: "2026-11-16T10:00:00Z",
      finished_at: "2026-11-16T10:01:00Z",
      raw: { credential: "never-render-this" },
    },
  ],
}

test("progress renders compact attempt badges and collapsed readable history in English and Chinese without fabricated fitness", {
  skip: !process.versions.bun,
}, async () => {
  for (const language of ["en", "zh"] as const) {
    const markup = await renderProgress([failedJob], language)
    assert.match(markup, /Phone A/)
    assert.match(markup, /Lease expired &lt;script&gt;/)
    assert.match(markup, /<details[^>]*>/)
    assert.doesNotMatch(markup, /<details[^>]*\bopen\b/)
    assert.doesNotMatch(
      markup,
      /never-render-this|credential|<script>|Score 0|得分 0/,
    )
    assert.match(
      markup,
      language === "en" ? /Attempts 3 \/ 3/ : /已尝试 3 \/ 3 次/,
    )
    assert.match(
      markup,
      language === "en" ? /Infrastructure failure/ : /基础设施故障/,
    )
    assert.match(
      markup,
      language === "en" ? /Attempt history \(1\)/ : /尝试记录（1）/,
    )
  }
})

test("progress remains compatible with legacy job fixtures missing attempt fields", {
  skip: !process.versions.bun,
}, async () => {
  const legacy: PoolJob = {
    id: "legacy-job",
    state: "completed",
    gateway_id: null,
    gateway_name: null,
    gateway_online: false,
    report: { score: 12 },
    updated_at: "2026-11-16T10:00:00Z",
  }
  const markup = await renderProgress([legacy], "en")
  assert.match(markup, /Score 12/)
  assert.doesNotMatch(markup, /Attempt history|Attempts \d|<details/)
})

test("waiting gateway progress preserves the server's zero attempts and total budget", {
  skip: !process.versions.bun,
}, async () => {
  const markup = await renderProgress(
    [
      {
        ...failedJob,
        state: "waiting_gateway",
        gateway_id: null,
        gateway_name: null,
        gateway_online: false,
        attempt_count: 0,
        max_attempts: 3,
        failure_category: null,
        attempt_history: [],
        report: { message: "Waiting for an available device" },
      },
    ],
    "en",
  )
  assert.match(markup, /Waiting for an available device/)
  assert.match(markup, /Attempts 0 \/ 3/)
  assert.doesNotMatch(markup, /Attempt history|Infrastructure failure|Score/)
})
