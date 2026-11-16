import type { GatewayView, JobView } from "../client/types.gen"

export type PoolGateway = GatewayView
export type PoolJob = JobView

export type PoolSelectionMode = "auto" | "selected"

function gatewayIds(value: Record<string, unknown>): string[] {
  return Array.isArray(value.gateway_ids)
    ? [
        ...new Set(
          value.gateway_ids.filter(
            (id): id is string =>
              typeof id === "string" && id.trim().length > 0,
          ),
        ),
      ]
    : []
}

export function poolSelectionMode(
  value: Record<string, unknown>,
): PoolSelectionMode {
  if (value.selection_mode === "auto" || value.selection_mode === "selected")
    return value.selection_mode
  return gatewayIds(value).length > 0 ? "selected" : "auto"
}

export function normalizeGatewayPool(
  value: Record<string, unknown>,
): Record<string, unknown> {
  const selection_mode = poolSelectionMode(value)
  const ids = selection_mode === "selected" ? gatewayIds(value) : []
  const max_attempts =
    typeof value.max_attempts === "number" &&
    Number.isInteger(value.max_attempts) &&
    value.max_attempts > 0
      ? value.max_attempts
      : 3
  const lease_timeout =
    typeof value.lease_timeout === "number" &&
    Number.isFinite(value.lease_timeout) &&
    value.lease_timeout > 0
      ? value.lease_timeout
      : 60
  if (
    value.selection_mode === selection_mode &&
    value.max_attempts === max_attempts &&
    value.lease_timeout === lease_timeout &&
    Array.isArray(value.gateway_ids) &&
    value.gateway_ids.length === ids.length &&
    value.gateway_ids.every((id, i) => id === ids[i])
  )
    return value
  return {
    ...value,
    selection_mode,
    gateway_ids: ids,
    max_attempts,
    lease_timeout,
  }
}

export function evaluatorId(contract: unknown): string | undefined {
  if (!contract || typeof contract !== "object" || Array.isArray(contract))
    return undefined
  const id = (contract as Record<string, unknown>).evaluator_id
  return typeof id === "string" && id.trim() ? id : undefined
}

function canonicalValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalValue).join(",")}]`
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalValue(item)}`)
      .join(",")}}`
  return JSON.stringify(value) ?? "undefined"
}

function canonical(contract: Record<string, unknown>): string {
  if (!Array.isArray(contract.metrics)) return canonicalValue(contract)
  const metrics = contract.metrics.filter(
    (metric): metric is Record<string, unknown> & { name: string } =>
      metric !== null &&
      typeof metric === "object" &&
      !Array.isArray(metric) &&
      typeof metric.name === "string",
  )
  if (metrics.length !== contract.metrics.length)
    return canonicalValue(contract)
  // Match backend contract_key's stable name sort without changing any fields.
  metrics.sort((left, right) =>
    left.name < right.name ? -1 : left.name > right.name ? 1 : 0,
  )
  return canonicalValue({ ...contract, metrics })
}

export function gatewayPoolState(
  value: Record<string, unknown>,
  gateways: PoolGateway[],
  chosenGroup?: string,
) {
  const mode = poolSelectionMode(value)
  const ids = mode === "selected" ? gatewayIds(value) : []
  const selected = ids.flatMap(
    (id) => gateways.find((gateway) => gateway.id === id) ?? [],
  )
  const configured = value.contract
  const contract =
    configured &&
    typeof configured === "object" &&
    !Array.isArray(configured) &&
    Object.keys(configured).length > 0
      ? (configured as Record<string, unknown>)
      : selected[0]?.contract
  const group =
    evaluatorId(contract) ?? chosenGroup ?? evaluatorId(selected[0]?.contract)
  const groups = [
    ...new Set(
      gateways.flatMap((gateway) => evaluatorId(gateway.contract) ?? []),
    ),
  ]
  if (group && !groups.includes(group)) groups.push(group)
  groups.sort()
  const unavailable = ids.filter(
    (id) => !gateways.some((gateway) => gateway.id === id),
  )
  const outsideGroup = selected
    .filter((gateway) => evaluatorId(gateway.contract) !== group)
    .map((gateway) => gateway.id)
  const compatible = (gateway: PoolGateway) =>
    evaluatorId(gateway.contract) === group &&
    (contract
      ? canonical(contract) === canonical(gateway.contract) &&
        selected.every((item) => item.contract_key === gateway.contract_key)
      : ids.length === 0)
  const options = new Map<
    string,
    { key: string; contract: Record<string, unknown>; fingerprint?: string }
  >()
  for (const gateway of gateways) {
    if (evaluatorId(gateway.contract) !== group) continue
    const key = canonical(gateway.contract)
    if (!options.has(key))
      options.set(key, {
        key,
        contract: gateway.contract,
        fingerprint: gateway.contract_key,
      })
  }
  const contractKey = contract ? canonical(contract) : undefined
  if (contract && contractKey && !options.has(contractKey))
    options.set(contractKey, { key: contractKey, contract })
  const contracts = [...options.values()].sort((left, right) =>
    left.key.localeCompare(right.key),
  )
  const members = contract
    ? gateways.filter(
        (gateway) =>
          compatible(gateway) && (mode === "auto" || ids.includes(gateway.id)),
      )
    : []
  const eligible = members.filter(
    (gateway) =>
      gateway.online &&
      gateway.ready !== false &&
      (gateway.protocol_version ?? 1) >= 2,
  )
  const available = eligible.filter(
    (gateway) => gateway.running < gateway.capacity,
  )
  return {
    mode,
    ids,
    contract,
    contractKey,
    contracts,
    group,
    groups,
    unavailable,
    outsideGroup,
    compatible,
    members,
    eligible,
    available,
  }
}

export function selectPoolEvaluator(
  value: Record<string, unknown>,
  gateways: PoolGateway[],
  group: string,
): Record<string, unknown> {
  const cleared = {
    ...normalizeGatewayPool(value),
    gateway_ids: [],
    contract: {},
  }
  const pool = gatewayPoolState(cleared, gateways, group)
  return {
    ...cleared,
    contract: pool.contracts.length === 1 ? pool.contracts[0].contract : {},
  }
}

export function selectPoolContract(
  value: Record<string, unknown>,
  contract: Record<string, unknown>,
): Record<string, unknown> {
  return { ...normalizeGatewayPool(value), gateway_ids: [], contract }
}

export function setPoolSelectionMode(
  value: Record<string, unknown>,
  gateways: PoolGateway[],
  mode: PoolSelectionMode,
): Record<string, unknown> {
  const pool = gatewayPoolState(value, gateways)
  return normalizeGatewayPool({
    ...value,
    selection_mode: mode,
    gateway_ids: mode === "selected" ? pool.ids : [],
    contract: pool.contract ?? {},
  })
}

export function togglePoolGateway(
  value: Record<string, unknown>,
  gateways: PoolGateway[],
  group: string | undefined,
  gateway: PoolGateway,
  checked: boolean,
): Record<string, unknown> {
  const pool = gatewayPoolState(value, gateways, group)
  if (checked && !pool.compatible(gateway)) return value
  return {
    ...normalizeGatewayPool(value),
    selection_mode: "selected",
    gateway_ids: checked
      ? [...new Set([...pool.ids, gateway.id])]
      : pool.ids.filter((id) => id !== gateway.id),
    // Keep the pinned contract even when its last gateway disappears or is removed.
    contract: pool.contract ?? (checked ? gateway.contract : {}),
  }
}

export function remoteFailureCategory(value: unknown) {
  if (typeof value !== "string" || !value.trim()) return undefined
  return ["candidate", "infrastructure", "evaluator", "configuration"].includes(
    value,
  )
    ? value
    : "unknown"
}

export function remoteAttemptHistory(value: unknown) {
  if (!Array.isArray(value)) return []
  const text = (item: unknown, limit = 300) =>
    typeof item === "string" && item.trim()
      ? item.trim().slice(0, limit)
      : undefined
  const timestamp = (item: unknown) =>
    typeof item === "string" && Number.isFinite(Date.parse(item))
      ? item
      : undefined
  return value.flatMap((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return []
    const number = item.attempt ?? item.attempt_count ?? item.attempt_number
    return [
      {
        number:
          typeof number === "number" && Number.isInteger(number) && number > 0
            ? number
            : index + 1,
        gateway: text(item.gateway_name, 120) ?? text(item.gateway_id, 120),
        category: remoteFailureCategory(item.failure_category),
        state: ["completed", "failed", "cancelled"].includes(item.state)
          ? (item.state as string)
          : undefined,
        message: text(item.message) ?? text(item.summary),
        startedAt: timestamp(item.started_at) ?? timestamp(item.claimed_at),
        finishedAt: timestamp(item.finished_at) ?? timestamp(item.ended_at),
      },
    ]
  })
}
