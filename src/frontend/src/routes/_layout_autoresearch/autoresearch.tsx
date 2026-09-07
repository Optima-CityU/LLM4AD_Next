import { createFileRoute } from "@tanstack/react-router"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type { ResearchSessionItem, ResearchSessionStatus } from "@/client"
import ArtifactsPanel from "@/components/AutoResearch/ArtifactsPanel"
import ChatPanel from "@/components/AutoResearch/ChatPanel"
import CreateSessionDialog from "@/components/AutoResearch/CreateSessionDialog"
import HeaderSessionSwitcher from "@/components/AutoResearch/HeaderSessionSwitcher"
import SessionSidebar from "@/components/AutoResearch/SessionSidebar"
import { TechPanel } from "@/components/AutoResearch/tech"
import {
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

  const handleRenameSession = async (id: string, title: string) => {
    try {
      await updateSessionMut.mutateAsync({
        sessionId: id,
        body: { title },
      })
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
      throw err
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

  const handleSwitchProfile = async (id: string, profile: string) => {
    try {
      await updateSessionMut.mutateAsync({
        sessionId: id,
        body: { profile },
      })
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? "error"
      toast.error(detail)
      throw err
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
              onRenameSession={handleRenameSession}
              onMoveSession={handleMoveSession}
              onDeleteSession={handleDeleteSession}
              onSwitchProfile={handleSwitchProfile}
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
