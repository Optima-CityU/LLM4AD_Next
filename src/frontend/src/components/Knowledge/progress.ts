export type ParseStepKind = "tool" | "model" | "retry" | "context"
export type ParseStepStatus = "running" | "success" | "failed" | "retrying"

export type ParseActivity = {
  type: string
  progress: number
  stage: string
  message: string
  step_id?: string
  step_kind?: ParseStepKind
  step_status?: ParseStepStatus
  tool_name?: "Read" | "Glob" | "Grep" | "Write" | "Edit"
  elapsed_seconds?: number
  attempt?: number
  max_retries?: number
  retry_delay_ms?: number
}

export type ParseActivityGroup = {
  stage: string
  progress: number
  type: string
  summary?: ParseActivity
  steps: ParseActivity[]
}

const stepKinds = new Set<ParseStepKind>(["tool", "model", "retry", "context"])
const stepStatuses = new Set<ParseStepStatus>([
  "running",
  "success",
  "failed",
  "retrying",
])
const toolNames = new Set<NonNullable<ParseActivity["tool_name"]>>([
  "Read",
  "Glob",
  "Grep",
  "Write",
  "Edit",
])

function boundedNumber(value: unknown, maximum: number) {
  const number = Number(value)
  return Number.isFinite(number)
    ? Math.max(0, Math.min(maximum, Math.round(number)))
    : undefined
}

export function parseActivityFromEvent(
  event: Record<string, unknown>,
): ParseActivity | null {
  const type = String(event.type || "progress")
  const message = String(event.message || "").trim()
  if (type === "output" || !message) return null
  const activity: ParseActivity = {
    type,
    progress: boundedNumber(event.progress, 100) ?? 0,
    stage: String(event.stage || "analyzing").slice(0, 64),
    message: message.slice(0, 500),
  }
  if (type !== "step") return activity

  const stepKind = String(event.step_kind || "") as ParseStepKind
  const stepStatus = String(event.step_status || "") as ParseStepStatus
  if (!stepKinds.has(stepKind) || !stepStatuses.has(stepStatus)) return null
  activity.step_id = String(event.step_id || "step").slice(0, 128)
  activity.step_kind = stepKind
  activity.step_status = stepStatus

  const toolName = String(event.tool_name || "") as NonNullable<
    ParseActivity["tool_name"]
  >
  if (stepKind === "tool" && toolNames.has(toolName))
    activity.tool_name = toolName
  for (const [field, maximum] of [
    ["elapsed_seconds", 86400],
    ["attempt", 100],
    ["max_retries", 100],
    ["retry_delay_ms", 3600000],
  ] as const) {
    const number = boundedNumber(event[field], maximum)
    if (number !== undefined) activity[field] = number
  }
  return activity
}

export function appendParseActivity(
  current: ParseActivity[],
  next: ParseActivity,
): ParseActivity[] {
  if (next.type === "output" || !next.message.trim()) return current
  const key = next.type === "step" ? next.step_id : next.stage
  const existingIndex = current.findIndex((event) =>
    next.type === "step"
      ? event.type === "step" && event.step_id === key
      : event.type !== "step" && event.stage === key,
  )
  if (existingIndex < 0) return [...current, next].slice(-80)
  const existing = current[existingIndex]
  if (JSON.stringify(existing) === JSON.stringify(next)) return current
  const updated = [...current]
  updated[existingIndex] = next
  return updated
}

export function groupParseActivities(
  activities: ParseActivity[],
): ParseActivityGroup[] {
  const groups: ParseActivityGroup[] = []
  const byStage = new Map<string, ParseActivityGroup>()
  for (const activity of activities) {
    let group = byStage.get(activity.stage)
    if (!group) {
      group = {
        stage: activity.stage,
        progress: activity.progress,
        type: activity.type,
        steps: [],
      }
      byStage.set(activity.stage, group)
      groups.push(group)
    }
    group.progress = Math.max(group.progress, activity.progress)
    if (activity.type === "step") group.steps.push(activity)
    else {
      group.summary = activity
      group.type = activity.type
    }
  }
  return groups
}
