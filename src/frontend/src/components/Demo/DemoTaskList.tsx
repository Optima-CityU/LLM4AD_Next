import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Circle,
  Clock,
  FolderOpen,
  Loader2,
  Plus,
  Settings2,
} from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { Button } from "@/components/ui/button"
import { buildDemoTask, DEMO_TASK_ID } from "@/data/demoFixtures"
import { setDemoPhase, useDemoState } from "@/hooks/useDemoMode"
import { useEvolution } from "@/hooks/useEvolution"
import { formatDateTime } from "@/lib/utils"
import { cn } from "@/lib/utils"

/**
 * Demo task list — visual twin of `EvolutionTaskList`.
 *
 * Same header (title + "+" icon trigger), same task-row layout (status
 * icon + name + status badge + timestamp), same "no tasks yet" empty
 * state. The only differences are:
 *   - the row never renders the rename/delete/copy hover actions
 *   - the create dialog locks to AI Build mode (manual is shown but
 *     non-clickable, mirroring real visual but disabled per leadership)
 *   - submitting the dialog flips the demo phase instead of calling the
 *     real createTask API
 */
function getStatusConfig(t: (key: string) => string) {
  return {
    uninitialized: {
      icon: Circle,
      color: "#6b7280",
      label: t("evolution.taskStatus.uninitialized"),
      bg: "rgba(107,114,128,0.12)",
    },
    pending: {
      icon: Clock,
      color: "#f59e0b",
      label: t("evolution.taskStatus.pending"),
      bg: "rgba(245,158,11,0.12)",
    },
    running: {
      icon: Loader2,
      color: "#00d4ff",
      label: t("evolution.taskStatus.running"),
      bg: "rgba(0,212,255,0.12)",
    },
    completed: {
      icon: CheckCircle2,
      color: "#10b981",
      label: t("evolution.taskStatus.completed"),
      bg: "rgba(16,185,129,0.12)",
    },
    failed: {
      icon: AlertTriangle,
      color: "#f87171",
      label: t("evolution.taskStatus.failed"),
      bg: "rgba(248,113,113,0.12)",
    },
  } as Record<
    string,
    { icon: typeof Circle; color: string; label: string; bg: string }
  >
}

interface NameFormData {
  name: string
}

export default function DemoTaskList() {
  const { t } = useTranslation()
  const demoState = useDemoState()
  const { selectedTask, setSelectedTask, effectiveStatus } = useEvolution()
  const [dialogOpen, setDialogOpen] = useState(false)
  const statusConfig = getStatusConfig(t)
  const taskExists = demoState.phase !== "uninitialized"

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header — mirrors EvolutionTaskList */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60">
        <h3 className="text-sm font-semibold text-primary tracking-wider uppercase">
          {t("evolution.taskList", { defaultValue: "Task List" })}
        </h3>
        <DemoCreateTaskDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          onCreated={(task) => {
            setSelectedTask(task)
          }}
        />
      </div>

      {/* Task list body */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1 scrollbar-thin">
        {!taskExists ? (
          <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
            <FolderOpen className="size-10 text-muted-foreground/40" />
            <p className="text-xs text-muted-foreground">
              {t("evolution.emptyTaskList", {
                defaultValue: "No tasks yet",
              })}
            </p>
          </div>
        ) : (
          <DemoTaskRow
            isActive={selectedTask?.id === DEMO_TASK_ID}
            displayStatus={effectiveStatus}
            statusConfig={statusConfig}
            onSelect={() => setSelectedTask(buildDemoTask(demoState.phase))}
          />
        )}
      </div>
    </div>
  )
}

function DemoTaskRow({
  isActive,
  displayStatus,
  statusConfig,
  onSelect,
}: {
  isActive: boolean
  displayStatus: string
  statusConfig: ReturnType<typeof getStatusConfig>
  onSelect: () => void
}) {
  const { t } = useTranslation()
  const status = statusConfig[displayStatus] ?? statusConfig.uninitialized
  const StatusIcon = status.icon

  // Date so the demo row's "created at" time isn't suspiciously fresh.
  const createdAt = "2026-06-17T10:00:00Z"

  return (
    <div
      data-tour="demo-task-row"
      className={cn(
        "group flex flex-col gap-1 px-3 py-2.5 rounded-md cursor-pointer transition-all duration-200 border",
        isActive
          ? "bg-primary/10 border-primary/40 shadow-[0_0_12px] shadow-primary/15"
          : "border-transparent hover:bg-accent/60 hover:border-border/40",
      )}
      onClick={onSelect}
    >
      <div className="flex items-center gap-2.5">
        <StatusIcon
          className={cn(
            "size-3.5 shrink-0",
            displayStatus === "running" && "animate-spin",
          )}
          style={{ color: status.color }}
        />
        <span
          className={cn(
            "text-sm truncate flex-1",
            isActive ? "text-primary" : "text-muted-foreground",
          )}
        >
          {t("demo.taskList.taskName", {
            defaultValue: "Demo · Greedy + Local Search",
          })}
        </span>
        <span
          className="text-[10px] shrink-0 px-1.5 py-0.5 rounded"
          style={{ color: status.color, background: status.bg }}
        >
          {status.label}
        </span>
      </div>
      <div className="flex items-center pl-6">
        <span className="text-[10px] text-muted-foreground/60">
          {formatDateTime(createdAt)}
        </span>
      </div>
    </div>
  )
}

/**
 * Demo's clone of `CreateTaskDialog` — same DialogTrigger ("+" icon
 * button in the panel header) and same body (task name + side-by-side
 * Manual/AI mode cards). The only divergences:
 *   - manual card is visually rendered but non-clickable (icon dimmed,
 *     `cursor-not-allowed`, no onClick)
 *   - submission skips createTask and flips the demo phase instead
 */
function DemoCreateTaskDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (task: ReturnType<typeof buildDemoTask>) => void
}) {
  const { t } = useTranslation()

  const form = useForm<NameFormData>({
    mode: "onBlur",
    defaultValues: {
      name: "TSP Heuristic",
    },
  })

  const handleSubmit = (data: NameFormData) => {
    if (!data.name.trim()) return
    onOpenChange(false)
    setDemoPhase("configuring")
    const task = buildDemoTask("configuring")
    onCreated({ ...task, name: data.name.trim() })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) form.reset({ name: "TSP Heuristic" })
      }}
    >
      <DialogTrigger asChild>
        <button
          type="button"
          data-tour="new-task-btn"
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded
            border border-primary/30 text-primary hover:bg-primary/10
            transition-colors"
        >
          <Plus className="size-3.5" />
          {t("evolution.createTask", { defaultValue: "New Task" })}
        </button>
      </DialogTrigger>
      <DialogContent
        data-tour="create-task-dialog"
        className="sm:max-w-lg"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>
            {t("evolution.createTaskTitle", {
              defaultValue: "Create a new task",
            })}
          </DialogTitle>
          <DialogDescription>
            {t("demo.taskList.dialogDescription", {
              defaultValue:
                "AI Build is pre-selected for the walkthrough. Click Create to continue.",
            })}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="name"
                rules={{
                  required: t("validation.taskNameRequired", {
                    defaultValue: "Task name is required",
                  }),
                  maxLength: 255,
                }}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("evolution.taskName", { defaultValue: "Task name" })}{" "}
                      <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={t("evolution.taskNamePlaceholder", {
                          defaultValue: "Enter a task name",
                        })}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Build mode selector — manual rendered but locked to AI */}
              <div className="space-y-2">
                <label className="text-sm font-medium leading-none">
                  {t("evolution.buildModeLabel", {
                    defaultValue: "Build mode",
                  })}
                </label>
                <div className="grid grid-cols-2 gap-3">
                  {/* Manual card — visible but non-interactive in demo */}
                  <div
                    aria-disabled="true"
                    className="relative flex flex-col items-start gap-1.5 rounded-lg border-2 p-3 text-left
                      border-border/60 opacity-60 cursor-not-allowed"
                    title={t("demo.taskList.manualDisabled", {
                      defaultValue:
                        "Manual mode is disabled in the walkthrough",
                    })}
                  >
                    <div className="flex items-center gap-2">
                      <div className="flex items-center justify-center size-6 rounded-md bg-muted text-muted-foreground">
                        <Settings2 className="size-3.5" />
                      </div>
                      <span className="text-sm font-medium text-foreground">
                        {t("evolution.buildModeManual", {
                          defaultValue: "Manual",
                        })}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      {t("evolution.buildModeManualDesc", {
                        defaultValue:
                          "Fill in every parameter step by step.",
                      })}
                    </p>
                  </div>

                  {/* AI card — selected, primary styling */}
                  <div
                    aria-selected="true"
                    className="relative flex flex-col items-start gap-1.5 rounded-lg border-2 p-3 text-left
                      border-primary bg-primary/5 shadow-sm shadow-primary/10"
                  >
                    <div className="flex items-center gap-2">
                      <div className="flex items-center justify-center size-6 rounded-md bg-primary/15 text-primary">
                        <Bot className="size-3.5" />
                      </div>
                      <span className="text-sm font-medium text-primary">
                        {t("evolution.buildModeAi", {
                          defaultValue: "AI Build",
                        })}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      {t("evolution.buildModeAiDesc", {
                        defaultValue:
                          "Configure through conversation with the AI.",
                      })}
                    </p>
                    <div className="absolute top-2 right-2 size-4 rounded-full bg-primary flex items-center justify-center">
                      <CheckCircle2 className="size-3 text-primary-foreground" />
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 rounded-md border border-primary/20 bg-primary/5 px-3 py-2.5">
                <Bot className="size-4 text-primary shrink-0" />
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {t("evolution.buildModeAiTemplateHint", {
                    defaultValue:
                      "AI Build doesn't need a template — the agent will guide you through every choice.",
                  })}
                </p>
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                {t("common.cancel", { defaultValue: "Cancel" })}
              </Button>
              <LoadingButton
                type="submit"
                data-tour="create-task-submit"
                loading={false}
              >
                {t("common.create", { defaultValue: "Create" })}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
