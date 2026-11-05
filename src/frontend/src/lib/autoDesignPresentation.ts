import type { AutoDesignFile, FileTreeNode } from "@/client"
import { autoDesignFileTree } from "@/lib/autoDesign"

export function autoDesignProjectStatus({
  loading,
  unavailable,
  unsaved,
  hasFiles,
  validationCurrent,
  report,
}: {
  loading: boolean
  unavailable: boolean
  unsaved: boolean
  hasFiles: boolean
  validationCurrent: boolean
  report: Record<string, unknown>
}) {
  if (unavailable) return "unavailable"
  if (loading) return "loading"
  if (unsaved) return "unsaved"
  if (report.status === "running") return "running"
  if (report.status === "failed") return "failed"
  if (report.status === "passed") return validationCurrent ? "ready" : "changed"
  return hasFiles ? "drafting" : "waiting"
}

export function autoDesignProjectRoot(projectPath: string | null | undefined) {
  return projectPath
    ?.replace(/^\/workspace\/source\/draft\/?/, "")
    .replace(/\/$/, "")
}

export function autoDesignProjectTree(
  files: AutoDesignFile[],
  projectPath: string | null | undefined,
): FileTreeNode[] {
  const root = autoDesignProjectRoot(projectPath)
  const prefix = root ? `${root}/` : ""
  const tree = autoDesignFileTree(
    autoDesignProjectFiles(files, projectPath).map((file) => ({
      ...file,
      path: prefix ? file.path.slice(prefix.length) : file.path,
    })),
  )
  const restorePaths = (nodes: FileTreeNode[]) => {
    for (const node of nodes) {
      node.path = prefix + node.path
      if (node.children) restorePaths(node.children)
    }
  }
  if (prefix) restorePaths(tree)
  return tree
}

export function autoDesignFileRevision(files: AutoDesignFile[]) {
  return files
    .map((file) => `${file.path}:${file.size}:${file.modified_time ?? ""}`)
    .sort()
    .join("\n")
}

export function autoDesignProjectFiles(
  files: AutoDesignFile[],
  projectPath: string | null | undefined,
): AutoDesignFile[] {
  if (!projectPath) return files
  const root = projectPath
    .replace(/^\/workspace\/source\/draft\/?/, "")
    .replace(/\/$/, "")
  return root ? files.filter((file) => file.path.startsWith(`${root}/`)) : files
}
