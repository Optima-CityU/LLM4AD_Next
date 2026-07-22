import type { TaskResponse } from "@/client"

/**
 * Resolve the task whose configuration the right panel should display.
 *
 * A selected root task can point to an active child version. In that case the
 * detail view and right panel must use the same effective task configuration.
 */
export function resolvePanelTask(
  selectedTask: TaskResponse | null,
  effectiveTask: TaskResponse | undefined,
): TaskResponse | null {
  return effectiveTask ?? selectedTask
}
