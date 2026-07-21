import { AlertTriangle, Loader2, LockKeyhole, Save, ServerCog } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { authFetch } from "@/utils/auth"

import type { MemoryConfig } from "./types"

const CONFIG_FIELDS = [
  ["include_user_memory", "user", "user_memory_limit"],
  ["include_project_memory", "project", "project_memory_limit"],
  ["include_task_memory", "task", "task_memory_limit"],
] as const

function endpointFor(kind: "user" | "project", projectId?: string) {
  const baseUrl = import.meta.env.VITE_API_URL || ""
  if (kind === "project") {
    return `${baseUrl}/api/v1/llm4ad/memory/projects/${projectId}/config`
  }
  return `${baseUrl}/api/v1/llm4ad/memory/user-config`
}

export default function MemoryConfigEditor({
  kind,
  projectId,
  title,
  description,
  enabled = true,
  disabledReason,
  readOnly = false,
  onLoaded,
  onSaved,
}: {
  kind: "user" | "project"
  projectId?: string
  title: string
  description: string
  enabled?: boolean
  disabledReason?: string
  readOnly?: boolean
  onLoaded?: (config: MemoryConfig) => void
  onSaved?: (config: MemoryConfig) => void
}) {
  const { t } = useTranslation()
  const [config, setConfig] = useState<MemoryConfig | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const endpoint = endpointFor(kind, projectId)

  const loadConfig = useCallback(async () => {
    if (!enabled) {
      setConfig(null)
      setIsLoading(false)
      return
    }
    setIsLoading(true)
    try {
      const response = await authFetch(endpoint)
      if (!response.ok) throw new Error(t("memory.config.loadFailed"))
      const loaded = await response.json()
      setConfig(loaded)
      onLoaded?.(loaded)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.config.loadFailed"))
    } finally {
      setIsLoading(false)
    }
  }, [enabled, endpoint, onLoaded, t])

  useEffect(() => {
    void loadConfig()
  }, [loadConfig])

  const update = (patch: Partial<MemoryConfig>) => {
    if (readOnly) return
    setConfig((current) => (current ? { ...current, ...patch } : current))
  }

  const save = async () => {
    if (readOnly) return
    if (!config) return
    setIsSaving(true)
    try {
      const response = await authFetch(endpoint, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          include_user_memory: config.include_user_memory,
          include_project_memory: config.include_project_memory,
          include_task_memory: config.include_task_memory,
          user_memory_limit: config.user_memory_limit,
          project_memory_limit: config.project_memory_limit,
          task_memory_limit: config.task_memory_limit,
          mindmemos_search_strategy: config.mindmemos_search_strategy,
          mindmemos_rerank: config.mindmemos_rerank,
          mindmemos_score_threshold: config.mindmemos_score_threshold,
          mindmemos_fail_open: config.mindmemos_fail_open,
        }),
      })
      if (!response.ok) throw new Error(t("memory.config.saveFailed"))
      const saved = await response.json()
      setConfig(saved)
      onSaved?.(saved)
      toast.success(t("memory.config.saved"))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("memory.config.saveFailed"))
    } finally {
      setIsSaving(false)
    }
  }

  if (!enabled) {
    return (
      <div className="rounded-lg border bg-muted/30 text-muted-foreground" data-tour="memory-default-policy">
        <div className="space-y-2 border-b px-4 py-3">
          <div className="flex items-start gap-2">
            <LockKeyhole className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold leading-5 text-foreground">{title}</h2>
              <p className="text-xs leading-5">{description}</p>
            </div>
          </div>
          <div className="pl-6 text-xs">
            {disabledReason || t("memory.config.disabledReason")}
          </div>
        </div>

        <div className="pointer-events-none select-none space-y-4 p-4 opacity-60">
          <div className="rounded-md border bg-background/50 px-3 py-2 text-xs">
            {t("memory.config.disabledHint")}
          </div>
          {CONFIG_FIELDS.map(([, scope]) => (
            <div key={scope} className="grid gap-3 rounded-md border bg-background/50 p-3 sm:grid-cols-[1fr_132px]">
              <div className="space-y-2">
                <div className="text-sm font-medium">{t(`memory.config.scopes.${scope}.label`)}</div>
                <div className="text-xs">
                  {t("memory.config.disabledScopeHint")}
                </div>
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs">{t(`memory.config.scopes.${scope}.limitLabel`)}</Label>
                <Input value="" placeholder={t("memory.config.notEnabled")} disabled />
              </div>
            </div>
          ))}
          <div className="space-y-4 rounded-md border bg-background/50 p-3">
            <div className="grid gap-2">
              <Label>{t("memory.config.searchStrategy")}</Label>
              <Input value="" placeholder={t("memory.config.notEnabled")} disabled />
              <p className="text-xs">{t("memory.config.disabledSearchStrategyHint")}</p>
            </div>
            <div className="grid gap-2">
              <Label>{t("memory.config.searchThreshold")}</Label>
              <Input value="" placeholder={t("memory.config.notEnabled")} disabled />
            </div>
          </div>
        </div>
        <div className="flex justify-end border-t px-4 py-3">
          <Button type="button" className="gap-1.5" disabled>
            <Save className="size-4" />
            {t("memory.config.save")}
          </Button>
        </div>
      </div>
    )
  }

  if (isLoading || !config) {
    return <MemoryConfigSkeleton title={title} description={description} />
  }

  return (
    <div className="rounded-lg border bg-card/60" data-tour="memory-default-policy">
      <div className="space-y-2 border-b px-4 py-3">
        <div className="flex items-start gap-2">
          <ServerCog className="mt-0.5 size-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold leading-5">{title}</h2>
            <p className="text-xs leading-5 text-muted-foreground">{description}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 pl-6">
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_enabled ? "default" : "secondary"}
          >
            {config.system_enabled ? t("memory.config.badges.systemEnabled") : t("memory.config.badges.systemDisabled")}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_runtime_available ? "default" : "secondary"}
          >
            {config.system_runtime_available ? t("memory.config.badges.runtimeAvailable") : t("memory.config.badges.legacyFallback")}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_api_key_configured ? "outline" : "destructive"}
          >
            {config.system_api_key_configured ? t("memory.config.badges.gatewayConfigured") : t("memory.config.badges.gatewayMissing")}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.mindmemos_binding_id ? "outline" : "destructive"}
          >
            {config.mindmemos_binding_id ? t("memory.config.badges.modelsBound") : t("memory.config.badges.modelsUnbound")}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_rerank_configured ? "outline" : "secondary"}
          >
            {config.system_rerank_enabled
              ? config.system_rerank_configured ? t("memory.config.badges.rerankConfigured") : t("memory.config.badges.rerankMissing")
              : t("memory.config.badges.rerankDisabled")}
          </Badge>
        </div>
      </div>

      <div className="grid gap-4 p-4">
        <div className="space-y-4">
          {!config.system_runtime_available && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              {t("memory.config.runtimeUnavailable")}
            </div>
          )}
          {config.system_runtime_available && !config.mindmemos_binding_id && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              {t("memory.config.modelsUnboundHint")}
            </div>
          )}
          <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
            {t("memory.config.overview")}
          </div>
          {CONFIG_FIELDS.map(([enabledKey, scope, limitKey]) => (
            <div key={enabledKey} className="grid gap-3 rounded-md border p-3 sm:grid-cols-[1fr_132px]">
              <div className="space-y-1">
                <label className="flex items-center gap-2 text-sm font-medium">
                  <Checkbox
                    checked={config[enabledKey]}
                    disabled={readOnly}
                    onCheckedChange={(checked) => update({ [enabledKey]: checked === true })}
                  />
                  {t(`memory.config.scopes.${scope}.label`)}
                </label>
                <p className="text-xs text-muted-foreground">
                  {t("memory.config.scopeHint")}
                </p>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`${kind}-${limitKey}`} className="text-xs">{t(`memory.config.scopes.${scope}.limitLabel`)}</Label>
                <Input
                  id={`${kind}-${limitKey}`}
                  type="number"
                  min={0}
                  value={config[limitKey]}
                  disabled={readOnly}
                  onChange={(event) =>
                    update({ [limitKey]: Number.parseInt(event.target.value || "0", 10) })
                  }
                />
              </div>
            </div>
          ))}
        </div>

        <div className="space-y-4 rounded-md border p-3">
          <div className="grid gap-2">
            <Label>{t("memory.config.searchStrategy")}</Label>
            <Select
              value={config.mindmemos_search_strategy}
              disabled={readOnly}
              onValueChange={(value) => update({ mindmemos_search_strategy: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fast">fast</SelectItem>
                <SelectItem value="agentic">agentic</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {t("memory.config.searchStrategyHint")}
            </p>
          </div>
          <div className="grid gap-2">
            <Label>{t("memory.config.searchThreshold")}</Label>
            <Input
              type="number"
              min={0}
              max={1}
              step="0.01"
              value={config.mindmemos_score_threshold ?? ""}
              disabled={readOnly || !config.mindmemos_rerank}
              onChange={(event) =>
                update({
                  mindmemos_score_threshold:
                    event.target.value === "" ? null : Number.parseFloat(event.target.value),
                })
              }
              placeholder={t("memory.config.optional")}
            />
            <p className="text-xs text-muted-foreground">
              {t("memory.config.searchThresholdHint")}
            </p>
          </div>
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Checkbox
                checked={config.mindmemos_rerank}
                disabled={readOnly}
                onCheckedChange={(checked) =>
                  update({
                    mindmemos_rerank: checked === true,
                    ...(checked === true ? {} : { mindmemos_score_threshold: null }),
                  })
                }
              />
              {t("memory.config.enableRerank")}
            </label>
            <p className="text-xs text-muted-foreground">
              {t("memory.config.rerankHint")}
            </p>
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Checkbox
                checked={config.mindmemos_fail_open}
                disabled={readOnly}
                onCheckedChange={(checked) => update({ mindmemos_fail_open: checked === true })}
              />
              {t("memory.config.failOpen")}
            </label>
            <p className="text-xs text-muted-foreground">
              {t("memory.config.failOpenHint")}
            </p>
            {config.mindmemos_fail_open && (
              <Alert className="border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
                <AlertTriangle className="size-4" />
                <AlertTitle>{t("memory.config.failOpenAlertTitle")}</AlertTitle>
                <AlertDescription className="text-amber-900 dark:text-amber-100">
                  {t("memory.config.failOpenAlertDescription")}
                </AlertDescription>
              </Alert>
            )}
          </div>
        </div>
      </div>
      <div className="flex justify-end border-t px-4 py-3">
        <Button type="button" className="gap-1.5" disabled={readOnly || isSaving} onClick={() => void save()}>
          {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
          {t("memory.config.save")}
        </Button>
      </div>
    </div>
  )
}

function MemoryConfigSkeleton({ title, description }: { title: string; description: string }) {
  const { t } = useTranslation()
  return (
    <div data-testid="memory-settings-skeleton" className="rounded-lg border bg-card/60">
      <div className="space-y-2 border-b px-4 py-3">
        <div className="flex items-start gap-2">
          <ServerCog className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold leading-5">{title}</h2>
            <p className="text-xs leading-5 text-muted-foreground">{description}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 pl-6">
          {[0, 1, 2, 3, 4].map((item) => (
            <Skeleton key={item} className="h-5 w-24 rounded-full" />
          ))}
        </div>
      </div>

      <div className="grid gap-4 p-4">
        <div className="space-y-4">
          <div className="rounded-md border bg-muted/20 px-3 py-2">
            <Skeleton className="h-4 w-full max-w-[440px]" />
          </div>
          {CONFIG_FIELDS.map(([, scope]) => (
            <div key={scope} className="grid gap-3 rounded-md border p-3 sm:grid-cols-[1fr_132px]">
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Skeleton className="size-4 rounded-sm" />
                  <span className="text-sm font-medium text-muted-foreground">{t(`memory.config.scopes.${scope}.label`)}</span>
                </div>
                <Skeleton className="h-3 w-full max-w-[320px]" />
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs text-muted-foreground">{t(`memory.config.scopes.${scope}.limitLabel`)}</Label>
                <Skeleton className="h-9 w-full rounded-md" />
              </div>
            </div>
          ))}
        </div>

        <div className="space-y-4 rounded-md border p-3">
          <div className="grid gap-2">
            <Label className="text-muted-foreground">{t("memory.config.searchStrategy")}</Label>
            <Skeleton className="h-9 w-full rounded-md" />
            <Skeleton className="h-3 w-full max-w-[420px]" />
          </div>
          <div className="grid gap-2">
            <Label className="text-muted-foreground">{t("memory.config.searchThreshold")}</Label>
            <Skeleton className="h-9 w-full rounded-md" />
            <Skeleton className="h-3 w-full max-w-[420px]" />
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Skeleton className="size-4 rounded-sm" />
              <Skeleton className="h-4 w-20" />
            </div>
            <Skeleton className="h-3 w-full max-w-[420px]" />
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Skeleton className="size-4 rounded-sm" />
              <Skeleton className="h-4 w-40" />
            </div>
            <Skeleton className="h-3 w-full max-w-[420px]" />
          </div>
        </div>
      </div>
      <div className="flex justify-end border-t px-4 py-3">
        <Skeleton className="h-9 w-24 rounded-md" />
      </div>
    </div>
  )
}
