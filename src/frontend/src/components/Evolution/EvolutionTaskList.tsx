import { zodResolver } from "@hookform/resolvers/zod"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Circle,
  Clock,
  Copy,
  FileArchive,
  FolderOpen,
  Layers3,
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
  type ExampleTemplateConfigItem,
  type ExampleTemplateItem,
  Llm4AdAutodesignService,
  Llm4AdPapersService,
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
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
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
import {
  useAutoDesignSessions,
  useCreateAutoDesignSession,
} from "@/hooks/useAutoDesign"
import { autoDesignKeys } from "@/lib/autoDesign"
import { taskKeys } from "@/lib/task-queries"
import { cn, formatDateTime } from "@/lib/utils"
import { handleError } from "@/utils"
import {
  getTemplateCaseKey,
  getTemplateGroupKey,
  humanizeConfigName,
  isExampleTemplateGroup,
  splitExampleTemplates,
} from "./exampleTemplateGroups"
import ImportTaskForm from "./ImportTaskForm"

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
  })
}
type NameFormData = {
  name: string
}

type CreateTaskFormData = {
  name: string
  template_name?: string
  config_name?: string
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
  const [isImporting, setIsImporting] = useState(false)
  const [buildMode, setBuildMode] = useState<
    "manual" | "import" | "autodesign" | "ai"
  >(isDemo ? "ai" : "manual")
  const navigate = useNavigate()
  const createDesign = useCreateAutoDesignSession()
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
  const templateBuckets = useMemo(
    () => splitExampleTemplates(templateList?.templates ?? []),
    [templateList?.templates],
  )

  const form = useForm<CreateTaskFormData>({
    resolver: zodResolver(createTaskSchema) as any,
    mode: "onBlur",
    defaultValues: {
      // Pre-fill the task name in the demo so the user can land on a single
      // click; real projects start blank so the user has to think about it.
      name: isDemo ? "TSP Heuristic" : "",
      template_name: undefined,
      config_name: undefined,
    },
  })

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
  const selectedTemplateIsGroup = selectedTemplate
    ? isExampleTemplateGroup(selectedTemplate)
    : false

  const templateDisplayName = (template: ExampleTemplateItem) => {
    const groupKey = getTemplateGroupKey(template.name)
    return groupKey ? t(`evolution.templateGroups.${groupKey}`) : template.name
  }

  const configDisplayName = (
    templateName: string,
    config: ExampleTemplateConfigItem,
  ) => {
    const caseKey = getTemplateCaseKey(templateName, config.name)
    return caseKey
      ? t(`evolution.templateCases.${caseKey}`)
      : humanizeConfigName(config.name)
  }

  const renderTemplateOption = (
    template: ExampleTemplateItem,
    index: number,
    grouped: boolean,
  ) => {
    const configCount = template.configs?.length ?? 0
    return (
      <Tooltip key={template.name}>
        <TooltipTrigger asChild>
          <SelectItem
            value={template.name}
            textValue={templateDisplayName(template)}
          >
            <span className="flex w-full min-w-0 items-center gap-2">
              {grouped ? (
                <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Layers3 className="size-3.5" />
                </span>
              ) : (
                <span className="w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground/60">
                  {index + 1}.
                </span>
              )}
              <span className="truncate">{templateDisplayName(template)}</span>
              {grouped && (
                <span className="ml-auto shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary/80">
                  {t("evolution.templateCaseCount", { count: configCount })}
                </span>
              )}
            </span>
          </SelectItem>
        </TooltipTrigger>
        <TooltipContent
          side="right"
          sideOffset={8}
          className="max-w-xs space-y-1.5 p-3"
        >
          <p className="text-xs font-medium">
            {grouped
              ? t("evolution.templateCaseCount", { count: configCount })
              : t("evolution.templateConfigCount", { count: configCount })}
          </p>
          {template.configs?.map((config) => {
            const description = isZh
              ? (config.description_zh ?? config.description_en)
              : (config.description_en ?? config.description_zh)
            return (
              <div
                key={config.name}
                className="text-[11px] leading-relaxed opacity-90"
              >
                <span className="font-medium">
                  {configDisplayName(template.name, config)}
                </span>
                {description && (
                  <span className="opacity-75">
                    {" — "}
                    {description}
                  </span>
                )}
              </div>
            )
          })}
        </TooltipContent>
      </Tooltip>
    )
  }

  useEffect(() => {
    if (effectiveConfigName && effectiveConfigName !== selectedConfigName) {
      form.setValue("config_name", effectiveConfigName)
    }
  }, [effectiveConfigName, selectedConfigName, form])

  const handleBuildModeChange = (
    mode: "manual" | "import" | "autodesign" | "ai",
  ) => {
    setBuildMode(mode)
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
      setBuildMode(isDemo ? "ai" : "manual")
      setDemoPhase("configuring")
      onCreated(DEMO_TASK)
      return
    }
    const payload: TaskCreate = {
      name: data.name,
      project_id: projectId,
      language: i18n.language?.startsWith("zh") ? "zh" : "en",
      ai_built: false,
    }
    if (data.template_name) {
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
    if (buildMode === "autodesign") {
      createDesign.mutate(
        { title: data.name, project_id: projectId },
        {
          onSuccess: (session) => {
            setIsOpen(false)
            form.reset()
            setBuildMode(isDemo ? "ai" : "manual")
            void navigate({
              to: "/evolution",
              search: { projectId, designSessionId: session.id },
            })
          },
          onError: () => showErrorToast(t("autodesign.sessionFailed")),
        },
      )
      return
    }
    if (!data.template_name) {
      setConfirmNoTemplateOpen(true)
      return
    }
    submitTask(data)
  }

  const buildModeSelector = (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="mb-2 text-sm font-medium leading-none">
        {t("evolution.buildModeLabel")}
      </legend>
      <div
        className={cn(
          "grid gap-3",
          isDemo ? "sm:grid-cols-2" : "sm:grid-cols-3",
        )}
      >
        {(
          [
            {
              mode: "manual",
              icon: Settings2,
              title: "buildModeManual",
              description: "buildModeManualDesc",
            },
            {
              mode: isDemo ? "ai" : "autodesign",
              icon: Bot,
              title: "buildModeAi",
              description: isDemo
                ? "buildModeAiDesc"
                : "buildModeAutoDesignDesc",
            },
            ...(!isDemo
              ? [
                  {
                    mode: "import" as const,
                    icon: FileArchive,
                    title: "buildModeImport",
                    description: "buildModeImportDesc",
                  },
                ]
              : []),
          ] as const
        ).map(({ mode, icon: Icon, title, description }) => {
          const selected = buildMode === mode
          return (
            <button
              key={mode}
              type="button"
              aria-pressed={selected}
              disabled={
                isImporting || mutation.isPending || createDesign.isPending
              }
              onClick={() => handleBuildModeChange(mode)}
              className={cn(
                "relative flex min-w-0 flex-col items-start gap-1.5 rounded-lg border-2 p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                selected
                  ? "border-primary bg-primary/5 shadow-sm shadow-primary/10"
                  : "border-border/60 hover:border-primary/40 hover:bg-accent/30",
              )}
            >
              <div
                className={cn(
                  "flex items-center gap-2 pr-5",
                  mode === "import" && "shrink-0 pr-0",
                )}
              >
                <span
                  className={cn(
                    "flex size-6 shrink-0 items-center justify-center rounded-md",
                    selected
                      ? "bg-primary/15 text-primary"
                      : "bg-muted text-muted-foreground",
                  )}
                >
                  <Icon className="size-3.5" />
                </span>
                <span
                  className={cn(
                    "text-sm font-medium",
                    selected ? "text-primary" : "text-foreground",
                  )}
                >
                  {t(`evolution.${title}`)}
                </span>
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {t(`evolution.${description}`)}
              </p>
              {selected && (
                <span className="absolute right-2 top-2 flex size-4 items-center justify-center rounded-full bg-primary">
                  <CheckCircle2 className="size-3 text-primary-foreground" />
                </span>
              )}
            </button>
          )
        })}
      </div>
    </fieldset>
  )

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (isImporting || mutation.isPending || createDesign.isPending) return
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
        className={cn(
          "max-h-[90dvh] sm:max-w-2xl",
          buildMode === "import"
            ? "flex flex-col overflow-hidden"
            : "overflow-y-auto",
        )}
        preventOutsideClose
      >
        <DialogHeader className="shrink-0">
          <DialogTitle>{t("evolution.createTaskTitle")}</DialogTitle>
          <DialogDescription>
            {t("evolution.createTaskDescription")}
          </DialogDescription>
        </DialogHeader>
        {buildMode === "import" ? (
          <ImportTaskForm
            projectId={projectId}
            name={form.watch("name")}
            onNameChange={(name) =>
              form.setValue("name", name, { shouldDirty: true })
            }
            buildModeSelector={buildModeSelector}
            onPendingChange={setIsImporting}
            onClose={() => {
              setIsOpen(false)
              setBuildMode(isDemo ? "ai" : "manual")
              form.reset()
            }}
            onImported={(task) => {
              queryClient.invalidateQueries({
                queryKey: getTasksQueryKey(projectId),
              })
              setIsOpen(false)
              setBuildMode(isDemo ? "ai" : "manual")
              form.reset()
              onCreated(task)
            }}
          />
        ) : (
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

                {buildModeSelector}

                {buildMode === "autodesign" || buildMode === "ai" ? (
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {t(
                      buildMode === "ai"
                        ? "evolution.buildModeAiTemplateHint"
                        : "autodesign.creationHint",
                    )}
                  </p>
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
                            <SelectContent className="w-[min(32rem,calc(100vw-2rem))]">
                              {templateBuckets.groups.length > 0 && (
                                <SelectGroup>
                                  <SelectLabel className="flex items-center gap-2 py-2 font-medium text-foreground/70">
                                    <Layers3 className="size-3.5 text-primary" />
                                    {t("evolution.templateGroupLabel")}
                                  </SelectLabel>
                                  {templateBuckets.groups.map(
                                    (template, index) =>
                                      renderTemplateOption(
                                        template,
                                        index,
                                        true,
                                      ),
                                  )}
                                </SelectGroup>
                              )}
                              {templateBuckets.groups.length > 0 &&
                                templateBuckets.standalone.length > 0 && (
                                  <SelectSeparator />
                                )}
                              {templateBuckets.standalone.length > 0 && (
                                <SelectGroup>
                                  <SelectLabel className="py-2 font-medium text-foreground/70">
                                    {t("evolution.standaloneTemplateLabel")}
                                  </SelectLabel>
                                  {templateBuckets.standalone.map(
                                    (template, index) =>
                                      renderTemplateOption(
                                        template,
                                        index,
                                        false,
                                      ),
                                  )}
                                </SelectGroup>
                              )}
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
                            <FormLabel>
                              {selectedTemplateIsGroup
                                ? t("evolution.caseLabel")
                                : t("evolution.configLabel")}
                            </FormLabel>
                            <Select
                              value={effectiveConfigName ?? ""}
                              onValueChange={(v) => field.onChange(v)}
                            >
                              <FormControl>
                                <SelectTrigger className="w-full">
                                  <SelectValue>
                                    {effectiveConfigItem
                                      ? configDisplayName(
                                          selectedTemplateName,
                                          effectiveConfigItem,
                                        )
                                      : undefined}
                                  </SelectValue>
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
                                          {configDisplayName(
                                            selectedTemplateName,
                                            cfg,
                                          )}
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
                                      {configDisplayName(
                                        selectedTemplateName,
                                        cfg,
                                      )}
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
              </div>
              <DialogFooter>
                <DialogClose asChild>
                  <Button
                    variant="outline"
                    disabled={mutation.isPending || createDesign.isPending}
                  >
                    {t("common.cancel")}
                  </Button>
                </DialogClose>
                <LoadingButton
                  type="submit"
                  data-tour="create-task-submit"
                  loading={mutation.isPending || createDesign.isPending}
                  className="bg-primary/20 text-primary border border-primary/40 hover:bg-primary/30"
                >
                  {t(
                    buildMode === "autodesign"
                      ? "autodesign.enterAgent"
                      : "common.create",
                  )}
                </LoadingButton>
              </DialogFooter>
            </form>
          </Form>
        )}
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

type TaskListTarget = {
  id: string
  name: string
  kind: "task" | "design"
}

type TaskListItem = TaskListTarget & { createdTime: string } & (
    | { kind: "task"; task: TaskResponse }
    | { kind: "design" }
  )

function TaskListActions({
  onRename,
  onDelete,
  onCopy,
}: {
  onRename: () => void
  onDelete: () => void
  onCopy?: () => void
}) {
  const { t } = useTranslation()
  const actions = [
    ...(onCopy
      ? [
          {
            label: t("evolution.copyTask"),
            Icon: Copy,
            action: onCopy,
            destructive: false,
          },
        ]
      : []),
    {
      label: t("evolution.renameTask"),
      Icon: Pencil,
      action: onRename,
      destructive: false,
    },
    {
      label: t("evolution.deleteTask"),
      Icon: Trash2,
      action: onDelete,
      destructive: true,
    },
  ]

  return (
    <>
      <div className="ml-auto hidden md:flex items-center gap-0.5 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
        {actions.map(({ label, Icon, action, destructive }) => (
          <button
            key={label}
            type="button"
            className={cn(
              "p-1 rounded text-muted-foreground",
              destructive
                ? "hover:bg-red-500/20 hover:text-red-400"
                : "hover:bg-accent/60 hover:text-primary",
            )}
            title={label}
            aria-label={label}
            onClick={(event) => {
              event.stopPropagation()
              action()
            }}
          >
            <Icon className="size-3" />
          </button>
        ))}
      </div>
      <div className="ml-auto md:hidden">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="p-1 rounded text-muted-foreground hover:text-primary hover:bg-accent/60"
              title={t("common.more")}
              aria-label={t("common.more")}
              onClick={(event) => event.stopPropagation()}
            >
              <MoreVertical className="size-3" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            sideOffset={4}
            onClick={(event) => event.stopPropagation()}
          >
            {actions
              .filter((action) => !action.destructive)
              .map(({ label, Icon, action }) => (
                <DropdownMenuItem
                  key={label}
                  className="text-xs cursor-pointer"
                  onSelect={action}
                >
                  <Icon className="size-3.5 mr-2" />
                  {label}
                </DropdownMenuItem>
              ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-xs cursor-pointer text-red-400 focus:text-red-400 focus:bg-red-500/10"
              onSelect={onDelete}
            >
              <Trash2 className="size-3.5 mr-2" />
              {t("evolution.deleteTask")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </>
  )
}

function RenameTaskDialog({
  task,
  open,
  onOpenChange,
  projectId,
  onRenamed,
}: {
  task: TaskListTarget
  open: boolean
  onOpenChange: (open: boolean) => void
  projectId: string
  onRenamed: (name: string) => void
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
    mutationFn: async (data: NameFormData) => {
      if (task.kind === "design") {
        await Llm4AdAutodesignService.operate({
          sessionId: task.id,
          requestBody: { operation: "rename", arguments: { name: data.name } },
        })
      } else {
        await Llm4AdTasksService.updateTask({
          taskId: task.id,
          requestBody: { name: data.name },
        })
      }
    },
    onSuccess: (_data, variables) => {
      onRenamed(variables.name)
      showSuccessToast(t("evolution.renameSuccess"))
      onOpenChange(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: getTasksQueryKey(projectId) })
      void queryClient.invalidateQueries({ queryKey: autoDesignKeys.all })
      if (task.kind === "task") {
        void queryClient.invalidateQueries({
          queryKey: taskKeys.detail(task.id),
        })
        void queryClient.invalidateQueries({ queryKey: ["taskTree"] })
      }
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
    designSessionId,
  } = useEvolution()
  const navigate = useNavigate()
  const designs = useAutoDesignSessions(
    projectId,
    Boolean(projectId) && !isDemoProjectId(projectId),
  )
  const drafts = (designs.data?.items ?? []).filter(
    (session) => !session.task_id,
  )
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
  const entries: TaskListItem[] = [
    ...drafts.map(
      (session): TaskListItem => ({
        id: session.id,
        name: session.title,
        kind: "design",
        createdTime: session.created_time,
      }),
    ),
    ...items.map(
      (task): TaskListItem => ({
        id: task.id,
        name: task.name,
        kind: "task",
        createdTime: task.created_time,
        task,
      }),
    ),
  ]
  const [renameTask, setRenameTask] = useState<TaskListTarget | null>(null)
  const [copyTaskId, setCopyTaskId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<TaskListTarget | null>(null)

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

  const selectEntry = (entry: TaskListItem | null, replace = false) => {
    setSelectedTask(entry?.kind === "task" ? entry.task : null)
    void navigate({
      to: "/evolution",
      search:
        entry?.kind === "design"
          ? { projectId, designSessionId: entry.id }
          : { projectId, taskId: entry?.id },
      replace,
    })
  }

  const deleteMutation = useMutation({
    mutationFn: async (target: TaskListTarget) => {
      if (target.kind === "design") {
        await Llm4AdPapersService.deleteWorkspace({ workspaceId: target.id })
      } else {
        await Llm4AdTasksService.deleteTask({ taskId: target.id })
      }
    },
    onSuccess: (_data, target) => {
      showSuccessToast(t("evolution.deleteSuccess"))
      const isSelected =
        target.kind === "design"
          ? designSessionId === target.id
          : !designSessionId && selectedTask?.id === target.id
      if (isSelected) {
        const index = entries.findIndex(
          (entry) => entry.kind === target.kind && entry.id === target.id,
        )
        const remaining = entries.filter(
          (entry) => entry.kind !== target.kind || entry.id !== target.id,
        )
        selectEntry(
          remaining[Math.min(index, remaining.length - 1)] ?? null,
          true,
        )
      }
      queryClient.removeQueries({
        queryKey: autoDesignKeys.all,
        predicate: (query) => query.queryKey.includes(target.id),
      })
      void queryClient.invalidateQueries({
        queryKey: getTasksQueryKey(projectId!),
      })
      void queryClient.invalidateQueries({ queryKey: autoDesignKeys.all })
    },
    onError: handleError.bind(showErrorToast),
  })

  const selectedTaskRef = useRef(selectedTask)
  selectedTaskRef.current = selectedTask

  useEffect(() => {
    if (!projectId || isTasksLoading || designSessionId) return
    const current = selectedTaskRef.current
    if (items.length > 0) {
      if (!current || current.project_id !== projectId) {
        setSelectedTask(items[0])
      } else {
        const updated = items.find((t) => t.id === current.id)
        if (
          updated &&
          (updated.status !== current.status || updated.name !== current.name)
        ) {
          setSelectedTask(updated)
        }
      }
    } else {
      setSelectedTask(null)
    }
  }, [projectId, items, isTasksLoading, setSelectedTask, designSessionId])

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
        {entries.length === 0 ? (
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
          entries.map((entry) => {
            const isActive =
              entry.kind === "design"
                ? designSessionId === entry.id
                : !designSessionId && selectedTask?.id === entry.id
            const baseStatus =
              entry.kind === "task"
                ? (entry.task.active_status ?? entry.task.status)
                : "uninitialized"
            const displayStatus =
              entry.kind === "task" && isActive ? effectiveStatus : baseStatus
            const status =
              statusConfig[displayStatus] ?? statusConfig.uninitialized
            const StatusIcon = entry.kind === "design" ? Bot : status.icon
            const openEntry = () => {
              selectEntry(entry)
              if (window.innerWidth < AUTO_COLLAPSE_WIDTH)
                setLeftCollapsed(true)
            }

            return (
              <div
                key={`${entry.kind}:${entry.id}`}
                data-tour={
                  entry.kind === "task" && entry.id === DEMO_TASK.id
                    ? "demo-task-row"
                    : undefined
                }
                className={cn(
                  "group flex flex-col gap-1 px-3 py-2.5 rounded-md transition-all duration-200 border",
                  isActive
                    ? "bg-primary/10 border-primary/40 shadow-[0_0_12px] shadow-primary/15"
                    : "border-transparent hover:bg-accent/60 hover:border-border/40",
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 items-center gap-2.5 text-left"
                  onClick={openEntry}
                >
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
                    {entry.name}
                  </span>
                  <span
                    className="text-[10px] shrink-0 px-1.5 py-0.5 rounded"
                    style={{ color: status.color, background: status.bg }}
                  >
                    {entry.kind === "design"
                      ? t("autodesign.designInProgress")
                      : status.label}
                  </span>
                </button>
                <div className="flex items-center pl-6">
                  <span className="text-[10px] text-muted-foreground/60">
                    {formatDateTime(entry.createdTime)}
                  </span>
                  <TaskListActions
                    onCopy={
                      entry.kind === "task"
                        ? () => setCopyTaskId(entry.id)
                        : undefined
                    }
                    onRename={() => setRenameTask(entry)}
                    onDelete={() => setDeleteTarget(entry)}
                  />
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
          onRenamed={(name) => {
            if (
              renameTask.kind === "task" &&
              selectedTask?.id === renameTask.id
            ) {
              setSelectedTask({ ...selectedTask, name })
            }
          }}
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
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleteTarget(null)
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
              <Button variant="outline" disabled={deleteMutation.isPending}>
                {t("common.cancel")}
              </Button>
            </DialogClose>
            <LoadingButton
              loading={deleteMutation.isPending}
              className="bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30"
              onClick={() => {
                if (deleteTarget)
                  deleteMutation.mutate(deleteTarget, {
                    onSuccess: () => setDeleteTarget(null),
                  })
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
