import { useQuery } from "@tanstack/react-query"
import { Database, Info, Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import {
  Llm4AdMemoryService,
  type MemoryCardResponse,
  type ProjectMemoryConfigResponse,
} from "@/client"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

import {
  createTaskMemoryOnboardingPresentation,
  downgradeUnavailableLongTermMemory,
  type TaskMemoryOnboardingPhase,
} from "./memoryMode"

type MemoryValue = Record<string, unknown>

// The manual picker only browses the shared scopes (task memory is retrieved,
// not pinned). Matches the generated listMemoryCards scope literal.
type PickerScope = "user" | "project"

const CARD_PICKER_PAGE_SIZE = 10

interface MemoryConfigStepProps {
  projectId: string
  value: unknown
  onChange: (value: MemoryValue) => void
  onBack?: () => void
  onNext?: () => void
  readOnly?: boolean
}

// Enable mode is a UI-only discriminator derived from enabled + type.
type EnableMode = "none" | "temporary" | "longterm"
type RetrievalMode = "auto" | "manual"
type InjectionMode = "topk" | "weight" | "random"

const DEFAULT_TASK_LIMIT = 5
const DEFAULT_SHARED_LIMIT = 3
const DEFAULT_SCORE_THRESHOLD = 0.65

function boolValue(value: unknown, fallback: boolean) {
  return typeof value === "boolean" ? value : fallback
}

function numberValue(value: unknown, fallback: number) {
  const parsed = typeof value === "number" ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function clampUnit(value: unknown, fallback: number) {
  const parsed = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(0, Math.min(1, parsed))
}

function timeoutInputValue(value: unknown, fallback: number): number | "" {
  if (value === "") return ""
  return numberValue(value, fallback)
}

function timeoutValue(value: string): number | "" {
  if (value === "") return ""
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? Math.max(0, parsed) : ""
}

function scoreValue(value: unknown) {
  if (value === null || value === undefined || value === "") return null
  const parsed = typeof value === "number" ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function safeMemory(value: unknown): MemoryValue {
  return value && typeof value === "object" && !Array.isArray(value)
    ? { ...(value as MemoryValue) }
    : {}
}

function disabledMemory(): MemoryValue {
  return { enabled: false, type: "local_yaml" }
}

function temporaryMemory(): MemoryValue {
  return { enabled: true, type: "local_yaml" }
}

function longTermMemory(value: unknown, config?: ProjectMemoryConfigResponse): MemoryValue {
  const current = safeMemory(value)
  const rerank = boolValue(current.mindmemos_rerank, config?.mindmemos_rerank ?? false)
  const retrievalMode: RetrievalMode =
    current.retrieval_mode === "manual" ? "manual" : "auto"
  const pinsControlSharedScopes = retrievalMode === "manual"
  return {
    enabled: true,
    type: "mindmemos_cloud",
    retrieval_mode: retrievalMode,
    pinned_card_ids: stringArray(current.pinned_card_ids),
    task_injection_mode: (["topk", "weight", "random"].includes(
      String(current.task_injection_mode),
    )
      ? String(current.task_injection_mode)
      : "topk") as InjectionMode,
    task_injection_lambda: clampUnit(current.task_injection_lambda, 0.5),
    // The manual picker is the sole shared-scope selector in manual mode, so
    // persist both scopes as enabled. This also keeps future task runs clear
    // of the hidden legacy include_* flags.
    include_user_memory: pinsControlSharedScopes
      ? true
      : boolValue(current.include_user_memory, config?.include_user_memory ?? true),
    include_project_memory: pinsControlSharedScopes
      ? true
      : boolValue(current.include_project_memory, config?.include_project_memory ?? true),
    include_task_memory: true,
    user_memory_limit: numberValue(current.user_memory_limit, config?.user_memory_limit ?? DEFAULT_SHARED_LIMIT),
    project_memory_limit: numberValue(current.project_memory_limit, config?.project_memory_limit ?? DEFAULT_SHARED_LIMIT),
    task_memory_limit: numberValue(current.task_memory_limit, config?.task_memory_limit ?? DEFAULT_TASK_LIMIT),
    mindmemos_search_strategy: String(
      current.mindmemos_search_strategy || config?.mindmemos_search_strategy || "fast",
    ),
    mindmemos_rerank: rerank,
    mindmemos_score_threshold: rerank
      ? scoreValue(current.mindmemos_score_threshold ?? config?.mindmemos_score_threshold ?? DEFAULT_SCORE_THRESHOLD)
      : null,
    mindmemos_fail_open: boolValue(current.mindmemos_fail_open, config?.mindmemos_fail_open ?? true),
    mindmemos_request_timeout: numberValue(current.mindmemos_request_timeout, 300),
    mindmemos_add_timeout: numberValue(current.mindmemos_add_timeout, 300),
    mindmemos_context_char_budget: numberValue(
      current.mindmemos_context_char_budget,
      20000,
    ),
    mindmemos_elite_code_slots: Math.min(
      5,
      Math.max(0, numberValue(current.mindmemos_elite_code_slots, 1)),
    ),
    mindmemos_elite_code_char_budget: Math.max(
      0,
      numberValue(current.mindmemos_elite_code_char_budget, 12000),
    ),
    mindmemos_extraction_prompt_language: ["auto", "ZH", "EN"].includes(
      String(current.mindmemos_extraction_prompt_language),
    )
      ? String(current.mindmemos_extraction_prompt_language)
      : "auto",
  }
}

function enableModeOf(value: unknown): EnableMode {
  const memory = safeMemory(value)
  if (memory.enabled === false) return "none"
  if (memory.type === "mindmemos_cloud") return "longterm"
  return "temporary"
}

export default function MemoryConfigStep({
  projectId,
  value,
  onChange,
  onBack,
  onNext,
  readOnly = false,
}: MemoryConfigStepProps) {
  const { t } = useTranslation()
  const [tourStep, setTourStep] = useState<number | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const { data: config, isLoading, isError } = useQuery({
    queryKey: ["projectMemoryConfig", projectId],
    queryFn: () =>
      Llm4AdMemoryService.getProjectMemoryConfig({ projectId }),
    enabled: !!projectId,
  })

  const runtimeAvailable = Boolean(
    config?.system_runtime_available && config?.mindmemos_binding_id,
  )
  const tourPhase: TaskMemoryOnboardingPhase = tourStep === 2
    ? "manual"
    : tourStep === 3
      ? "injection"
      : tourStep === 4
        ? "advanced"
        : "auto"
  const isTaskMemoryTourActive = tourStep !== null
  const memory = isTaskMemoryTourActive && runtimeAvailable
    ? createTaskMemoryOnboardingPresentation(safeMemory(value), tourPhase)
    : safeMemory(value)
  const interactionLocked = readOnly || isTaskMemoryTourActive
  const enableMode = enableModeOf(memory)
  const isLongTerm = enableMode === "longterm"
  const retrievalMode: RetrievalMode = memory.retrieval_mode === "manual" ? "manual" : "auto"
  const injectionMode = String(memory.task_injection_mode || "topk") as InjectionMode
  const pinnedIds = stringArray(memory.pinned_card_ids)

  const statusLabel = runtimeAvailable
    ? t("evolution.memoryConfig.status.ready")
    : isLoading
      ? t("evolution.memoryConfig.status.checking")
      : t("evolution.memoryConfig.status.unavailable")

  // Long-term memory needs an available runtime; fall back to temporary memory
  // if a previously-selected long-term config can no longer be served.
  useEffect(() => {
    if (interactionLocked || isLoading) return
    const current = safeMemory(value)
    if (current.type === "mindmemos_cloud" && current.enabled !== false && !runtimeAvailable) {
      onChange(downgradeUnavailableLongTermMemory(current))
    }
  }, [interactionLocked, isLoading, onChange, runtimeAvailable, value])

  const updateField = (key: string, nextValue: unknown) => {
    const nextMemory = { ...longTermMemory(memory, config), [key]: nextValue }
    if (key === "retrieval_mode" && nextValue === "manual") {
      nextMemory.include_user_memory = true
      nextMemory.include_project_memory = true
    }
    if (key === "mindmemos_rerank" && nextValue !== true) {
      nextMemory.mindmemos_score_threshold = null
    }
    onChange(nextMemory)
  }

  // The dialog seeds its draft from the full flat pinned list and only edits
  // the cards visible in its own scope, so the committed array already carries
  // the other scope's pinned ids untouched.
  const commitPinned = (nextPinnedIds: string[]) => {
    updateField("pinned_card_ids", nextPinnedIds)
  }

  const sharedScopeRows = useMemo(
    () => [
      {
        enabledName: "include_user_memory",
        limitName: "user_memory_limit",
        defaultLimit: DEFAULT_SHARED_LIMIT,
        label: t("evolution.memoryConfig.scopes.user"),
      },
      {
        enabledName: "include_project_memory",
        limitName: "project_memory_limit",
        defaultLimit: DEFAULT_SHARED_LIMIT,
        label: t("evolution.memoryConfig.scopes.project"),
      },
    ],
    [t],
  )

  const injectionModes: { value: InjectionMode; label: string; help: string }[] = [
    {
      value: "topk",
      label: t("evolution.memoryConfig.injection.topk"),
      help: t("evolution.memoryConfig.injection.topkHelp"),
    },
    {
      value: "weight",
      label: t("evolution.memoryConfig.injection.weight"),
      help: t("evolution.memoryConfig.injection.weightHelp"),
    },
    {
      value: "random",
      label: t("evolution.memoryConfig.injection.random"),
      help: t("evolution.memoryConfig.injection.randomHelp"),
    },
  ]

  // Manual mode allows selecting nothing (inject no shared memory), so the
  // wizard never blocks progression on an empty selection.
  const nextDisabled = false

  return (
    <div className="flex h-full flex-col">
      <OnboardingTour
        tourId="memory-task-config"
        enabled={!isLoading}
        stepIndex={tourStep ?? 0}
        onStepIndexChange={setTourStep}
        onStepChange={setTourStep}
        steps={runtimeAvailable ? [
          {
            selector: '[data-tour="memory-mode-selector"]',
            title: t("tour.memory.taskModeTitle"),
            content: t("tour.memory.taskModeContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="memory-retrieval-auto"]',
            title: t("tour.memory.autoRetrievalTitle"),
            content: t("tour.memory.autoRetrievalContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="memory-retrieval-manual"]',
            title: t("tour.memory.manualRetrievalTitle"),
            content: t("tour.memory.manualRetrievalContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="memory-task-injection"]',
            title: t("tour.memory.taskInjectionTitle"),
            content: t("tour.memory.taskInjectionContent"),
            placement: "top",
          },
          {
            selector: '[data-tour="memory-advanced-config"]',
            title: t("tour.memory.advancedConfigTitle"),
            content: t("tour.memory.advancedConfigContent"),
            placement: "top",
          },
        ] : [
          {
            selector: '[data-tour="memory-mode-selector"]',
            title: t("tour.memory.taskModeTitle"),
            content: t("tour.memory.longTermUnavailableContent"),
            placement: "bottom",
          },
        ]}
      />
      <div className="min-h-0 flex-1 overflow-y-auto pr-1" inert={isTaskMemoryTourActive}>
        <div className="space-y-5">
          {/* Header */}
          <div className="rounded-md border bg-card/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3">
                <div className="rounded-md bg-primary/10 p-2 text-primary">
                  <Database className="size-4" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold">
                    {t("evolution.memoryConfig.title")}
                  </h3>
                  <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                    {t("evolution.memoryConfig.description")}
                  </p>
                </div>
              </div>
              <Badge variant={runtimeAvailable ? "default" : "secondary"}>
                {isLoading && <Loader2 className="mr-1 size-3 animate-spin" />}
                {statusLabel}
              </Badge>
            </div>
          </div>

          {/* Step 1: enable mode */}
          <div className="space-y-2">
            <Label className="text-sm font-medium">
              {t("evolution.memoryConfig.enable.label")}
            </Label>
            <div className="grid gap-3 md:grid-cols-3" data-tour="memory-mode-selector">
              <EnableCard
                title={t("evolution.memoryConfig.enable.none")}
                hint={t("evolution.memoryConfig.enable.noneHint")}
                selected={enableMode === "none"}
                disabled={interactionLocked}
                onSelect={() => onChange(disabledMemory())}
              />
              <EnableCard
                title={t("evolution.memoryConfig.enable.temporary")}
                hint={t("evolution.memoryConfig.enable.temporaryHint")}
                selected={enableMode === "temporary"}
                disabled={interactionLocked}
                onSelect={() => onChange(temporaryMemory())}
              />
              <EnableCard
                title={t("evolution.memoryConfig.enable.longterm")}
                hint={
                  runtimeAvailable
                    ? t("evolution.memoryConfig.enable.longtermHint")
                    : t("evolution.memoryConfig.enable.longtermUnavailable")
                }
                selected={enableMode === "longterm"}
                disabled={interactionLocked || !runtimeAvailable}
                onSelect={() => onChange(longTermMemory(memory, config))}
              />
            </div>
            {(isError || !runtimeAvailable) && !isLoading && (
              <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
                <Info className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  {isError
                    ? t("evolution.memoryConfig.configLoadFailed")
                    : t("evolution.memoryConfig.unavailable")}
                </span>
              </div>
            )}
          </div>

          {isLongTerm && (
            <>
              {/* Step 2: retrieval mode */}
              <div
                className="space-y-2"
                data-tour={retrievalMode === "auto" ? "memory-retrieval-auto" : "memory-retrieval-manual"}
              >
                <Label className="text-sm font-medium">
                  {t("evolution.memoryConfig.retrieval.label")}
                  <span className="ml-1 text-destructive">*</span>
                </Label>
                <div className="grid gap-3 md:grid-cols-2">
                  <EnableCard
                    title={t("evolution.memoryConfig.retrieval.auto")}
                    hint={t("evolution.memoryConfig.retrieval.autoHint")}
                    selected={retrievalMode === "auto"}
                    disabled={interactionLocked}
                    onSelect={() => updateField("retrieval_mode", "auto")}
                  />
                  <EnableCard
                    title={t("evolution.memoryConfig.retrieval.manual")}
                    hint={t("evolution.memoryConfig.retrieval.manualHint")}
                    selected={retrievalMode === "manual"}
                    disabled={interactionLocked}
                    onSelect={() => updateField("retrieval_mode", "manual")}
                  />
                </div>

                {retrievalMode === "auto" && (
                  <div className="grid gap-3 rounded-md border bg-background/70 p-3 md:grid-cols-2">
                    <p className="text-xs leading-5 text-muted-foreground md:col-span-2">
                      {t("evolution.memoryConfig.injectionCount.help")}
                    </p>
                    {sharedScopeRows.map((row) => (
                      <div key={row.enabledName} className="grid gap-1.5">
                        <div className="flex items-center gap-2">
                          <Checkbox
                            checked={boolValue(memory[row.enabledName], true)}
                            disabled={interactionLocked}
                            onCheckedChange={(checked) =>
                              updateField(row.enabledName, checked === true)
                            }
                          />
                          <Label className="text-xs font-medium">{row.label}</Label>
                        </div>
                        <Input
                          type="number"
                          min={0}
                          value={numberValue(memory[row.limitName], row.defaultLimit)}
                          disabled={interactionLocked || !boolValue(memory[row.enabledName], true)}
                          onChange={(event) =>
                            updateField(
                              row.limitName,
                              Number.parseInt(event.target.value || "0", 10),
                            )
                          }
                        />
                      </div>
                    ))}
                  </div>
                )}

                {retrievalMode === "manual" && (
                  <div className="space-y-3 rounded-md border bg-background/70 p-3">
                    <div className="flex items-center justify-between">
                      <p className="text-xs leading-5 text-muted-foreground">
                        {t("evolution.memoryConfig.manualPicker.help")}
                      </p>
                      <Badge variant="secondary" className="ml-2 shrink-0">
                        {t("evolution.memoryConfig.manualPicker.totalSelected", {
                          count: pinnedIds.length,
                        })}
                      </Badge>
                    </div>
                    <div className="grid gap-2 md:grid-cols-2">
                      <ManualScopePickerButton
                        scope="user"
                        title={t("evolution.memoryConfig.scopes.user")}
                        pinnedIds={pinnedIds}
                        readOnly={interactionLocked}
                        onCommit={commitPinned}
                      />
                      <ManualScopePickerButton
                        scope="project"
                        projectId={projectId}
                        title={t("evolution.memoryConfig.scopes.project")}
                        pinnedIds={pinnedIds}
                        readOnly={interactionLocked}
                        onCommit={commitPinned}
                      />
                    </div>
                    <p className="text-[11px] leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.manualPicker.optional")}
                    </p>
                  </div>
                )}
              </div>

              {/* Step 3: task memory injection mode */}
              <div className="space-y-2" data-tour="memory-task-injection">
                <Label className="text-sm font-medium">
                  {t("evolution.memoryConfig.injection.label")}
                  <span className="ml-1 text-destructive">*</span>
                </Label>
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("evolution.memoryConfig.injection.help")}
                </p>
                <div className="grid gap-3 md:grid-cols-3">
                  {injectionModes.map((mode) => (
                    <EnableCard
                      key={mode.value}
                      title={mode.label}
                      hint={mode.help}
                      selected={injectionMode === mode.value}
                      disabled={interactionLocked}
                      onSelect={() => updateField("task_injection_mode", mode.value)}
                    />
                  ))}
                </div>
                {injectionMode === "weight" && (
                  <div className="grid gap-1.5 rounded-md border bg-background/70 p-3">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs font-medium">
                        {t("evolution.memoryConfig.injection.lambdaLabel")}
                      </Label>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {clampUnit(memory.task_injection_lambda, 0.5).toFixed(2)}
                      </span>
                    </div>
                    <Input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={clampUnit(memory.task_injection_lambda, 0.5)}
                      disabled={interactionLocked}
                      onChange={(event) =>
                        updateField("task_injection_lambda", clampUnit(event.target.value, 0.5))
                      }
                    />
                    <p className="text-[11px] text-muted-foreground">
                      {t("evolution.memoryConfig.injection.lambdaHelp")}
                    </p>
                  </div>
                )}
                <div className="grid gap-1.5 rounded-md border bg-background/70 p-3">
                  <Label className="text-xs font-medium">
                    {t("evolution.memoryConfig.scopes.task")}
                  </Label>
                  <Input
                    type="number"
                    min={0}
                    value={numberValue(memory.task_memory_limit, DEFAULT_TASK_LIMIT)}
                    disabled={interactionLocked}
                    onChange={(event) =>
                      updateField(
                        "task_memory_limit",
                        Number.parseInt(event.target.value || "0", 10),
                      )
                    }
                  />
                  <p className="text-[11px] text-muted-foreground">
                    {t("evolution.memoryConfig.injection.taskLimitHelp")}
                  </p>
                </div>
              </div>

              {/* Step 4: advanced config */}
              <details
                className="rounded-md border bg-background/70"
                data-tour="memory-advanced-config"
                open={isTaskMemoryTourActive ? tourStep === 4 : advancedOpen}
                onToggle={(event) => {
                  if (!isTaskMemoryTourActive) setAdvancedOpen(event.currentTarget.open)
                }}
              >
                <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
                  {t("evolution.memoryConfig.advanced.label")}
                </summary>
                <div className="grid gap-4 border-t p-4 md:grid-cols-2">
                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.searchStrategy")}</Label>
                    <Select
                      value={String(memory.mindmemos_search_strategy || "fast")}
                      disabled={interactionLocked}
                      onValueChange={(nextValue) =>
                        updateField("mindmemos_search_strategy", nextValue)
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="fast">fast</SelectItem>
                        <SelectItem value="agentic">agentic</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.searchStrategyHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.scoreThreshold")}</Label>
                    <Input
                      type="number"
                      min={0}
                      max={1}
                      step="0.01"
                      value={
                        memory.mindmemos_score_threshold == null
                          ? ""
                          : String(memory.mindmemos_score_threshold)
                      }
                      disabled={interactionLocked || !boolValue(memory.mindmemos_rerank, false)}
                      placeholder={t("evolution.memoryConfig.scoreThresholdPlaceholder")}
                      onChange={(event) =>
                        updateField("mindmemos_score_threshold", scoreValue(event.target.value))
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.scoreThresholdHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.requestTimeout")}</Label>
                    <Input
                      type="number"
                      min={0}
                      step={1}
                      value={timeoutInputValue(memory.mindmemos_request_timeout, 300)}
                      disabled={interactionLocked}
                      onChange={(event) =>
                        updateField("mindmemos_request_timeout", timeoutValue(event.target.value))
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.requestTimeoutHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.addTimeout")}</Label>
                    <Input
                      type="number"
                      min={0}
                      step={1}
                      value={timeoutInputValue(memory.mindmemos_add_timeout, 300)}
                      disabled={interactionLocked}
                      onChange={(event) =>
                        updateField("mindmemos_add_timeout", timeoutValue(event.target.value))
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.addTimeoutHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.contextCharBudget")}</Label>
                    <Input
                      type="number"
                      min={0}
                      step={1000}
                      value={numberValue(memory.mindmemos_context_char_budget, 20000)}
                      disabled={interactionLocked}
                      onChange={(event) =>
                        updateField(
                          "mindmemos_context_char_budget",
                          Math.max(0, Number.parseInt(event.target.value || "0", 10)),
                        )
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.contextCharBudgetHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.eliteCodeSlots")}</Label>
                    <Input
                      type="number"
                      min={0}
                      max={5}
                      step={1}
                      value={numberValue(memory.mindmemos_elite_code_slots, 1)}
                      disabled={interactionLocked}
                      onChange={(event) =>
                        updateField(
                          "mindmemos_elite_code_slots",
                          Math.min(
                            5,
                            Math.max(0, Number.parseInt(event.target.value || "0", 10)),
                          ),
                        )
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.eliteCodeSlotsHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.eliteCodeCharBudget")}</Label>
                    <Input
                      type="number"
                      min={0}
                      step={1000}
                      value={numberValue(memory.mindmemos_elite_code_char_budget, 12000)}
                      disabled={interactionLocked}
                      onChange={(event) =>
                        updateField(
                          "mindmemos_elite_code_char_budget",
                          Math.max(0, Number.parseInt(event.target.value || "0", 10)),
                        )
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.eliteCodeCharBudgetHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.extractionLanguage")}</Label>
                    <Select
                      value={String(memory.mindmemos_extraction_prompt_language || "auto")}
                      disabled={interactionLocked}
                      onValueChange={(nextValue) =>
                        updateField("mindmemos_extraction_prompt_language", nextValue)
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">
                          {t("evolution.memoryConfig.extractionLanguages.auto")}
                        </SelectItem>
                        <SelectItem value="ZH">
                          {t("evolution.memoryConfig.extractionLanguages.ZH")}
                        </SelectItem>
                        <SelectItem value="EN">
                          {t("evolution.memoryConfig.extractionLanguages.EN")}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.extractionLanguageHelp")}
                    </p>
                  </div>

                  <div className="flex items-start gap-2 rounded-md border bg-muted/20 p-3">
                    <Checkbox
                      checked={boolValue(memory.mindmemos_rerank, false)}
                      disabled={interactionLocked || !config?.system_rerank_enabled}
                      onCheckedChange={(checked) =>
                        updateField("mindmemos_rerank", checked === true)
                      }
                    />
                    <div className="grid gap-1">
                      <Label className="text-sm">{t("evolution.memoryConfig.rerank")}</Label>
                      <p className="text-xs leading-5 text-muted-foreground">
                        {config?.system_rerank_enabled
                          ? t("evolution.memoryConfig.rerankHelp")
                          : t("evolution.memoryConfig.rerankUnavailable")}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-start gap-2 rounded-md border bg-muted/20 p-3">
                    <Checkbox
                      checked={boolValue(memory.mindmemos_fail_open, true)}
                      disabled={interactionLocked}
                      onCheckedChange={(checked) =>
                        updateField("mindmemos_fail_open", checked === true)
                      }
                    />
                    <div className="grid gap-1">
                      <Label className="text-sm">{t("evolution.memoryConfig.failOpen")}</Label>
                      <p className="text-xs leading-5 text-muted-foreground">
                        {t("evolution.memoryConfig.failOpenHelp")}
                      </p>
                    </div>
                  </div>
                </div>
              </details>

              <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs leading-5 text-muted-foreground">
                {t("evolution.memoryConfig.runtimeManaged")}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="shrink-0 py-2">
        <div className="flex justify-center gap-6">
          {onBack && (
            <Button type="button" variant="outline" onClick={onBack} disabled={interactionLocked}>
              {t("common.previousStep")}
            </Button>
          )}
          {onNext && (
            <Button type="button" onClick={onNext} disabled={nextDisabled || interactionLocked}>
              {t("common.nextStep")}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

interface EnableCardProps {
  title: string
  hint: string
  selected: boolean
  disabled?: boolean
  onSelect: () => void
}

function EnableCard({ title, hint, selected, disabled, onSelect }: EnableCardProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className={[
        "flex flex-col items-start gap-1 rounded-md border p-3 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary"
          : "bg-background/70 hover:bg-accent/40",
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
      ].join(" ")}
    >
      <span className="text-sm font-medium">{title}</span>
      <span className="text-xs leading-5 text-muted-foreground">{hint}</span>
    </button>
  )
}

interface ManualScopePickerButtonProps {
  scope: PickerScope
  projectId?: string
  title: string
  pinnedIds: string[]
  readOnly: boolean
  onCommit: (nextPinnedIds: string[]) => void
}

// A dialog-based paginated picker so large user/project memory sets stay
// browsable (the shared scopes can grow without bound over time). The dialog
// edits a local draft and only commits to the parent on confirm; the button
// shows how many pinned ids belong to this scope.
function ManualScopePickerButton({
  scope,
  projectId,
  title,
  pinnedIds,
  readOnly,
  onCommit,
}: ManualScopePickerButtonProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [draftIds, setDraftIds] = useState<string[]>(pinnedIds)

  const { data, isLoading, isError } = useQuery({
    queryKey: ["memoryCardsPage", scope, projectId, page],
    queryFn: () =>
      Llm4AdMemoryService.listMemoryCards({
        scope,
        projectId: scope === "project" ? projectId : undefined,
        page,
        pageSize: CARD_PICKER_PAGE_SIZE,
      }),
    enabled: open,
  })

  // Scope card ids (first page, larger size) for per-scope count + partitioning
  // the flat pinned list on commit. Enabled whenever the scope is usable.
  const { data: scopeIndex } = useQuery({
    queryKey: ["memoryScopeIndex", scope, projectId],
    queryFn: () =>
      Llm4AdMemoryService.listMemoryCards({
        scope,
        projectId: scope === "project" ? projectId : undefined,
        page: 1,
        pageSize: 100,
      }),
    enabled: !readOnly && (scope === "user" || Boolean(projectId)),
  })
  const scopeCardIds = useMemo(
    () => new Set(((scopeIndex?.items ?? []) as MemoryCardResponse[]).map((c) => c.id)),
    [scopeIndex],
  )
  const selectedInScope = pinnedIds.filter((id) => scopeCardIds.has(id)).length

  // Only offer enabled memories; disabled cards are never injectable.
  const cards = ((data?.items ?? []) as MemoryCardResponse[]).filter(
    (card) => card.enabled !== false,
  )
  const total = data?.total ?? null
  const hasMore = data?.has_more ?? false

  const openDialog = (next: boolean) => {
    if (next) setDraftIds(pinnedIds)
    setOpen(next)
  }

  const toggleDraft = (cardId: string, checked: boolean) => {
    setDraftIds((current) => {
      const next = new Set(current)
      if (checked) next.add(cardId)
      else next.delete(cardId)
      return Array.from(next)
    })
  }

  const confirm = () => {
    onCommit(draftIds)
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={openDialog}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" className="justify-between" disabled={readOnly}>
          <span className="truncate">{title}</span>
          <Badge variant="secondary" className="ml-2 shrink-0">
            {selectedInScope}
          </Badge>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {t("evolution.memoryConfig.manualPicker.dialogHint")}
          </DialogDescription>
        </DialogHeader>
        <ManualScopePickerList
          cards={cards}
          pinnedIds={draftIds}
          readOnly={readOnly}
          isLoading={isLoading}
          isError={isError}
          onToggle={toggleDraft}
        />
        <ManualScopePickerPager
          page={page}
          hasMore={hasMore}
          total={total}
          pageSize={CARD_PICKER_PAGE_SIZE}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => p + 1)}
        />
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={() => setOpen(false)}>
            {t("evolution.memoryConfig.manualPicker.cancel")}
          </Button>
          <Button type="button" size="sm" onClick={confirm}>
            {t("evolution.memoryConfig.manualPicker.confirm")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface ManualScopePickerListProps {
  cards: MemoryCardResponse[]
  pinnedIds: string[]
  readOnly: boolean
  isLoading: boolean
  isError: boolean
  onToggle: (cardId: string, checked: boolean) => void
}

function ManualScopePickerList({
  cards,
  pinnedIds,
  readOnly,
  isLoading,
  isError,
  onToggle,
}: ManualScopePickerListProps) {
  const { t } = useTranslation()
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        <span className="text-xs">{t("evolution.memoryConfig.manualPicker.loading")}</span>
      </div>
    )
  }
  if (isError) {
    return (
      <p className="py-8 text-center text-xs text-destructive">
        {t("evolution.memoryConfig.manualPicker.loadFailed")}
      </p>
    )
  }
  if (cards.length === 0) {
    return (
      <p className="py-8 text-center text-xs text-muted-foreground">
        {t("evolution.memoryConfig.manualPicker.empty")}
      </p>
    )
  }
  return (
    <div className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
      {cards.map((card) => (
        <label
          key={card.id}
          htmlFor={`memory-card-${card.id}`}
          className="flex cursor-pointer items-start gap-2 rounded-md border bg-background px-3 py-2"
        >
          <Checkbox
            id={`memory-card-${card.id}`}
            checked={pinnedIds.includes(card.id)}
            disabled={readOnly}
            onCheckedChange={(checked) => onToggle(card.id, checked === true)}
          />
          <span className="min-w-0">
            <span className="block truncate text-xs font-medium">{card.title || card.id}</span>
            <span className="block truncate text-[11px] text-muted-foreground">{card.content}</span>
          </span>
        </label>
      ))}
    </div>
  )
}

interface ManualScopePickerPagerProps {
  page: number
  hasMore: boolean
  total: number | null
  pageSize: number
  onPrev: () => void
  onNext: () => void
}

function ManualScopePickerPager({
  page,
  hasMore,
  total,
  pageSize,
  onPrev,
  onNext,
}: ManualScopePickerPagerProps) {
  const { t } = useTranslation()
  const totalPages = total != null ? Math.max(1, Math.ceil(total / pageSize)) : null
  return (
    <div className="flex items-center justify-between pt-1">
      <span className="text-[11px] text-muted-foreground">
        {totalPages != null
          ? t("evolution.memoryConfig.manualPicker.pageOf", { page, total: totalPages })
          : t("evolution.memoryConfig.manualPicker.page", { page })}
      </span>
      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" disabled={page <= 1} onClick={onPrev}>
          {t("evolution.memoryConfig.manualPicker.prev")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!hasMore}
          onClick={onNext}
        >
          {t("evolution.memoryConfig.manualPicker.next")}
        </Button>
      </div>
    </div>
  )
}
