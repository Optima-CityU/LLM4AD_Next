function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

/** Preserve evaluator fields while supporting older planner-only logs. */
export function normalizeNodeEvaluation(
  data: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const planner = asRecord(data.evaluation)
  const full = asRecord(asRecord(data.custom_metadata)?.evaluation_result)
  const result = full ? { ...planner, ...full } : planner && { ...planner }
  const failure = data.evaluation_failure
  if (!result)
    return typeof failure === "string" ? { error_message: failure } : undefined
  if (result.error_message === undefined) {
    const error =
      !full && typeof planner?.error === "string" ? planner.error : failure
    if (typeof error === "string") result.error_message = error
  }
  return result
}

/** Return only a completed evaluation's finite, non-placeholder score. */
export function evaluationRecordScore(
  result: Record<string, unknown> | undefined,
  state?: string,
): number | undefined {
  if (state !== undefined && state !== "completed") return undefined
  return result?.success !== false &&
    typeof result?.score === "number" &&
    Number.isFinite(result.score)
    ? result.score
    : undefined
}

/** Keep unrendered fields under their original keys without flattening metadata. */
export function rawEvaluationFields(
  result: Record<string, unknown>,
  displayedScore?: number,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(result).filter(([name, value]) => {
      if (name === "score" && displayedScore !== undefined) return false
      if (["metrics", "monitor_metrics"].includes(name) && asRecord(value))
        return false
      if (
        ["error_message", "observation"].includes(name) &&
        typeof value === "string" &&
        value.length > 0
      )
        return false
      return true
    }),
  )
}
