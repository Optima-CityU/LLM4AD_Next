import {
  Bot,
  Check,
  ChevronsUpDown,
  ClipboardCheck,
  Code,
  Info,
  Settings,
  X,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ProviderResponse } from "@/client"
import DefaultModelSettings from "@/components/LlmProvider/DefaultModelSettings"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
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
import { Skeleton } from "@/components/ui/skeleton"
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { cn } from "@/lib/utils"
import { formatProviderModel } from "@/lib/providerModel"

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

const SPECIAL_PROVIDERS = ["", "default", "mock"] as const

function isSpecialProvider(v: string): boolean {
  return SPECIAL_PROVIDERS.includes(v as (typeof SPECIAL_PROVIDERS)[number])
}

interface ModelInputProps {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  disabled?: boolean
  readOnly?: boolean
  placeholder?: string
  compact?: boolean
}

function ModelInput({
  value,
  onChange,
  suggestions,
  disabled,
  readOnly,
  placeholder,
  compact,
}: ModelInputProps) {
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [open])

  const isReadOnly = readOnly || disabled

  return (
    <div ref={containerRef} className="relative">
      <Input
        ref={inputRef}
        value={value === "codex-default" ? "" : value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => !isReadOnly && suggestions.length > 0 && setOpen(true)}
        placeholder={value === "codex-default" ? formatProviderModel(value) : placeholder}
        disabled={disabled}
        readOnly={readOnly}
        className={cn(
          "pr-8",
          compact && "h-8 text-xs",
          readOnly && "bg-muted/50 cursor-not-allowed",
        )}
      />
      {suggestions.length > 0 && !isReadOnly && (
        <button
          type="button"
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          onClick={() => {
            setOpen(!open)
            inputRef.current?.focus()
          }}
        >
          <ChevronsUpDown className="size-3.5" />
        </button>
      )}
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 top-full mt-1 left-0 w-full max-h-48 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg p-1">
          {suggestions.map((model) => (
            <button
              key={model}
              type="button"
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent cursor-pointer",
                value === model && "bg-accent",
              )}
              onClick={() => {
                onChange(model)
                setOpen(false)
              }}
            >
              <Check
                className={cn(
                  "size-3.5 shrink-0",
                  value === model ? "opacity-100" : "opacity-0",
                )}
              />
              {formatProviderModel(model)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

interface ProviderSelectWithClearProps {
  value: string
  onValueChange: (v: string) => void
  providers: ProviderResponse[]
  t: (key: string) => string
  defaultHint?: string
  compact?: boolean
}

function ProviderSelectWithClear({
  value,
  onValueChange,
  providers,
  t,
  defaultHint,
  compact,
}: ProviderSelectWithClearProps) {
  return (
    <div className="relative">
      <Select value={value || undefined} onValueChange={onValueChange}>
        <SelectTrigger
          className={cn("w-full", compact && "text-xs")}
          size={compact ? "sm" : "default"}
        >
          <SelectValue
            placeholder={t("evolution.providerSelect.selectProvider")}
          />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>
              {t("evolution.providerSelect.providerLabel")}
            </SelectLabel>
            <SelectItem value="default">
              <div className="flex items-center gap-2">
                <Badge
                  variant="secondary"
                  className="text-[10px] font-medium px-1.5 py-0 bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30"
                >
                  DEFAULT
                </Badge>
                <span className="text-muted-foreground">
                  {defaultHint || t("evolution.providerSelect.useDefault")}
                </span>
              </div>
            </SelectItem>
            <SelectItem value="mock">
              <div className="flex items-center gap-2">
                <Badge
                  variant="secondary"
                  className="text-[10px] font-medium px-1.5 py-0 bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30"
                >
                  MOCK
                </Badge>
                <span className="text-muted-foreground">
                  {t("evolution.providerSelect.useMock")}
                </span>
              </div>
            </SelectItem>
            {providers.length > 0 && <SelectSeparator />}
            {providers.map((provider) => (
              <SelectItem key={provider.id} value={provider.id}>
                <div className="flex items-center gap-2">
                  <span>{provider.name}</span>
                  <Badge variant="outline" className="text-xs font-normal">
                    {provider.type}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      {value && (
        <button
          type="button"
          className="absolute right-8 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground rounded-sm p-0.5"
          onClick={(e) => {
            e.stopPropagation()
            onValueChange("")
          }}
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

interface ProviderModelRowProps {
  icon: React.ReactNode
  label: string
  providers: ProviderResponse[]
  providerValue: string
  modelValue: string
  onUpdate: (fields: Record<string, string>) => void
  t: (key: string) => string
  defaultHint?: string
  compact?: boolean
}

function ProviderModelRow({
  icon,
  label,
  providers,
  providerValue,
  modelValue,
  onUpdate,
  t,
  defaultHint,
  compact,
}: ProviderModelRowProps) {
  const selectedProvider = providers.find((p) => p.id === providerValue)
  const availableModels = selectedProvider
    ? parseModels(selectedProvider.model)
    : []
  const modelDisabled = isSpecialProvider(providerValue)

  const handleProviderChange = (v: string) => {
    if (!v) {
      onUpdate({ provider: "", model: "" })
    } else if (isSpecialProvider(v)) {
      onUpdate({ provider: v, model: "" })
    } else {
      const provider = providers.find((p) => p.id === v)
      const models = provider ? parseModels(provider.model) : []
      onUpdate({
        provider: v,
        model: models.length === 1 ? models[0] : "",
      })
    }
  }

  if (compact) {
    return (
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground px-0.5">
          <span className="[&>svg]:size-3.5">{icon}</span>
          <span>{label}</span>
        </div>
        <ProviderSelectWithClear
          value={providerValue}
          onValueChange={handleProviderChange}
          providers={providers}
          t={t}
          defaultHint={defaultHint}
          compact
        />
        <ModelInput
          value={modelDisabled ? "" : modelValue}
          onChange={(v) => onUpdate({ model: v })}
          suggestions={availableModels}
          disabled={false}
          readOnly={modelDisabled}
          placeholder={
            modelDisabled
              ? t("evolution.providerSelect.noModelNeeded")
              : t("evolution.providerSelect.inputModel")
          }
          compact
        />
      </div>
    )
  }

  return (
    <div className="grid grid-cols-[120px_1fr_1fr] items-center gap-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        <span>{label}</span>
      </div>

      <ProviderSelectWithClear
        value={providerValue}
        onValueChange={handleProviderChange}
        providers={providers}
        t={t}
        defaultHint={defaultHint}
      />

      <ModelInput
        value={modelDisabled ? "" : modelValue}
        onChange={(v) => onUpdate({ model: v })}
        suggestions={availableModels}
        disabled={false}
        readOnly={modelDisabled}
        placeholder={
          modelDisabled
            ? t("evolution.providerSelect.noModelNeeded")
            : t("evolution.providerSelect.inputModel")
        }
      />
    </div>
  )
}

interface EvolutionProviderSelectProps {
  plannerProvider: string
  plannerModel: string
  coderProvider: string
  coderModel: string
  onPlannerChange: (provider: string, model: string) => void
  onCoderChange: (provider: string, model: string) => void
  compact?: boolean
}

export default function EvolutionProviderSelect({
  plannerProvider,
  plannerModel,
  coderProvider,
  coderModel,
  onPlannerChange,
  onCoderChange,
  compact,
}: EvolutionProviderSelectProps) {
  const { t } = useTranslation()
  const [defaultModelOpen, setDefaultModelOpen] = useState(false)

  const { data: providers, isLoading } = useProviders()

  const { data: defaultModels } = useUserDefaultModels()

  const providerList = providers?.items ?? []

  const plannerHint = [
    defaultModels?.planner_provider_name,
    defaultModels?.planner_model_name ? formatProviderModel(defaultModels.planner_model_name) : "",
  ]
    .filter(Boolean)
    .join(", ")
  const coderHint = [
    defaultModels?.coder_provider_name,
    defaultModels?.coder_model_name ? formatProviderModel(defaultModels.coder_model_name) : "",
  ]
    .filter(Boolean)
    .join(", ")

  const handleUpdate = (
    prefix: "planner" | "coder",
    fields: Record<string, string>,
  ) => {
    const currentProvider =
      prefix === "planner" ? plannerProvider : coderProvider
    const currentModel = prefix === "planner" ? plannerModel : coderModel
    const nextProvider =
      "provider" in fields ? fields.provider : currentProvider
    const nextModel = "model" in fields ? fields.model : currentModel
    const cb = prefix === "planner" ? onPlannerChange : onCoderChange
    cb(nextProvider, nextModel)
  }

  if (isLoading) {
    return (
      <div className={compact ? "space-y-2" : "space-y-3"}>
        {!compact && <Skeleton className="h-5 w-48" />}
        <Skeleton className={compact ? "h-8 w-full" : "h-9 w-full"} />
        <Skeleton className={compact ? "h-8 w-full" : "h-9 w-full"} />
        <Skeleton className={compact ? "h-8 w-full" : "h-9 w-full"} />
      </div>
    )
  }

  if (compact) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={() => setDefaultModelOpen(true)}
            className="flex items-center gap-1 text-[10px] text-primary hover:text-primary/80 transition-colors"
          >
            <Settings className="size-3" />
            <span>{t("llmProvider.defaultModel.title")}</span>
          </button>
          <DefaultModelSettings
            open={defaultModelOpen}
            onOpenChange={setDefaultModelOpen}
          />
        </div>
        <ProviderModelRow
          icon={<Bot className="size-4" />}
          label={t("evolution.providerSelect.plannerLabel")}
          providers={providerList}
          providerValue={plannerProvider}
          modelValue={plannerModel}
          onUpdate={(fields) => handleUpdate("planner", fields)}
          t={t}
          defaultHint={plannerHint}
          compact
        />
        <ProviderModelRow
          icon={<Code className="size-4" />}
          label={t("evolution.providerSelect.coderLabel")}
          providers={providerList}
          providerValue={coderProvider}
          modelValue={coderModel}
          onUpdate={(fields) => handleUpdate("coder", fields)}
          t={t}
          defaultHint={coderHint}
          compact
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">
          {t("evolution.providerSelect.title")}
        </h4>
        <div className="flex items-center gap-3">
          {!plannerProvider && !coderProvider && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Info className="size-3.5" />
              <span>{t("evolution.providerSelect.defaultHint")}</span>
            </div>
          )}
          <button
            type="button"
            onClick={() => setDefaultModelOpen(true)}
            className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors"
          >
            <Settings className="size-3" />
            <span>{t("llmProvider.defaultModel.title")}</span>
          </button>
          <DefaultModelSettings
            open={defaultModelOpen}
            onOpenChange={setDefaultModelOpen}
          />
        </div>
      </div>

      <div className="grid grid-cols-[120px_1fr_1fr] gap-3 mb-2">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("evolution.providerSelect.roleHeader")}
        </div>
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("evolution.providerSelect.providerHeader")}
        </div>
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("evolution.providerSelect.modelHeader")}
        </div>
      </div>

      <div className="space-y-3">
        <ProviderModelRow
          icon={<Bot className="size-4" />}
          label={t("evolution.providerSelect.plannerLabel")}
          providers={providerList}
          providerValue={plannerProvider}
          modelValue={plannerModel}
          onUpdate={(fields) => handleUpdate("planner", fields)}
          t={t}
          defaultHint={plannerHint}
        />
        <ProviderModelRow
          icon={<Code className="size-4" />}
          label={t("evolution.providerSelect.coderLabel")}
          providers={providerList}
          providerValue={coderProvider}
          modelValue={coderModel}
          onUpdate={(fields) => handleUpdate("coder", fields)}
          t={t}
          defaultHint={coderHint}
        />
      </div>
    </div>
  )
}

interface EvaluatorProviderSelectProps {
  evaluatorProvider: string
  evaluatorModel: string
  onEvaluatorChange: (provider: string, model: string) => void
  compact?: boolean
}

/**
 * Provider/model selector for the evaluator step.
 *
 * Mirrors the planner/coder rows of {@link EvolutionProviderSelect}, but binds
 * to a single role whose values are stored under ``evaluator.provider`` and
 * ``evaluator.provider_model``.
 */
export function EvaluatorProviderSelect({
  evaluatorProvider,
  evaluatorModel,
  onEvaluatorChange,
  compact,
}: EvaluatorProviderSelectProps) {
  const { t } = useTranslation()
  const [defaultModelOpen, setDefaultModelOpen] = useState(false)

  const { data: providers, isLoading } = useProviders()

  const { data: defaultModels } = useUserDefaultModels()

  const providerList = providers?.items ?? []

  // The evaluator has no dedicated default-model slot, so it falls back to the
  // user's "other" default — same hint format as the planner/coder rows.
  const evaluatorHint = [
    defaultModels?.other_provider_name,
    defaultModels?.other_model_name ? formatProviderModel(defaultModels.other_model_name) : "",
  ]
    .filter(Boolean)
    .join(", ")

  const handleUpdate = (fields: Record<string, string>) => {
    const nextProvider =
      "provider" in fields ? fields.provider : evaluatorProvider
    const nextModel = "model" in fields ? fields.model : evaluatorModel
    onEvaluatorChange(nextProvider, nextModel)
  }

  if (isLoading) {
    return (
      <div className={compact ? "space-y-2" : "space-y-3"}>
        {!compact && <Skeleton className="h-5 w-48" />}
        <Skeleton className={compact ? "h-8 w-full" : "h-9 w-full"} />
      </div>
    )
  }

  if (compact) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={() => setDefaultModelOpen(true)}
            className="flex items-center gap-1 text-[10px] text-primary hover:text-primary/80 transition-colors"
          >
            <Settings className="size-3" />
            <span>{t("llmProvider.defaultModel.title")}</span>
          </button>
          <DefaultModelSettings
            open={defaultModelOpen}
            onOpenChange={setDefaultModelOpen}
          />
        </div>
        <ProviderModelRow
          icon={<ClipboardCheck className="size-4" />}
          label={t("evolution.providerSelect.evaluatorLabel")}
          providers={providerList}
          providerValue={evaluatorProvider}
          modelValue={evaluatorModel}
          onUpdate={handleUpdate}
          t={t}
          defaultHint={evaluatorHint}
          compact
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">
          {t("evolution.providerSelect.title")}
        </h4>
        <div className="flex items-center gap-3">
          {!evaluatorProvider && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Info className="size-3.5" />
              <span>{t("evolution.providerSelect.defaultHint")}</span>
            </div>
          )}
          <button
            type="button"
            onClick={() => setDefaultModelOpen(true)}
            className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors"
          >
            <Settings className="size-3" />
            <span>{t("llmProvider.defaultModel.title")}</span>
          </button>
          <DefaultModelSettings
            open={defaultModelOpen}
            onOpenChange={setDefaultModelOpen}
          />
        </div>
      </div>

      <div className="grid grid-cols-[120px_1fr_1fr] gap-3 mb-2">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("evolution.providerSelect.roleHeader")}
        </div>
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("evolution.providerSelect.providerHeader")}
        </div>
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("evolution.providerSelect.modelHeader")}
        </div>
      </div>

      <div className="space-y-3">
        <ProviderModelRow
          icon={<ClipboardCheck className="size-4" />}
          label={t("evolution.providerSelect.evaluatorLabel")}
          providers={providerList}
          providerValue={evaluatorProvider}
          modelValue={evaluatorModel}
          onUpdate={handleUpdate}
          t={t}
          defaultHint={evaluatorHint}
        />
      </div>
    </div>
  )
}
