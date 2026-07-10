import { createFileRoute } from "@tanstack/react-router"
import { AlertCircle, CheckCircle2, Loader2, RefreshCw, Settings2 } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import MemoryCardManager from "@/components/Memory/MemoryCardManager"
import MemoryConfigEditor from "@/components/Memory/MemoryConfigEditor"
import MemoryProviderBindingEditor from "@/components/Memory/MemoryProviderBindingEditor"
import type { MemoryHealth, MemoryProviderBinding } from "@/components/Memory/types"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { authFetch } from "@/utils/auth"

export const Route = createFileRoute("/_layout/memory")({
  component: MemoryPage,
  head: () => ({
    meta: [{ title: "Memory - LLM4AD_Next" }],
  }),
})

function MemoryPage() {
  const [health, setHealth] = useState<MemoryHealth | null>(null)
  const [binding, setBinding] = useState<MemoryProviderBinding | null>(null)
  const [isCheckingHealth, setIsCheckingHealth] = useState(true)
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const memoryReady = health?.ok === true && binding?.configured === true

  const loadHealth = useCallback(async () => {
    setIsCheckingHealth(true)
    try {
      const baseUrl = import.meta.env.VITE_API_URL || ""
      const response = await authFetch(`${baseUrl}/api/v1/llm4ad/memory/health`)
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail || "检测 MindMemOS 状态失败")
      }
      setHealth(payload)
      if (payload?.ok) {
        const bindingResponse = await authFetch(`${baseUrl}/api/v1/llm4ad/memory/provider-binding`)
        const bindingPayload = await bindingResponse.json().catch(() => null)
        setBinding(bindingResponse.ok ? bindingPayload : null)
      } else {
        setBinding(null)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "检测 MindMemOS 状态失败"
      setHealth({
        ok: false,
        message,
        system_runtime_available: false,
        system_enabled: false,
        system_chat_configured: false,
        system_embedding_configured: false,
        system_api_key_configured: false,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: false,
        auth_ok: false,
        error_code: "frontend.health_failed",
      })
      setBinding(null)
      toast.error(message)
    } finally {
      setIsCheckingHealth(false)
    }
  }, [])

  useEffect(() => {
    void loadHealth()
  }, [loadHealth])

  const refreshMemoryStatus = useCallback(() => {
    void loadHealth()
  }, [loadHealth])

  const healthBadge = () => {
    if (isCheckingHealth) {
      return (
        <Badge variant="secondary" className="gap-1.5">
          <Loader2 className="size-3 animate-spin" />
          正在检测
        </Badge>
      )
    }
    if (memoryReady) {
      return (
        <Badge className="gap-1.5">
          <CheckCircle2 className="size-3" />
          记忆服务正常
        </Badge>
      )
    }
    if (health?.ok && binding?.configured !== true) {
      return (
        <Badge variant="secondary" className="gap-1.5">
          <AlertCircle className="size-3" />
          模型未绑定
        </Badge>
      )
    }
    return (
      <Badge variant="destructive" className="gap-1.5">
        <AlertCircle className="size-3" />
        记忆服务异常
      </Badge>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 border-b pb-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight">全局记忆</h1>
          <p className="text-sm text-muted-foreground">
            管理跨项目复用的长期经验。默认注入策略可在右侧设置中调整。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {healthBadge()}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="outline"
                aria-label="重新检测记忆服务"
                disabled={isCheckingHealth}
                onClick={() => void loadHealth()}
              >
                {isCheckingHealth ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <RefreshCw className="size-4" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>重新检测记忆服务</TooltipContent>
          </Tooltip>
          <Sheet open={isSettingsOpen} onOpenChange={setIsSettingsOpen}>
            <SheetTrigger asChild>
              <Button type="button" variant="outline" className="gap-1.5">
                <Settings2 className="size-4" />
                默认策略
              </Button>
            </SheetTrigger>
            <SheetContent className="!w-[min(100vw,36rem)] !max-w-none transform-gpu gap-0 overflow-hidden p-0 will-change-transform data-[state=closed]:duration-200 data-[state=open]:duration-300 sm:!max-w-none">
              <SheetHeader className="shrink-0 border-b pr-10">
                <SheetTitle>用户默认记忆策略</SheetTitle>
                <SheetDescription>
                  控制新项目和新任务默认使用的记忆注入范围、数量和检索策略。
                </SheetDescription>
              </SheetHeader>
              <div className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable] px-4 py-4">
                {isCheckingHealth ? (
                  <MemorySettingsSkeleton />
                ) : (
                  <>
                    <MemoryProviderBindingEditor binding={binding} onSaved={refreshMemoryStatus} />
                    <div className="h-4" />
                    <MemoryConfigEditor
                      kind="user"
                      title="默认注入策略"
                      description="这些设置会影响后续创建的新项目和新任务。"
                      enabled={memoryReady}
                      disabledReason={
                        health?.ok
                          ? "当前用户尚未绑定记忆模型。请先在上方绑定 Chat 与 Embedding 模型，再配置默认注入策略。"
                          : health?.message || "MindMemOS 当前不可用，无法配置默认注入策略。"
                      }
                      onSaved={refreshMemoryStatus}
                    />
                  </>
                )}
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>

      {!memoryReady && !isCheckingHealth && (
        <Alert className="border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
          <AlertCircle className="size-4" />
          <AlertTitle>{health?.ok ? "记忆模型未绑定" : "MindMemOS 当前不可用"}</AlertTitle>
          <AlertDescription className="text-amber-900 dark:text-amber-100">
            {health?.ok
              ? "请在右上角默认策略中绑定 Chat 与 Embedding 模型后再管理记忆。"
              : health?.message || "请检查系统环境配置或稍后重新检测。"}
          </AlertDescription>
        </Alert>
      )}

      <MemoryCardManager
        scope="user"
        title="用户全局记忆"
        description="跨项目复用的长期经验，适合通用算法经验、偏好和稳定约束。"
        disabled={!memoryReady}
        loadEnabled={memoryReady}
        disabledReason={
          isCheckingHealth
            ? "正在检测 MindMemOS 服务状态。"
            : health?.ok
              ? "当前用户尚未绑定记忆模型，请先在默认策略中绑定 Chat 与 Embedding。"
              : health?.message || "MindMemOS 当前不可用，无法管理远端记忆。"
        }
      />
    </div>
  )
}

function MemorySettingsSkeleton() {
  return (
    <div className="space-y-4">
      <div className="min-h-[140px] rounded-lg border bg-card/60 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-5 w-28" />
            <Skeleton className="h-4 w-full max-w-[360px]" />
            <Skeleton className="h-4 w-3/4 max-w-[280px]" />
          </div>
          <Skeleton className="h-8 w-24 shrink-0" />
        </div>
        <div className="mt-5 rounded-md border bg-muted/20 px-3 py-3">
          <Skeleton className="h-4 w-full max-w-[420px]" />
        </div>
      </div>

      <div className="min-h-[620px] rounded-lg border bg-muted/30">
        <div className="space-y-2 border-b px-4 py-3">
          <div className="flex items-start gap-2">
            <Skeleton className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-4 w-full max-w-[360px]" />
            </div>
          </div>
          <Skeleton className="ml-6 h-4 w-full max-w-[420px]" />
        </div>

        <div className="space-y-4 p-4">
          <Skeleton className="h-10 w-full rounded-md" />
          {[0, 1, 2].map((item) => (
            <div key={item} className="grid gap-3 rounded-md border bg-background/50 p-3 sm:grid-cols-[1fr_132px]">
              <div className="space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-full max-w-[320px]" />
              </div>
              <div className="space-y-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-9 w-full" />
              </div>
            </div>
          ))}
          <div className="space-y-4 rounded-md border bg-background/50 p-3">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        </div>
      </div>
    </div>
  )
}
