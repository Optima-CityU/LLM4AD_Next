/**
 * AutoResearch 供应商 + 模型选择器（参考 evolution 的
 * `EvolutionProviderSelect` / `ChatTuneView` 的 AI 构建选择器）。
 *
 * - Provider：shadcn `Select`，内置 DEFAULT / MOCK 徽章 + 用户配置的真实供应商。
 * - Model：带候选下拉的 combobox；选中特殊供应商（default/mock）时禁用。
 *
 * 后端 `provider_id` 接受 `'default'` / `'mock'` / UUID。
 */

import { Check, ChevronsUpDown, Loader2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ProviderResponse } from "@/client"
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
import { useProviders, useUserDefaultModels } from "@/hooks/useProviders"
import { cn } from "@/lib/utils"

const SPECIAL_PROVIDERS = ["", "default", "mock"] as const

export function isSpecialProvider(v: string): boolean {
  return SPECIAL_PROVIDERS.includes(v as (typeof SPECIAL_PROVIDERS)[number])
}

function parseModels(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
}

/** 模型输入框：带候选下拉建议（来自所选 provider 的 model 字段）。 */
function ModelInput({
  value,
  onChange,
  suggestions,
  disabled,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  disabled?: boolean
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", onClickOutside)
    return () => document.removeEventListener("mousedown", onClickOutside)
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <Input
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => !disabled && suggestions.length > 0 && setOpen(true)}
        placeholder={placeholder}
        disabled={disabled}
        className={cn("h-8 text-xs pr-8", disabled && "bg-muted/50")}
      />
      {suggestions.length > 0 && !disabled && (
        <button
          type="button"
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          onClick={() => {
            setOpen((v) => !v)
            inputRef.current?.focus()
          }}
        >
          <ChevronsUpDown className="size-3.5" />
        </button>
      )}
      {open && suggestions.length > 0 && !disabled && (
        <div className="absolute z-50 top-full mt-1 left-0 w-full max-h-48 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg p-1">
          {suggestions.map((model) => (
            <button
              key={model}
              type="button"
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs hover:bg-accent cursor-pointer",
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
              {model}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

interface Props {
  provider: string
  model: string
  onChange: (provider: string, model: string) => void
  /** 允许「跟随会话默认」的空值项（输入栏覆盖场景）。 */
  allowInherit?: boolean
  className?: string
}

/**
 * Provider + Model 两栏选择器（紧凑）。左 Select 选供应商，右 combobox 填模型。
 * 选到 default/mock 时模型框自动禁用清空。
 */
export default function ProviderModelPicker({
  provider,
  model,
  onChange,
  allowInherit,
  className,
}: Props) {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useProviders()
  const { data: defaultModels } = useUserDefaultModels()
  const providers: ProviderResponse[] = data?.items ?? []

  // 「默认」项显示实际生效的系统默认供应商/模型（与 evolution 的 AI 构建选择器一致）。
  const defaultHint = [
    defaultModels?.planner_provider_name,
    defaultModels?.planner_model_name,
  ]
    .filter(Boolean)
    .join(" / ")

  const selected = providers.find((p) => p.id === provider)
  const availableModels = selected ? parseModels(selected.model) : []
  const modelDisabled = isSpecialProvider(provider)

  const handleProvider = (v: string) => {
    if (v === "__inherit__" || !v || isSpecialProvider(v)) {
      onChange(v === "__inherit__" ? "" : v, "")
      return
    }
    const p = providers.find((x) => x.id === v)
    const models = p ? parseModels(p.model) : []
    onChange(v, models.length === 1 ? models[0] : "")
  }

  return (
    <div className={cn("grid grid-cols-2 gap-2", className)}>
      <Select
        value={provider || undefined}
        onValueChange={handleProvider}
      >
        <SelectTrigger size="sm" className="w-full text-xs">
          <SelectValue placeholder={t("autoResearch.provider.inherit")} />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>{t("autoResearch.create.providerLabel")}</SelectLabel>
            {allowInherit && (
              <SelectItem value="__inherit__">
                <span className="text-muted-foreground">
                  {t("autoResearch.provider.inherit")}
                </span>
              </SelectItem>
            )}
            <SelectItem value="default">
              <div className="flex items-center gap-2">
                <Badge
                  variant="secondary"
                  className="text-[10px] font-medium px-1.5 py-0 bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30"
                >
                  DEFAULT
                </Badge>
                <span className="text-muted-foreground">
                  {defaultHint || t("autoResearch.provider.default")}
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
                  {t("autoResearch.provider.mock")}
                </span>
              </div>
            </SelectItem>
            {providers.length > 0 && <SelectSeparator />}
            {/* 用户自定义供应商的加载/失败态：default/mock 始终可用，故这里只在
                自定义列表区给出轻量反馈，不阻断整个选择器。 */}
            {isLoading && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 text-[11px] text-muted-foreground/60">
                <Loader2 className="size-3 animate-spin" />
                {t("autoResearch.provider.loading", {
                  defaultValue: "加载供应商...",
                })}
              </div>
            )}
            {isError && !isLoading && (
              <div className="px-2 py-1.5 text-[11px] text-destructive/80">
                {t("autoResearch.provider.loadError", {
                  defaultValue: "供应商加载失败",
                })}
              </div>
            )}
            {providers.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                <div className="flex items-center gap-2">
                  <span>{p.name}</span>
                  <Badge variant="outline" className="text-[10px] font-normal">
                    {p.type}
                  </Badge>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <ModelInput
        value={modelDisabled ? "" : model}
        onChange={(v) => onChange(provider, v)}
        suggestions={availableModels}
        disabled={modelDisabled}
        placeholder={
          modelDisabled
            ? t("autoResearch.provider.noModelNeeded")
            : t("autoResearch.create.modelPlaceholder")
        }
      />
    </div>
  )
}
