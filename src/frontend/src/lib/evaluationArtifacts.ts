import type { EvaluationFile, EvaluationSummary, FileTreeNode } from "@/client"

export function evaluationNeedsPolling(
  summary?: Pick<EvaluationSummary, "state" | "transfer_pending">,
): boolean {
  return (
    summary?.transfer_pending === true ||
    [
      "queued",
      "accepted",
      "pending",
      "running",
      "cancel_requested",
      "recovery_required",
      "reconnecting",
    ].includes(summary?.state ?? "")
  )
}

export function evaluationFileTree(files: EvaluationFile[]): FileTreeNode[] {
  const roots: FileTreeNode[] = []
  for (const file of files) {
    const parts = file.name.split("/")
    let nodes = roots
    for (let index = 0; index < parts.length; index++) {
      const logicalPath = parts.slice(0, index + 1).join("/")
      const type = index === parts.length - 1 ? "file" : "directory"
      const path = type === "directory" ? `${logicalPath}/` : logicalPath
      let node = nodes.find((item) => item.path === path)
      if (!node) {
        node = {
          name: parts[index],
          path,
          type,
          children: [],
        }
        nodes.push(node)
      }
      nodes = node.children ?? []
    }
  }
  return roots
}

export function evaluationPreviewKind(
  file: EvaluationFile,
): "image" | "text" | null {
  if (
    ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(
      file.media_type ?? "",
    )
  )
    return "image"
  if (
    file.media_type?.startsWith("text/") ||
    file.media_type === "application/json" ||
    /\.(json|txt|md|markdown|csv|log|ya?ml|xml)$/i.test(file.name)
  )
    return "text"
  return null
}

export function formatEvaluationBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}
