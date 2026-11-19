import type {
  FileTreeNode,
  PaperHistoryComparison,
  PaperHistoryResponse,
} from "@/client"

export type CandidateFileDiff = {
  path: string
  original: string | null
  replacement: string | null
  binary: boolean
  originalBytes?: number
  replacementBytes?: number
}
export type CandidateDiff = {
  files: CandidateFileDiff[]
  sourceVersionId?: string
  contentHash?: string
}

function readPatchSide(
  file: Record<string, unknown>,
  field: "original" | "replacement",
) {
  const text = file[`${field}_text`]
  if (typeof text === "string") return text
  if (file[field] === null) return null
  if (typeof file[field] !== "string") throw new Error("Invalid file diff")
  const bytes = Uint8Array.from(atob(file[field] as string), (character) =>
    character.charCodeAt(0),
  )
  const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes)
  if (decoded.includes("\0")) throw new Error("Binary file")
  return decoded
}

/** Decode the two owned patch formats while keeping binary changes explicit. */
export function parseRevisionDiff(content: string): CandidateDiff {
  const patch: unknown = JSON.parse(content)
  if (!patch || typeof patch !== "object") throw new Error("Invalid patch")
  const value = patch as Record<string, unknown>
  if (value.schema_version === "paper-source-diff-v1") {
    if (!Array.isArray(value.files) || !value.files.length)
      throw new Error("Empty patch")
    const seen = new Set<string>()
    const files = value.files.map((raw: unknown): CandidateFileDiff => {
      if (!raw || typeof raw !== "object") throw new Error("Invalid file")
      const file = raw as Record<string, unknown>
      if (typeof file.path !== "string" || !file.path || seen.has(file.path))
        throw new Error("Invalid file path")
      seen.add(file.path)
      try {
        return {
          path: file.path,
          original: readPatchSide(file, "original"),
          replacement: readPatchSide(file, "replacement"),
          binary: false,
        }
      } catch {
        if (
          !["original", "replacement"].every(
            (key) => file[key] === null || typeof file[key] === "string",
          )
        )
          throw new Error("Invalid binary diff")
        return {
          path: file.path,
          original: null,
          replacement: null,
          binary: true,
          originalBytes:
            typeof file.original === "string" ? atob(file.original).length : 0,
          replacementBytes:
            typeof file.replacement === "string"
              ? atob(file.replacement).length
              : 0,
        }
      }
    })
    return {
      files,
      sourceVersionId:
        typeof value.source_version_id === "string"
          ? value.source_version_id
          : undefined,
      contentHash:
        typeof value.content_hash === "string" ? value.content_hash : undefined,
    }
  }
  if (
    typeof value.source_path !== "string" ||
    typeof value.original_text !== "string" ||
    typeof value.replacement_text !== "string"
  )
    throw new Error("Unsupported patch")
  return {
    files: [
      {
        path: value.source_path,
        original: value.original_text,
        replacement: value.replacement_text,
        binary: false,
      },
    ],
  }
}

/** Turn arbitrary workspace output paths into the shared file tree contract. */
export function buildHistoryTree(
  files: Pick<PaperHistoryComparison["files"][number], "path">[],
): FileTreeNode[] {
  const roots: FileTreeNode[] = []
  const directories = new Map<string, FileTreeNode>()
  for (const file of files) {
    const parts = file.path.split("/")
    let children = roots
    for (let index = 0; index < parts.length; index++) {
      const path = parts.slice(0, index + 1).join("/")
      if (index === parts.length - 1) {
        children.push({ name: parts[index], path: file.path, type: "file" })
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
  return roots
}

/** Prefer the immediately preceding revision as baseline and the live revision as target. */
export function initialHistoryPair(history: PaperHistoryResponse) {
  const revisions = [...history.revisions].sort((a, b) =>
    b.created_time.localeCompare(a.created_time),
  )
  const target =
    revisions.find((item) => item.id === history.current_revision_id) ??
    revisions.find((item) => item.is_current) ??
    revisions[0]
  const base =
    revisions[revisions.findIndex((item) => item.id === target?.id) + 1] ??
    revisions.find((item) => item.id !== target?.id) ??
    target
  return { baseId: base?.id ?? "", targetId: target?.id ?? "" }
}

/** Resolve a Markdown image within its revision's project root without accepting traversal. */
export function resolveHistoryImagePath(
  selectedFile: string,
  source: string,
): string | null {
  if (!source || /^[a-z][a-z\d+.-]*:/i.test(source) || source.startsWith("/"))
    return null
  let relative: string
  try {
    relative = decodeURIComponent(source.split(/[?#]/, 1)[0])
  } catch {
    return null
  }
  if (
    !relative ||
    /^[a-z][a-z\d+.-]*:/i.test(relative) ||
    relative.startsWith("/") ||
    /[\\\x00-\x1f\x7f]/.test(relative) ||
    /%(?:2e|2f|5c|00)/i.test(relative)
  )
    return null
  const selected = selectedFile.split("/")
  if (
    selected.some(
      (part) => !part || part === "." || part === ".." || part.includes("\\"),
    )
  )
    return null
  const stack = selected.slice(0, -1)
  const floor = [
    "source",
    "outputs",
    "artifacts",
    "evidence",
    "reviews",
  ].includes(selected[0])
    ? 1
    : 0
  for (const part of relative.split("/")) {
    if (!part || part === ".") continue
    if (part === "..") {
      if (stack.length <= floor) return null
      stack.pop()
    } else stack.push(part)
  }
  return stack.length > floor ? stack.join("/") : null
}
