import {
  AlertTriangle,
  ArrowDownUp,
  ChevronDown,
  ChevronRight,
  Copy,
  CopyPlus,
  Download,
  Eye,
  EyeOff,
  FilePlus,
  Folder,
  FolderOpen,
  FolderPlus,
  Loader2,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react"
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { ARL_ROLES, type ARLRole } from "@/hooks/useARLWebSocket"
import { downloadARLWorkspaceArchive } from "@/hooks/useAutoResearch"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import { usePersistentState } from "@/hooks/usePersistentState"
import { cn } from "@/lib/utils"
import ARLFilePreviewDialog, {
  type ARLPreviewTarget,
} from "./ARLFilePreviewDialog"
import { useARLFiles } from "./ARLSessionContext"
import { FileKindIcon, formatSize } from "./ArtifactPreviewDialog"
import { ArtifactIconButton, middleEllipsis } from "./ArtifactTree"
import {
  ARL_FS_SORT_OPTIONS,
  type ARLFsDirectory,
  type ARLFsEntry,
  type ARLFsSortOption,
  flattenFsTree,
  normalizeDirectory,
  normalizeFsPath,
  parentFsPath,
  pruneFsSubtree,
  validateFsName,
} from "./arlFiles"

/** Row indent per depth, matching the classic artifact tree. */
const INDENT_PER_DEPTH = 12

/** Same left-to-right order as the chat panes. */
const ROLE_ORDER: ARLRole[] = ["validator", "worker"]

/** Tree state of one container's workspace. */
interface RoleTree {
  directories: Map<string, ARLFsDirectory>
  expanded: Set<string>
  /** Directories with a listing request in flight. */
  loading: Set<string>
  /** Root listing failed; shown in place of the tree. */
  error: string | null
}

function emptyTree(): RoleTree {
  return {
    directories: new Map(),
    expanded: new Set(),
    loading: new Set(),
    error: null,
  }
}

function emptyTrees(): Record<ARLRole, RoleTree> {
  return { worker: emptyTree(), validator: emptyTree() }
}

/** Create / rename dialog target. */
type NameDialogState =
  | {
      mode: "create"
      role: ARLRole
      parentPath: string
      kind: "file" | "directory"
    }
  | { mode: "rename"; role: ARLRole; entry: ARLFsEntry }

/**
 * Right-hand panel for ARL sessions: a file explorer over each container's
 * workspace, modelled on Paseo's native file pane.
 *
 * Talks to the daemon through the page's shared ARL socket (`fs_request`).
 * Directories load lazily on expand; the tree refreshes on demand, after
 * every mutation, and whenever an agent finishes a turn (that is when its
 * files change). Clicking a file opens a preview that doubles as an editor;
 * the download menu zips one or both workspaces over HTTP.
 */
export default function ARLArtifactsPanel() {
  const { t } = useTranslation()
  const { sessionId, request, ready, turnStatus } = useARLFiles()
  const [, copy] = useCopyToClipboard()

  const [role, setRole] = usePersistentState<ARLRole>(
    "autoresearch:arlFiles:role",
    "worker",
  )
  const [sort, setSort] = usePersistentState<ARLFsSortOption>(
    "autoresearch:arlFiles:sort",
    "name",
  )
  // 默认显示隐藏文件：.aris / .claude 等配置正是 ARL 工作区里常要看的。
  // key 带 v2，让此前存下的「隐藏」旧默认值失效。
  const [showHidden, setShowHidden] = usePersistentState<boolean>(
    "autoresearch:arlFiles:showHidden:v2",
    true,
  )
  const [trees, setTrees] = useState<Record<ARLRole, RoleTree>>(emptyTrees)
  const [nameDialog, setNameDialog] = useState<NameDialogState | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<{
    role: ARLRole
    entry: ARLFsEntry
  } | null>(null)
  const [preview, setPreview] = useState<ARLPreviewTarget | null>(null)
  const [zipping, setZipping] = useState(false)

  // Effects below read the latest trees without re-subscribing on every change.
  const treesRef = useRef(trees)
  treesRef.current = trees

  const updateTree = useCallback(
    (r: ARLRole, fn: (tree: RoleTree) => RoleTree) =>
      setTrees((prev) => ({ ...prev, [r]: fn(prev[r]) })),
    [],
  )

  /**
   * Fetch listings and merge them in.
   *
   * With `replace`, the cache is rebuilt from just these listings, so
   * folders deleted on disk (or by an agent) disappear on refresh instead of
   * lingering from an older listing.
   */
  const loadDirs = useCallback(
    async (r: ARLRole, paths: string[], replace = false) => {
      const targets = [...new Set(paths.map(normalizeFsPath))]
      updateTree(r, (tree) => ({
        ...tree,
        loading: new Set([...tree.loading, ...targets]),
      }))
      const results = await Promise.all(
        targets.map((path) =>
          request(r, "list", { path }).then(
            (data) => ({
              path,
              dir: normalizeDirectory(data.directory, path),
              error: null as string | null,
            }),
            (err: Error) => ({ path, dir: null, error: err.message }),
          ),
        ),
      )
      const childError = results.find((x) => x.error && x.path !== ".")
      if (childError) toast.error(`${childError.path}: ${childError.error}`)
      updateTree(r, (tree) => {
        const directories = replace
          ? new Map<string, ARLFsDirectory>()
          : new Map(tree.directories)
        const expanded = new Set(tree.expanded)
        if (replace) {
          // Keep listings for folders expanded while this refresh was in
          // flight: they are not in `targets`, and dropping them would leave
          // an expanded folder with no children and no refetch.
          for (const p of expanded) {
            const dir = tree.directories.get(p)
            if (!targets.includes(p) && dir) directories.set(p, dir)
          }
        }
        let error = tree.error
        for (const res of results) {
          if (res.dir) {
            // Key by what we asked for: the daemon may spell the path differently.
            directories.set(res.path, { ...res.dir, path: res.path })
            if (res.path === ".") error = null
          } else if (res.path === ".") {
            error = res.error
          } else {
            // A folder that can no longer be listed is collapsed, not left spinning.
            expanded.delete(res.path)
          }
        }
        const loading = new Set(tree.loading)
        for (const p of targets) loading.delete(p)
        return { ...tree, directories, expanded, loading, error }
      })
    },
    [request, updateTree],
  )

  /** Reload the root and every expanded folder of one container. */
  const refresh = useCallback(
    (r: ARLRole) => {
      const tree = treesRef.current[r]
      return loadDirs(r, [".", ...tree.expanded], true)
    },
    [loadDirs],
  )

  // First load once a container is reachable; reload after a reconnect or
  // when an agent's turn ends (the moment it is likely to have written files).
  const prevRef = useRef<Record<ARLRole, { ready: boolean; running: boolean }>>(
    {
      worker: { ready: false, running: false },
      validator: { ready: false, running: false },
    },
  )
  useEffect(() => {
    for (const r of ARL_ROLES) {
      const prev = prevRef.current[r]
      const running = turnStatus[r] === "running"
      const loaded = treesRef.current[r].directories.has(".")
      if (ready[r]) {
        const becameReady = !prev.ready
        const turnEnded = prev.running && !running
        if (!loaded && becameReady) void loadDirs(r, ["."])
        else if (loaded && (becameReady || turnEnded)) void refresh(r)
      }
      prevRef.current[r] = { ready: ready[r], running }
    }
  }, [ready, turnStatus, loadDirs, refresh])

  const toggleDir = useCallback(
    (path: string) => {
      const tree = treesRef.current[role]
      if (tree.expanded.has(path)) {
        updateTree(role, (t0) => {
          const expanded = new Set(t0.expanded)
          expanded.delete(path)
          return { ...t0, expanded }
        })
        return
      }
      updateTree(role, (t0) => ({
        ...t0,
        expanded: new Set([...t0.expanded, path]),
      }))
      if (!tree.directories.has(path)) void loadDirs(role, [path])
    },
    [role, loadDirs, updateTree],
  )

  /** Submit the create / rename dialog. Resolves to an error message, or null. */
  const submitName = useCallback(
    async (state: NameDialogState, name: string): Promise<string | null> => {
      const trimmed = name.trim()
      try {
        if (state.mode === "create") {
          await request(state.role, "create", {
            parent_path: state.parentPath,
            name: trimmed,
            kind: state.kind,
          })
          // Make sure the new entry is visible: open its parent folder.
          if (state.parentPath !== ".") {
            updateTree(state.role, (tree) => ({
              ...tree,
              expanded: new Set([...tree.expanded, state.parentPath]),
            }))
          }
          await loadDirs(state.role, [state.parentPath])
        } else {
          const { entry } = state
          if (trimmed === entry.name) return null
          await request(state.role, "rename", {
            path: entry.path,
            name: trimmed,
          })
          updateTree(state.role, (tree) => ({
            ...tree,
            ...pruneFsSubtree(tree.directories, tree.expanded, entry.path),
          }))
          if (preview?.role === state.role && preview.path === entry.path) {
            setPreview(null)
          }
          await loadDirs(state.role, [parentFsPath(entry.path)])
        }
        return null
      } catch (err) {
        return (err as Error).message
      }
    },
    [request, loadDirs, updateTree, preview],
  )

  const duplicate = useCallback(
    async (r: ARLRole, entry: ARLFsEntry) => {
      try {
        await request(r, "duplicate", { path: entry.path })
        await loadDirs(r, [parentFsPath(entry.path)])
      } catch (err) {
        toast.error((err as Error).message)
      }
    },
    [request, loadDirs],
  )

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return
    const { role: r, entry } = deleteTarget
    setDeleteTarget(null)
    try {
      await request(r, "delete", { path: entry.path })
      updateTree(r, (tree) => ({
        ...tree,
        ...pruneFsSubtree(tree.directories, tree.expanded, entry.path),
      }))
      if (
        preview?.role === r &&
        (preview.path === entry.path ||
          preview.path.startsWith(`${entry.path}/`))
      ) {
        setPreview(null)
      }
      await loadDirs(r, [parentFsPath(entry.path)])
    } catch (err) {
      toast.error((err as Error).message)
    }
  }, [deleteTarget, request, updateTree, loadDirs, preview])

  const copyPath = useCallback(
    (path: string) => {
      void copy(path).then((ok) => {
        if (ok) toast.success(t("autoResearch.arl.files.pathCopied"))
      })
    },
    [copy, t],
  )

  /** A save changes size / mtime; refresh the listing that shows them. */
  const onFileSaved = useCallback(
    (target: ARLPreviewTarget) =>
      void loadDirs(target.role, [parentFsPath(target.path)]),
    [loadDirs],
  )

  // 打包走 HTTP 直接读宿主侧 bind mount，不依赖容器在线；换票据失败才会报错。
  const downloadZip = useCallback(
    (scope: ARLRole | "all") => {
      if (!sessionId || zipping) return
      setZipping(true)
      void downloadARLWorkspaceArchive(sessionId, scope)
        .catch((err: Error) =>
          toast.error(
            `${t("autoResearch.arl.files.downloadFailed")}: ${err.message}`,
          ),
        )
        .finally(() => setZipping(false))
    },
    [sessionId, zipping, t],
  )

  const tree = trees[role]
  const rows = useMemo(
    () =>
      flattenFsTree({
        directories: tree.directories,
        expanded: tree.expanded,
        sort,
        showHidden,
      }),
    [tree.directories, tree.expanded, sort, showHidden],
  )
  const rootLoaded = tree.directories.has(".")
  const refreshing = tree.loading.has(".")
  const canAct = ready[role]

  const cycleSort = () => {
    const index = ARL_FS_SORT_OPTIONS.indexOf(sort)
    setSort(ARL_FS_SORT_OPTIONS[(index + 1) % ARL_FS_SORT_OPTIONS.length])
  }

  return (
    <aside className="flex h-full w-full flex-col overflow-hidden bg-transparent">
      {/* 标题行：容器切换（两个工作区各自一棵树） */}
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border/40 px-3">
        <FolderOpen className="size-3.5 shrink-0 text-primary/80" />
        <span className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wider text-foreground/80">
          {t("autoResearch.arl.files.title")}
        </span>
        <div
          role="tablist"
          aria-label={t("autoResearch.arl.files.title")}
          className="flex shrink-0 items-center rounded-md bg-muted p-0.5"
        >
          {ROLE_ORDER.map((r) => (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={role === r}
              onClick={() => setRole(r)}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
                role === r
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "size-1.5 rounded-full",
                  ready[r] ? "bg-emerald-500" : "bg-muted-foreground/40",
                )}
              />
              {t(`autoResearch.arl.${r}`)}
            </button>
          ))}
        </div>
      </div>

      {/* 操作行：左排序，右侧新建 / 隐藏文件 / 下载 / 刷新 */}
      <div className="flex h-9 shrink-0 items-center gap-1 px-2">
        <button
          type="button"
          onClick={cycleSort}
          title={t("autoResearch.arl.files.sortTitle")}
          className="flex h-6 items-center gap-1 rounded px-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
        >
          <ArrowDownUp className="size-3" />
          {t(`autoResearch.arl.files.sort.${sort}`)}
          <ChevronDown className="size-3 opacity-60" />
        </button>
        <span className="flex-1" />
        <ArtifactIconButton
          icon={FilePlus}
          title={t("autoResearch.arl.files.newFile")}
          disabled={!canAct}
          onClick={() =>
            setNameDialog({
              mode: "create",
              role,
              parentPath: ".",
              kind: "file",
            })
          }
        />
        <ArtifactIconButton
          icon={FolderPlus}
          title={t("autoResearch.arl.files.newFolder")}
          disabled={!canAct}
          onClick={() =>
            setNameDialog({
              mode: "create",
              role,
              parentPath: ".",
              kind: "directory",
            })
          }
        />
        <ArtifactIconButton
          icon={showHidden ? Eye : EyeOff}
          title={
            showHidden
              ? t("autoResearch.arl.files.hideHidden")
              : t("autoResearch.arl.files.showHidden")
          }
          onClick={() => setShowHidden((v) => !v)}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              disabled={!sessionId || zipping}
              title={t("autoResearch.arl.files.download")}
              aria-label={t("autoResearch.arl.files.download")}
              className="grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
            >
              {zipping ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Download className="size-3.5" />
              )}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-40">
            <DropdownMenuItem onSelect={() => downloadZip("all")}>
              {t("autoResearch.arl.files.downloadAll")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {ROLE_ORDER.map((r) => (
              <DropdownMenuItem key={r} onSelect={() => downloadZip(r)}>
                {t("autoResearch.arl.files.downloadRole", {
                  role: t(`autoResearch.arl.${r}`),
                })}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <ArtifactIconButton
          icon={RefreshCw}
          title={t("autoResearch.arl.files.refresh")}
          busy={refreshing}
          disabled={!canAct || refreshing}
          onClick={() => void refresh(role)}
        />
      </div>

      {/* 文件树 */}
      <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-2">
        {!ready[role] && !rootLoaded ? (
          <PanelNotice icon={<Loader2 className="size-4 animate-spin" />}>
            {t("autoResearch.arl.files.waiting")}
          </PanelNotice>
        ) : tree.error && !rootLoaded ? (
          <PanelNotice
            icon={<AlertTriangle className="size-4 text-destructive" />}
          >
            <span className="break-words text-destructive">{tree.error}</span>
            <Button
              size="sm"
              variant="outline"
              className="mt-2 h-7 text-xs"
              onClick={() => void loadDirs(role, ["."])}
            >
              {t("autoResearch.arl.files.retry")}
            </Button>
          </PanelNotice>
        ) : !rootLoaded ? (
          <PanelNotice icon={<Loader2 className="size-4 animate-spin" />}>
            {t("autoResearch.arl.files.loading")}
          </PanelNotice>
        ) : rows.length === 0 ? (
          <PanelNotice>
            {showHidden
              ? t("autoResearch.arl.files.empty")
              : t("autoResearch.arl.files.emptyVisible")}
          </PanelNotice>
        ) : (
          <ul className="space-y-0.5">
            {rows.map(({ entry, depth }) => (
              <FsRow
                key={entry.path}
                entry={entry}
                depth={depth}
                expanded={tree.expanded.has(entry.path)}
                loading={tree.loading.has(entry.path)}
                disabled={!canAct}
                onToggle={toggleDir}
                onOpen={(path) => setPreview({ role, path })}
                onNew={(parentPath, kind) =>
                  setNameDialog({ mode: "create", role, parentPath, kind })
                }
                onRename={(e) =>
                  setNameDialog({ mode: "rename", role, entry: e })
                }
                onDuplicate={(e) => void duplicate(role, e)}
                onDelete={(e) => setDeleteTarget({ role, entry: e })}
                onCopyPath={copyPath}
              />
            ))}
          </ul>
        )}
      </div>

      {/* Mounted per open so the input starts with the right value and the
          rename preselection runs against it. */}
      {nameDialog && (
        <NameDialog
          state={nameDialog}
          onClose={() => setNameDialog(null)}
          onSubmit={submitName}
        />
      )}

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("autoResearch.arl.files.deleteTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription className="break-all">
              {deleteTarget &&
                t(
                  deleteTarget.entry.kind === "directory"
                    ? "autoResearch.arl.files.deleteFolderConfirm"
                    : "autoResearch.arl.files.deleteFileConfirm",
                  { path: deleteTarget.entry.path },
                )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={() => void confirmDelete()}
            >
              {t("autoResearch.arl.files.actions.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ARLFilePreviewDialog
        target={preview}
        onOpenChange={(open) => !open && setPreview(null)}
        onSaved={onFileSaved}
      />
    </aside>
  )
}

/** Centered placeholder for the loading / error / empty states. */
function PanelNotice({
  icon,
  children,
}: {
  icon?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-xs text-muted-foreground/70">
      {icon}
      {children}
    </div>
  )
}

/** One tree row: click to expand (folder) or preview (file); actions in a menu. */
function FsRow({
  entry,
  depth,
  expanded,
  loading,
  disabled,
  onToggle,
  onOpen,
  onNew,
  onRename,
  onDuplicate,
  onDelete,
  onCopyPath,
}: {
  entry: ARLFsEntry
  depth: number
  expanded: boolean
  loading: boolean
  disabled: boolean
  onToggle: (path: string) => void
  onOpen: (path: string) => void
  onNew: (parentPath: string, kind: "file" | "directory") => void
  onRename: (entry: ARLFsEntry) => void
  onDuplicate: (entry: ARLFsEntry) => void
  onDelete: (entry: ARLFsEntry) => void
  onCopyPath: (path: string) => void
}) {
  const { t } = useTranslation()
  const isDir = entry.kind === "directory"
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <li>
      <div
        style={{ paddingLeft: `${depth * INDENT_PER_DEPTH + 4}px` }}
        className={cn(
          "group flex items-center gap-1 rounded py-1 pr-1 text-xs hover:bg-primary/6",
          menuOpen && "bg-primary/6",
        )}
      >
        <button
          type="button"
          onClick={() => (isDir ? onToggle(entry.path) : onOpen(entry.path))}
          title={entry.path}
          aria-expanded={isDir ? expanded : undefined}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
        >
          {isDir ? (
            loading ? (
              <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground" />
            ) : expanded ? (
              <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
            )
          ) : (
            <span className="w-3 shrink-0" />
          )}
          {isDir ? (
            expanded ? (
              <FolderOpen className="size-3.5 shrink-0 text-amber-500" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-amber-500" />
            )
          ) : (
            <FileKindIcon name={entry.name} />
          )}
          <span className="min-w-0 flex-1 truncate text-foreground/80">
            {middleEllipsis(entry.name, 18, 10)}
          </span>
          {!isDir && (
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/60">
              {formatSize(entry.size)}
            </span>
          )}
        </button>
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              disabled={disabled}
              aria-label={t("autoResearch.arl.files.actions.more")}
              className={cn(
                "grid size-5 shrink-0 place-items-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-primary/10 hover:text-primary focus:opacity-100 group-hover:opacity-100 disabled:hidden",
                menuOpen && "opacity-100",
              )}
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            {isDir ? (
              <>
                <DropdownMenuItem onSelect={() => onNew(entry.path, "file")}>
                  <FilePlus />
                  {t("autoResearch.arl.files.newFile")}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() => onNew(entry.path, "directory")}
                >
                  <FolderPlus />
                  {t("autoResearch.arl.files.newFolder")}
                </DropdownMenuItem>
              </>
            ) : (
              <DropdownMenuItem onSelect={() => onOpen(entry.path)}>
                <Eye />
                {t("autoResearch.arl.files.actions.preview")}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => onRename(entry)}>
              <Pencil />
              {t("autoResearch.arl.files.actions.rename")}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onDuplicate(entry)}>
              <CopyPlus />
              {t("autoResearch.arl.files.actions.duplicate")}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onCopyPath(entry.path)}>
              <Copy />
              {t("autoResearch.arl.files.actions.copyPath")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => onDelete(entry)}
            >
              <Trash2 />
              {t("autoResearch.arl.files.actions.delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  )
}

/** Create / rename dialog; stays open with the daemon's error on failure. */
function NameDialog({
  state,
  onClose,
  onSubmit,
}: {
  state: NameDialogState
  onClose: () => void
  onSubmit: (state: NameDialogState, name: string) => Promise<string | null>
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(() =>
    state.mode === "rename" ? state.entry.name : "",
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const title =
    state.mode === "rename"
      ? t("autoResearch.arl.files.renameTitle")
      : state.kind === "directory"
        ? t("autoResearch.arl.files.newFolder")
        : t("autoResearch.arl.files.newFile")
  const where =
    state.mode === "create"
      ? state.parentPath === "."
        ? t(`autoResearch.arl.${state.role}`)
        : `${t(`autoResearch.arl.${state.role}`)} / ${state.parentPath}`
      : state.entry.path

  const submit = async () => {
    const problem = validateFsName(name)
    if (problem) {
      setError(t(`autoResearch.arl.files.nameError.${problem}`))
      return
    }
    setBusy(true)
    const failure = await onSubmit(state, name)
    setBusy(false)
    if (failure) setError(failure)
    else onClose()
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="text-sm">{title}</DialogTitle>
          <p className="break-all font-mono text-[11px] text-muted-foreground">
            {where}
          </p>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
          className="space-y-2"
        >
          <Input
            autoFocus
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              setError(null)
            }}
            placeholder={t("autoResearch.arl.files.namePlaceholder")}
            aria-label={t("autoResearch.arl.files.namePlaceholder")}
            aria-invalid={!!error}
            onFocus={(e) => {
              // Rename: preselect the stem so typing keeps the extension.
              if (state.mode !== "rename") return
              const value = e.currentTarget.value
              const dot =
                state.entry.kind === "file" ? value.lastIndexOf(".") : -1
              e.currentTarget.setSelectionRange(0, dot > 0 ? dot : value.length)
            }}
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
