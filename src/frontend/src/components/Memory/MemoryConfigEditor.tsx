import { AlertTriangle, Loader2, LockKeyhole, Save, ServerCog } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
  ["include_user_memory", "注入用户级记忆", "user_memory_limit", "用户级注入条数"],
  ["include_project_memory", "注入项目级记忆", "project_memory_limit", "项目级注入条数"],
  ["include_task_memory", "注入任务级记忆", "task_memory_limit", "任务级注入条数"],
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
  onLoaded,
  onSaved,
}: {
  kind: "user" | "project"
  projectId?: string
  title: string
  description: string
  enabled?: boolean
  disabledReason?: string
  onLoaded?: (config: MemoryConfig) => void
  onSaved?: (config: MemoryConfig) => void
}) {
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
      if (!response.ok) throw new Error("加载记忆配置失败")
      const loaded = await response.json()
      setConfig(loaded)
      onLoaded?.(loaded)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载记忆配置失败")
    } finally {
      setIsLoading(false)
    }
  }, [enabled, endpoint, onLoaded])

  useEffect(() => {
    void loadConfig()
  }, [loadConfig])

  const update = (patch: Partial<MemoryConfig>) => {
    setConfig((current) => (current ? { ...current, ...patch } : current))
  }

  const save = async () => {
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
      if (!response.ok) throw new Error("保存记忆配置失败")
      const saved = await response.json()
      setConfig(saved)
      onSaved?.(saved)
      toast.success("记忆配置已保存")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存记忆配置失败")
    } finally {
      setIsSaving(false)
    }
  }

  if (!enabled) {
    return (
      <div className="rounded-lg border bg-muted/30 text-muted-foreground">
        <div className="space-y-2 border-b px-4 py-3">
          <div className="flex items-start gap-2">
            <LockKeyhole className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold leading-5 text-foreground">{title}</h2>
              <p className="text-xs leading-5">{description}</p>
            </div>
          </div>
          <div className="pl-6 text-xs">
            {disabledReason || "记忆服务未就绪，暂不能配置默认注入策略。"}
          </div>
        </div>

        <div className="pointer-events-none select-none space-y-4 p-4 opacity-60">
          <div className="rounded-md border bg-background/50 px-3 py-2 text-xs">
            绑定 Chat 与 Embedding 模型后，才能配置后续任务默认注入哪些范围的记忆。
          </div>
          {CONFIG_FIELDS.map(([, label, , limitLabel]) => (
            <div key={label} className="grid gap-3 rounded-md border bg-background/50 p-3 sm:grid-cols-[1fr_132px]">
              <div className="space-y-2">
                <div className="text-sm font-medium">{label}</div>
                <div className="text-xs">
                  模型绑定完成前，不会读取或保存该范围的默认注入数量。
                </div>
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs">{limitLabel}</Label>
                <Input value="" placeholder="未启用" disabled />
              </div>
            </div>
          ))}
          <div className="space-y-4 rounded-md border bg-background/50 p-3">
            <div className="grid gap-2">
              <Label>搜索策略</Label>
              <Input value="" placeholder="未启用" disabled />
              <p className="text-xs">绑定模型后可选择 fast 或 agentic。</p>
            </div>
            <div className="grid gap-2">
              <Label>搜索阈值</Label>
              <Input value="" placeholder="未启用" disabled />
            </div>
          </div>
        </div>
        <div className="flex justify-end border-t px-4 py-3">
          <Button type="button" className="gap-1.5" disabled>
            <Save className="size-4" />
            保存配置
          </Button>
        </div>
      </div>
    )
  }

  if (isLoading || !config) {
    return (
      <div className="flex min-h-[620px] items-center justify-center rounded-lg border bg-card/60 text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        正在加载记忆配置...
      </div>
    )
  }

  return (
    <div className="rounded-lg border bg-card/60">
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
            {config.system_enabled ? "系统已启用" : "系统未启用"}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_runtime_available ? "default" : "secondary"}
          >
            {config.system_runtime_available ? "记忆可用" : "沿用旧记忆"}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_api_key_configured ? "outline" : "destructive"}
          >
            {config.system_api_key_configured ? "网关认证已配置" : "网关认证未配置"}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.mindmemos_binding_id ? "outline" : "destructive"}
          >
            {config.mindmemos_binding_id ? "模型已绑定" : "模型未绑定"}
          </Badge>
          <Badge
            className="shrink-0 whitespace-nowrap"
            variant={config.system_rerank_configured ? "outline" : "secondary"}
          >
            {config.system_rerank_enabled
              ? config.system_rerank_configured ? "Rerank 已配置" : "Rerank 未配置"
              : "Rerank 未启用"}
          </Badge>
        </div>
      </div>

      <div className="grid gap-4 p-4">
        <div className="space-y-4">
          {!config.system_runtime_available && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              MindMemOS 服务或网关认证未就绪；当前任务会沿用旧记忆模块。
            </div>
          )}
          {config.system_runtime_available && !config.mindmemos_binding_id && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              当前用户尚未绑定记忆抽取和检索模型。绑定后，新任务才会默认使用 MindMemOS。
            </div>
          )}
          <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
            这里控制后续任务提示词中最多注入哪些范围的记忆。MindMemOS 连接地址和网关认证由环境变量托管；
            Chat 与 Embedding 模型在记忆设置中绑定。
          </div>
          {CONFIG_FIELDS.map(([enabledKey, label, limitKey, limitLabel]) => (
            <div key={enabledKey} className="grid gap-3 rounded-md border p-3 sm:grid-cols-[1fr_132px]">
              <div className="space-y-1">
                <label className="flex items-center gap-2 text-sm font-medium">
                  <Checkbox
                    checked={config[enabledKey]}
                    onCheckedChange={(checked) => update({ [enabledKey]: checked === true })}
                  />
                  {label}
                </label>
                <p className="text-xs text-muted-foreground">
                  每次生成提示词时，最多从该范围取右侧数量的相关记忆注入上下文。
                </p>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`${kind}-${limitKey}`} className="text-xs">{limitLabel}</Label>
                <Input
                  id={`${kind}-${limitKey}`}
                  type="number"
                  min={0}
                  value={config[limitKey]}
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
            <Label>搜索策略</Label>
            <Select
              value={config.mindmemos_search_strategy}
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
              fast 延迟低，适合日常任务；agentic 会做更深入的检索，可能更准，但更慢且成本更高。
            </p>
          </div>
          <div className="grid gap-2">
            <Label>搜索阈值</Label>
            <Input
              type="number"
              min={0}
              max={1}
              step="0.01"
              value={config.mindmemos_score_threshold ?? ""}
              onChange={(event) =>
                update({
                  mindmemos_score_threshold:
                    event.target.value === "" ? null : Number.parseFloat(event.target.value),
                })
              }
              placeholder="可留空"
            />
            <p className="text-xs text-muted-foreground">
              相关度过滤门槛。值越高越严格，注入更少但更精准；留空表示不过滤。
            </p>
          </div>
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Checkbox
                checked={config.mindmemos_rerank}
                onCheckedChange={(checked) => update({ mindmemos_rerank: checked === true })}
              />
              启用重排
            </label>
            <p className="text-xs text-muted-foreground">
              对初步检索结果二次排序，提高相关性，但会增加延迟和模型调用成本。
            </p>
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Checkbox
                checked={config.mindmemos_fail_open}
                onCheckedChange={(checked) => update({ mindmemos_fail_open: checked === true })}
              />
              记忆服务异常时继续运行任务
            </label>
            <p className="text-xs text-muted-foreground">
              关闭时，MindMemOS 异常会让任务失败，适合严格验证记忆效果的场景。
            </p>
            {config.mindmemos_fail_open && (
              <Alert className="border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
                <AlertTriangle className="size-4" />
                <AlertTitle>已开启异常放行</AlertTitle>
                <AlertDescription className="text-amber-900 dark:text-amber-100">
                  任务不会因 MindMemOS 异常中断，但会跳过远端记忆，可能导致本次演化缺少历史经验。
                </AlertDescription>
              </Alert>
            )}
          </div>
        </div>
      </div>
      <div className="flex justify-end border-t px-4 py-3">
        <Button type="button" className="gap-1.5" disabled={isSaving} onClick={() => void save()}>
          {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
          保存配置
        </Button>
      </div>
    </div>
  )
}
