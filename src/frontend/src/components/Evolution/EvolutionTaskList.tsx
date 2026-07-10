import { zodResolver } from "@hookform/resolvers/zod"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Circle,
  Clock,
  Copy,
  Database,
  FolderOpen,
  Loader2,
  MoreVertical,
  Pencil,
  Plus,
  Settings2,
  Trash2,
  X,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import {
  Llm4AdTasksService,
  type TaskCreate,
  type TaskResponse,
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
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { DEMO_TASK, isDemoProjectId } from "@/data/demoFixtures"
import useCustomToast from "@/hooks/useCustomToast"
import { getDemoState, setDemoPhase, useDemoState } from "@/hooks/useDemoMode"
import { useEvolution } from "@/hooks/useEvolution"
import { formatDateTime } from "@/lib/utils"
import { handleError } from "@/utils"
import { authFetch } from "@/utils/auth"
import type { MemoryConfig } from "@/components/Memory/types"

const AUTO_COLLAPSE_WIDTH = 1600

export function getTasksQueryKey(projectId: string) {
  return ["listTasks", projectId]
}

export function getTasksQueryOptions(projectId: string) {
  return {
    queryFn: () =>
      Llm4AdTasksService.listTasks({ projectId, skip: 0, limit: 100 }),
    queryKey: getTasksQueryKey(projectId),
    enabled: !!projectId,
  }
}

const TASKS_PAGE_SIZE = 30

export function getTasksInfiniteQueryKey(projectId: string) {
  return ["listTasks", projectId, "infinite"] as const
}

export function useInfiniteTasks(projectId: string) {
  const isDemo = isDemoProjectId(projectId)
  // The demo task only "exists" once the user has clicked New Task. Subscribe
  // to the demo phase so the list refreshes the moment the simulation starts.
  // Real projects keep their original query key intact so external callers
  // (TaskVersionTree, useCopyBeforeRun) can still setQueryData on it.
  const demoState = useDemoState()
  return useInfiniteQuery({
    queryKey: isDemo
      ? [...getTasksInfiniteQueryKey(projectId), demoState.phase]
      : getTasksInfiniteQueryKey(projectId),
    queryFn: ({ pageParam = 0 }) => {
      if (isDemo) {
        const taskExists = getDemoState().phase !== "uninitialized"
        return Promise.resolve({
          items: taskExists && pageParam === 0 ? [DEMO_TASK] : [],
          total: taskExists ? 1 : 0,
        })
      }
      return Llm4AdTasksService.listTasks({
        projectId,
        skip: pageParam,
        limit: TASKS_PAGE_SIZE,
      })
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((acc, p) => acc + p.items.length, 0)
      return loaded < lastPage.total ? loaded : undefined
    },
    enabled: !!projectId,
    staleTime: 30_000,
  })
}

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

function getNameSchema(t: (key: string) => string) {
  return z.object({
    name: z
      .string()
      .min(1, { message: t("validation.taskNameRequired") })
      .max(255),
  })
}

function getCreateTaskSchema(t: (key: string) => string) {
  return getNameSchema(t).extend({
    template_name: z.string().optional(),
    config_name: z.string().optional(),
    memory_enabled: z.boolean().default(true),
    include_user_memory: z.boolean().default(true),
    include_project_memory: z.boolean().default(true),
    include_task_memory: z.boolean().default(true),
    user_memory_limit: z.coerce.number().int().min(0).default(5),
    project_memory_limit: z.coerce.number().int().min(0).default(5),
    task_memory_limit: z.coerce.number().int().min(0).default(5),
  })
}
type NameFormData = {
  name: string
}

type CreateTaskFormData = {
  name: string
  template_name?: string
  config_name?: string
  memory_enabled: boolean
  include_user_memory: boolean
  include_project_memory: boolean
  include_task_memory: boolean
  user_memory_limit: number
  project_memory_limit: number
  task_memory_limit: number
}

export function CreateTaskDialog({
  projectId,
  onCreated,
  variant = "compact",
}: {
  projectId: string
  onCreated: (task: TaskResponse) => void
  variant?: "compact" | "large" | "iconOnly"
}) {
  const isDemo = isDemoProjectId(projectId)
  const [isOpen, setIsOpen] = useState(false)
  const [confirmNoTemplateOpen, setConfirmNoTemplateOpen] = useState(false)
  // In the demo, default the dialog to AI build so the walkthrough lands on
  // the path it actually simulates. Real projects keep manual as default.
  const [buildMode, setBuildMode] = useState<"ai" | "manual">(
    isDemo ? "ai" : "manual",
  )
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t, i18n } = useTranslation()
  const isZh = i18n.language?.startsWith("zh")
  const createTaskSchema = getCreateTaskSchema(t)

  const { data: templateList } = useQuery({
    queryKey: ["listExampleTemplates"],
    queryFn: () => Llm4AdTasksService.listExampleTemplates(),
    enabled: isOpen,
  })
  const { data: projectMemoryConfig } = useQuery<MemoryConfig>({
    queryKey: ["projectMemoryConfig", projectId],
    queryFn: async () => {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await authFetch(
        `${baseUrl}/api/v1/llm4ad/memory/projects/${projectId}/config`,
      )
      if (!response.ok) throw new Error("Failed to load memory config")
      return response.json()
    },
    enabled: isOpen && !!projectId && !isDemo,
  })

  const form = useForm<CreateTaskFormData>({
    resolver: zodResolver(createTaskSchema) as any,
    mode: "onBlur",
    defaultValues: {
      // Pre-fill the task name in the demo so the user can land on a single
      // click; real projects start blank so the user has to think about it.
      name: isDemo ? "TSP Heuristic" : "",
      template_name: undefined,
      config_name: undefined,
      memory_enabled: true,
      include_user_memory: true,
      include_project_memory: true,
      include_task_memory: true,
      user_memory_limit: 5,
      project_memory_limit: 5,
      task_memory_limit: 5,
    },
  })

  const isAiMode = buildMode === "ai"
  const memoryEnabled = form.watch("memory_enabled")
  const memoryRuntimeAvailable = Boolean(
    projectMemoryConfig?.system_runtime_available && projectMemoryConfig?.mindmemos_binding_id,
  )

  const selectedTemplateName = form.watch("template_name")
  const selectedConfigName = form.watch("config_name")
  const selectedTemplate = templateList?.templates?.find(
    (tpl) => tpl.name === selectedTemplateName,
  )
  const configOptions = selectedTemplate?.configs ?? []
  const effectiveConfigName = configOptions.some(
    (cfg) => cfg.name === selectedConfigName,
  )
    ? selectedConfigName
    : configOptions[0]?.name
  const effectiveConfigItem = configOptions.find(
    (cfg) => cfg.name === effectiveConfigName,
  )
  const effectiveConfigDesc = effectiveConfigItem
    ? isZh
      ? (effectiveConfigItem.description_zh ??
        effectiveConfigItem.description_en)
      : (effectiveConfigItem.description_en ??
        effectiveConfigItem.description_zh)
    : undefined

  useEffect(() => {
    if (effectiveConfigName && effectiveConfigName !== selectedConfigName) {
      form.setValue("config_name", effectiveConfigName)
    }
  }, [effectiveConfigName, selectedConfigName, form])

  useEffect(() => {
    if (!isOpen || !projectMemoryConfig) return
    form.setValue(
      "memory_enabled",
      Boolean(projectMemoryConfig.system_runtime_available && projectMemoryConfig.mindmemos_binding_id),
    )
    form.setValue("include_user_memory", projectMemoryConfig.include_user_memory)
    form.setValue("include_project_memory", projectMemoryConfig.include_project_memory)
    form.setValue("include_task_memory", projectMemoryConfig.include_task_memory)
    form.setValue("user_memory_limit", projectMemoryConfig.user_memory_limit)
    form.setValue("project_memory_limit", projectMemoryConfig.project_memory_limit)
    form.setValue("task_memory_limit", projectMemoryConfig.task_memory_limit)
  }, [form, isOpen, projectMemoryConfig])

  const handleBuildModeChange = (mode: "ai" | "manual") => {
    setBuildMode(mode)
    if (mode === "ai") {
      form.setValue("template_name", undefined)
      form.setValue("config_name", undefined)
    }
  }

  const mutation = useMutation({
    mutationFn: (data: TaskCreate) =>
      Llm4AdTasksService.createTask({ requestBody: data }),
    onSuccess: (data) => {
      showSuccessToast(t("evolution.createSuccess"))
      setIsOpen(false)
      onCreated(data)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: getTasksQueryKey(projectId) })
    },
  })

  const submitTask = (data: CreateTaskFormData) => {
    // Demo mode: skip the real createTask API. Whichever mode the user picked
    // (AI build / Manual stepper) we land on the simulated AI Build view —
    // demo only mocks the AI path. Setting demo phase to "configuring"
    // triggers DemoBuildView via the layout's phase listener.
    if (isDemo) {
      setIsOpen(false)
      form.reset()
      setBuildMode("ai")
      setDemoPhase("configuring")
      onCreated(DEMO_TASK)
      return
    }
    const payload: TaskCreate = {
      name: data.name,
      project_id: projectId,
      language: i18n.language?.startsWith("zh") ? "zh" : "en",
      ai_built: isAiMode,
      input_args: {
        memory: {
          enabled: data.memory_enabled && memoryRuntimeAvailable,
          type: data.memory_enabled && memoryRuntimeAvailable ? "mindmemos_cloud" : "local_yaml",
          include_user_memory: data.include_user_memory,
          include_project_memory: data.include_project_memory,
          include_task_memory: data.include_task_memory,
          user_memory_limit: data.user_memory_limit,
          project_memory_limit: data.project_memory_limit,
          task_memory_limit: data.task_memory_limit,
        },
      },
    }
    if (!isAiMode && data.template_name) {
      payload.template_name = data.template_name
      if (data.config_name) {
        payload.config_name = data.config_name
      }
    }
    mutation.mutate(payload)
  }

  const onSubmit = (data: CreateTaskFormData) => {
    // Demo mode: short-circuit every branch so the user always lands on the
    // simulated build session regardless of which mode they ticked or whether
    // they picked a template.
    if (isDemo) {
      submitTask(data)
      return
    }
    if (isAiMode) {
      submitTask(data)
      return
    }
    if (!data.template_name) {
      setConfirmNoTemplateOpen(true)
      return
    }
    submitTask(data)
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        setIsOpen(open)
        if (!open) {
          form.reset()
          setBuildMode(isDemo ? "ai" : "manual")
        }
      }}
    >
      <DialogTrigger asChild>
        {variant === "large" ? (
          <button
            type="button"
            data-tour="new-task-btn"
            className="cta-breathe flex items-center gap-2.5 px-8 py-3.5 text-base font-semibold rounded-xl
              border-2 border-primary/50 text-primary bg-primary/5
              hover:bg-primary/15 hover:border-primary/70
              transition-colors"
          >
            <Plus className="size-5" strokeWidth={2.5} />
            {t("evolution.createTask")}
          </button>
        ) : variant === "iconOnly" ? (
          <button
            type="button"
            data-tour="new-task-btn"
            className="flex items-center justify-center size-7 rounded-md
              border border-primary/30 text-primary hover:bg-primary/10 hover:border-primary/50
              transition-colors"
            title={t("evolution.createTask")}
            aria-label={t("evolution.createTask")}
          >
            <Plus className="size-3.5" />
          </button>
        ) : (
          <button
            type="button"
            data-tour="new-task-btn"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded
              border border-primary/30 text-primary hover:bg-primary/10
              transition-colors"
          >
            <Plus className="size-3.5" />
            {t("evolution.createTask")}
          </button>
        )}
      </DialogTrigger>
      <DialogContent
        data-tour="create-task-dialog"
        className="sm:max-w-lg"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{t("evolution.createTaskTitle")}</DialogTitle>
          <DialogDescription>
            {t("evolution.createTaskDescription")}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("evolution.taskName")}{" "}
                      <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={t("evolution.taskNamePlaceholder")}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Build mode selector */}
              <div className="space-y-2">
                <label className="text-sm font-medium leading-none">
                  {t("evolution.buildModeLabel")}
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => handleBuildModeChange("manual")}
                    className={`relative flex flex-col items-start gap-1.5 rounded-lg border-2 p-3 text-left transition-all
                      ${
                        !isAiMode
                          ? "border-primary bg-primary/5 shadow-sm shadow-primary/10"
                          : "border-border/60 hover:border-primary/40 hover:bg-accent/30"
                      }`}
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className={`flex items-center justify-center size-6 rounded-md
                        ${
                          !isAiMode
                            ? "bg-primary/15 text-primary"
                            : "bg-muted text-muted-foreground"
                        }`}
                      >
                        <Settings2 className="size-3.5" />
                      </div>
                      <span
                        className={`text-sm font-medium ${!isAiMode ? "text-primary" : "text-foreground"}`}
                      >
                        {t("evolution.buildModeManual")}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      {t("evolution.buildModeManualDesc")}
                    </p>
                    {!isAiMode && (
                      <div className="absolute top-2 right-2 size-4 rounded-full bg-primary flex items-center justify-center">
                        <CheckCircle2 className="size-3 text-primary-foreground" />
                      </div>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleBuildModeChange("ai")}
                    className={`relative flex flex-col items-start gap-1.5 rounded-lg border-2 p-3 text-left transition-all
                      ${
                        isAiMode
                          ? "border-primary bg-primary/5 shadow-sm shadow-primary/10"
                          : "border-border/60 hover:border-primary/40 hover:bg-accent/30"
                      }`}
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className={`flex items-center justify-center size-6 rounded-md
                        ${
                          isAiMode
                            ? "bg-primary/15 text-primary"
                            : "bg-muted text-muted-foreground"
                        }`}
                      >
                        <Bot className="size-3.5" />
                      </div>
                      <span
                        className={`text-sm font-medium ${isAiMode ? "text-primary" : "text-foreground"}`}
                      >
                        {t("evolution.buildModeAi")}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      {t("evolution.buildModeAiDesc")}
                    </p>
                    {isAiMode && (
                      <div className="absolute top-2 right-2 size-4 rounded-full bg-primary flex items-center justify-center">
                        <CheckCircle2 className="size-3 text-primary-foreground" />
                      </div>
                    )}
                  </button>
                </div>
              </div>

              {/* Template/Config — only for manual mode */}
              {isAiMode ? (
                <div className="flex items-center gap-2 rounded-md border border-primary/20 bg-primary/5 px-3 py-2.5">
                  <Bot className="size-4 text-primary shrink-0" />
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {t("evolution.buildModeAiTemplateHint")}
                  </p>
                </div>
              ) : (
                <>
                  <FormField
                    control={form.control}
                    name="template_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{t("evolution.templateLabel")}</FormLabel>
                        <Select
                          value={field.value ?? ""}
                          onValueChange={(v) => {
                            const value = v || undefined
                            field.onChange(value)
                            const tpl = value
                              ? templateList?.templates?.find(
                                  (t) => t.name === value,
                                )
                              : undefined
                            const firstCfg =
                              tpl?.configs?.[0]?.name ?? undefined
                            form.setValue("config_name", firstCfg, {
                              shouldDirty: true,
                              shouldTouch: true,
                            })
                          }}
                        >
                          <FormControl>
                            <SelectTrigger className="w-full overflow-hidden [&_[data-slot=select-value]]:flex-1 [&_[data-slot=select-value]]:min-w-0 [&_[data-slot=select-value]]:truncate">
                              <SelectValue
                                placeholder={t("evolution.selectTemplate")}
                              />
                              {field.value && (
                                <button
                                  type="button"
                                  className="shrink-0 -mr-1.5 p-0.5 rounded-sm pointer-events-auto
                                    text-muted-foreground/60 hover:text-foreground transition-colors"
                                  title={t("evolution.clearTemplate")}
                                  onPointerDown={(e) => {
                                    e.preventDefault()
                                    e.stopPropagation()
                                    field.onChange(undefined)
                                    form.setValue("config_name", undefined, {
                                      shouldDirty: true,
                                      shouldTouch: true,
                                    })
                                  }}
                                >
                                  <X className="size-3.5" />
                                </button>
                              )}
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {templateList?.templates?.map((tpl, idx) => {
                              const cfgCount = tpl.configs?.length ?? 0
                              return (
                                <Tooltip key={tpl.name}>
                                  <TooltipTrigger asChild>
                                    <SelectItem
                                      value={tpl.name}
                                      textValue={tpl.name}
                                    >
                                      <span className="flex items-center gap-2">
                                        <span className="text-muted-foreground/60 text-xs tabular-nums">
                                          {idx + 1}.
                                        </span>
                                        {tpl.name}
                                        {cfgCount > 1 && (
                                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary/80">
                                            {cfgCount} configs
                                          </span>
                                        )}
                                      </span>
                                    </SelectItem>
                                  </TooltipTrigger>
                                  <TooltipContent
                                    side="right"
                                    sideOffset={8}
                                    className="max-w-xs p-3 space-y-1.5"
                                  >
                                    <p className="font-medium text-xs">
                                      {t("evolution.templateConfigCount", {
                                        count: cfgCount,
                                      })}
                                    </p>
                                    {tpl.configs?.map((cfg) => {
                                      const desc = isZh
                                        ? (cfg.description_zh ??
                                          cfg.description_en)
                                        : (cfg.description_en ??
                                          cfg.description_zh)
                                      return (
                                        <div
                                          key={cfg.name}
                                          className="text-[11px] leading-relaxed opacity-90"
                                        >
                                          <span className="font-medium">
                                            {cfg.name}
                                          </span>
                                          {desc && (
                                            <span className="opacity-75">
                                              {" — "}
                                              {desc}
                                            </span>
                                          )}
                                        </div>
                                      )
                                    })}
                                  </TooltipContent>
                                </Tooltip>
                              )
                            })}
                          </SelectContent>
                        </Select>
                        {!field.value && (
                          <p className="text-xs text-muted-foreground">
                            {t("evolution.noTemplateHint")}
                          </p>
                        )}
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  {selectedTemplateName && configOptions.length > 0 && (
                    <FormField
                      control={form.control}
                      name="config_name"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("evolution.configLabel")}</FormLabel>
                          <Select
                            value={effectiveConfigName ?? ""}
                            onValueChange={(v) => field.onChange(v)}
                          >
                            <FormControl>
                              <SelectTrigger className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {configOptions.map((cfg) => {
                                const desc = isZh
                                  ? (cfg.description_zh ?? cfg.description_en)
                                  : (cfg.description_en ?? cfg.description_zh)
                                return desc ? (
                                  <Tooltip key={cfg.name}>
                                    <TooltipTrigger asChild>
                                      <SelectItem value={cfg.name}>
                                        {cfg.name}
                                      </SelectItem>
                                    </TooltipTrigger>
                                    <TooltipContent
                                      side="right"
                                      sideOffset={8}
                                      className="max-w-xs"
                                    >
                                      {desc}
                                    </TooltipContent>
                                  </Tooltip>
                                ) : (
                                  <SelectItem key={cfg.name} value={cfg.name}>
                                    {cfg.name}
                                  </SelectItem>
                                )
                              })}
                            </SelectContent>
                          </Select>
                          {effectiveConfigDesc && (
                            <p className="text-xs text-muted-foreground">
                              {effectiveConfigDesc}
                            </p>
                          )}
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}
                </>
              )}
              <div className="rounded-lg border bg-muted/20 p-3 space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Database className="size-4 text-primary" />
                  <div className="min-w-0 flex-1">
                    <Label className="text-sm font-medium">记忆注入</Label>
                    <p className="text-[11px] text-muted-foreground">
                      新任务会继承项目默认记忆策略，也可以在这里覆盖本次任务。
                    </p>
                  </div>
                  {projectMemoryConfig && (
                    <span
                      className={`rounded px-2 py-0.5 text-[10px] ${
                        memoryRuntimeAvailable
                          ? "bg-primary/10 text-primary"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {memoryRuntimeAvailable
                        ? "MindMemOS 可用"
                        : "沿用旧记忆"}
                    </span>
                  )}
                </div>
                <FormField
                  control={form.control}
                  name="memory_enabled"
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2 space-y-0">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          disabled={!memoryRuntimeAvailable}
                          onCheckedChange={(checked) => field.onChange(checked === true)}
                        />
                      </FormControl>
                      <FormLabel className="text-sm font-normal">使用记忆注入</FormLabel>
                    </FormItem>
                  )}
                />
                {!memoryRuntimeAvailable && (
                  <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
                    MindMemOS 服务未就绪或当前用户尚未绑定记忆模型，当前任务将沿用旧记忆模块。
                  </p>
                )}
                <div className="grid gap-3 sm:grid-cols-3">
                  {[
                    ["include_user_memory", "user_memory_limit", "用户级"],
                    ["include_project_memory", "project_memory_limit", "项目级"],
                    ["include_task_memory", "task_memory_limit", "任务级"],
                  ].map(([enabledName, limitName, label]) => (
                    <div key={enabledName} className="rounded-md border bg-background/60 p-2">
                      <FormField
                        control={form.control}
                        name={enabledName as keyof CreateTaskFormData}
                        render={({ field }) => (
                          <FormItem className="flex items-center gap-2 space-y-0">
                            <FormControl>
                              <Checkbox
                                checked={field.value as boolean}
                                disabled={!memoryEnabled || !memoryRuntimeAvailable}
                                onCheckedChange={(checked) => field.onChange(checked === true)}
                              />
                            </FormControl>
                            <FormLabel className="text-xs font-medium">{label}记忆</FormLabel>
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name={limitName as keyof CreateTaskFormData}
                        render={({ field }) => (
                          <FormItem className="mt-2">
                            <FormLabel className="text-[11px] text-muted-foreground">
                              注入数量
                            </FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                disabled={!memoryEnabled || !memoryRuntimeAvailable}
                                value={field.value as number}
                                onChange={(event) =>
                                  field.onChange(Number.parseInt(event.target.value || "0", 10))
                                }
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <LoadingButton
                type="submit"
                data-tour="create-task-submit"
                loading={mutation.isPending}
                className="bg-primary/20 text-primary border border-primary/40 hover:bg-primary/30"
              >
                {t("common.create")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>

      <AlertDialog
        open={confirmNoTemplateOpen}
        onOpenChange={setConfirmNoTemplateOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="size-5 shrink-0" />
              {t("evolution.noTemplateConfirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("evolution.noTemplateConfirmDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t("evolution.noTemplateConfirmSelect")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmNoTemplateOpen(false)
                submitTask(form.getValues())
              }}
              className="bg-amber-500 text-white hover:bg-amber-500/90 dark:bg-amber-500 dark:hover:bg-amber-500/90"
            >
              {t("evolution.noTemplateConfirmContinue")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}

function RenameTaskDialog({
  task,
  open,
  onOpenChange,
  projectId,
}: {
  task: { id: string; name: string }
  open: boolean
  onOpenChange: (open: boolean) => void
  projectId: string
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t } = useTranslation()
  const nameSchema = getNameSchema(t)

  const form = useForm<NameFormData>({
    resolver: zodResolver(nameSchema),
    mode: "onBlur",
    defaultValues: { name: task.name },
  })

  const mutation = useMutation({
    mutationFn: (data: NameFormData) =>
      Llm4AdTasksService.updateTask({
        taskId: task.id,
        requestBody: { name: data.name },
      }),
    onSuccess: () => {
      showSuccessToast(t("evolution.renameSuccess"))
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: getTasksQueryKey(projectId) })
    },
  })

  const onSubmit = (data: NameFormData) => {
    mutation.mutate(data)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>{t("evolution.renameTaskTitle")}</DialogTitle>
          <DialogDescription>
            {t("evolution.renameTaskDescription")}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("evolution.taskName")}{" "}
                      <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={t("evolution.taskNamePlaceholder")}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <LoadingButton
                type="submit"
                loading={mutation.isPending}
                className="bg-primary/20 text-primary border border-primary/40 hover:bg-primary/30"
              >
                {t("common.save")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default function EvolutionTaskList() {
  const {
    projectId,
    projectValid,
    selectedTask,
    setSelectedTask,
    setLeftCollapsed,
    effectiveStatus,
  } = useEvolution()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t } = useTranslation()
  const statusConfig = getStatusConfig(t)
  const {
    data: tasksPages,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading: isTasksLoading,
  } = useInfiniteTasks(projectId ?? "")
  const items = useMemo(
    () => tasksPages?.pages.flatMap((p) => p.items) ?? [],
    [tasksPages],
  )
  const total = tasksPages?.pages[0]?.total ?? 0
  const [renameTask, setRenameTask] = useState<{
    id: string
    name: string
  } | null>(null)
  const [copyTaskId, setCopyTaskId] = useState<string | null>(null)
  const [deleteTaskId, setDeleteTaskId] = useState<string | null>(null)

  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || !hasNextPage) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { rootMargin: "120px" },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const copyMutation = useMutation({
    mutationFn: (taskId: string) => Llm4AdTasksService.copyTask({ taskId }),
    onSuccess: (data) => {
      showSuccessToast(t("evolution.copySuccess"))
      setSelectedTask(data)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: getTasksQueryKey(projectId!) })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => Llm4AdTasksService.deleteTask({ taskId }),
    onSuccess: (_data, deletedId) => {
      showSuccessToast(t("evolution.deleteSuccess"))
      if (selectedTask?.id === deletedId) {
        const idx = items.findIndex((it) => it.id === deletedId)
        const remaining = items.filter((it) => it.id !== deletedId)
        const next =
          remaining.length === 0
            ? null
            : remaining[Math.min(idx, remaining.length - 1)]
        setSelectedTask(next)
      }
      queryClient.invalidateQueries({
        queryKey: getTasksQueryKey(projectId!),
      })
    },
    onError: handleError.bind(showErrorToast),
  })

  const selectedTaskRef = useRef(selectedTask)
  selectedTaskRef.current = selectedTask

  useEffect(() => {
    if (!projectId || isTasksLoading) return
    const current = selectedTaskRef.current
    if (items.length > 0) {
      if (!current || current.project_id !== projectId) {
        setSelectedTask(items[0])
      } else {
        const updated = items.find((t) => t.id === current.id)
        if (updated && updated.status !== current.status) {
          setSelectedTask(updated)
        }
      }
    } else {
      setSelectedTask(null)
    }
  }, [projectId, items, isTasksLoading, setSelectedTask])

  if (!projectId) return null

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60">
        <h3 className="text-sm font-semibold text-primary tracking-wider uppercase">
          {t("evolution.taskList")}
        </h3>
        {projectValid && (
          <CreateTaskDialog
            projectId={projectId}
            onCreated={(task) => {
              setSelectedTask(task)
              if (window.innerWidth < AUTO_COLLAPSE_WIDTH)
                setLeftCollapsed(true)
            }}
          />
        )}
      </div>

      {/* Task List */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1 scrollbar-thin">
        {items.length === 0 ? (
          isTasksLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="size-4 animate-spin text-muted-foreground/60" />
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <FolderOpen className="size-10 text-muted-foreground/40" />
              <p className="text-xs text-muted-foreground">
                {t("evolution.emptyTaskList")}
              </p>
            </div>
          )
        ) : (
          items.map((task) => {
            const isActive = selectedTask?.id === task.id
            const baseStatus = task.active_status ?? task.status
            const displayStatus = isActive ? effectiveStatus : baseStatus
            const status =
              statusConfig[displayStatus] ?? statusConfig.uninitialized
            const StatusIcon = status.icon

            return (
              <div
                key={task.id}
                data-tour={
                  task.id === DEMO_TASK.id ? "demo-task-row" : undefined
                }
                className={`group flex flex-col gap-1 px-3 py-2.5 rounded-md cursor-pointer
                  transition-all duration-200 border
                  ${
                    isActive
                      ? "bg-primary/10 border-primary/40 shadow-[0_0_12px] shadow-primary/15"
                      : "border-transparent hover:bg-accent/60 hover:border-border/40"
                  }`}
                onClick={() => {
                  setSelectedTask(task)
                  if (window.innerWidth < AUTO_COLLAPSE_WIDTH)
                    setLeftCollapsed(true)
                }}
              >
                {/* Row 1: icon + name + badge */}
                <div className="flex items-center gap-2.5">
                  <StatusIcon
                    className={`size-3.5 shrink-0${displayStatus === "running" ? " animate-spin" : ""}`}
                    style={{ color: status.color }}
                  />
                  <span
                    className={`text-sm truncate flex-1 ${
                      isActive ? "text-primary" : "text-muted-foreground"
                    }`}
                  >
                    {task.name}
                  </span>
                  <span
                    className="text-[10px] shrink-0 px-1.5 py-0.5 rounded"
                    style={{ color: status.color, background: status.bg }}
                  >
                    {status.label}
                  </span>
                </div>

                {/* Row 2: created time + hover actions */}
                <div className="flex items-center pl-6">
                  <span className="text-[10px] text-muted-foreground/60">
                    {formatDateTime(task.created_time)}
                  </span>
                  <div
                    className="ml-auto hidden md:flex items-center gap-0.5
                    opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-accent/60 text-muted-foreground hover:text-primary"
                      title={t("evolution.copyTask")}
                      onClick={(e) => {
                        e.stopPropagation()
                        setCopyTaskId(task.id)
                      }}
                    >
                      <Copy className="size-3" />
                    </button>
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-accent/60 text-muted-foreground hover:text-primary"
                      title={t("evolution.renameTask")}
                      onClick={(e) => {
                        e.stopPropagation()
                        setRenameTask({ id: task.id, name: task.name })
                      }}
                    >
                      <Pencil className="size-3" />
                    </button>
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-red-500/20 text-muted-foreground hover:text-red-400"
                      title={t("evolution.deleteTask")}
                      onClick={(e) => {
                        e.stopPropagation()
                        setDeleteTaskId(task.id)
                      }}
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </div>

                  {/* Kebab menu for mobile/touch devices */}
                  <div className="ml-auto md:hidden">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          type="button"
                          className="p-1 rounded text-muted-foreground hover:text-primary
                            hover:bg-accent/60"
                          title={t("common.more")}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <MoreVertical className="size-3" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent
                        align="end"
                        sideOffset={4}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <DropdownMenuItem
                          className="text-xs cursor-pointer"
                          onClick={(e) => {
                            e.stopPropagation()
                            setCopyTaskId(task.id)
                          }}
                        >
                          <Copy className="size-3.5 mr-2" />
                          {t("evolution.copyTask")}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-xs cursor-pointer"
                          onClick={(e) => {
                            e.stopPropagation()
                            setRenameTask({ id: task.id, name: task.name })
                          }}
                        >
                          <Pencil className="size-3.5 mr-2" />
                          {t("evolution.renameTask")}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-xs cursor-pointer text-red-400 focus:text-red-400 focus:bg-red-500/10"
                          onClick={(e) => {
                            e.stopPropagation()
                            setDeleteTaskId(task.id)
                          }}
                        >
                          <Trash2 className="size-3.5 mr-2" />
                          {t("evolution.deleteTask")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              </div>
            )
          })
        )}
        {items.length > 0 && (
          <div
            ref={sentinelRef}
            className="flex items-center justify-center py-3 text-[11px] text-muted-foreground/70"
          >
            {isFetchingNextPage ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 className="size-3 animate-spin" />
                {t("common.loading")}
              </span>
            ) : hasNextPage ? (
              <button
                type="button"
                onClick={() => fetchNextPage()}
                className="text-primary/80 hover:text-primary transition-colors"
              >
                {t("evolution.loadMore")}
              </button>
            ) : (
              <span>{t("evolution.allLoaded", { count: total })}</span>
            )}
          </div>
        )}
      </div>

      {/* Back to projects footer */}
      <div className="shrink-0 px-3 py-2.5 border-t border-border/60">
        <Link
          to="/projects"
          className="flex items-center gap-2 px-3 py-2 rounded-md text-xs font-medium
            text-muted-foreground hover:text-primary hover:bg-accent/60
            border border-transparent hover:border-border/40 transition-colors w-full"
        >
          <ArrowLeft className="size-3.5 shrink-0" />
          <span className="truncate">{t("evolution.backToProjects")}</span>
        </Link>
      </div>

      {/* Dialogs */}
      {renameTask && (
        <RenameTaskDialog
          task={renameTask}
          open={!!renameTask}
          onOpenChange={(open) => {
            if (!open) setRenameTask(null)
          }}
          projectId={projectId}
        />
      )}

      {/* Copy confirm dialog */}
      <Dialog
        open={!!copyTaskId}
        onOpenChange={(open) => {
          if (!open) setCopyTaskId(null)
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("evolution.copyTask")}</DialogTitle>
            <DialogDescription>
              {t("evolution.copyTaskDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{t("common.cancel")}</Button>
            </DialogClose>
            <LoadingButton
              loading={copyMutation.isPending}
              className="bg-primary/20 text-primary border border-primary/40 hover:bg-primary/30"
              onClick={() => {
                if (copyTaskId) {
                  copyMutation.mutate(copyTaskId, {
                    onSettled: () => setCopyTaskId(null),
                  })
                }
              }}
            >
              {t("evolution.confirmCopy")}
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm dialog */}
      <Dialog
        open={!!deleteTaskId}
        onOpenChange={(open) => {
          if (!open) setDeleteTaskId(null)
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("evolution.deleteTask")}</DialogTitle>
            <DialogDescription>
              {t("evolution.deleteTaskDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{t("common.cancel")}</Button>
            </DialogClose>
            <LoadingButton
              loading={deleteMutation.isPending}
              className="bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30"
              onClick={() => {
                if (deleteTaskId) {
                  deleteMutation.mutate(deleteTaskId, {
                    onSettled: () => setDeleteTaskId(null),
                  })
                }
              }}
            >
              {t("evolution.confirmDelete")}
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
