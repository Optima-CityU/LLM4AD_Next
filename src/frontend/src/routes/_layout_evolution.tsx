import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createFileRoute,
  Link,
  Outlet,
  redirect,
  useNavigate,
} from "@tanstack/react-router"
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock,
  FolderOpen,
  Loader2,
  LogOut,
  Monitor,
  Settings,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { z } from "zod"
import type { ReportType, TaskResponse, TaskStatus } from "@/client"
import { Llm4AdProjectsService, Llm4AdTasksService } from "@/client"
import LanguageToggle from "@/components/Common/LanguageToggle"
import ThemeToggle from "@/components/Common/ThemeToggle"
import AIChatFloating from "@/components/Evolution/AIChatFloating"
import ConnectionStatus from "@/components/Evolution/ConnectionStatus"
import EvolutionRightPanel, {
  CollapsedRightPanelActions,
} from "@/components/Evolution/EvolutionRightPanel"
import EvolutionTaskList, {
  CreateTaskDialog,
  useInfiniteTasks,
} from "@/components/Evolution/EvolutionTaskList"
import { resolvePanelTask } from "@/components/Evolution/task-panel-task"
import ProjectMemoryDialog from "@/components/Memory/ProjectMemoryDialog"
import ShortcutsHelp from "@/components/Evolution/ShortcutsHelp"
import TechBackground from "@/components/Evolution/TechBackground"
import TechCorner from "@/components/Evolution/TechCorner"
import TechPanel from "@/components/Evolution/TechPanel"
import DemoEvolutionTour from "@/components/Onboarding/DemoEvolutionTour"
import { getProjectIcon } from "@/components/Projects/ProjectIcons"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  buildDemoTask,
  DEMO_PROJECT,
  DEMO_TASK,
  isDemoProjectId,
  isDemoTaskId,
} from "@/data/demoFixtures"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import {
  enterDemo,
  exitDemo,
  setDemoGeneration,
  useDemoState,
} from "@/hooks/useDemoMode"
import type { SelectedNodeInfo } from "@/hooks/useEvolution"
import { EvolutionContext } from "@/hooks/useEvolution"
import { useEvolutionNodes } from "@/hooks/useEvolutionNodes"
import { usePersistentState } from "@/hooks/usePersistentState"
import {
  providersQueryOptions,
  userDefaultModelsQueryOptions,
} from "@/hooks/useProviders"
import { useTaskLogs } from "@/hooks/useTaskLogs"
import { resetTaskCaches, taskKeys } from "@/lib/task-queries"
import { getInitials } from "@/utils"
import icon from "/assets/images/logo.svg"

const searchSchema = z.object({
  projectId: z.string().optional(),
})

const LEFT_PANEL_WIDTH = 256
const AUTO_COLLAPSE_WIDTH = 1600

const TASK_STATUS_DOT: Record<string, string> = {
  uninitialized: "#6b7280",
  pending: "#f59e0b",
  running: "#00d4ff",
  completed: "#10b981",
  failed: "#ef4444",
  stopped: "#6b7280",
}

function CollapsedTaskSelector({
  projectId,
  projectValid,
  selectedTask,
  setSelectedTask,
  setLeftCollapsed,
}: {
  projectId: string
  projectValid: boolean
  selectedTask: TaskResponse | null
  setSelectedTask: (task: TaskResponse | null) => void
  setLeftCollapsed: (v: boolean) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const {
    data: tasksPages,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
  } = useInfiniteTasks(projectId)
  const items = useMemo(
    () => tasksPages?.pages.flatMap((p) => p.items) ?? [],
    [tasksPages],
  )
  const total = tasksPages?.pages[0]?.total ?? 0

  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const scrollRootRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = sentinelRef.current
    const root = scrollRootRef.current
    if (!el || !open || !hasNextPage) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { root: root ?? null, rootMargin: "60px" },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [open, hasNextPage, isFetchingNextPage, fetchNextPage])

  const selectedColor = selectedTask
    ? (TASK_STATUS_DOT[selectedTask.active_status ?? selectedTask.status] ??
      "#6b7280")
    : "#6b7280"

  return (
    <div className="flex items-center gap-1.5">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="group flex items-center gap-2 px-2.5 py-1.5 rounded-md
              border border-border/50 bg-card/60 hover:border-primary/40 hover:bg-primary/5
              transition-colors text-xs min-w-0 max-w-[260px]"
            title={selectedTask?.name}
          >
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground/80 shrink-0">
              {t("evolution.currentTask")}
            </span>
            {selectedTask ? (
              <>
                <span
                  className="size-1.5 rounded-full shrink-0"
                  style={{ backgroundColor: selectedColor }}
                />
                <span className="text-primary truncate max-w-[150px]">
                  {selectedTask.name}
                </span>
              </>
            ) : (
              <span className="text-muted-foreground italic">
                {t("evolution.selectTask")}
              </span>
            )}
            <ChevronDown className="size-3.5 text-muted-foreground/70 group-hover:text-primary shrink-0 transition-colors" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-[260px] p-0">
          <div
            ref={scrollRootRef}
            className="max-h-[400px] overflow-y-auto p-1"
          >
            <DropdownMenuLabel className="text-[10px] uppercase tracking-wider text-muted-foreground/80 flex items-center justify-between">
              <span>{t("evolution.taskList")}</span>
              {total > 0 && (
                <span className="text-muted-foreground/60 normal-case tracking-normal">
                  {items.length}/{total}
                </span>
              )}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {items.length === 0 ? (
              isLoading ? (
                <div className="flex items-center justify-center py-6">
                  <Loader2 className="size-4 animate-spin text-muted-foreground/60" />
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 py-6 px-3 text-center">
                  <FolderOpen className="size-6 text-muted-foreground/40" />
                  <span className="text-[11px] text-muted-foreground">
                    {t("evolution.emptyTaskList")}
                  </span>
                </div>
              )
            ) : (
              <>
                {items.map((task) => {
                  const status = task.active_status ?? task.status
                  const dot = TASK_STATUS_DOT[status] ?? "#6b7280"
                  const isActive = selectedTask?.id === task.id
                  return (
                    <DropdownMenuItem
                      key={task.id}
                      onSelect={() => setSelectedTask(task)}
                      className={`flex items-center gap-2 cursor-pointer ${
                        isActive ? "bg-primary/10" : ""
                      }`}
                    >
                      <span
                        className="size-1.5 rounded-full shrink-0"
                        style={{ backgroundColor: dot }}
                      />
                      <span
                        className={`flex-1 truncate text-xs ${isActive ? "text-primary font-medium" : ""}`}
                      >
                        {task.name}
                      </span>
                      {status === "running" && (
                        <Loader2 className="size-3 text-cyan-400 animate-spin shrink-0" />
                      )}
                      {status === "pending" && (
                        <Clock className="size-3 text-amber-500 shrink-0" />
                      )}
                      {status === "completed" && (
                        <CheckCircle2 className="size-3 text-emerald-500 shrink-0" />
                      )}
                      {status === "failed" && (
                        <AlertTriangle className="size-3 text-red-500 shrink-0" />
                      )}
                      {status === "uninitialized" && (
                        <Circle className="size-3 text-muted-foreground/60 shrink-0" />
                      )}
                    </DropdownMenuItem>
                  )
                })}
                <div
                  ref={sentinelRef}
                  className="flex items-center justify-center py-2 text-[10px] text-muted-foreground/70"
                >
                  {isFetchingNextPage ? (
                    <span className="inline-flex items-center gap-1.5">
                      <Loader2 className="size-3 animate-spin" />
                      {t("common.loading")}
                    </span>
                  ) : hasNextPage ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        fetchNextPage()
                      }}
                      className="text-primary/80 hover:text-primary transition-colors"
                    >
                      {t("evolution.loadMore")}
                    </button>
                  ) : (
                    <span>{t("evolution.allLoaded", { count: total })}</span>
                  )}
                </div>
              </>
            )}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
      {projectValid && (
        <CreateTaskDialog
          projectId={projectId}
          onCreated={(task) => {
            setSelectedTask(task)
            if (window.innerWidth < AUTO_COLLAPSE_WIDTH) setLeftCollapsed(true)
          }}
          variant="iconOnly"
        />
      )}
    </div>
  )
}

export const Route = createFileRoute("/_layout_evolution")({
  component: Layout,
  validateSearch: searchSchema,
  beforeLoad: async () => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
      })
    }
  },
})

function UserAvatarMenu() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  if (!user) return null

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-2 px-2 py-1 rounded-md
            hover:bg-accent/60 transition-colors border border-transparent
            hover:border-border/40 focus:outline-none"
        >
          <Avatar className="size-7">
            <AvatarFallback className="text-xs font-medium bg-primary/10 text-primary border border-primary/30">
              {getInitials(user.full_name || "U")}
            </AvatarFallback>
          </Avatar>
          <span className="text-xs text-muted-foreground max-w-[80px] truncate hidden sm:inline">
            {user.full_name}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-48">
        <DropdownMenuLabel className="font-normal px-3 py-2">
          <p className="text-sm font-medium">{user.full_name}</p>
          <p className="text-xs text-muted-foreground">{user.email}</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => navigate({ to: "/settings" })}
        >
          <Settings className="size-4 mr-2" />
          {t("layout.accountSettings")}
        </DropdownMenuItem>
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => navigate({ to: "/projects" })}
        >
          <Monitor className="size-4 mr-2" />
          {t("layout.systemManagement")}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-destructive focus:bg-destructive/15 focus:text-destructive cursor-pointer"
          onClick={logout}
        >
          <LogOut className="size-4 mr-2" />
          {t("layout.logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function Layout() {
  const { t } = useTranslation()
  const { projectId } = Route.useSearch()
  const queryClient = useQueryClient()
  const isDemo = isDemoProjectId(projectId)
  const demoState = useDemoState()
  const [selectedTask, setSelectedTaskRaw] = useState<TaskResponse | null>(null)
  const [selectedChildTaskId, setSelectedChildTaskId] = useState<string | null>(
    null,
  )

  // Sync demo session: entering /evolution with the demo project id activates
  // demo mode (covers the case where the user visits the URL directly).
  // Switching to any non-demo project clears it. Cleanup on unmount also
  // exits — but only if the same effect run started demo mode, so navigating
  // demo → real → demo doesn't trigger an extra exit on the second cleanup.
  useEffect(() => {
    if (!isDemo) {
      exitDemo()
      // Clear any lingering demo task selection so a real project never
      // briefly renders against the simulated DEMO_TASK while its first list
      // query is in flight. Only drop the selection when it actually points at
      // a demo task — clearing unconditionally would race with the task list's
      // auto-select effect (children run their effects before parents), wiping
      // a freshly auto-selected real task on a warm-cache re-entry and leaving
      // the list with nothing selected.
      setSelectedTaskRaw((prev) =>
        prev && isDemoTaskId(prev.id) ? null : prev,
      )
      setSelectedChildTaskId((prev) =>
        prev && isDemoTaskId(prev) ? null : prev,
      )
      return
    }
    enterDemo("uninitialized")
    return () => exitDemo()
  }, [isDemo])

  // Once the simulated task is created (phase ≠ uninitialized) auto-select it
  // so the right panes mount; before that we want the user to land on the
  // "no tasks yet — click New Task" empty state.
  useEffect(() => {
    if (!isDemo) return
    if (demoState.phase === "uninitialized") {
      setSelectedTaskRaw(null)
      return
    }
    setSelectedTaskRaw(buildDemoTask(demoState.phase))
    // Invalidate the cached demo task detail so views observing the query
    // re-render with the new phase-derived status.
    queryClient.invalidateQueries({ queryKey: taskKeys.detail(DEMO_TASK.id) })
    queryClient.invalidateQueries({ queryKey: ["getTask", DEMO_TASK.id] })
  }, [isDemo, demoState.phase, queryClient])

  // Running-phase animation: stream the demo's pre-baked generations one at a
  // time so the user sees the canvas come alive instead of jumping straight to
  // the final state. Stops at the last generation; the user advances to the
  // `completed` phase by clicking "Got it" on the tour tooltip.
  useEffect(() => {
    if (!isDemo || demoState.phase !== "running") return
    const TOTAL_GENS = 12
    const STEP_MS = 220
    let gen = 0
    const id = window.setInterval(() => {
      gen += 1
      if (gen > TOTAL_GENS) {
        window.clearInterval(id)
        return
      }
      setDemoGeneration(gen)
    }, STEP_MS)
    return () => window.clearInterval(id)
  }, [isDemo, demoState.phase])
  // Wrap setSelectedTask: when switching to a *different* root task, clear any
  // explicit child override in the same render batch. This prevents the
  // detail query from firing twice (once for the stale root id, once for the
  // resolved active child) when the user clicks a task in the list.
  const selectedTaskRefForSetter = useRef<TaskResponse | null>(null)
  selectedTaskRefForSetter.current = selectedTask
  const setSelectedTask = useCallback((task: TaskResponse | null) => {
    const prev = selectedTaskRefForSetter.current
    const prevRoot = prev ? prev.group_id || prev.id : null
    const nextRoot = task ? task.group_id || task.id : null
    setSelectedTaskRaw(task)
    if (prevRoot !== nextRoot) setSelectedChildTaskId(null)
  }, [])
  const [selectedNodes, setSelectedNodes] = useState<SelectedNodeInfo[]>([])
  const [leftCollapsed, setLeftCollapsed] = usePersistentState(
    "evo:leftCollapsed",
    false,
  )
  // Always show the left task panel in demo mode regardless of the user's
  // persisted preference — the walkthrough relies on the side-by-side layout.
  // The persisted preference is preserved for when the user returns to a real
  // project.
  const effectiveLeftCollapsed = isDemo ? false : leftCollapsed
  // Default collapse right panel on narrow screens (≤1280px) to give the
  // center workspace breathing room. Persists user override.
  const [rightCollapsed, setRightCollapsed] = usePersistentState(
    "evo:rightCollapsed",
    typeof window !== "undefined" && window.innerWidth <= 1280,
  )
  const RIGHT_PANEL_MIN_WIDTH = 300
  const [rightWidth, setRightWidth] = usePersistentState(
    "evo:rightWidth",
    RIGHT_PANEL_MIN_WIDTH,
  )
  const [isResizingRight, setIsResizingRight] = useState(false)

  // Unified handler for the right control: click toggles collapse, drag resizes.
  // When expanded: a mouse-down moving more than DRAG_THRESHOLD becomes a drag.
  // When collapsed: any click expands (no drag-to-grow zero-width panel).
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
          // Drag only resizes (clamped to min/max); collapse is click-only.
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
          // No drag → treat as click → toggle collapse
          setRightCollapsed((v) => !v)
        }
      }
      document.addEventListener("mousemove", onMove)
      document.addEventListener("mouseup", onUp)
    },
    [rightCollapsed, setRightCollapsed, setRightWidth],
  )

  // Clamp width on window resize so it never exceeds half-screen
  useEffect(() => {
    const onResize = () => {
      const max = Math.floor(window.innerWidth / 2)
      setRightWidth((w) => Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(max, w)))
    }
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [setRightWidth])

  // effectiveTaskId resolves the version to render. Priority:
  //   1. Explicit override (selectedChildTaskId, set by version-tree switches
  //      or copy-before-run) — kept in sync with the active child by the
  //      setSelectedTask wrapper, which clears it on root switch.
  //   2. selectedTask.active_child_id when non-null — comes from the task list
  //      response, so no extra fetch is required on initial selection.
  //   3. selectedTask.id — when active_child_id is null, the active version IS
  //      the root task itself.
  const effectiveTaskId = useMemo(() => {
    if (!selectedTask) return null
    if (selectedChildTaskId) return selectedChildTaskId
    return selectedTask.active_child_id ?? selectedTask.id
  }, [selectedChildTaskId, selectedTask])

  // Visualization state
  const [currentGeneration, setCurrentGeneration] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [activeTab, setActiveTab] = useState("overview")
  const [isConfiguring, setIsConfiguring] = useState(false)
  const [isViewingParams, setIsViewingParams] = useState(false)
  const [isViewingAiBuildHistory, setIsViewingAiBuildHistory] = useState(false)
  const [forceManualConfigTaskId, setForceManualConfigTaskId] = useState<
    string | null
  >(null)

  // Keyboard shortcuts: [ / ] toggle side panels; arrows step generation; space toggles play
  useEffect(() => {
    const isTypingTarget = (el: EventTarget | null) => {
      if (!(el instanceof HTMLElement)) return false
      const tag = el.tagName
      return (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        el.isContentEditable
      )
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target)) return
      if (e.ctrlKey || e.metaKey || e.altKey) return

      if (e.key === "[") {
        e.preventDefault()
        setLeftCollapsed((v) => !v)
      } else if (e.key === "]") {
        e.preventDefault()
        setRightCollapsed((v) => !v)
      }
    }

    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [setLeftCollapsed, setRightCollapsed])

  // Insight navigation signals (one-shot, consumed by InsightsPanel)
  const [insightActiveType, setInsightActiveType] = useState<ReportType | null>(
    null,
  )
  const [insightSelectedNodeIds, setInsightSelectedNodeIds] = useState<
    string[] | null
  >(null)
  const [insightSelectedBestNodeId, setInsightSelectedBestNodeId] = useState<
    string | null
  >(null)

  // One-shot focus request consumed by IslandGAVisualization. The nonce lets
  // the same node be re-focused (e.g. user clicks the locate button twice).
  const [focusNodeRequest, setFocusNodeRequest] = useState<{
    id: string
    nonce: number
    select: boolean
  } | null>(null)
  const [taskMemoryCreatedSignal, setTaskMemoryCreatedSignal] = useState<{
    event: Record<string, unknown>
    nonce: number
  } | null>(null)
  const [taskMemoryInjectedSignal, setTaskMemoryInjectedSignal] = useState<{
    event: Record<string, unknown>
    nonce: number
  } | null>(null)
  const requestFocusNode = useCallback((id: string, select = true) => {
    if (!id) return
    setFocusNodeRequest({ id, nonce: Date.now(), select })
  }, [])

  // Toggle a node in/out of the selection array
  const toggleSelectedNode = useCallback((node: SelectedNodeInfo) => {
    setSelectedNodes((prev) => {
      const exists = prev.some((n) => n.id === node.id)
      return exists ? prev.filter((n) => n.id !== node.id) : [...prev, node]
    })
  }, [])

  // Reset visualization state when root task changes. The child-id reset is
  // handled synchronously by setSelectedTask; here we only handle one-off
  // setup for the new task (prefetch shared queries).
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally reset only on task ID change
  useEffect(() => {
    setCurrentGeneration(0)
    setIsPlaying(false)
    setSelectedNodes([])

    if (!selectedTask) return

    // Prefetch shared provider/default-model queries once per task selection.
    queryClient.prefetchQuery(providersQueryOptions)
    queryClient.prefetchQuery(userDefaultModelsQueryOptions)
  }, [selectedTask?.id])

  // Reset visualization state when effective task changes (child switch)
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally reset only on effectiveTaskId change
  useEffect(() => {
    setCurrentGeneration(0)
    setIsPlaying(false)
    setSelectedNodes([])
    setActiveTab("overview")
    setIsViewingParams(false)
    setTaskMemoryInjectedSignal(null)
  }, [effectiveTaskId])

  // Ref for stable access to selectedTask in callbacks
  const selectedTaskRef = useRef(selectedTask)
  selectedTaskRef.current = selectedTask
  const effectiveTaskIdRef = useRef(effectiveTaskId)
  effectiveTaskIdRef.current = effectiveTaskId

  // Reset task data and refresh from server
  const handleResetTask = useCallback(() => {
    const eid = effectiveTaskIdRef.current
    if (eid) {
      resetTaskCaches(queryClient, eid, projectId)
    }
    setCurrentGeneration(0)
    setIsPlaying(false)
    setSelectedNodes([])
  }, [projectId, queryClient])

  // Update local task status from SSE status events
  const handleStatusChange = useCallback(
    (_status: string) => {
      const eid = effectiveTaskIdRef.current
      if (eid) {
        queryClient.invalidateQueries({ queryKey: taskKeys.detail(eid) })
      }
      if (projectId) {
        queryClient.invalidateQueries({ queryKey: taskKeys.tasks(projectId) })
      }
      const rootId =
        selectedTaskRef.current?.group_id || selectedTaskRef.current?.id
      if (rootId) {
        queryClient.invalidateQueries({ queryKey: taskKeys.tree(rootId) })
      }
      // Sync active_status on the selectedTask local state so the left panel
      // header reflects the latest status without waiting for the list refetch.
      // "done" is a stream-end signal, map it to "completed".
      const normalizedStatus = _status === "done" ? "completed" : _status
      if (selectedTaskRef.current && normalizedStatus) {
        setSelectedTaskRaw((prev) =>
          prev
            ? { ...prev, active_status: normalizedStatus as TaskStatus }
            : prev,
        )
      }
    },
    [projectId, queryClient],
  )

  // Fetch effective task to get its status for hooks
  const { data: effectiveTask } = useQuery({
    queryKey: taskKeys.detail(effectiveTaskId!),
    queryFn: () =>
      isDemoTaskId(effectiveTaskId)
        ? Promise.resolve(buildDemoTask(demoState.phase))
        : Llm4AdTasksService.getTask({ taskId: effectiveTaskId! }),
    enabled: !!effectiveTaskId,
  })

  const effectiveStatus =
    effectiveTask?.status ?? selectedTask?.status ?? "uninitialized"

  // Initialize isConfiguring once per effective task: uninitialized → configuring.
  // User can subsequently toggle off via UI; we don't re-derive on every render.
  const initializedTaskIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!effectiveTask || !effectiveTaskId) {
      initializedTaskIdRef.current = null
      return
    }
    if (initializedTaskIdRef.current === effectiveTaskId) return
    initializedTaskIdRef.current = effectiveTaskId
    setIsConfiguring(effectiveTask.status === "uninitialized")
    setIsViewingAiBuildHistory(false)
  }, [effectiveTask, effectiveTaskId])

  // Real evolution data from REST (terminal) / fed by SSE (active)
  const { data: evolutionData, feedGenerated } = useEvolutionNodes(
    effectiveTaskId ?? undefined,
    effectiveStatus,
    !!effectiveTaskId,
  )

  // Single SSE connection for logs + generated events
  const {
    entries: logEntries,
    isLoading: logLoading,
    error: logError,
  } = useTaskLogs(effectiveTaskId ?? "", effectiveStatus, !!effectiveTaskId, {
    onResetTask: handleResetTask,
    onStatusChange: handleStatusChange,
    onGenerated: feedGenerated,
    onMemoryCardCreated: (event) => {
      setTaskMemoryCreatedSignal({ event, nonce: Date.now() })
    },
    onMemoryInjected: (event) => {
      setTaskMemoryInjectedSignal({ event, nonce: Date.now() })
    },
  })

  // maxGeneration is always derived from the actual node data — no separate state
  const maxGeneration = evolutionData.maxGeneration

  // Auto-follow: when maxGeneration increases or task changes, jump to latest generation
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally include effectiveTaskId for task switch
  useEffect(() => {
    setCurrentGeneration(maxGeneration)
  }, [maxGeneration, effectiveTaskId])

  // setMaxGeneration kept for interface compatibility (no-op, maxGeneration is derived)
  const setMaxGeneration = useCallback((_max: number) => {}, [])

  const {
    data: project,
    isError: projectError,
    isLoading: projectLoading,
  } = useQuery({
    queryKey: ["getProject", projectId],
    queryFn: () =>
      isDemoProjectId(projectId)
        ? Promise.resolve(DEMO_PROJECT)
        : Llm4AdProjectsService.getProject({ projectId: projectId! }),
    enabled: !!projectId,
  })

  const projectValid = !!projectId && !!project && !projectError
  const projectName = project?.name

  // Project not found overlay
  if (projectId && !projectLoading && !projectValid) {
    return (
      <div className="flex flex-col h-screen items-center justify-center bg-background text-foreground">
        <div
          className="relative p-8 rounded-full mb-6"
          style={{
            background:
              "radial-gradient(circle, rgba(239,68,68,0.1) 0%, transparent 70%)",
          }}
        >
          <AlertTriangle
            className="size-20 text-red-400/60"
            style={{
              filter: "drop-shadow(0 0 16px rgba(239,68,68,0.3))",
            }}
          />
          <div
            className="absolute inset-0 rounded-full animate-ping opacity-15"
            style={{ border: "1px solid rgba(239,68,68,0.3)" }}
          />
        </div>
        <h1 className="text-2xl font-bold text-foreground mb-2">
          {t("evolution.projectNotExist")}
        </h1>
        <p className="text-sm text-muted-foreground mb-8">
          {t("evolution.projectMayBeDeleted")}
        </p>
        <Link
          to="/projects"
          className="px-6 py-2.5 rounded-lg text-sm font-medium text-primary
            border border-primary/30 hover:bg-primary/10 transition-colors"
        >
          {t("evolution.backToProjects")}
        </Link>
      </div>
    )
  }

  return (
    <EvolutionContext.Provider
      value={{
        projectId,
        projectValid,
        selectedTask,
        setSelectedTask,
        selectedChildTaskId,
        setSelectedChildTaskId,
        effectiveTaskId,
        effectiveStatus,
        currentGeneration,
        maxGeneration,
        setCurrentGeneration,
        setMaxGeneration,
        isPlaying,
        setIsPlaying,
        selectedNodes,
        setSelectedNodes,
        toggleSelectedNode,
        evolutionData,
        logEntries,
        logLoading,
        logError,
        activeTab,
        setActiveTab,
        isConfiguring,
        setIsConfiguring,
        isViewingParams,
        setIsViewingParams,
        isViewingAiBuildHistory,
        setIsViewingAiBuildHistory,
        forceManualConfigTaskId,
        setForceManualConfigTaskId,
        insightActiveType,
        setInsightActiveType,
        insightSelectedNodeIds,
        setInsightSelectedNodeIds,
        insightSelectedBestNodeId,
        setInsightSelectedBestNodeId,
        focusNodeRequest,
        requestFocusNode,
        leftCollapsed: effectiveLeftCollapsed,
        setLeftCollapsed,
        resetTaskData: handleResetTask,
        updateTaskStatus: handleStatusChange,
        taskMemoryCreatedSignal,
        taskMemoryInjectedSignal,
      }}
    >
      <div className="flex flex-col h-screen overflow-hidden bg-background text-foreground">
        {/* Particle network background — disabled in IDE tab to reduce visual noise */}
        {activeTab !== "ide" && <TechBackground />}

        {/* Scan-line overlay — disabled in IDE tab */}
        {activeTab !== "ide" && (
          <div
            className="pointer-events-none fixed inset-0 z-50 opacity-0 dark:opacity-[0.03]"
            style={{
              backgroundImage:
                "repeating-linear-gradient(0deg, transparent, transparent 2px, color-mix(in srgb, var(--primary) 10%, transparent) 2px, color-mix(in srgb, var(--primary) 10%, transparent) 4px)",
            }}
          />
        )}

        {/* Header — three-column grid. Side columns size to their content
            (auto) so the logo/task-selector and the action buttons are always
            fully visible; the center title takes the remaining space
            (minmax(0,1fr)) and truncates when tight, so it can never overlap
            either side. The title stays centered within that leftover space. */}
        <header className="relative z-10 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 sm:gap-4 px-3 sm:px-6 py-3 shrink-0 bg-background/95 backdrop-blur border-b border-border shadow-sm">
          {/* Left */}
          <div className="flex items-center gap-2 sm:gap-4 min-w-0">
            <Link
              to="/projects"
              className="flex items-center gap-2.5 group hover:opacity-80 transition-opacity min-w-0"
            >
              <div className="relative shrink-0">
                <img
                  src={icon}
                  alt="OpenLoopX"
                  className="h-8 w-auto landing-spin-periodic"
                />
                <div className="absolute inset-0 rounded-full bg-primary/20 blur-md opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              </div>
              <span className="text-base font-bold tracking-wider landing-gradient-animated shrink-0">
                OpenLoopX
              </span>
              <span className="hidden lg:inline-block text-[10px] font-medium px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20 shrink-0">
                {t("evolution.simulationTitle")}
              </span>
            </Link>
            {/* Current task selector: only when left panel collapsed (avoid duplication) */}
            {effectiveLeftCollapsed && projectId && (
              <CollapsedTaskSelector
                projectId={projectId}
                projectValid={projectValid}
                selectedTask={selectedTask}
                setSelectedTask={setSelectedTask}
                setLeftCollapsed={setLeftCollapsed}
              />
            )}
          </div>

          {/* Center: project name, absolutely centered relative to viewport.
              Width is capped so a long name never overlaps the left/right
              header columns. When the project has a description, hovering the
              title shows a tooltip card; otherwise it falls back to a native
              name tooltip. */}
          <div className="flex items-center justify-center min-w-0">
            {projectName &&
              (() => {
                const Icon = getProjectIcon(project?.icon)
                const description = project?.description?.trim()
                const titleInner = (
                  <>
                    <Icon className="size-5 shrink-0 text-primary glow-primary" />
                    <span className="text-base tracking-wide truncate text-primary glow-primary">
                      {projectName}
                    </span>
                  </>
                )

                if (!description) {
                  return (
                    <div
                      className="flex items-center gap-2 min-w-0 max-w-[min(38vw,440px)]"
                      title={projectName}
                    >
                      {titleInner}
                    </div>
                  )
                }

                return (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="flex items-center gap-2 min-w-0 max-w-[min(38vw,440px)] cursor-help">
                        {titleInner}
                      </div>
                    </TooltipTrigger>
                    <TooltipContent
                      side="bottom"
                      sideOffset={8}
                      className="max-w-[340px] space-y-1 px-3 py-2"
                    >
                      <p className="font-semibold leading-snug">
                        {projectName}
                      </p>
                      <p className="text-background/80 leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
                        {description}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                )
              })()}
          </div>

          {/* Right: actions. Sized by content (auto column) so the buttons are
              always fully visible and never overlapped by the center title. */}
          <div className="flex items-center justify-end gap-3 text-xs text-muted-foreground">
            {/* Collapsed right panel → surface its task actions in the top bar */}
            {rightCollapsed && (
              <>
                <CollapsedRightPanelActions
                  task={selectedTask}
                  onExpand={() => setRightCollapsed(false)}
                />
                <div className="w-px h-5 bg-border/40" />
              </>
            )}
            {isDemo && (
              <Link
                to="/projects"
                data-tour="demo-exit"
                className="hidden md:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
                  border border-primary/40 bg-primary/10 text-primary text-[11px] font-medium
                  uppercase tracking-wider hover:bg-primary/20 transition-colors"
                title={t("demo.exitTitle", { defaultValue: "Exit demo" })}
              >
                <span className="size-1.5 rounded-full bg-primary animate-pulse" />
                {t("demo.banner", { defaultValue: "Demo" })}
              </Link>
            )}
            {!isDemo && projectValid && projectId && (
              <ProjectMemoryDialog
                projectId={projectId}
                projectName={projectName}
              />
            )}
            <ConnectionStatus />
            <ShortcutsHelp />
            <div className="hidden sm:flex items-center gap-3">
              <LanguageToggle />
              <ThemeToggle />
              <div className="w-px h-5 bg-border/40" />
            </div>
            <UserAvatarMenu />
          </div>
        </header>

        {/* Main body: Left | Center | Right */}
        <div className="flex flex-1 min-h-0 relative z-10">
          {/* Left panel - Task List */}
          <aside
            className="shrink-0 flex flex-col overflow-hidden transition-[width] duration-300 ease-in-out bg-background/95"
            style={{
              width: effectiveLeftCollapsed ? "0px" : `${LEFT_PANEL_WIDTH}px`,
            }}
            {...(effectiveLeftCollapsed ? { inert: true } : {})}
          >
            <TechPanel className="h-full flex flex-col w-64">
              <EvolutionTaskList />
            </TechPanel>
          </aside>

          {/* Left collapse toggle — hidden in demo mode so the layout stays
              fixed at side-by-side for the walkthrough. */}
          {!isDemo && (
            <button
              type="button"
              onClick={() => setLeftCollapsed(!leftCollapsed)}
              className="group relative z-20 shrink-0 flex items-center justify-center w-6
                hover:bg-primary/10 transition-colors text-muted-foreground hover:text-primary
                border-l border-r border-border/40 hover:border-primary/30"
              title={
                leftCollapsed
                  ? t("evolution.expandTaskList")
                  : t("evolution.collapseTaskList")
              }
            >
              {leftCollapsed ? (
                <ChevronRight className="size-4" />
              ) : (
                <ChevronLeft className="size-4" />
              )}
            </button>
          )}

          {/* Center - Main Content */}
          <main className="flex-1 min-w-0 relative overflow-hidden flex flex-col">
            <TechCorner position="top-left" size={20} />
            <TechCorner position="top-right" size={20} />
            <TechCorner position="bottom-left" size={20} />
            <TechCorner position="bottom-right" size={20} />
            <div className="flex-1 min-h-0 overflow-hidden p-4">
              <Outlet />
            </div>
          </main>

          {/* Right collapse toggle — click toggles, drag resizes (when expanded) */}
          <button
            type="button"
            onMouseDown={handleRightControlMouseDown}
            aria-label={
              rightCollapsed
                ? t("evolution.expandPanel")
                : t("evolution.collapsePanel")
            }
            aria-valuemin={RIGHT_PANEL_MIN_WIDTH}
            aria-valuemax={Math.floor(window.innerWidth / 2)}
            aria-valuenow={rightCollapsed ? 0 : rightWidth}
            className={`group relative z-20 shrink-0 flex items-center justify-center w-6
              hover:bg-primary/10 transition-colors text-muted-foreground hover:text-primary
              border-l border-r border-border/40 hover:border-primary/30
              ${isResizingRight ? "bg-primary/15 border-primary/50 text-primary" : ""}
              ${rightCollapsed ? "cursor-pointer" : "cursor-ew-resize"}`}
            title={
              rightCollapsed
                ? t("evolution.expandPanel")
                : t("evolution.dragOrClickRight", {
                    defaultValue: "Drag to resize · Click to collapse",
                  })
            }
          >
            {rightCollapsed ? (
              <ChevronLeft className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </button>

          {/* Right panel */}
          <aside
            className={`relative shrink-0 flex flex-col overflow-hidden bg-background/95 ${
              isResizingRight
                ? ""
                : "transition-[width] duration-300 ease-in-out"
            }`}
            style={{
              width: rightCollapsed ? "0px" : `${rightWidth}px`,
            }}
            {...(rightCollapsed ? { inert: true } : {})}
          >
            <div
              className="h-full shrink-0"
              style={{ width: `${rightWidth}px` }}
            >
              <TechPanel className="h-full flex flex-col">
                <EvolutionRightPanel task={resolvePanelTask(selectedTask, effectiveTask)} />
              </TechPanel>
            </div>
          </aside>
        </div>

        {/* Floating AI Chat */}
        <AIChatFloating />

        {/* Demo walkthrough — only renders in demo mode */}
        {isDemo && <DemoEvolutionTour />}
      </div>
    </EvolutionContext.Provider>
  )
}
