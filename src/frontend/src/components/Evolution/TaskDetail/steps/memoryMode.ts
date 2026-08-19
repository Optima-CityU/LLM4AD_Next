export function downgradeUnavailableLongTermMemory(
  memory: Record<string, unknown>,
): Record<string, unknown> {
  if (memory.type === "mindmemos_cloud" && memory.enabled !== false) {
    return { ...memory, enabled: true, type: "local_yaml" }
  }

  return memory
}

export type TaskMemoryOnboardingPhase = "auto" | "manual" | "injection" | "advanced"

/**
 * Builds the value rendered during the task-memory walkthrough.  It is a
 * derived display value only: callers must never persist it to the task.
 */
export function createTaskMemoryOnboardingPresentation(
  memory: Record<string, unknown>,
  phase: TaskMemoryOnboardingPhase,
): Record<string, unknown> {
  return {
    ...memory,
    enabled: true,
    type: "mindmemos_cloud",
    retrieval_mode: phase === "manual" ? "manual" : "auto",
    include_user_memory: true,
    include_project_memory: true,
    include_task_memory: true,
    user_memory_limit: 3,
    project_memory_limit: 3,
    task_memory_limit: 5,
    task_injection_mode: "weight",
    task_injection_lambda: 0.5,
    mindmemos_search_strategy: phase === "advanced" ? "agentic" : "fast",
    mindmemos_rerank: true,
    mindmemos_score_threshold: 0.65,
    mindmemos_fail_open: true,
    mindmemos_request_timeout: 300,
    mindmemos_add_timeout: 300,
    mindmemos_extraction_prompt_language: "auto",
  }
}
