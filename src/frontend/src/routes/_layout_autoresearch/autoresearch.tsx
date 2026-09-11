import { createFileRoute } from "@tanstack/react-router"
import {
  ChevronLeft,
  ChevronRight,
  DownloadCloud,
  Loader2,
  Repeat,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchSessionItem, ResearchSessionStatus } from "@/client"
import ArtifactsPanel from "@/components/AutoResearch/ArtifactsPanel"
import ChatPanel from "@/components/AutoResearch/ChatPanel"
import CreateSessionDialog from "@/components/AutoResearch/CreateSessionDialog"
import EditSessionDialog from "@/components/AutoResearch/EditSessionDialog"
import HeaderSessionSwitcher from "@/components/AutoResearch/HeaderSessionSwitcher"
import SessionSidebar from "@/components/AutoResearch/SessionSidebar"
import { TechPanel } from "@/components/AutoResearch/tech"
import {
  AlertDialog,
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
  downloadResearchArtifactsArchive,
  useCopyResearchSession,
  useCreateResearchFolder,
  useDeleteResearchFolder,
  useDeleteResearchSession,
  useResearchFolders,
  useResearchSessionDetail,
  useUpdateResearchFolder,
  useUpdateResearchSession,
} from "@/hooks/useAutoResearch"
import { useAutoResearchHeader } from "@/hooks/useAutoResearchHeader"
import { usePersistentState } from "@/hooks/usePersistentState"
import { cn } from "@/lib/utils"

const LEFT_PANEL_WIDTH = 264
const RIGHT_PANEL_WIDTH = 384
const RIGHT_PANEL_MIN_WIDTH = 300

export const Route = createFileRoute("/_layout_autoresearch/autoresearch")({
  component: AutoResearchPage,
  head: () => ({
    meta: [{ title: "AutoResearch - LLM4AD_Next" }],
  }),
})

/** 三栏主页面：会话侧栏 + 对话主区 + 产物面板占位。 */
function AutoResearchPage() {
  const { t } = useTranslation()
  const [activeSessionId, setActiveSessionId] = usePersistentState<
    string | null
  >("autoresearch:activeSessionId", null)
  const [createOpen, setCreateOpen] = useState(false)
  const [createInitialFolder, setCreateInitialFolder] = useState<string | null>(
    null,
  )

  // 会话检索：关键词（debounce 后走服务端 ILIKE）+ 状态筛选（服务端）。
  const [search, setSearch] = useState("")
  const [debouncedQ, setDebouncedQ] = useState("")
  const [statusFilter, setStatusFilter] = useState<Set<ResearchSessionStatus>>(
    new Set(),
  )
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(search.trim()), 300)
    return () => clearTimeout(id)
  }, [search])
  const toggleStatus = (s: ResearchSessionStatus) =>
    setStatusFilter((prev) => {
      const next = new Set(prev)
      if (next.has(s)) next.delete(s)
      else next.add(s)
      return next
    })

  const foldersQ = useResearchFolders()
  const createFolderMut = useCreateResearchFolder()
  const updateFolderMut = useUpdateResearchFolder()
  const deleteFolderMut = useDeleteResearchFolder()
  const updateSessionMut = useUpdateResearchSession()
  const deleteSessionMut = useDeleteResearchSession()
  const copySessionMut = useCopyResearchSession()

  const folders = useMemo(() => foldersQ.data?.items ?? [], [foldersQ.data])
  // 未分组会话数：来自 folders 响应，供未分组分组头部计数 + 空态判定。
  const ungroupedCount = foldersQ.data?.ungrouped_session_count ?? 0

  // 当前会话独立取详情——会话列表已改为分页懒加载，主区不再从扁平列表兜底，
  // 完全依赖详情查询确定选中会话（脱离侧栏分页/检索状态）。
  const activeDetailQ = useResearchSessionDetail(activeSessionId)
  const activeSession = activeDetailQ.data?.session ?? null

  // 仅在详情确定会话不可用（404 已删除 / 403 无权限）时回退到 null；
  // 瞬时 500 / 网络错误保留选中，交由 React Query 自动重试。
  useEffect(() => {
    if (!activeSessionId) return
    const status = (activeDetailQ.error as { status?: number } | null)?.status
    if (status === 404 || status === 403) setActiveSessionId(null)
  }, [activeSessionId, activeDetailQ.error, setActiveSessionId])

  // 记录激活会话所属文件夹（持久化）：刷新后侧栏据此默认展开该文件夹，
  // 从而只加载「未分组第一页 + 激活文件夹第一页」，其余文件夹保持折叠不请求。
  const [activeFolderId, setActiveFolderId] = usePersistentState<string | null>(
    "autoresearch:activeFolderId",
    null,
  )
  useEffect(() => {
    const s = activeDetailQ.data?.session
    if (s) setActiveFolderId(s.folder_id ?? null)
  }, [activeDetailQ.data?.session, setActiveFolderId])

  const openCreate = useCallback((folderId: string | null) => {
    setCreateInitialFolder(folderId)
    setCreateOpen(true)
  }, [])

  const handleCreated = (session: ResearchSessionItem) => {
    setActiveSessionId(session.id)
  }

  const handleCreateFolder = async (name: string) => {
    try {
      await createFolderMut.mutateAsync({ name })
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        t("autoResearch.sidebar.folderExists")
      toast.error(detail)
      throw err
    }
  }

  const handleRenameFolder = async (id: string, name: string) => {
    try {
      await updateFolderMut.mutateAsync({
        folderId: id,
        body: { name },
      })
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
      throw err
    }
  }

  const handleDeleteFolder = async (id: string) => {
    try {
      await deleteFolderMut.mutateAsync(id)
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
    }
  }

  const handleMoveSession = async (id: string, folderId: string | null) => {
    try {
      await updateSessionMut.mutateAsync({
        sessionId: id,
        // 显式带 folder_id 键（含 null），backend PATCH 语义据此判定移动
        body: { folder_id: folderId },
      })
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
    }
  }

  // 返回值即「是否已生效」：编辑弹框据此决定保存后是关闭还是留在原地。
  const handleSwitchProfile = async (
    s: ResearchSessionItem,
    profile: string,
  ) => {
    try {
      await updateSessionMut.mutateAsync({
        sessionId: s.id,
        body: { profile },
      })
      return true
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
      return false
    }
  }

  const handleDeleteSession = async (id: string) => {
    try {
      await deleteSessionMut.mutateAsync(id)
      if (activeSessionId === id) setActiveSessionId(null)
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        t("autoResearch.sidebar.sessionRunning")
      toast.error(detail)
    }
  }

  // 复制会话：副本沿用原标题/画像/运行状态但开启全新生命周期（新 UUID、stream_id 清空）；
  // 成功后跳转到副本并在侧栏可见。副本与原会话标题相同，靠列表上的展开区分。
  // 复制是不可逆的落盘操作，先经二次确认。
  const [copyTarget, setCopyTarget] = useState<ResearchSessionItem | null>(null)
  const [copyBusy, setCopyBusy] = useState(false)
  const handleCopySession = async (id: string) => {
    try {
      const copy = await copySessionMut.mutateAsync(id)
      setCopyTarget(null)
      setActiveSessionId(copy.id)
      const title = copy.title || "?"
      toast.success(t("autoResearch.sidebar.copySessionSuccess", { title }))
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
    } finally {
      setCopyBusy(false)
    }
  }

  // 编辑弹框：待编辑会话 + 跨类 profile 切换的挂起请求（等用户在确认框里拍板）。
  const [editTarget, setEditTarget] = useState<ResearchSessionItem | null>(null)
  const [switchTarget, setSwitchTarget] = useState<ResearchSessionItem | null>(
    null,
  )
  const [pendingProfile, setPendingProfile] = useState<string>("")
  const [switchDownloading, setSwitchDownloading] = useState(false)
  const [switching, setSwitching] = useState(false)
  // 确认框的 resolve：由它的按钮回调兑现，让编辑弹框能 await 用户的决定。
  const switchResolve = useRef<((ok: boolean) => void) | null>(null)

  /**
   * 跨类 profile 切换的二次确认：弹「清空产物」警告（带打包下载入口），
   * 用户确认才真正提交。返回 true = 已切换成功，false = 放弃或失败。
   */
  const confirmSwitchProfile = (
    session: ResearchSessionItem,
    profile: string,
  ) =>
    new Promise<boolean>((resolve) => {
      switchResolve.current = resolve
      setPendingProfile(profile)
      setSwitchTarget(session)
    })

  const settleSwitch = (ok: boolean) => {
    setSwitchTarget(null)
    setPendingProfile("")
    const resolve = switchResolve.current
    switchResolve.current = null
    resolve?.(ok)
  }

  const [leftCollapsed, setLeftCollapsed] = usePersistentState(
    "autoresearch:leftCollapsed",
    false,
  )
  const [rightCollapsed, setRightCollapsed] = usePersistentState(
    "autoresearch:rightCollapsed",
    false,
  )

  // 侧栏收起时，把会话/分组切换器注入顶栏 logo 右侧；展开时清空。
  const { setHeaderLeft } = useAutoResearchHeader()
  useEffect(() => {
    if (!leftCollapsed) {
      setHeaderLeft(null)
      return
    }
    setHeaderLeft(
      <HeaderSessionSwitcher
        folders={folders}
        ungroupedCount={ungroupedCount}
        activeSession={activeSession}
        onSelectSession={setActiveSessionId}
        onCreateSession={openCreate}
      />,
    )
    return () => setHeaderLeft(null)
  }, [
    leftCollapsed,
    folders,
    ungroupedCount,
    activeSession,
    setHeaderLeft,
    setActiveSessionId,
    openCreate,
  ])

  // 右侧面板宽度可拖拽调整（逻辑对齐 evolution 右侧栏）。拖拽手柄=右折叠条：
  // 拖动改宽、点击（未越过阈值）折叠/展开；宽度持久化，窗口缩放时夹到屏宽一半内。
  const [rightWidth, setRightWidth] = usePersistentState(
    "autoresearch:rightWidth",
    RIGHT_PANEL_WIDTH,
  )
  const [isResizingRight, setIsResizingRight] = useState(false)

  const handleRightControlMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      const startX = e.clientX
      const DRAG_THRESHOLD = 4
      const wasCollapsed = rightCollapsed
      let didDrag = false

      const onMove = (ev: MouseEvent) => {
        if (wasCollapsed) return
        if (!didDrag && Math.abs(ev.clientX - startX) > DRAG_THRESHOLD) {
          didDrag = true
          setIsResizingRight(true)
          document.body.style.userSelect = "none"
          document.body.style.cursor = "ew-resize"
        }
        if (didDrag) {
          const next = window.innerWidth - ev.clientX
          const max = Math.floor(window.innerWidth / 2)
          setRightWidth(Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(max, next)))
        }
      }

      const onUp = () => {
        document.removeEventListener("mousemove", onMove)
        document.removeEventListener("mouseup", onUp)
        if (didDrag) {
          setIsResizingRight(false)
          document.body.style.userSelect = ""
          document.body.style.cursor = ""
        } else {
          setRightCollapsed((v) => !v)
        }
      }

      document.addEventListener("mousemove", onMove)
      document.addEventListener("mouseup", onUp)
    },
    [rightCollapsed, setRightCollapsed, setRightWidth],
  )

  // 窗口缩放时把右侧宽度夹到屏宽一半内。
  useEffect(() => {
    const onResize = () => {
      const max = Math.floor(window.innerWidth / 2)
      setRightWidth((w) => Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(max, w)))
    }
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [setRightWidth])

  return (
    <div className="flex h-full min-h-0">
      {/* 左：会话侧栏（TechPanel 框住，可折叠） */}
      <aside
        className="shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out"
        style={{ width: leftCollapsed ? 0 : LEFT_PANEL_WIDTH }}
        {...(leftCollapsed ? { inert: true } : {})}
      >
        <div className="h-full" style={{ width: LEFT_PANEL_WIDTH }}>
          <TechPanel className="h-full flex flex-col bg-muted/60 dark:bg-background/50">
            <SessionSidebar
              folders={folders}
              loading={foldersQ.isLoading}
              foldersError={foldersQ.isError}
              onRetryFolders={() => foldersQ.refetch()}
              ungroupedCount={ungroupedCount}
              activeFolderId={activeFolderId}
              debouncedSearch={debouncedQ}
              search={search}
              onSearchChange={setSearch}
              statusFilter={statusFilter}
              onToggleStatus={toggleStatus}
              onClearStatus={() => setStatusFilter(new Set())}
              activeSessionId={activeSessionId}
              onSelectSession={setActiveSessionId}
              onCreateSession={openCreate}
              onCreateFolder={handleCreateFolder}
              onRenameFolder={handleRenameFolder}
              onDeleteFolder={handleDeleteFolder}
              onEditSession={(s) => setEditTarget(s)}
              onMoveSession={handleMoveSession}
              onDeleteSession={handleDeleteSession}
              onCopySession={(s) => setCopyTarget(s)}
              onSwitchProfile={(s, profile) => {
                void confirmSwitchProfile(s, profile)
              }}
            />
          </TechPanel>
        </div>
      </aside>

      {/* 左折叠开关 */}
      <CollapseToggle
        side="left"
        collapsed={leftCollapsed}
        onToggle={() => setLeftCollapsed((v) => !v)}
        label={t("autoResearch.sidebar.title")}
      />

      {/* 中：对话主区（重点区）。两主题同款布局——留外边距 + 圆角 + 边框，作为浮起的
          卡片；仅背景不同：浅色纯白、深色沿用主色渐变。 */}
      <main className="flex-1 min-w-0 overflow-hidden p-2">
        <TechPanel
          showCorners={false}
          className="h-full flex flex-col rounded-xl border border-border/60 bg-card dark:bg-linear-to-b dark:from-primary/[0.07] dark:via-card/50 dark:to-background/40"
        >
          <ChatPanel
            session={activeSession}
            onCreateSession={() => openCreate(null)}
          />
        </TechPanel>
      </main>

      {/* 右侧拖拽手柄：拖动改宽 · 点击折叠/展开（对齐 evolution 右侧栏） */}
      <button
        type="button"
        onMouseDown={handleRightControlMouseDown}
        aria-label={
          rightCollapsed
            ? t("evolution.expandPanel")
            : t("evolution.collapsePanel")
        }
        title={
          rightCollapsed
            ? t("autoResearch.tabs.artifacts")
            : t("autoResearch.sidebar.dragOrClick", {
                defaultValue: "拖动调整宽度 · 点击收起",
              })
        }
        className={cn(
          `group relative z-20 shrink-0 flex items-center justify-center w-5
          border-l border-r text-muted-foreground/70
          hover:bg-primary/10 hover:text-primary hover:border-primary/30
          transition-colors`,
          "border-r-border/40 border-l-transparent",
          isResizingRight && "bg-primary/15 border-primary/50 text-primary",
          rightCollapsed ? "cursor-pointer" : "cursor-ew-resize",
        )}
      >
        <span className="grid size-5 place-items-center rounded-full border border-border/50 bg-card/80 shadow-sm transition-colors group-hover:border-primary/40 group-hover:bg-primary/10">
          {rightCollapsed ? (
            <ChevronLeft className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </span>
      </button>

      {/* 右：产物 / 最优解面板（可折叠 + 可拖拽调宽） */}
      <aside
        className={cn(
          "shrink-0 overflow-hidden",
          !isResizingRight && "transition-[width] duration-300 ease-in-out",
        )}
        style={{ width: rightCollapsed ? 0 : rightWidth }}
        {...(rightCollapsed ? { inert: true } : {})}
      >
        <div className="h-full" style={{ width: rightWidth }}>
          <TechPanel className="h-full flex flex-col bg-muted/60 dark:bg-background/50">
            <ArtifactsPanel
              session={activeSession}
              rightCollapsed={rightCollapsed}
            />
          </TechPanel>
        </div>
      </aside>

      <CreateSessionDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        folders={folders}
        initialFolderId={createInitialFolder}
        onCreated={handleCreated}
      />

      {/* 编辑会话：字段与新建弹框同款，但没有模板选择与「创建并运行」。 */}
      <EditSessionDialog
        open={!!editTarget}
        onOpenChange={(o) => !o && setEditTarget(null)}
        session={editTarget}
        folders={folders}
        onProfileSwitchRequired={confirmSwitchProfile}
      />

      {/* 切换实验类型二次确认：警告将清空第 9 步之后产物 + 打包下载入口。
          侧栏菜单与编辑弹框共用：前者 fire-and-forget，后者 await 结果。 */}
      <Dialog
        open={!!switchTarget}
        onOpenChange={(open) => {
          if (!open) settleSwitch(false)
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
                  target: t(`autoResearch.profile.${pendingProfile}`),
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
                void downloadResearchArtifactsArchive(switchTarget.id)
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
              onClick={() => settleSwitch(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              disabled={switching}
              onClick={async () => {
                if (!switchTarget || !pendingProfile) return
                setSwitching(true)
                try {
                  const ok = await handleSwitchProfile(
                    switchTarget,
                    pendingProfile,
                  )
                  settleSwitch(ok)
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

      {/* 复制会话二次确认：复制会深度拷贝 DB + 落盘产物，先确认再执行。 */}
      <AlertDialog
        open={!!copyTarget}
        onOpenChange={(o) => !o && !copyBusy && setCopyTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("autoResearch.sidebar.copySession")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("autoResearch.sidebar.copySessionConfirm", {
                title: copyTarget?.title ?? "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={copyBusy}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <Button
              disabled={copyBusy}
              onClick={() => {
                if (!copyTarget) return
                setCopyBusy(true)
                void handleCopySession(copyTarget.id)
              }}
            >
              {copyBusy && <Loader2 className="size-4 animate-spin" />}
              {copyBusy
                ? t("autoResearch.sidebar.copying", {
                    defaultValue: "复制中...",
                  })
                : t("common.confirm")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** 侧栏折叠竖条开关（对齐演化页的 w-6 折叠条）。 */
function CollapseToggle({
  side,
  collapsed,
  onToggle,
  label,
}: {
  side: "left" | "right"
  collapsed: boolean
  onToggle: () => void
  label: string
}) {
  // 展开时箭头指向「收起」方向；折叠时指向「展开」方向
  const pointLeft = side === "left" ? !collapsed : collapsed
  const Icon = pointLeft ? ChevronLeft : ChevronRight
  return (
    <button
      type="button"
      onClick={onToggle}
      title={label}
      aria-label={label}
      className={cn(
        `group relative z-20 shrink-0 flex items-center justify-center w-5
        border-l border-r text-muted-foreground/70
        hover:bg-primary/10 hover:text-primary hover:border-primary/30
        transition-colors`,
        // 朝向侧栏的一侧保留边框（侧栏与中间区的分隔线），另一侧默认透明、hover 才显现
        side === "left"
          ? "border-l-border/40 border-r-transparent"
          : "border-r-border/40 border-l-transparent",
      )}
    >
      {/* 常显小圆片承载箭头：静默态淡显、hover 高亮，点击目标更明确 */}
      <span className="grid size-5 place-items-center rounded-full border border-border/50 bg-card/80 shadow-sm transition-colors group-hover:border-primary/40 group-hover:bg-primary/10">
        <Icon className="size-3.5" />
      </span>
    </button>
  )
}
