import { useQuery } from "@tanstack/react-query"
import { Database, Info, Loader2 } from "lucide-react"
import { useEffect, useMemo } from "react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { MemoryCard, MemoryCardPage, MemoryConfig } from "@/components/Memory/types"
import { authFetch } from "@/utils/auth"

type MemoryValue = Record<string, unknown>

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

function longTermMemory(value: unknown, config?: MemoryConfig): MemoryValue {
  const current = safeMemory(value)
  const rerank = boolValue(current.mindmemos_rerank, config?.mindmemos_rerank ?? false)
  const retrievalMode: RetrievalMode =
    current.retrieval_mode === "manual" ? "manual" : "auto"
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
    // In auto mode we retrieve+inject the shared scopes; in manual mode the
    // pinned set is injected instead, so the shared toggles stay enabled.
    include_user_memory: boolValue(current.include_user_memory, config?.include_user_memory ?? true),
    include_project_memory: boolValue(current.include_project_memory, config?.include_project_memory ?? true),
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
    mindmemos_request_timeout: numberValue(
      current.mindmemos_request_timeout,
      config?.mindmemos_request_timeout ?? 300,
    ),
    mindmemos_add_timeout: numberValue(
      current.mindmemos_add_timeout,
      config?.mindmemos_add_timeout ?? 300,
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
  const memory = safeMemory(value)

  const { data: config, isLoading, isError } = useQuery<MemoryConfig>({
    queryKey: ["projectMemoryConfig", projectId],
    queryFn: async () => {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await authFetch(
        `${baseUrl}/api/v1/llm4ad/memory/projects/${projectId}/config`,
      )
      if (!response.ok) throw new Error("Failed to load project memory config")
      return response.json()
    },
    enabled: !!projectId,
  })

  const runtimeAvailable = Boolean(
    config?.system_runtime_available && config?.mindmemos_binding_id,
  )
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

  // Long-term memory needs an available runtime; fall back to disabled state
  // if a previously-selected long-term config can no longer be served.
  useEffect(() => {
    if (readOnly || isLoading) return
    const current = safeMemory(value)
    if (current.type === "mindmemos_cloud" && current.enabled !== false && !runtimeAvailable) {
      onChange(disabledMemory())
    }
  }, [isLoading, onChange, readOnly, runtimeAvailable, value])

  // Available memory cards for the manual retrieval picker (user + project scope).
  const { data: userCards } = useQuery<MemoryCardPage>({
    queryKey: ["memoryCards", "user", projectId],
    queryFn: async () => {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await authFetch(
        `${baseUrl}/api/v1/llm4ad/memory/cards?scope=user&page=1&page_size=50`,
      )
      if (!response.ok) throw new Error("Failed to load user memory cards")
      return response.json()
    },
    enabled: isLongTerm && retrievalMode === "manual" && runtimeAvailable,
  })
  const { data: projectCards } = useQuery<MemoryCardPage>({
    queryKey: ["memoryCards", "project", projectId],
    queryFn: async () => {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await authFetch(
        `${baseUrl}/api/v1/llm4ad/memory/cards?scope=project&project_id=${projectId}&page=1&page_size=50`,
      )
      if (!response.ok) throw new Error("Failed to load project memory cards")
      return response.json()
    },
    enabled: isLongTerm && retrievalMode === "manual" && runtimeAvailable && !!projectId,
  })

  // Keep global and project cards as separate lists so the user can pick from
  // each scope independently (or select nothing from either).
  const globalPickerCards = useMemo(() => userCards?.items ?? [], [userCards])
  const projectPickerCards = useMemo(() => projectCards?.items ?? [], [projectCards])

  const updateField = (key: string, nextValue: unknown) => {
    const nextMemory = { ...longTermMemory(memory, config), [key]: nextValue }
    if (key === "mindmemos_rerank" && nextValue !== true) {
      nextMemory.mindmemos_score_threshold = null
    }
    onChange(nextMemory)
  }

  const togglePinned = (cardId: string, checked: boolean) => {
    const next = new Set(pinnedIds)
    if (checked) next.add(cardId)
    else next.delete(cardId)
    updateField("pinned_card_ids", Array.from(next))
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
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
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
            <div className="grid gap-3 md:grid-cols-3">
              <EnableCard
                title={t("evolution.memoryConfig.enable.none")}
                hint={t("evolution.memoryConfig.enable.noneHint")}
                selected={enableMode === "none"}
                disabled={readOnly}
                onSelect={() => onChange(disabledMemory())}
              />
              <EnableCard
                title={t("evolution.memoryConfig.enable.temporary")}
                hint={t("evolution.memoryConfig.enable.temporaryHint")}
                selected={enableMode === "temporary"}
                disabled={readOnly}
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
                disabled={readOnly || !runtimeAvailable}
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
              <div className="space-y-2">
                <Label className="text-sm font-medium">
                  {t("evolution.memoryConfig.retrieval.label")}
                  <span className="ml-1 text-destructive">*</span>
                </Label>
                <div className="grid gap-3 md:grid-cols-2">
                  <EnableCard
                    title={t("evolution.memoryConfig.retrieval.auto")}
                    hint={t("evolution.memoryConfig.retrieval.autoHint")}
                    selected={retrievalMode === "auto"}
                    disabled={readOnly}
                    onSelect={() => updateField("retrieval_mode", "auto")}
                  />
                  <EnableCard
                    title={t("evolution.memoryConfig.retrieval.manual")}
                    hint={t("evolution.memoryConfig.retrieval.manualHint")}
                    selected={retrievalMode === "manual"}
                    disabled={readOnly}
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
                            disabled={readOnly}
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
                          disabled={readOnly || !boolValue(memory[row.enabledName], true)}
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
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.manualPicker.help")}
                    </p>
                    <ManualScopePicker
                      title={t("evolution.memoryConfig.scopes.user")}
                      cards={globalPickerCards}
                      pinnedIds={pinnedIds}
                      readOnly={readOnly}
                      emptyLabel={t("evolution.memoryConfig.manualPicker.empty")}
                      onToggle={togglePinned}
                    />
                    <ManualScopePicker
                      title={t("evolution.memoryConfig.scopes.project")}
                      cards={projectPickerCards}
                      pinnedIds={pinnedIds}
                      readOnly={readOnly}
                      emptyLabel={t("evolution.memoryConfig.manualPicker.empty")}
                      onToggle={togglePinned}
                    />
                    <p className="text-[11px] leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.manualPicker.optional")}
                    </p>
                  </div>
                )}
              </div>

              {/* Step 3: task memory injection mode */}
              <div className="space-y-2">
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
                      disabled={readOnly}
                      onSelect={() => updateField("task_injection_mode", mode.value)}
                    />
                  ))}
                </div>
                <div className="grid gap-1.5 rounded-md border bg-background/70 p-3">
                  <Label className="text-xs font-medium">
                    {t("evolution.memoryConfig.scopes.task")}
                  </Label>
                  <Input
                    type="number"
                    min={0}
                    value={numberValue(memory.task_memory_limit, DEFAULT_TASK_LIMIT)}
                    disabled={readOnly}
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
              <details className="rounded-md border bg-background/70">
                <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
                  {t("evolution.memoryConfig.advanced.label")}
                </summary>
                <div className="grid gap-4 border-t p-4 md:grid-cols-2">
                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.searchStrategy")}</Label>
                    <Select
                      value={String(memory.mindmemos_search_strategy || "fast")}
                      disabled={readOnly}
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
                      disabled={readOnly || !boolValue(memory.mindmemos_rerank, false)}
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
                      disabled={readOnly}
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
                      disabled={readOnly}
                      onChange={(event) =>
                        updateField("mindmemos_add_timeout", timeoutValue(event.target.value))
                      }
                    />
                    <p className="text-xs leading-5 text-muted-foreground">
                      {t("evolution.memoryConfig.addTimeoutHelp")}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label>{t("evolution.memoryConfig.extractionLanguage")}</Label>
                    <Select
                      value={String(memory.mindmemos_extraction_prompt_language || "auto")}
                      disabled={readOnly}
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
                      disabled={readOnly || !config?.system_rerank_enabled}
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
                      disabled={readOnly}
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
            <Button type="button" variant="outline" onClick={onBack}>
              {t("common.previousStep")}
            </Button>
          )}
          {onNext && (
            <Button type="button" onClick={onNext} disabled={nextDisabled}>
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

interface ManualScopePickerProps {
  title: string
  cards: MemoryCard[]
  pinnedIds: string[]
  readOnly: boolean
  emptyLabel: string
  onToggle: (cardId: string, checked: boolean) => void
}

function ManualScopePicker({
  title,
  cards,
  pinnedIds,
  readOnly,
  emptyLabel,
  onToggle,
}: ManualScopePickerProps) {
  const selectedCount = cards.filter((card) => pinnedIds.includes(card.id)).length
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">{title}</span>
        <span className="text-[11px] text-muted-foreground">{selectedCount}</span>
      </div>
      <div className="max-h-44 space-y-1.5 overflow-y-auto">
        {cards.length === 0 ? (
          <p className="py-3 text-center text-xs text-muted-foreground">{emptyLabel}</p>
        ) : (
          cards.map((card) => (
            <label
              key={card.id}
              className="flex cursor-pointer items-start gap-2 rounded-md border bg-background px-3 py-2"
            >
              <Checkbox
                checked={pinnedIds.includes(card.id)}
                disabled={readOnly}
                onCheckedChange={(checked) => onToggle(card.id, checked === true)}
              />
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium">{card.title || card.id}</span>
                <span className="block truncate text-[11px] text-muted-foreground">
                  {card.content}
                </span>
              </span>
            </label>
          ))
        )}
      </div>
    </div>
  )
}
