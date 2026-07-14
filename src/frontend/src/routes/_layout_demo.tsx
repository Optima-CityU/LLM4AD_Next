import { useQueryClient } from "@tanstack/react-query"
import {
  createFileRoute,
  Link,
  Outlet,
  redirect,
  useNavigate,
} from "@tanstack/react-router"
import { LogOut, Settings } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ReportType, TaskResponse, TaskStatus } from "@/client"
import LanguageToggle from "@/components/Common/LanguageToggle"
import ThemeToggle from "@/components/Common/ThemeToggle"
import DemoRightPanel from "@/components/Demo/DemoRightPanel"
import DemoTaskList from "@/components/Demo/DemoTaskList"
import TechBackground from "@/components/Evolution/TechBackground"
import TechCorner from "@/components/Evolution/TechCorner"
import TechPanel from "@/components/Evolution/TechPanel"
import DemoTour from "@/components/Onboarding/DemoTour"
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
  buildDemoTask,
  DEMO_PROJECT,
  DEMO_TASK,
  DEMO_TASK_ID,
  demoPhaseToStatus,
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
import { useTaskLogs } from "@/hooks/useTaskLogs"
import { taskKeys } from "@/lib/task-queries"
import { getInitials } from "@/utils"
import icon from "/assets/images/logo.svg"

/**
 * Top-level layout for the read-only demo walkthrough.
 *
 * Sits at `/demo` and is fully separate from `_layout_evolution`. The split
 * keeps demo logic (mocked task lifecycle, scripted AI conversation, fake
 * generation animation) out of the production evolution route, so changes
 * here can never regress real algorithm-design tasks.
 *
 * The layout owns:
 *   - the demo session lifecycle (enterDemo on mount, exitDemo on unmount)
 *   - the running-phase generation animation that streams nodes onto the canvas
 *   - the EvolutionContext.Provider so child surfaces can pull nodes/logs/
 *     selection state through the same hooks the real route uses
 */
export const Route = createFileRoute("/_layout_demo")({
  component: DemoLayout,
  beforeLoad: async ({ location }) => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
        search: { redirect: location.pathname + (location.searchStr || "") },
      })
    }
  },
})

function DemoUserAvatarMenu() {
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

function DemoLayout() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const demoState = useDemoState()

  // Activate demo session on mount, clear it on unmount. This is the single
  // source of truth: any consumer of `useDemoState()` sees `active=true` while
  // we sit on `/demo`, and never anywhere else.
  useEffect(() => {
    enterDemo("uninitialized")
    return () => exitDemo()
  }, [])

  // Selected task tracks the simulated DEMO_TASK once the user clicks
  // "create" in DemoTaskList. Before that, the left panel renders the
  // "no tasks yet" empty state.
  const [selectedTask, setSelectedTaskRaw] = useState<TaskResponse | null>(null)
  const [selectedChildTaskId, setSelectedChildTaskId] = useState<string | null>(
    null,
  )

  useEffect(() => {
    if (demoState.phase === "uninitialized") {
      setSelectedTaskRaw(null)
      return
    }
    setSelectedTaskRaw(buildDemoTask(demoState.phase))
    // Bust the demo task detail cache so any consumer reading via React Query
    // re-renders with the new phase-derived status.
    queryClient.invalidateQueries({ queryKey: taskKeys.detail(DEMO_TASK_ID) })
    queryClient.invalidateQueries({ queryKey: ["getTask", DEMO_TASK_ID] })
  }, [demoState.phase, queryClient])

  // Running-phase animation: stream the demo's pre-baked generations one at a
  // time so the user sees the canvas come alive instead of jumping straight to
  // the final state. Stops at the last generation and waits for the user to
  // advance the tour into `completed`.
  useEffect(() => {
    if (demoState.phase !== "running") return
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
  }, [demoState.phase])

  // Evolution viz state — driven by the demo phase via existing demo
  // short-circuits inside the hooks.
  const [currentGeneration, setCurrentGeneration] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [selectedNodes, setSelectedNodes] = useState<SelectedNodeInfo[]>([])
  const [activeTab, setActiveTab] = useState("overview")
  const [isConfiguring, setIsConfiguring] = useState(false)
  const [isViewingParams, setIsViewingParams] = useState(false)
  const [isViewingAiBuildHistory, setIsViewingAiBuildHistory] = useState(false)
  const [forceManualConfigTaskId, setForceManualConfigTaskId] = useState<
    string | null
  >(null)
  const [insightActiveType, setInsightActiveType] = useState<ReportType | null>(
    null,
  )
  const [insightSelectedNodeIds, setInsightSelectedNodeIds] = useState<
    string[] | null
  >(null)
  const [insightSelectedBestNodeId, setInsightSelectedBestNodeId] = useState<
    string | null
  >(null)
  const [focusNodeRequest, setFocusNodeRequest] = useState<{
    id: string
    nonce: number
    select: boolean
  } | null>(null)
  const requestFocusNode = useCallback((id: string, select = true) => {
    if (!id) return
    setFocusNodeRequest({ id, nonce: Date.now(), select })
  }, [])
  const toggleSelectedNode = useCallback((node: SelectedNodeInfo) => {
    setSelectedNodes((prev) => {
      const exists = prev.some((n) => n.id === node.id)
      return exists ? prev.filter((n) => n.id !== node.id) : [...prev, node]
    })
  }, [])

  // Wrap setSelectedTask: clearing the child override in the same render
  // batch on root-task switch matches the production behavior in
  // `_layout_evolution`.
  const selectedTaskRef = useRef<TaskResponse | null>(null)
  selectedTaskRef.current = selectedTask
  const setSelectedTask = useCallback((task: TaskResponse | null) => {
    const prev = selectedTaskRef.current
    const prevRoot = prev ? prev.group_id || prev.id : null
    const nextRoot = task ? task.group_id || task.id : null
    setSelectedTaskRaw(task)
    if (prevRoot !== nextRoot) setSelectedChildTaskId(null)
  }, [])

  // Effective task / status feeds the visualisation hooks. In demo mode the
  // "effective task" is always DEMO_TASK once the user has clicked create;
  // before that, no task is selected so the empty state shows.
  const effectiveTaskId = selectedTask ? DEMO_TASK_ID : null
  const effectiveStatus: TaskStatus = demoPhaseToStatus(demoState.phase)

  // Real hooks — they short-circuit on `isDemoTaskId` internally and return
  // fixture-derived data without touching the network.
  const { data: evolutionData, feedGenerated } = useEvolutionNodes(
    effectiveTaskId ?? undefined,
    effectiveStatus,
    !!effectiveTaskId,
  )
  const {
    entries: logEntries,
    isLoading: logLoading,
    error: logError,
  } = useTaskLogs(effectiveTaskId ?? "", effectiveStatus, !!effectiveTaskId)
  const maxGeneration = evolutionData.maxGeneration

  // Keep the playback head pinned to the latest available generation so the
  // canvas auto-follows the streaming animation.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional auto-follow on stream
  useEffect(() => {
    setCurrentGeneration(maxGeneration)
  }, [maxGeneration])

  const setMaxGeneration = useCallback((_max: number) => {
    // No-op — maxGeneration is derived from evolution data.
  }, [])
  const resetTaskData = useCallback(() => {
    setCurrentGeneration(0)
    setIsPlaying(false)
    setSelectedNodes([])
  }, [])
  const updateTaskStatus = useCallback((_status: string) => {
    // No-op — demo status is derived from the phase, not pushed externally.
  }, [])

  // _feedGenerated: hooks expose this for SSE-driven tasks to push nodes
  // back. Demo mode never streams via SSE, so reference it once to silence
  // unused-binding lint and keep the hook contract intact.
  void feedGenerated

  const contextValue = useMemo(
    () => ({
      projectId: DEMO_PROJECT.id,
      projectValid: true,
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
      // Left panel never collapses in the demo — the walkthrough relies on
      // the side-by-side layout.
      leftCollapsed: false,
      setLeftCollapsed: () => {},
      resetTaskData,
      updateTaskStatus,
      taskMemoryCreatedSignal: null,
      taskMemoryInjectedSignal: null,
    }),
    [
      selectedTask,
      setSelectedTask,
      selectedChildTaskId,
      effectiveTaskId,
      effectiveStatus,
      currentGeneration,
      maxGeneration,
      setMaxGeneration,
      isPlaying,
      selectedNodes,
      toggleSelectedNode,
      evolutionData,
      logEntries,
      logLoading,
      logError,
      activeTab,
      isConfiguring,
      isViewingParams,
      isViewingAiBuildHistory,
      forceManualConfigTaskId,
      insightActiveType,
      insightSelectedNodeIds,
      insightSelectedBestNodeId,
      focusNodeRequest,
      requestFocusNode,
      resetTaskData,
      updateTaskStatus,
    ],
  )

  // Quiet lints for fixture re-exports we keep available to children.
  void DEMO_TASK

  return (
    <EvolutionContext.Provider value={contextValue}>
      <div className="flex flex-col h-screen overflow-hidden bg-background text-foreground">
        {activeTab !== "ide" && <TechBackground />}

        {/* Header — three-column grid keeps project name absolutely centered */}
        <header className="relative z-10 grid grid-cols-[1fr_auto_1fr] items-center gap-2 sm:gap-4 px-3 sm:px-6 py-3 shrink-0 bg-background/95 backdrop-blur border-b border-border shadow-sm">
          <div className="flex items-center gap-2 sm:gap-4 min-w-0">
            <Link
              to="/projects"
              className="flex items-center gap-2.5 group hover:opacity-80 transition-opacity min-w-0"
            >
              <div className="relative shrink-0">
                <img
                  src={icon}
                  alt="LLM4AD_Next"
                  className="h-8 w-auto landing-spin-periodic"
                />
                <div className="absolute inset-0 rounded-full bg-primary/20 blur-md opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              </div>
              <span className="text-base font-bold tracking-wider landing-gradient-animated shrink-0">
                LLM4AD_Next
              </span>
              <span className="hidden lg:inline-block text-[10px] font-medium px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20 shrink-0">
                {t("evolution.simulationTitle", { defaultValue: "Simulation" })}
              </span>
            </Link>
          </div>

          <div className="flex items-center justify-center min-w-0">
            {(() => {
              const ProjectIcon = getProjectIcon(DEMO_PROJECT.icon)
              return (
                <div
                  className="flex items-center gap-2 min-w-0 max-w-[min(38vw,440px)]"
                  title={DEMO_PROJECT.name}
                >
                  <ProjectIcon className="size-5 shrink-0 text-primary glow-primary" />
                  <span className="text-base tracking-wide truncate text-primary glow-primary">
                    {t("demo.projectName", {
                      defaultValue: DEMO_PROJECT.name,
                    })}
                  </span>
                </div>
              )
            })()}
          </div>

          <div className="flex items-center justify-end gap-3 text-xs text-muted-foreground min-w-0">
            <div className="hidden sm:flex items-center gap-3">
              <LanguageToggle />
              <ThemeToggle />
              <div className="w-px h-5 bg-border/40" />
            </div>
            <DemoUserAvatarMenu />
          </div>
        </header>

        {/* Three-pane body. Left/right panels are mounted by sub-components
            via the same EvolutionContext the real evolution layout uses;
            the center pane swaps based on `demoState.phase`. */}
        <div className="flex flex-1 min-h-0 relative z-10">
          <aside className="shrink-0 flex flex-col overflow-hidden bg-background/95 w-64">
            <TechPanel className="h-full flex flex-col w-64">
              <DemoTaskList />
            </TechPanel>
          </aside>

          <main className="flex-1 min-w-0 relative overflow-hidden flex flex-col">
            <TechCorner position="top-left" size={20} />
            <TechCorner position="top-right" size={20} />
            <TechCorner position="bottom-left" size={20} />
            <TechCorner position="bottom-right" size={20} />
            <div className="flex-1 min-h-0 overflow-hidden p-4">
              <Outlet />
            </div>
          </main>

          <aside className="relative shrink-0 flex flex-col overflow-hidden bg-background/95 w-[300px]">
            <TechPanel className="h-full flex flex-col">
              <DemoRightPanel />
            </TechPanel>
          </aside>
        </div>

        <DemoTour />
      </div>
    </EvolutionContext.Provider>
  )
}
