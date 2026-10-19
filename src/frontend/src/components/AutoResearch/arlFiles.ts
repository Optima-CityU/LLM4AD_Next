/**
 * File explorer for the ARL right-hand panel.
 *
 * Each container's daemon serves its workspace through Paseo's
 * `file_explorer_request` / `fs.entry.*` RPCs; the backend relays them as
 * `fs_request` → `fs_result`, paired by `request_id`. This module holds the
 * pure pieces — normalizing listings, flattening the lazily-loaded tree,
 * path helpers and the request registry — so they can be tested without a
 * DOM. The tree logic mirrors Paseo's `file-explorer/tree.ts`.
 */

/** One entry of a directory listing. Paths are relative to the workspace. */
export interface ARLFsEntry {
  name: string
  path: string
  kind: "file" | "directory"
  size: number
  modifiedAt: string
}

/** A loaded directory; the root is keyed `"."`. */
export interface ARLFsDirectory {
  path: string
  entries: ARLFsEntry[]
}

/** A file read for preview. Images arrive base64, other binaries without content. */
export interface ARLFsFile {
  path: string
  kind: "text" | "image" | "binary"
  encoding: string
  content?: string
  mimeType?: string
  size: number
  modifiedAt: string
  /** Daemon's precise version stamp; preferred over mtime for save conflicts. */
  revision?: string
}

/**
 * Outcome of a `write`. A conflict is not an error: the file changed on disk
 * since it was opened, and the user decides whether to overwrite or reload.
 */
export type ARLFsWriteResult =
  | { status: "written"; modifiedAt: string; size: number; revision?: string }
  | {
      status: "conflict"
      version: { status: string; modifiedAt?: string; revision?: string }
    }

export type ARLFsSortOption = "name" | "modified" | "size"

export const ARL_FS_SORT_OPTIONS: ARLFsSortOption[] = [
  "name",
  "modified",
  "size",
]

/** Operations understood by the backend's `fs_request`. */
export type ARLFsOp =
  | "list"
  | "read"
  | "write"
  | "create"
  | "rename"
  | "duplicate"
  | "delete"

/** Row of the flattened tree, ready to render. */
export interface ARLFsTreeRow {
  entry: ARLFsEntry
  depth: number
}

/** Normalize a relative path: no leading `./` or `/`, no trailing `/`, root is `"."`. */
export function normalizeFsPath(path: string | undefined | null): string {
  const trimmed = (path ?? "")
    .replace(/\\/g, "/")
    .replace(/^(\.\/)+/, "")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "")
  return trimmed === "" || trimmed === "." ? "." : trimmed
}

/** Join a parent path and a child name. */
export function joinFsPath(parent: string, name: string): string {
  const base = normalizeFsPath(parent)
  return base === "." ? name : `${base}/${name}`
}

/** Parent directory of a path; top-level entries belong to `"."`. */
export function parentFsPath(path: string): string {
  const norm = normalizeFsPath(path)
  const index = norm.lastIndexOf("/")
  return index < 0 ? "." : norm.slice(0, index)
}

/** True when `path` is `ancestor` itself or lies beneath it. */
export function isWithinFsPath(path: string, ancestor: string): boolean {
  const a = normalizeFsPath(ancestor)
  if (a === ".") return true
  const p = normalizeFsPath(path)
  return p === a || p.startsWith(`${a}/`)
}

/**
 * Validate a name typed into the create / rename dialog.
 *
 * Only a single path segment is accepted: the daemon creates inside a given
 * parent and renames in place, so slashes would silently mean something else.
 *
 * @returns An i18n key suffix describing the problem, or null when valid.
 */
export function validateFsName(name: string): "empty" | "invalid" | null {
  const trimmed = name.trim()
  if (!trimmed) return "empty"
  if (trimmed === "." || trimmed === ".." || /[/\\]/.test(trimmed))
    return "invalid"
  return null
}

/**
 * Coerce a raw `directory` payload into a well-formed listing.
 *
 * Entries without a name are dropped; missing paths are rebuilt from the
 * directory path so every row has a stable key.
 */
export function normalizeDirectory(
  raw: unknown,
  fallbackPath = ".",
): ARLFsDirectory {
  const record = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >
  const path = normalizeFsPath(
    typeof record.path === "string" ? record.path : fallbackPath,
  )
  const entries: ARLFsEntry[] = []
  if (Array.isArray(record.entries)) {
    for (const item of record.entries) {
      if (!item || typeof item !== "object") continue
      const e = item as Record<string, unknown>
      const name = typeof e.name === "string" ? e.name : ""
      if (!name) continue
      entries.push({
        name,
        path: normalizeFsPath(
          typeof e.path === "string" ? e.path : joinFsPath(path, name),
        ),
        kind: e.kind === "directory" ? "directory" : "file",
        size: typeof e.size === "number" ? e.size : 0,
        modifiedAt: typeof e.modifiedAt === "string" ? e.modifiedAt : "",
      })
    }
  }
  return { path, entries }
}

/** Directories first, then by the chosen key (newest / largest first). */
export function sortFsEntries(
  entries: ARLFsEntry[],
  sort: ARLFsSortOption,
): ARLFsEntry[] {
  return [...entries].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1
    switch (sort) {
      case "modified":
        return (
          (Date.parse(b.modifiedAt) || 0) - (Date.parse(a.modifiedAt) || 0) ||
          a.name.localeCompare(b.name)
        )
      case "size":
        return b.size - a.size || a.name.localeCompare(b.name)
      default:
        return a.name.localeCompare(b.name)
    }
  })
}

/**
 * Flatten the loaded directories into rows in display order.
 *
 * Only directories that are both expanded and loaded contribute children, so
 * an expanded-but-loading folder simply shows no children yet.
 */
export function flattenFsTree({
  directories,
  expanded,
  sort,
  showHidden,
}: {
  directories: ReadonlyMap<string, ARLFsDirectory>
  expanded: ReadonlySet<string>
  sort: ARLFsSortOption
  showHidden: boolean
}): ARLFsTreeRow[] {
  const rows: ARLFsTreeRow[] = []
  const visit = (dirPath: string, depth: number) => {
    const dir = directories.get(dirPath)
    if (!dir) return
    const visible = showHidden
      ? dir.entries
      : dir.entries.filter((e) => !e.name.startsWith("."))
    for (const entry of sortFsEntries(visible, sort)) {
      rows.push({ entry, depth })
      if (entry.kind === "directory" && expanded.has(entry.path)) {
        visit(entry.path, depth + 1)
      }
    }
  }
  visit(".", 0)
  return rows
}

/**
 * Drop cached listings and expansion state under a path that was renamed or
 * deleted, so stale children cannot reappear under a reused name.
 */
export function pruneFsSubtree(
  directories: ReadonlyMap<string, ARLFsDirectory>,
  expanded: ReadonlySet<string>,
  path: string,
): { directories: Map<string, ARLFsDirectory>; expanded: Set<string> } {
  const target = normalizeFsPath(path)
  const nextDirs = new Map(directories)
  for (const key of directories.keys()) {
    if (key !== "." && isWithinFsPath(key, target)) nextDirs.delete(key)
  }
  const nextExpanded = new Set(
    [...expanded].filter((p) => !isWithinFsPath(p, target)),
  )
  return { directories: nextDirs, expanded: nextExpanded }
}

/** Successful `fs_result` data (minus the envelope fields). */
export type ARLFsResult = Record<string, unknown>

interface PendingRequest {
  resolve: (data: ARLFsResult) => void
  reject: (error: Error) => void
  timer: any
}

/**
 * Pairs `fs_request` frames with their `fs_result` by `request_id`.
 *
 * Every request settles exactly once: on its result frame, on timeout, or
 * when the socket drops (`failAll`) — a reply cannot arrive on a dead socket,
 * and leaving the promise hanging would keep its spinner up forever.
 */
export class ARLFsRequests {
  private pending = new Map<string, PendingRequest>()
  private counter = 0
  private timeoutMs: number
  private setTimer: (fn: () => void, ms: number) => any
  private clearTimer: (handle: any) => void

  constructor(opts?: {
    timeoutMs?: number
    setTimer?: (fn: () => void, ms: number) => any
    clearTimer?: (handle: any) => void
  }) {
    this.timeoutMs = opts?.timeoutMs ?? 30_000
    this.setTimer = opts?.setTimer ?? ((fn, ms) => setTimeout(fn, ms))
    this.clearTimer = opts?.clearTimer ?? ((h) => clearTimeout(h))
  }

  /** Number of requests still waiting for a reply. */
  get size(): number {
    return this.pending.size
  }

  /** Register a request; returns its id and a promise for the result. */
  begin(): { id: string; promise: Promise<ARLFsResult> } {
    this.counter += 1
    const id = `fs-${Date.now().toString(36)}-${this.counter}`
    const promise = new Promise<ARLFsResult>((resolve, reject) => {
      const timer = this.setTimer(() => {
        if (this.pending.delete(id)) reject(new Error("Request timed out"))
      }, this.timeoutMs)
      this.pending.set(id, { resolve, reject, timer })
    })
    return { id, promise }
  }

  /**
   * Settle the request a result frame belongs to.
   *
   * @returns False for unknown ids (late replies after a timeout or reconnect).
   */
  settle(data: unknown): boolean {
    const record = (data && typeof data === "object" ? data : {}) as Record<
      string,
      unknown
    >
    const id = typeof record.request_id === "string" ? record.request_id : ""
    const entry = this.pending.get(id)
    if (!entry) return false
    this.pending.delete(id)
    this.clearTimer(entry.timer)
    if (record.ok === true) entry.resolve(record)
    else
      entry.reject(
        new Error(
          typeof record.message === "string" && record.message
            ? record.message
            : "File operation failed",
        ),
      )
    return true
  }

  /** Reject one request, e.g. when its frame could not be sent. */
  fail(id: string, message: string): void {
    const entry = this.pending.get(id)
    if (!entry) return
    this.pending.delete(id)
    this.clearTimer(entry.timer)
    entry.reject(new Error(message))
  }

  /** Reject everything in flight (socket dropped or session changed). */
  failAll(message: string): void {
    for (const id of [...this.pending.keys()]) this.fail(id, message)
  }
}
