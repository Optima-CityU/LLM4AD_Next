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

export function visibleEvaluationFiles(
  files: EvaluationFile[],
): EvaluationFile[] {
  const unique = [...new Map(files.map((file) => [file.id, file])).values()]
  // Behavior images can alias named attachments. Keep the named image in the
  // gallery without removing either immutable attachment from the record.
  const namedImages = new Set(
    unique
      .filter(
        (file) =>
          !file.name.startsWith("behavior/") &&
          evaluationPreviewKind(file) === "image" &&
          /^[a-f0-9]{64}$/i.test(file.sha256),
      )
      .map((file) => file.sha256.toLowerCase()),
  )
  return unique.filter(
    (file) =>
      !(
        file.name.startsWith("behavior/") &&
        evaluationPreviewKind(file) === "image" &&
        namedImages.has(file.sha256?.toLowerCase())
      ),
  )
}

const IMAGE_DOWNLOAD_EXTENSIONS: Record<string, string> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/webp": ".webp",
  "image/gif": ".gif",
}

/** Return a basename, adding a known image extension only when none exists. */
export function evaluationDownloadName(
  file: Pick<EvaluationFile, "name" | "media_type">,
): string {
  const name = file.name.replace(/\\/g, "/").split("/").pop() ?? file.name
  if (/\.[^.]+$/.test(name)) return name
  const mediaType = (file.media_type ?? "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase()
  return name + (IMAGE_DOWNLOAD_EXTENSIONS[mediaType] ?? "")
}

export function formatEvaluationBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}
