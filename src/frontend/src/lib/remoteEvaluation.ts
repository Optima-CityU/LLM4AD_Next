import type { JobView } from "../client/types.gen.ts"

export type RemoteEvaluationJob = JobView

export function selectedGateway(value: Record<string, unknown>) {
  if (value.selection_mode !== undefined && value.selection_mode !== "selected")
    return undefined
  const previous = value.gateway_ids
  if (previous && (!Array.isArray(previous) || previous.length > 1))
    return undefined
  const legacy = Array.isArray(previous) ? previous[0] : undefined
  if (typeof value.gateway_id === "string" && value.gateway_id)
    return !legacy || value.gateway_id === legacy ? value.gateway_id : undefined
  return typeof legacy === "string" && legacy ? legacy : undefined
}

export function gatewaySelection(
  value: Record<string, unknown>,
  gatewayId?: string,
) {
  const next: Record<string, unknown> = {
    ...value,
    gateway_id: gatewayId ?? selectedGateway(value),
  }
  if (gatewayId !== undefined) delete next.contract
  for (const key of [
    "gateway_ids",
    "selection_mode",
    "max_attempts",
    "lease_timeout",
  ])
    delete next[key]
  return next
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
        state: ["completed", "failed", "cancelled", "reconnecting"].includes(
          item.state,
        )
          ? (item.state as string)
          : undefined,
        message: text(item.message) ?? text(item.summary),
        startedAt: timestamp(item.started_at) ?? timestamp(item.claimed_at),
        finishedAt: timestamp(item.finished_at) ?? timestamp(item.ended_at),
      },
    ]
  })
}
