import type { TaskResponse } from "@/client"

type VersionTarget = Pick<
  TaskResponse,
  "id" | "parent_id" | "group_id" | "active_child_id"
>

type TaskListItem = Pick<
  TaskResponse,
  "id" | "name" | "status" | "active_child_id" | "active_status"
>

/** Pin explicit child links while allowing root links to follow the active version. */
export function linkedTaskVersionOverride(task: VersionTarget): string | null {
  return task.parent_id || (task.group_id && task.group_id !== task.id)
    ? task.id
    : null
}

/** Include version changes when refreshing the selected task from the list. */
export function taskListItemChanged(
  current: TaskListItem,
  updated: TaskListItem,
): boolean {
  return (
    current.id !== updated.id ||
    current.name !== updated.name ||
    current.status !== updated.status ||
    (current.active_child_id ?? null) !== (updated.active_child_id ?? null) ||
    (current.active_status ?? current.status) !==
      (updated.active_status ?? updated.status)
  )
}
