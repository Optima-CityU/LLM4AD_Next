import { z } from "zod"

const executionStatus = z.enum([
  "running",
  "waiting",
  "completed",
  "failed",
  "interrupted",
])

export const researchWorkflowStepSchema = z.object({
  id: z.string().min(1),
  title: z.string().default(""),
  goal: z.string().default(""),
  approach: z.string().default(""),
  summary: z.string().default(""),
  stage: z.string().default(""),
  status: executionStatus,
  outputPaths: z.array(z.string()).default([]),
  runId: z.string().optional(),
  startedAt: z.string().optional(),
  updatedAt: z.string().optional(),
  revision: z.union([z.string(), z.number().int().nonnegative()]).optional(),
})

export type ResearchWorkflowStep = z.infer<typeof researchWorkflowStepSchema>

export const researchAgentProgressSchema = z.object({
  sessionId: z.string(),
  runId: z.string().optional(),
  sequence: z.number().int().nonnegative().optional(),
  status: z.enum([
    "ready",
    "running",
    "waiting",
    "idle",
    "interrupted",
    "failed",
    "completed",
  ]),
  currentTool: z.string().nullable(),
  workflowSequence: z.number().int().nonnegative().optional(),
  workflowSteps: z.array(researchWorkflowStepSchema).optional(),
  agents: z.array(
    z.object({
      id: z.string(),
      runId: z.string().optional(),
      parentId: z.string(),
      stage: z.string(),
      description: z.string(),
      status: executionStatus,
      startedAt: z.string().optional(),
      updatedAt: z.string().optional(),
      artifactRunId: z.string().optional(),
      toolCount: z.number().int().nonnegative(),
      currentTool: z.string().nullable(),
      tools: z.array(
        z.object({
          id: z.string().optional(),
          name: z.string(),
          done: z.boolean(),
          failed: z.boolean(),
        }),
      ),
    }),
  ),
})

export type ResearchAgentProgress = z.infer<typeof researchAgentProgressSchema>
export type ResearchProgressStep = ResearchAgentProgress["agents"][number]

export function researchProgressStepKey(step: ResearchProgressStep): string {
  return JSON.stringify([step.runId ?? "", step.id])
}

export function isResearchStepActive(
  step: Pick<ResearchProgressStep, "status">,
): boolean {
  return step.status === "running" || step.status === "waiting"
}

/** Merge replayed snapshots without losing attempts or reviving finished work. */
function mergeRawResearchProgress(
  previous: ResearchAgentProgress | null,
  incoming: ResearchAgentProgress,
): ResearchAgentProgress {
  const sameRun =
    previous &&
    (previous.runId ?? previous.sessionId) ===
      (incoming.runId ?? incoming.sessionId)
  if (
    sameRun &&
    previous.sequence !== undefined &&
    incoming.sequence !== undefined &&
    incoming.sequence <= previous.sequence
  )
    return previous

  const agents = new Map<string, ResearchProgressStep>()
  for (const snapshot of [previous, incoming]) {
    if (!snapshot) continue
    for (const raw of snapshot.agents) {
      const step = {
        ...raw,
        runId: raw.runId ?? snapshot.runId ?? snapshot.sessionId,
      }
      const key = researchProgressStepKey(step)
      const old = agents.get(key)
      if (old) {
        if (!isResearchStepActive(old) && isResearchStepActive(step)) continue
        const tools = new Map(
          old.tools.map((tool, index) => [tool.id ?? String(index), tool]),
        )
        step.tools.forEach((tool, index) => {
          const toolKey = tool.id ?? String(index)
          const prior = tools.get(toolKey)
          tools.set(
            toolKey,
            prior
              ? {
                  ...tool,
                  done: prior.done || tool.done,
                  failed: prior.failed || tool.failed,
                }
              : tool,
          )
        })
        step.tools = [...tools.values()]
        step.toolCount = Math.max(
          old.toolCount,
          step.toolCount,
          step.tools.length,
        )
      }
      if (!isResearchStepActive(step)) step.currentTool = null
      agents.set(key, step)
    }
  }
  const result = { ...incoming, agents: [...agents.values()] }
  if (sameRun && incoming.status === "ready" && previous.status !== "ready") {
    result.status = previous.status
    result.currentTool = previous.currentTool
  }
  return result
}

const knownResearchStages = new Set([
  "formatting",
  "literature",
  "rationale",
  "objectives",
  "methods",
  "innovation_plan",
  "foundation_feasibility",
  "final_review",
  "rebuttal_baseline",
  "autorebuttal",
  "ac_summary",
  "discovery_baseline",
  "discovery_candidates",
  "discovery_evaluation",
  "discovery",
  "autodesign",
  "reviews",
  "boundary",
  "targets",
  "branch",
])

function knownStage(stage: string): string | null {
  const normalized = stage.startsWith("proposal_")
    ? stage.slice("proposal_".length)
    : stage
  return knownResearchStages.has(normalized) ? normalized : null
}

/** Keep an explicitly reported logical ID stable across coordinator runs. */
export function researchWorkflowStepKey(
  step: Pick<ResearchWorkflowStep, "id" | "stage">,
): string {
  return `step:${step.id}`
}

/** Merge independently sequenced logical reports without changing raw event history. */
export function mergeResearchAgentProgress(
  previous: ResearchAgentProgress | null,
  incoming: ResearchAgentProgress,
): ResearchAgentProgress {
  const result = mergeRawResearchProgress(previous, incoming)
  const sameRun = Boolean(
    previous &&
      (previous.runId ?? previous.sessionId) ===
        (incoming.runId ?? incoming.sessionId),
  )
  const staleWorkflow =
    sameRun &&
    previous?.workflowSequence !== undefined &&
    (incoming.workflowSequence === undefined ||
      incoming.workflowSequence <= previous.workflowSequence)
  const acceptWorkflow = incoming.workflowSteps !== undefined && !staleWorkflow
  if (!acceptWorkflow) {
    if (!previous?.workflowSteps) return result
    if (result === previous) return previous
    return {
      ...result,
      workflowSteps: previous.workflowSteps,
      workflowSequence: sameRun
        ? previous.workflowSequence
        : incoming.workflowSequence,
    }
  }
  const steps = new Map<string, ResearchWorkflowStep>()
  for (const snapshot of [previous, incoming]) {
    if (!snapshot) continue
    for (const raw of snapshot.workflowSteps ?? []) {
      const step = {
        ...raw,
        runId: raw.runId ?? snapshot.runId ?? snapshot.sessionId,
      }
      const key = researchWorkflowStepKey(step)
      const old = steps.get(key)
      if (
        old?.runId === step.runId &&
        typeof old.revision === "number" &&
        typeof step.revision === "number" &&
        step.revision < old.revision
      )
        continue
      if (old?.updatedAt && step.updatedAt && step.updatedAt < old.updatedAt)
        continue
      steps.set(key, step)
    }
  }
  return {
    ...result,
    workflowSteps: [...steps.values()],
    workflowSequence: incoming.workflowSequence,
  }
}

type PublishedStage = {
  status: string
  run_id?: string
  updated_time?: string
  summary?: string | null
}

export function isPublishedResearchStage(
  stage: PublishedStage | undefined,
): boolean {
  return Boolean(
    stage?.run_id &&
      ["ready", "stale", "needs_revision"].includes(stage.status),
  )
}

/** Present explicit logical IDs and collapse only named legacy stage attempts. */
export function researchProgressSteps(
  progress: ResearchAgentProgress | null,
  stageStates: Record<string, PublishedStage>,
): ResearchWorkflowStep[] {
  const steps = new Map<string, ResearchWorkflowStep>()
  const explicit = (progress?.workflowSteps ?? []).filter(
    (step) => step.stage !== "coordinator",
  )
  const explicitStages = new Set(
    explicit.map((step) => knownStage(step.stage) ?? step.stage),
  )
  for (const [rawStage, state] of Object.entries(stageStates).sort(
    ([, a], [, b]) =>
      (a.updated_time ?? "").localeCompare(b.updated_time ?? ""),
  )) {
    const stage = knownStage(rawStage)
    if (!stage || explicitStages.has(stage) || !isPublishedResearchStage(state))
      continue
    const step: ResearchWorkflowStep = {
      id: `stage:${stage}`,
      stage,
      title: "",
      goal: "",
      approach: "",
      summary: state.summary ?? "",
      status: "completed",
      outputPaths: [],
      runId: state.run_id,
      updatedAt: state.updated_time,
    }
    steps.set(`stage:${stage}`, step)
  }
  for (const worker of progress?.agents ?? []) {
    const stage = knownStage(worker.stage)
    if (!stage || explicitStages.has(stage)) continue
    const key = `stage:${stage}`
    const previous = steps.get(key)
    steps.set(key, {
      id: key,
      stage,
      title: "",
      goal: "",
      approach: "",
      summary: worker.status === "completed" ? (previous?.summary ?? "") : "",
      status: worker.status,
      outputPaths: [],
      runId: worker.runId,
      startedAt: worker.startedAt,
      updatedAt: worker.updatedAt,
    })
  }
  for (const raw of explicit) {
    const step = { ...raw, stage: knownStage(raw.stage) ?? raw.stage }
    const key = researchWorkflowStepKey(step)
    steps.set(key, { ...step, id: key })
  }
  const result = [...steps.values()]
  if (progress?.status === "failed" || progress?.status === "interrupted") {
    const runId = progress.runId ?? progress.sessionId
    const current = result.reduce(
      (latest, step, index) =>
        isResearchStepActive(step) && (!step.runId || step.runId === runId)
          ? index
          : latest,
      -1,
    )
    if (current >= 0)
      result[current] = { ...result[current], status: progress.status }
  }
  return result
}
