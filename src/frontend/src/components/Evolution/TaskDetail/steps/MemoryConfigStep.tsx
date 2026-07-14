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
import type { MemoryConfig } from "@/components/Memory/types"
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

const DEFAULT_TASK_LIMIT = 5
const DEFAULT_SHARED_LIMIT = 0
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

function safeMemory(value: unknown): MemoryValue {
  return value && typeof value === "object" && !Array.isArray(value)
    ? { ...(value as MemoryValue) }
    : {}
}

function localYamlMemory(): MemoryValue {
  return {
    enabled: true,
    type: "local_yaml",
  }
}

function mindmemosMemory(value: unknown, config?: MemoryConfig): MemoryValue {
  const current = safeMemory(value)
  const rerank = boolValue(current.mindmemos_rerank, config?.mindmemos_rerank ?? false)
  return {
    enabled: true,
    type: "mindmemos_cloud",
    include_user_memory: boolValue(current.include_user_memory, config?.include_user_memory ?? false),
    include_project_memory: boolValue(current.include_project_memory, config?.include_project_memory ?? false),
    include_task_memory: boolValue(current.include_task_memory, config?.include_task_memory ?? true),
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
      config?.mindmemos_request_timeout ?? 60,
    ),
    mindmemos_add_timeout: numberValue(
      current.mindmemos_add_timeout,
      config?.mindmemos_add_timeout ?? 120,
    ),
    mindmemos_extraction_prompt_language: ["auto", "ZH", "EN"].includes(
      String(current.mindmemos_extraction_prompt_language),
    )
      ? String(current.mindmemos_extraction_prompt_language)
      : "auto",
  }
}

function isMindmemosSelected(value: unknown) {
  const memory = safeMemory(value)
  return memory.enabled !== false && memory.type === "mindmemos_cloud"
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
  const useMindmemos = runtimeAvailable && isMindmemosSelected(memory)
  const statusLabel = runtimeAvailable
    ? t("evolution.taskMemoryConfig.status.ready")
    : isLoading
      ? t("evolution.taskMemoryConfig.status.checking")
      : t("evolution.taskMemoryConfig.status.local")

  useEffect(() => {
    if (readOnly || isLoading) return
    const current = safeMemory(value)
    if (current.type) {
      if (!runtimeAvailable && current.type === "mindmemos_cloud") {
        onChange(localYamlMemory())
      }
      return
    }
    onChange(runtimeAvailable ? mindmemosMemory(current, config) : localYamlMemory())
  }, [config, isLoading, onChange, readOnly, runtimeAvailable, value])

  const scopeRows = useMemo(
    () => [
      {
        enabledName: "include_user_memory",
        limitName: "user_memory_limit",
        defaultEnabled: false,
        defaultLimit: DEFAULT_SHARED_LIMIT,
        label: t("evolution.taskMemoryConfig.scopes.user"),
      },
      {
        enabledName: "include_project_memory",
        limitName: "project_memory_limit",
        defaultEnabled: false,
        defaultLimit: DEFAULT_SHARED_LIMIT,
        label: t("evolution.taskMemoryConfig.scopes.project"),
      },
      {
        enabledName: "include_task_memory",
        limitName: "task_memory_limit",
        defaultEnabled: true,
        defaultLimit: DEFAULT_TASK_LIMIT,
        label: t("evolution.taskMemoryConfig.scopes.task"),
      },
    ],
    [t],
  )

  const updateMindmemosField = (key: string, nextValue: unknown) => {
    const nextMemory = {
      ...mindmemosMemory(memory, config),
      [key]: nextValue,
    }
    if (key === "mindmemos_rerank" && nextValue !== true) {
      nextMemory.mindmemos_score_threshold = null
    }
    onChange({
      ...nextMemory,
    })
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className="space-y-5">
          <div className="rounded-md border bg-card/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3">
                <div className="rounded-md bg-primary/10 p-2 text-primary">
                  <Database className="size-4" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold">
                    {t("evolution.taskMemoryConfig.title")}
                  </h3>
                  <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                    {t("evolution.taskMemoryConfig.description")}
                  </p>
                </div>
              </div>
              <Badge variant={runtimeAvailable ? "default" : "secondary"}>
                {isLoading && <Loader2 className="mr-1 size-3 animate-spin" />}
                {statusLabel}
              </Badge>
            </div>

            <div className="mt-4 flex items-start gap-2 rounded-md border bg-background/70 p-3">
              <Checkbox
                id="task-memory-mindmemos"
                checked={useMindmemos}
                disabled={readOnly || !runtimeAvailable}
                onCheckedChange={(checked) => {
                  onChange(checked === true ? mindmemosMemory(memory, config) : localYamlMemory())
                }}
              />
              <div className="grid gap-1">
                <Label htmlFor="task-memory-mindmemos" className="text-sm font-medium">
                  {t("evolution.taskMemoryConfig.useMindmemos")}
                </Label>
                <p className="text-xs leading-5 text-muted-foreground">
                  {runtimeAvailable
                    ? t("evolution.taskMemoryConfig.useMindmemosHint")
                    : t("evolution.taskMemoryConfig.localFallbackHint")}
                </p>
              </div>
            </div>

            {(isError || !runtimeAvailable) && !isLoading && (
              <div className="mt-3 flex gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
                <Info className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  {isError
                    ? t("evolution.taskMemoryConfig.configLoadFailed")
                    : t("evolution.taskMemoryConfig.unavailable")}
                </span>
              </div>
            )}
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {scopeRows.map((row) => (
              <div key={row.enabledName} className="rounded-md border bg-background/70 p-3">
                <div className="flex items-center gap-2">
                  <Checkbox
                    checked={boolValue(memory[row.enabledName], row.defaultEnabled)}
                    disabled={readOnly || !useMindmemos}
                    onCheckedChange={(checked) => updateMindmemosField(row.enabledName, checked === true)}
                  />
                  <Label className="text-xs font-medium">{row.label}</Label>
                </div>
                <div className="mt-3 grid gap-1.5">
                  <Label className="text-[11px] text-muted-foreground">
                    {t("evolution.taskMemoryConfig.limit")}
                  </Label>
                  <Input
                    type="number"
                    min={0}
                    value={numberValue(memory[row.limitName], row.defaultLimit)}
                    disabled={readOnly || !useMindmemos}
                    onChange={(event) =>
                      updateMindmemosField(row.limitName, Number.parseInt(event.target.value || "0", 10))
                    }
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="grid gap-4 rounded-md border bg-background/70 p-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label>{t("evolution.taskMemoryConfig.searchStrategy")}</Label>
              <Select
                value={String(memory.mindmemos_search_strategy || "fast")}
                disabled={readOnly || !useMindmemos}
                onValueChange={(nextValue) => updateMindmemosField("mindmemos_search_strategy", nextValue)}
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
                {t("evolution.taskMemoryConfig.searchStrategyHelp")}
              </p>
            </div>

            <div className="grid gap-2">
              <Label>{t("evolution.taskMemoryConfig.scoreThreshold")}</Label>
              <Input
                type="number"
                min={0}
                max={1}
                step="0.01"
                value={memory.mindmemos_score_threshold == null ? "" : String(memory.mindmemos_score_threshold)}
                disabled={readOnly || !useMindmemos || !boolValue(memory.mindmemos_rerank, false)}
                placeholder={t("evolution.taskMemoryConfig.scoreThresholdPlaceholder")}
                onChange={(event) =>
                  updateMindmemosField("mindmemos_score_threshold", scoreValue(event.target.value))
                }
              />
              <p className="text-xs leading-5 text-muted-foreground">
                {t("evolution.taskMemoryConfig.scoreThresholdHelp")}
              </p>
            </div>

            <div className="grid gap-2">
              <Label>{t("evolution.taskMemoryConfig.requestTimeout")}</Label>
              <Input
                type="number"
                min={0}
                step={1}
                value={timeoutInputValue(memory.mindmemos_request_timeout, 60)}
                disabled={readOnly || !useMindmemos}
                onChange={(event) =>
                  updateMindmemosField(
                    "mindmemos_request_timeout",
                    timeoutValue(event.target.value),
                  )
                }
              />
              <p className="text-xs leading-5 text-muted-foreground">
                {t("evolution.taskMemoryConfig.requestTimeoutHelp")}
              </p>
            </div>

            <div className="grid gap-2">
              <Label>{t("evolution.taskMemoryConfig.addTimeout")}</Label>
              <Input
                type="number"
                min={0}
                step={1}
                value={timeoutInputValue(memory.mindmemos_add_timeout, 120)}
                disabled={readOnly || !useMindmemos}
                onChange={(event) =>
                  updateMindmemosField(
                    "mindmemos_add_timeout",
                    timeoutValue(event.target.value),
                  )
                }
              />
              <p className="text-xs leading-5 text-muted-foreground">
                {t("evolution.taskMemoryConfig.addTimeoutHelp")}
              </p>
            </div>

            <div className="grid gap-2">
              <Label>{t("evolution.taskMemoryConfig.extractionLanguage")}</Label>
              <Select
                value={String(memory.mindmemos_extraction_prompt_language || "auto")}
                disabled={readOnly || !useMindmemos}
                onValueChange={(nextValue) =>
                  updateMindmemosField("mindmemos_extraction_prompt_language", nextValue)
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">{t("evolution.taskMemoryConfig.extractionLanguages.auto")}</SelectItem>
                  <SelectItem value="ZH">{t("evolution.taskMemoryConfig.extractionLanguages.ZH")}</SelectItem>
                  <SelectItem value="EN">{t("evolution.taskMemoryConfig.extractionLanguages.EN")}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs leading-5 text-muted-foreground">
                {t("evolution.taskMemoryConfig.extractionLanguageHelp")}
              </p>
            </div>

            <div className="flex items-start gap-2 rounded-md border bg-muted/20 p-3">
              <Checkbox
                checked={boolValue(memory.mindmemos_rerank, false)}
                disabled={readOnly || !useMindmemos || !config?.system_rerank_enabled}
                onCheckedChange={(checked) => updateMindmemosField("mindmemos_rerank", checked === true)}
              />
              <div className="grid gap-1">
                <Label className="text-sm">{t("evolution.taskMemoryConfig.rerank")}</Label>
                <p className="text-xs leading-5 text-muted-foreground">
                  {config?.system_rerank_enabled
                    ? t("evolution.taskMemoryConfig.rerankHelp")
                    : t("evolution.taskMemoryConfig.rerankUnavailable")}
                </p>
              </div>
            </div>

            <div className="flex items-start gap-2 rounded-md border bg-muted/20 p-3">
              <Checkbox
                checked={boolValue(memory.mindmemos_fail_open, true)}
                disabled={readOnly || !useMindmemos}
                onCheckedChange={(checked) => updateMindmemosField("mindmemos_fail_open", checked === true)}
              />
              <div className="grid gap-1">
                <Label className="text-sm">{t("evolution.taskMemoryConfig.failOpen")}</Label>
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("evolution.taskMemoryConfig.failOpenHelp")}
                </p>
              </div>
            </div>
          </div>

          <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs leading-5 text-muted-foreground">
            {t("evolution.taskMemoryConfig.runtimeManaged")}
          </div>
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
            <Button type="button" onClick={onNext}>
              {t("common.nextStep")}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
