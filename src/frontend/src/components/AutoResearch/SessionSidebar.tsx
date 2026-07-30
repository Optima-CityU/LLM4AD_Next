import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  DownloadCloud,
  FolderPlus,
  ListFilter,
  Loader2,
  MoreHorizontal,
  Plus,
  Repeat,
  Search,
  Sparkles,
  X,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type {
  ResearchFolderItem,
  ResearchSessionItem,
  ResearchSessionStatus,
} from "@/client"
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
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  downloadResearchArtifactsArchive,
  useInfiniteFolderSessions,
  useInfiniteSearchSessions,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import { PROFILE_OPTIONS } from "./shared"
import { SectionLabel, sessionDotClasses } from "./tech"

interface Props {
  folders: ResearchFolderItem[]
  /** 文件夹列表加载中（仅影响首屏骨架）。会话分页各分组内部自管。 */
  loading: boolean
  /** 文件夹列表加载失败：首屏展示错误态 + 重试，避免请求失败时静默空白。 */
  foldersError?: boolean
  /** 重新拉取文件夹列表（错误态重试按钮触发）。 */
  onRetryFolders?: () => void
  /** 未分组会话总数（来自 folders 响应的 ungrouped_session_count）。 */
  ungroupedCount: number
  /** 激活会话所属文件夹 id：该文件夹默认展开（刷新后自动加载它的第一页）。 */
  activeFolderId: string | null
  /** 防抖后的搜索关键词（受控，父层已 debounce）。 */
  debouncedSearch: string
  /** 关键词输入（受控，即时值，用于输入框显示）。 */
  search: string
  onSearchChange: (v: string) => void
  /** 状态筛选集合（受控，空集合 = 全部）。 */
  statusFilter: Set<ResearchSessionStatus>
  onToggleStatus: (s: ResearchSessionStatus) => void
  onClearStatus: () => void
  activeSessionId: string | null
  onSelectSession: (id: string) => void
  onCreateSession: (folderId: string | null) => void
  onCreateFolder: (name: string) => Promise<void> | void
  onRenameFolder: (id: string, name: string) => Promise<void> | void
  onDeleteFolder: (id: string) => Promise<void> | void
  onRenameSession: (id: string, title: string) => Promise<void> | void
  onMoveSession: (id: string, folderId: string | null) => Promise<void> | void
  onDeleteSession: (id: string) => Promise<void> | void
  /** 切换会话 profile（实验类型），会清空第 9 步之后的产物。 */
  onSwitchProfile: (id: string, profile: string) => Promise<void> | void
}

/** 可筛选的会话状态（枚举子集，覆盖用户会关注的运行态）。 */
const STATUS_FILTERS: ResearchSessionStatus[] = [
  "running",
  "paused",
  "completed",
  "failed",
]

/**
 * 左侧会话侧栏：展示分组 + 未分组会话，支持创建 / 重命名 / 移动 / 删除。
 *
 * 顶部提供关键词搜索（对 topic + title 服务端 ILIKE）与状态筛选（服务端），
 * 两者均受控于父层并直接驱动 `listSessions` 查询，故 `sessions` 到达时即为
 * 已过滤结果，组件内不再二次过滤。分组本身扁平呈现，不做嵌套。
 */
export default function SessionSidebar({
  folders,
  loading,
  foldersError,
  onRetryFolders,
  ungroupedCount,
  activeFolderId,
  debouncedSearch,
  search,
  onSearchChange,
  statusFilter,
  onToggleStatus,
  onClearStatus,
  activeSessionId,
  onSelectSession,
  onCreateSession,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onRenameSession,
  onMoveSession,
  onDeleteSession,
  onSwitchProfile,
}: Props) {
  const { t } = useTranslation()
  const [createFolderOpen, setCreateFolderOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState("")
  const [renameFolder, setRenameFolder] = useState<ResearchFolderItem | null>(
    null,
  )
  const [renameFolderName, setRenameFolderName] = useState("")
  const [deleteFolder, setDeleteFolder] = useState<ResearchFolderItem | null>(
    null,
  )
  const [renameSession, setRenameSession] =
    useState<ResearchSessionItem | null>(null)
  const [renameSessionTitle, setRenameSessionTitle] = useState("")
  const [deleteSession, setDeleteSession] =
    useState<ResearchSessionItem | null>(null)
  // 异步操作提交中标记：给对话框主按钮上 loading/disabled，避免慢网络下重复提交
  // （重复建文件夹/重复删除），并给出「正在进行」的可见反馈。
  const [folderBusy, setFolderBusy] = useState(false)
  const [sessionBusy, setSessionBusy] = useState(false)
  // 切换实验类型：待确认的会话 + 目标 profile（null=对话框关闭）+ 产物打包下载中标记 + 切换提交中标记。
  const [switchTarget, setSwitchTarget] = useState<{
    session: ResearchSessionItem
    profile: string
  } | null>(null)
  const [switchDownloading, setSwitchDownloading] = useState(false)
  const [switching, setSwitching] = useState(false)

  // 拖放：正在拖的会话 + 当前悬停的放置目标分组 key（null=未分组假分组用 "__ungrouped__"）。
  const [dragSession, setDragSession] = useState<{
    id: string
    folderId: string | null
  } | null>(null)
  const [dragOverKey, setDragOverKey] = useState<string | null>(null)

  // 放下：目标分组与源分组不同才移动（同组为无操作）。folderKey 用 "__ungrouped__"
  // 表示移出分组（folderId=null）。
  const handleDropToFolder = (folderKey: string) => {
    const drag = dragSession
    setDragSession(null)
    setDragOverKey(null)
    if (!drag) return
    const targetFolderId = folderKey === "__ungrouped__" ? null : folderKey
    if (targetFolderId === drag.folderId) return
    void onMoveSession(drag.id, targetFolderId)
  }

  // 检索态：走扁平跨文件夹分页；否则按文件夹懒加载。用 debounced 值判定。
  const hasFilter = debouncedSearch.trim().length > 0 || statusFilter.size > 0

  // 展开的分组 key 集合（仅当前会话内存，不持久化）。用「已展开集」而非「折叠 map」，
  // 语义更直接：初始只展开未分组 + 激活文件夹，其余折叠、不请求。
  //
  // 关键：activeFolderId 只在挂载时读一次作为初始展开集；之后切换 session 使
  // activeFolderId 变化也不再自动动文件夹——展开与否完全交给用户手动 toggle。
  // （不持久化：避免刷新后多个文件夹仍展开、各自拉第一页。）
  const [expanded, setExpanded] = useState<Set<string>>(
    () =>
      new Set<string>([
        "__ungrouped__",
        ...(activeFolderId ? [activeFolderId] : []),
      ]),
  )
  const toggleExpanded = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // 删除分组提示用的会话数：直接取文件夹响应里的 session_count。
  const sessionCountInDeleteTarget = deleteFolder?.session_count ?? 0

  // 检索态的扁平分页查询（仅命中搜索/筛选时启用）。
  const searchQ = useInfiniteSearchSessions(
    { q: debouncedSearch.trim(), status: [...statusFilter] },
    { enabled: hasFilter },
  )
  const searchSessions = useMemo(
    () => (searchQ.data?.pages ?? []).flatMap((p) => p.items ?? []),
    [searchQ.data],
  )
  const searchFetching = hasFilter && searchQ.isFetching

  const handleCreateFolder = async () => {
    const name = newFolderName.trim()
    if (!name || folderBusy) return
    setFolderBusy(true)
    try {
      await onCreateFolder(name)
      setNewFolderName("")
      setCreateFolderOpen(false)
    } catch (_) {
      // 由父层通过 mutation.onError 报错；这里保持对话框打开
    } finally {
      setFolderBusy(false)
    }
  }

  const handleRenameFolder = async () => {
    if (!renameFolder || folderBusy) return
    const name = renameFolderName.trim()
    if (!name) return
    setFolderBusy(true)
    try {
      await onRenameFolder(renameFolder.id, name)
      setRenameFolder(null)
    } catch (_) {
      /* keep dialog */
    } finally {
      setFolderBusy(false)
    }
  }

  const handleRenameSession = async () => {
    if (!renameSession || sessionBusy) return
    const title = renameSessionTitle.trim()
    if (!title) return
    setSessionBusy(true)
    try {
      await onRenameSession(renameSession.id, title)
      setRenameSession(null)
    } catch (_) {
      /* keep dialog */
    } finally {
      setSessionBusy(false)
    }
  }

  return (
    <aside className="h-full w-full flex flex-col bg-transparent overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between h-12 px-4 border-b border-border/60 shrink-0">
        <SectionLabel className="text-primary">
          {t("autoResearch.sidebar.title")}
        </SectionLabel>
        <Button
          variant="ghost"
          size="icon-sm"
          className="size-7 rounded-md border border-transparent text-muted-foreground hover:text-primary hover:bg-primary/10 hover:border-primary/40 transition-colors"
          title={t("autoResearch.sidebar.newFolder")}
          onClick={() => setCreateFolderOpen(true)}
        >
          <FolderPlus className="size-3.5" />
        </Button>
      </div>

      {/* 新建研究：主行动按钮，填充主色 + 图标文字，突出于其它入口 */}
      <div className="px-2.5 pt-2.5 pb-1 shrink-0">
        <Button
          className="w-full h-9 gap-1.5 rounded-lg bg-primary text-primary-foreground font-medium shadow-sm shadow-primary/20 hover:bg-primary/90 transition-colors"
          onClick={() => onCreateSession(null)}
        >
          <Sparkles className="size-4" />
          {t("autoResearch.sidebar.newResearch")}
        </Button>
      </div>

      {/* 搜索框：关键词走服务端 ILIKE（topic + title） */}
      <div className="px-2.5 pt-2 pb-1.5 border-b border-border/60 shrink-0">
        <div className="group relative flex items-center">
          <Search className="pointer-events-none absolute left-2.5 size-3.5 text-muted-foreground/60 group-focus-within:text-primary transition-colors" />
          <input
            type="text"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={t("autoResearch.sidebar.searchPlaceholder")}
            className="w-full h-8 rounded-md border border-border/60 bg-card dark:bg-background/60 pl-8 pr-8 text-xs text-foreground placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30 transition-colors"
          />
          <div className="absolute right-2 flex items-center">
            {searchFetching ? (
              <Loader2 className="size-3.5 animate-spin text-muted-foreground/60" />
            ) : (
              search.length > 0 && (
                <button
                  type="button"
                  onClick={() => onSearchChange("")}
                  title={t("common.clear")}
                  className="text-muted-foreground/60 hover:text-foreground transition-colors"
                >
                  <X className="size-3.5" />
                </button>
              )
            )}
          </div>
        </div>
      </div>

      {/* 状态筛选：受控，走服务端 status 过滤。多标签自动换行，避免横向滚动条 */}
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-2 py-1.5 border-b border-border/60 shrink-0">
        <ListFilter className="size-3 shrink-0 text-muted-foreground/60" />
        {STATUS_FILTERS.map((s) => {
          const on = statusFilter.has(s)
          return (
            <button
              key={s}
              type="button"
              onClick={() => onToggleStatus(s)}
              className={cn(
                "shrink-0 rounded-full border px-1.5 py-0.5 text-xs leading-none transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                on
                  ? "border-primary bg-primary/20 text-primary font-semibold shadow-sm shadow-primary/20"
                  : "border-border/60 text-muted-foreground hover:border-primary/40 hover:text-foreground",
              )}
            >
              {t(`autoResearch.status.${s}`)}
            </button>
          )
        })}
        {statusFilter.size > 0 && (
          <button
            type="button"
            onClick={onClearStatus}
            title={t("common.clear")}
            className="shrink-0 ml-auto text-muted-foreground/60 hover:text-foreground transition-colors"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading && (
          <div className="text-[11px] text-muted-foreground/60 px-2 py-3">
            {t("autoResearch.sidebar.loading")}
          </div>
        )}

        {/* 文件夹列表加载失败：错误态 + 重试，避免请求失败时静默空白 */}
        {!loading && foldersError && (
          <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
            <AlertTriangle className="size-5 text-destructive/70" />
            <p className="text-[11px] text-muted-foreground/70">
              {t("autoResearch.sidebar.loadError")}
            </p>
            {onRetryFolders && (
              <button
                type="button"
                onClick={onRetryFolders}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-border/60 text-[11px] text-muted-foreground hover:text-primary hover:border-primary/40 hover:bg-primary/5 transition-colors"
              >
                <Repeat className="size-3" />
                {t("common.retry")}
              </button>
            )}
          </div>
        )}

        {/* 检索 / 筛选态：扁平跨文件夹分页，不按分组分桶 */}
        {!loading && !foldersError && hasFilter && (
          <SearchResults
            sessions={searchSessions}
            query={searchQ}
            folders={folders}
            activeSessionId={activeSessionId}
            onSelectSession={onSelectSession}
            onDeleteSession={setDeleteSession}
            onRenameSession={(s) => {
              setRenameSessionTitle(s.title)
              setRenameSession(s)
            }}
            onMoveSession={onMoveSession}
            onSwitchProfile={(s, target) =>
              setSwitchTarget({ session: s, profile: target })
            }
            onClearFilters={() => {
              onSearchChange("")
              onClearStatus()
            }}
          />
        )}

        {!loading &&
          !foldersError &&
          !hasFilter &&
          ungroupedCount === 0 &&
          folders.length === 0 && (
            <button
              type="button"
              onClick={() => onCreateSession(null)}
              className="w-full flex flex-col items-center gap-2 px-4 py-8 text-center text-[11px] text-muted-foreground/60 hover:text-primary hover:bg-primary/5 rounded-md transition-all duration-200"
            >
              <Sparkles className="size-5 text-muted-foreground/40" />
              {t("autoResearch.sidebar.noSessions")}
            </button>
          )}

        {/* Ungrouped：有未分组会话时才渲染，内部按需分页 */}
        {!foldersError && !hasFilter && ungroupedCount > 0 && (
          <FolderSessionGroup
            folderId={null}
            label={t("autoResearch.sidebar.ungrouped")}
            totalCount={ungroupedCount}
            folders={folders}
            activeSessionId={activeSessionId}
            onSelectSession={onSelectSession}
            onDeleteSession={setDeleteSession}
            onRenameSession={(s) => {
              setRenameSessionTitle(s.title)
              setRenameSession(s)
            }}
            onMoveSession={onMoveSession}
            onSwitchProfile={(s, target) =>
              setSwitchTarget({ session: s, profile: target })
            }
            expanded={expanded.has("__ungrouped__")}
            onToggle={() => toggleExpanded("__ungrouped__")}
            dragSession={dragSession}
            dragOverKey={dragOverKey}
            onDragStartSession={setDragSession}
            onDragEnterGroup={setDragOverKey}
            onDragEndSession={() => {
              setDragSession(null)
              setDragOverKey(null)
            }}
            onDropToGroup={handleDropToFolder}
            headerActions={
              <Button
                variant="ghost"
                size="icon-sm"
                className="size-6 text-muted-foreground hover:text-primary"
                title={t("autoResearch.sidebar.newSession")}
                onClick={() => onCreateSession(null)}
              >
                <Plus className="size-3" />
              </Button>
            }
          />
        )}

        {/* Folders */}
        {!foldersError &&
          !hasFilter &&
          folders.map((folder) => (
            <FolderSessionGroup
              key={folder.id}
              folderId={folder.id}
              label={folder.name}
              totalCount={folder.session_count ?? 0}
              folders={folders}
              activeSessionId={activeSessionId}
              onSelectSession={onSelectSession}
              onDeleteSession={setDeleteSession}
              onRenameSession={(s) => {
                setRenameSessionTitle(s.title)
                setRenameSession(s)
              }}
              onMoveSession={onMoveSession}
              onSwitchProfile={(s, target) =>
              setSwitchTarget({ session: s, profile: target })
            }
              expanded={expanded.has(folder.id)}
              onToggle={() => toggleExpanded(folder.id)}
              dragSession={dragSession}
              dragOverKey={dragOverKey}
              onDragStartSession={setDragSession}
              onDragEnterGroup={setDragOverKey}
              onDragEndSession={() => {
                setDragSession(null)
                setDragOverKey(null)
              }}
              onDropToGroup={handleDropToFolder}
              headerActions={
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="size-6 text-muted-foreground hover:text-primary"
                      title={t("autoResearch.sidebar.folderOptions")}
                      aria-label={t("autoResearch.sidebar.folderOptions")}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoreHorizontal className="size-3" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-40">
                    <DropdownMenuItem
                      onSelect={() => onCreateSession(folder.id)}
                    >
                      <Plus className="size-3.5 mr-2" />
                      {t("autoResearch.sidebar.newSession")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => {
                        setRenameFolderName(folder.name)
                        setRenameFolder(folder)
                      }}
                    >
                      {t("autoResearch.sidebar.renameFolder")}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onSelect={() => setDeleteFolder(folder)}
                    >
                      {t("autoResearch.sidebar.deleteFolder")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              }
            />
          ))}
      </div>

      {/* Create folder dialog */}
      <Dialog open={createFolderOpen} onOpenChange={setCreateFolderOpen}>
        <DialogContent className="sm:max-w-[360px]" preventOutsideClose>
          <DialogHeader>
            <DialogTitle>{t("autoResearch.sidebar.createFolder")}</DialogTitle>
          </DialogHeader>
          <div className="py-3">
            <Input
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              placeholder={t("autoResearch.sidebar.folderNamePlaceholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreateFolder()
              }}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={folderBusy}
              onClick={() => {
                setNewFolderName("")
                setCreateFolderOpen(false)
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleCreateFolder()}
              disabled={!newFolderName.trim() || folderBusy}
            >
              {folderBusy && <Loader2 className="size-4 animate-spin" />}
              {t("autoResearch.sidebar.createFolder")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rename folder dialog */}
      <Dialog
        open={!!renameFolder}
        onOpenChange={(open) => !open && setRenameFolder(null)}
      >
        <DialogContent className="sm:max-w-[360px]" preventOutsideClose>
          <DialogHeader>
            <DialogTitle>{t("autoResearch.sidebar.renameFolder")}</DialogTitle>
          </DialogHeader>
          <div className="py-3">
            <Input
              value={renameFolderName}
              onChange={(e) => setRenameFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleRenameFolder()
              }}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={folderBusy}
              onClick={() => setRenameFolder(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleRenameFolder()}
              disabled={!renameFolderName.trim() || folderBusy}
            >
              {folderBusy && <Loader2 className="size-4 animate-spin" />}
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete folder confirm */}
      <AlertDialog
        open={!!deleteFolder}
        onOpenChange={(open) => !open && setDeleteFolder(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("autoResearch.sidebar.deleteFolder")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("autoResearch.sidebar.deleteFolderConfirm", {
                name: deleteFolder?.name ?? "",
                count: sessionCountInDeleteTarget,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={folderBusy}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={folderBusy}
              onClick={async (e) => {
                // 阻止 AlertDialog 默认的「点击即关闭」，删除完成前保持打开并显示 loading。
                e.preventDefault()
                if (!deleteFolder || folderBusy) return
                setFolderBusy(true)
                try {
                  await onDeleteFolder(deleteFolder.id)
                  setDeleteFolder(null)
                } finally {
                  setFolderBusy(false)
                }
              }}
            >
              {folderBusy && <Loader2 className="size-4 animate-spin" />}
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Rename session dialog */}
      <Dialog
        open={!!renameSession}
        onOpenChange={(open) => !open && setRenameSession(null)}
      >
        <DialogContent className="sm:max-w-[360px]" preventOutsideClose>
          <DialogHeader>
            <DialogTitle>{t("autoResearch.sidebar.renameSession")}</DialogTitle>
          </DialogHeader>
          <div className="py-3">
            <div className="relative">
              <Input
                value={renameSessionTitle}
                onChange={(e) => setRenameSessionTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleRenameSession()
                }}
                maxLength={255}
                autoFocus
              />
              {/* 字数统计 */}
              {renameSessionTitle.length > 0 && (
                <div
                  className={`absolute top-1/2 -translate-y-1/2 right-2 px-1.5 py-0.5 rounded text-[10px] font-mono tabular-nums backdrop-blur-sm pointer-events-none ${
                    renameSessionTitle.length > 255
                      ? "bg-destructive/90 text-destructive-foreground"
                      : renameSessionTitle.length > 229
                        ? "bg-amber-500/90 text-white"
                        : "bg-muted/80 text-muted-foreground"
                  }`}
                >
                  {renameSessionTitle.length} / 255
                </div>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={sessionBusy}
              onClick={() => setRenameSession(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleRenameSession()}
              disabled={!renameSessionTitle.trim() || sessionBusy}
            >
              {sessionBusy && <Loader2 className="size-4 animate-spin" />}
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete session confirm */}
      <AlertDialog
        open={!!deleteSession}
        onOpenChange={(open) => !open && setDeleteSession(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("autoResearch.sidebar.deleteSession")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("autoResearch.sidebar.deleteSessionConfirm", {
                title: deleteSession?.title ?? "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={sessionBusy}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={sessionBusy}
              onClick={async (e) => {
                e.preventDefault()
                if (!deleteSession || sessionBusy) return
                setSessionBusy(true)
                try {
                  await onDeleteSession(deleteSession.id)
                  setDeleteSession(null)
                } finally {
                  setSessionBusy(false)
                }
              }}
            >
              {sessionBusy && <Loader2 className="size-4 animate-spin" />}
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 切换实验类型确认：警告将清空第 9 步之后产物，提供打包下载入口 */}
      <Dialog
        open={!!switchTarget}
        onOpenChange={(open) => {
          if (!open && !switching) setSwitchTarget(null)
        }}
      >
        <DialogContent className="sm:max-w-[440px]" preventOutsideClose>
          <DialogHeader>
            <DialogTitle>
              {t("autoResearch.sidebar.switchProfileTitle")}
            </DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">
              {switchTarget &&
                t("autoResearch.sidebar.switchProfileConfirm", {
                  target: t(`autoResearch.profile.${switchTarget.profile}`),
                })}
            </DialogDescription>
          </DialogHeader>

          {/* 产物打包下载：与右侧「打包下载全部」同一接口 */}
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/[0.06] p-3 space-y-2">
            <p className="text-xs text-amber-600 dark:text-amber-300/90 leading-relaxed">
              {t("autoResearch.sidebar.switchProfileDownloadHint")}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="w-full gap-1.5"
              disabled={switchDownloading}
              onClick={() => {
                if (!switchTarget || switchDownloading) return
                setSwitchDownloading(true)
                void downloadResearchArtifactsArchive(switchTarget.session.id)
                  .catch((err: unknown) =>
                    toast.error((err as Error)?.message ?? "download failed"),
                  )
                  .finally(() => setSwitchDownloading(false))
              }}
            >
              {switchDownloading ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <DownloadCloud className="size-3.5" />
              )}
              {t("autoResearch.artifacts.downloadAll")}
            </Button>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              disabled={switching}
              onClick={() => setSwitchTarget(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              disabled={switching}
              onClick={async () => {
                if (!switchTarget) return
                setSwitching(true)
                try {
                  await onSwitchProfile(
                    switchTarget.session.id,
                    switchTarget.profile,
                  )
                  setSwitchTarget(null)
                } catch (_) {
                  /* 父层已 toast，保持对话框打开 */
                } finally {
                  setSwitching(false)
                }
              }}
            >
              {switching ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Repeat className="size-3.5" />
              )}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}

interface FolderGroupProps {
  /** 文件夹 id；null = 未分组假分组。 */
  folderId: string | null
  label: string
  /** 该分组会话总数（来自文件夹响应），用于头部计数展示。 */
  totalCount: number
  folders: ResearchFolderItem[]
  activeSessionId: string | null
  onSelectSession: (id: string) => void
  onDeleteSession: (s: ResearchSessionItem) => void
  onRenameSession: (s: ResearchSessionItem) => void
  onMoveSession: (id: string, folderId: string | null) => Promise<void> | void
  onSwitchProfile: (s: ResearchSessionItem, target: string) => void
  headerActions?: React.ReactNode
  /** 是否展开（受控，父层用已展开集判定）。 */
  expanded: boolean
  onToggle: () => void
  dragSession?: { id: string; folderId: string | null } | null
  dragOverKey?: string | null
  onDragStartSession?: (drag: { id: string; folderId: string | null }) => void
  onDragEnterGroup?: (key: string) => void
  onDragEndSession?: () => void
  onDropToGroup?: (folderKey: string) => void
}

/**
 * 单个文件夹分组：展开时懒加载本文件夹（或未分组）的会话，游标分页 +
 * 底部「加载更多」。折叠时不请求（enabled=false），实现按需加载。
 */
function FolderSessionGroup({
  folderId,
  label,
  totalCount,
  folders,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  onRenameSession,
  onMoveSession,
  onSwitchProfile,
  headerActions,
  expanded,
  onToggle,
  dragSession,
  dragOverKey,
  onDragStartSession,
  onDragEnterGroup,
  onDragEndSession,
  onDropToGroup,
}: FolderGroupProps) {
  const { t } = useTranslation()
  const key = folderId ?? "__ungrouped__"

  // 展开才拉数据（懒加载）。折叠时 enabled=false，不发请求。
  const query = useInfiniteFolderSessions(folderId, { enabled: expanded })
  const sessions = useMemo(
    () => (query.data?.pages ?? []).flatMap((p) => p.items ?? []),
    [query.data],
  )

  // 是否可作为放置目标：需要拖放能力开启、有正在拖的会话，且其源分组 ≠ 本组。
  const dndEnabled = !!onDropToGroup
  const isDropTarget =
    dndEnabled && !!dragSession && dragSession.folderId !== folderId
  const isDragOver = isDropTarget && dragOverKey === key

  const dropHandlers = isDropTarget
    ? {
        onDragOver: (e: React.DragEvent) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = "move"
        },
        onDragEnter: (e: React.DragEvent) => {
          e.preventDefault()
          onDragEnterGroup?.(key)
        },
        onDrop: (e: React.DragEvent) => {
          e.preventDefault()
          onDropToGroup?.(key)
        },
      }
    : {}

  return (
    <div
      className={cn(
        "mb-2 rounded-md transition-colors",
        isDragOver && "bg-primary/10 ring-1 ring-primary/40",
        isDropTarget && !isDragOver && "ring-1 ring-dashed ring-primary/20",
      )}
      {...dropHandlers}
    >
      <div className="group flex items-center h-7 rounded transition-colors hover:text-foreground text-muted-foreground/80">
        <button
          type="button"
          onClick={onToggle}
          className="flex-1 min-w-0 flex items-center gap-1 px-2 h-full"
        >
          {expanded ? (
            <ChevronDown className="size-3 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 text-muted-foreground" />
          )}
          <SectionLabel className="truncate flex-1 text-left">
            {label}
          </SectionLabel>
          <span className="ml-1 shrink-0 rounded-full bg-muted/50 px-1.5 py-px text-[10px] tabular-nums text-muted-foreground/70">
            {totalCount}
          </span>
        </button>
        {headerActions && (
          <span className="opacity-0 group-hover:opacity-100 transition-opacity pr-1">
            {headerActions}
          </span>
        )}
      </div>
      {expanded && (
        <div className="mt-0.5 space-y-0.5">
          {/* 首次加载骨架 */}
          {query.isLoading && (
            <div className="flex items-center gap-1.5 px-4 py-2 text-[10px] text-muted-foreground/50">
              <Loader2 className="size-3 animate-spin" />
              {t("autoResearch.sidebar.loading")}
            </div>
          )}
          {!query.isLoading && sessions.length === 0 && (
            <p className="px-4 py-2 text-[10px] text-muted-foreground/50">
              {isDragOver ? t("autoResearch.sidebar.dropHere") : "—"}
            </p>
          )}
          {sessions.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              folders={folders}
              currentFolderId={folderId}
              isActive={session.id === activeSessionId}
              dragging={dragSession?.id === session.id}
              draggable={dndEnabled}
              onDragStart={onDragStartSession}
              onDragEnd={onDragEndSession}
              onSelect={onSelectSession}
              onRename={onRenameSession}
              onMove={onMoveSession}
              onDelete={onDeleteSession}
              onSwitchProfile={onSwitchProfile}
            />
          ))}
          {query.hasNextPage && (
            <LoadMoreRow
              loading={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            />
          )}
        </div>
      )}
    </div>
  )
}

/** 「加载更多」行：翻下一页游标。 */
function LoadMoreRow({
  loading,
  onClick,
}: {
  loading: boolean
  onClick: () => void
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="w-full flex items-center justify-center gap-1.5 py-1.5 text-[11px] text-muted-foreground hover:text-primary transition-colors disabled:opacity-60"
    >
      {loading ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <ChevronDown className="size-3" />
      )}
      {t("autoResearch.chat.loadMore")}
    </button>
  )
}

interface SearchResultsProps {
  sessions: ResearchSessionItem[]
  query: ReturnType<typeof useInfiniteSearchSessions>
  folders: ResearchFolderItem[]
  activeSessionId: string | null
  onSelectSession: (id: string) => void
  onDeleteSession: (s: ResearchSessionItem) => void
  onRenameSession: (s: ResearchSessionItem) => void
  onMoveSession: (id: string, folderId: string | null) => Promise<void> | void
  onSwitchProfile: (s: ResearchSessionItem, target: string) => void
  onClearFilters: () => void
}

/** 检索 / 筛选态的扁平结果列表（跨文件夹），带游标「加载更多」。 */
function SearchResults({
  sessions,
  query,
  folders,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  onRenameSession,
  onMoveSession,
  onSwitchProfile,
  onClearFilters,
}: SearchResultsProps) {
  const { t } = useTranslation()

  if (query.isLoading) {
    return (
      <div className="flex items-center justify-center gap-1.5 py-8 text-[11px] text-muted-foreground/60">
        <Loader2 className="size-4 animate-spin" />
        {t("autoResearch.sidebar.loading")}
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-8 text-center text-[11px] text-muted-foreground/70">
        <AlertTriangle className="size-5 text-destructive/70" />
        {t("autoResearch.sidebar.loadError")}
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="inline-flex items-center gap-1 text-primary hover:underline"
        >
          <Repeat className="size-3" />
          {t("common.retry")}
        </button>
      </div>
    )
  }

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-8 text-center text-[11px] text-muted-foreground/60">
        <Search className="size-5 text-muted-foreground/40" />
        {t("autoResearch.sidebar.noResults")}
        <button
          type="button"
          onClick={onClearFilters}
          className="text-primary hover:underline"
        >
          {t("common.clear")}
        </button>
      </div>
    )
  }

  return (
    <div className="mb-2">
      <div className="flex items-center h-7 px-2 text-muted-foreground/80">
        <SectionLabel className="flex-1">
          {t("autoResearch.sidebar.searchResults")}
        </SectionLabel>
        <span className="text-[10px] text-muted-foreground/50">
          {sessions.length}
        </span>
      </div>
      <div className="mt-0.5 space-y-0.5">
        {sessions.map((session) => (
          <SessionRow
            key={session.id}
            session={session}
            folders={folders}
            currentFolderId={session.folder_id}
            isActive={session.id === activeSessionId}
            draggable={false}
            onSelect={onSelectSession}
            onRename={onRenameSession}
            onMove={onMoveSession}
            onDelete={onDeleteSession}
            onSwitchProfile={onSwitchProfile}
          />
        ))}
        {query.hasNextPage && (
          <LoadMoreRow
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          />
        )}
      </div>
    </div>
  )
}

interface SessionRowProps {
  session: ResearchSessionItem
  folders: ResearchFolderItem[]
  currentFolderId: string | null
  isActive: boolean
  /** 本行是否正被拖动（降低不透明度）。 */
  dragging?: boolean
  /** 是否允许拖动（检索结果视图关闭）。 */
  draggable?: boolean
  onDragStart?: (drag: { id: string; folderId: string | null }) => void
  onDragEnd?: () => void
  onSelect: (id: string) => void
  onRename: (s: ResearchSessionItem) => void
  onMove: (id: string, folderId: string | null) => Promise<void> | void
  onDelete: (s: ResearchSessionItem) => void
  onSwitchProfile: (s: ResearchSessionItem, target: string) => void
}

function statusDot(status: string) {
  return sessionDotClasses(status)
}

function SessionRow({
  session,
  folders,
  currentFolderId,
  isActive,
  dragging,
  draggable,
  onDragStart,
  onDragEnd,
  onSelect,
  onRename,
  onMove,
  onDelete,
  onSwitchProfile,
}: SessionRowProps) {
  const { t } = useTranslation()
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: 拖放为指针增强；行内已有 button 承载选择/键盘可达性
    <div
      draggable={draggable}
      onDragStart={
        draggable
          ? (e) => {
              e.dataTransfer.effectAllowed = "move"
              // 需要设置数据 payload，否则 Firefox 不触发拖拽。
              e.dataTransfer.setData("text/plain", session.id)
              onDragStart?.({ id: session.id, folderId: currentFolderId })
            }
          : undefined
      }
      onDragEnd={draggable ? () => onDragEnd?.() : undefined}
      className={cn(
        "group relative flex items-center rounded-md h-8 text-xs transition-all duration-200",
        draggable && "cursor-grab active:cursor-grabbing",
        dragging && "opacity-40",
        isActive
          ? "bg-primary/10 border border-primary/40 text-primary"
          : "border border-transparent text-foreground/80 hover:bg-accent/60 hover:border-border/40 hover:text-foreground",
      )}
    >
      <button
        type="button"
        onClick={() => onSelect(session.id)}
        className="flex-1 min-w-0 flex items-center gap-2 pl-3 pr-1 h-full text-left cursor-pointer"
      >
        <span
          className={cn(
            "size-1.5 rounded-full shrink-0",
            statusDot(session.status),
          )}
          title={t(`autoResearch.status.${session.status}`, session.status)}
        />
        <span className="flex-1 truncate" title={session.title}>
          {session.title}
        </span>
        {session.best_objective != null && (
          <span
            className="shrink-0 text-[10px] font-mono tabular-nums text-emerald-500/90"
            title={t("autoResearch.state.bestObjective")}
          >
            {session.best_objective}
          </span>
        )}
      </button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-6 opacity-0 group-hover:opacity-100 focus:opacity-100 text-muted-foreground hover:text-primary"
            title={t("autoResearch.sidebar.sessionOptions")}
            aria-label={t("autoResearch.sidebar.sessionOptions")}
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem onSelect={() => onRename(session)}>
            {t("autoResearch.sidebar.renameSession")}
          </DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              {t("autoResearch.sidebar.moveTo")}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-44">
              <DropdownMenuItem
                disabled={currentFolderId === null}
                onSelect={() => onMove(session.id, null)}
              >
                {t("autoResearch.sidebar.moveToUngrouped")}
              </DropdownMenuItem>
              {folders.length > 0 && <DropdownMenuSeparator />}
              {folders.map((folder) => (
                <DropdownMenuItem
                  key={folder.id}
                  disabled={folder.id === currentFolderId}
                  onSelect={() => onMove(session.id, folder.id)}
                >
                  {folder.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Repeat className="size-3.5 mr-2" />
              {t("autoResearch.sidebar.switchProfile")}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-44">
              {PROFILE_OPTIONS.map((p) => (
                <DropdownMenuItem
                  key={p}
                  disabled={p === session.profile}
                  onSelect={() => onSwitchProfile(session, p)}
                >
                  {t(`autoResearch.profile.${p}`)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-destructive focus:text-destructive"
            onSelect={() => onDelete(session)}
          >
            {t("autoResearch.sidebar.deleteSession")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
