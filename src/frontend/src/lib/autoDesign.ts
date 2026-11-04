import type {
  AutoDesignFile,
  AutoDesignFileList,
  FileTreeNode,
  TaskResponse,
  TaskTreeResponse,
} from "@/client"

export type TaskWorkbenchView = "workbench" | "agent"

export function taskViewPanelId(taskId: string, view: TaskWorkbenchView) {
  return `task-view-${taskId}-${view}`
}

export type AutoDesignFileArea = AutoDesignFileList["area"]

export const autoDesignKeys = {
  all: ["autodesign"] as const,
  sessions: ["autodesign", "sessions"] as const,
  taskSession: (taskId: string) =>
    ["autodesign", "task-session", taskId] as const,
  session: (sessionId: string) => ["autodesign", "session", sessionId] as const,
  context: (sessionId: string, taskId?: string) =>
    ["autodesign", "context", sessionId, taskId ?? null] as const,
  files: (sessionId: string, area: AutoDesignFileArea, taskId?: string) =>
    ["autodesign", "files", sessionId, area, taskId ?? null] as const,
  file: (
    sessionId: string,
    area: AutoDesignFileArea,
    path: string,
    taskId?: string,
  ) => ["autodesign", "file", sessionId, area, taskId ?? null, path] as const,
}

export function autoDesignFileTree(files: AutoDesignFile[]): FileTreeNode[] {
  const tree: FileTreeNode[] = []
  const directories = new Map<string, FileTreeNode>()
  for (const file of files) {
    const parts = file.path.split("/")
    let children = tree
    for (let index = 0; index < parts.length; index++) {
      const path = parts.slice(0, index + 1).join("/")
      if (index === parts.length - 1) {
        children.push({ name: parts[index], path, type: "file" })
      } else {
        let directory = directories.get(path)
        if (!directory) {
          directory = {
            name: parts[index],
            path,
            type: "directory",
            children: [],
          }
          directories.set(path, directory)
          children.push(directory)
        }
        children = directory.children!
      }
    }
  }
  return tree
}

export function rememberAgentVersion(
  opened: string[],
  taskId: string,
): string[] {
  return opened.includes(taskId) ? opened : [...opened, taskId]
}

export function autoDesignTaskSearch(task: { id: string; project_id: string }) {
  return { projectId: task.project_id, taskId: task.id }
}

export function autoDesignModelProfile(session: {
  analysis_provider_id: string | null
  analysis_model_name: string | null
  analysis_context_window_tokens: number
  analysis_max_output_tokens: number
  conversation_preferences?: {
    reply_language?: string
    additional_guidance?: string
  }
}) {
  return [
    session.analysis_provider_id,
    session.analysis_model_name,
    session.analysis_context_window_tokens,
    session.analysis_max_output_tokens,
    JSON.stringify(session.conversation_preferences ?? {}),
  ].join(":")
}

export const AUTO_DESIGN_REFERENCE_ACCEPT =
  ".txt,.md,.markdown,.rst,.tex,.typ,.bib,.csv,.tsv,.json,.yaml,.yml,.toml"

export function isAutoDesignReferenceName(name: string) {
  return AUTO_DESIGN_REFERENCE_ACCEPT.split(",").some((extension) =>
    name.toLowerCase().endsWith(extension),
  )
}

export function isAutoDesignExecutionLocked(status: string | null | undefined) {
  return status === "pending" || status === "running"
}

export function canUseTaskAgent(
  taskId: string | null,
  task: Pick<TaskResponse, "id" | "status"> | undefined,
  family:
    | {
        root: Pick<TaskTreeResponse["root"], "id" | "status">
        children: Pick<TaskTreeResponse["root"], "id" | "status">[]
      }
    | undefined,
) {
  if (!taskId || task?.id !== taskId || !family) return false
  const versions = [family.root, ...family.children]
  return (
    versions.some((version) => version.id === taskId) &&
    !isAutoDesignExecutionLocked(task.status) &&
    !versions.some((version) => isAutoDesignExecutionLocked(version.status))
  )
}
