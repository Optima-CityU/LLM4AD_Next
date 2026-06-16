import { createFileRoute } from "@tanstack/react-router"
import { Cpu, FolderOpen, PanelLeft, Plus } from "lucide-react"
import { useTranslation } from "react-i18next"
import {
  CreateTaskDialog,
  useInfiniteTasks,
} from "@/components/Evolution/EvolutionTaskList"
import TaskDetail from "@/components/Evolution/TaskDetail/TaskDetail"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import { useEvolution } from "@/hooks/useEvolution"

const AUTO_COLLAPSE_WIDTH = 1600

export const Route = createFileRoute("/_layout_evolution/evolution")({
  component: RouteComponent,
})

function RouteComponent() {
  const { t } = useTranslation()
  const {
    projectId,
    projectValid,
    selectedTask,
    setSelectedTask,
    setLeftCollapsed,
    leftCollapsed,
    effectiveTaskId,
  } = useEvolution()

  const { data: tasksPages } = useInfiniteTasks(projectId ?? "")
  const hasAnyTask = (tasksPages?.pages[0]?.total ?? 0) > 0

  if (!projectId) {
    return (
      <div className="flex flex-col items-center justify-center gap-5 h-full min-h-[400px]">
        <div
          className="relative p-6 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(0,212,255,0.08) 0%, transparent 70%)",
          }}
        >
          <Cpu
            className="size-16 text-muted-foreground/40 dark:text-[#1e3a5f]"
            style={{
              filter: "drop-shadow(0 0 12px rgba(0,212,255,0.15))",
            }}
          />
          <div
            className="absolute inset-0 rounded-full animate-pulse opacity-20"
            style={{ border: "1px solid rgba(0,212,255,0.3)" }}
          />
        </div>
        <div className="text-center space-y-2">
          <p className="text-lg font-semibold text-foreground/70 dark:text-[#4a6a8a]">
            {t("evolution.noProjectTitle")}
          </p>
          <p className="text-sm text-muted-foreground dark:text-[#2a4a6b]">
            {t("evolution.noProjectDescription")}
          </p>
        </div>
      </div>
    )
  }

  // Project valid but no tasks — show CTA
  if (projectValid && (!hasAnyTask || !selectedTask)) {
    if (!hasAnyTask) {
      return (
        <div className="flex flex-col items-center justify-center gap-6 h-full min-h-[400px]">
          <div
            className="relative p-6 rounded-full"
            style={{
              background:
                "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)",
            }}
          >
            <Plus
              className="size-16 text-primary/50 dark:text-[#00d4ff]/40"
              style={{
                filter: "drop-shadow(0 0 16px rgba(0,212,255,0.25))",
              }}
            />
            <div
              className="absolute inset-0 rounded-full animate-pulse opacity-30"
              style={{ border: "1px solid rgba(0,212,255,0.3)" }}
            />
          </div>
          <div className="text-center space-y-2">
            <p className="text-lg font-semibold text-foreground/80 dark:text-[#8ca0bc]">
              {t("evolution.firstTaskTitle")}
            </p>
            <p className="text-sm text-muted-foreground dark:text-[#4a6a8a]">
              {t("evolution.firstTaskDescription")}
            </p>
          </div>
          <CreateTaskDialog
            projectId={projectId}
            onCreated={(task) => {
              setSelectedTask(task)
              if (window.innerWidth < AUTO_COLLAPSE_WIDTH)
                setLeftCollapsed(true)
            }}
            variant="large"
          />
          <OnboardingTour
            tourId="evolution-create"
            steps={[
              {
                selector: '[data-tour="new-task-btn"]',
                title: t("tour.evolutionCreate.title"),
                content: t("tour.evolutionCreate.content"),
                placement: "bottom",
              },
            ]}
          />
        </div>
      )
    }

    // Tasks exist but none selected
    return (
      <div className="flex flex-col items-center justify-center gap-5 h-full min-h-[400px]">
        <div
          className="relative p-6 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(0,212,255,0.08) 0%, transparent 70%)",
          }}
        >
          <FolderOpen
            className="size-16 text-muted-foreground/40 dark:text-[#1e3a5f]"
            style={{
              filter: "drop-shadow(0 0 12px rgba(0,212,255,0.15))",
            }}
          />
        </div>
        <div className="text-center space-y-2">
          <p className="text-lg font-semibold text-foreground/70 dark:text-[#4a6a8a]">
            {t("evolution.selectTaskTitle")}
          </p>
          <p className="text-sm text-muted-foreground dark:text-[#2a4a6b]">
            {t("evolution.selectTaskDescription")}
          </p>
        </div>
        {leftCollapsed && (
          <button
            type="button"
            onClick={() => setLeftCollapsed(false)}
            className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg
              border border-primary/40 text-primary hover:bg-primary/10
              transition-colors"
          >
            <PanelLeft className="size-4" />
            {t("evolution.expandTaskList")}
          </button>
        )}
      </div>
    )
  }

  if (!selectedTask || !effectiveTaskId) return null

  return <TaskDetail taskId={effectiveTaskId} />
}
