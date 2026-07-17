import { createFileRoute } from "@tanstack/react-router"
import { AlertCircle, CheckCircle2, Loader2, RefreshCw, Settings2 } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import MemoryCardManager, {
  type OnboardingMemoryDemoPhase,
} from "@/components/Memory/MemoryCardManager"
import MemoryConfigEditor from "@/components/Memory/MemoryConfigEditor"
import MemoryProviderBindingEditor from "@/components/Memory/MemoryProviderBindingEditor"
import type { MemoryHealth, MemoryProviderBinding } from "@/components/Memory/types"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
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
  const { t } = useTranslation()
  const [health, setHealth] = useState<MemoryHealth | null>(null)
  const [binding, setBinding] = useState<MemoryProviderBinding | null>(null)
  const [bindingError, setBindingError] = useState<string | null>(null)
  const [isCheckingHealth, setIsCheckingHealth] = useState(true)
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [memoryTourStep, setMemoryTourStep] = useState(0)
  const [isMemoryTourActive, setIsMemoryTourActive] = useState(false)
  const [onboardingDemoPhase, setOnboardingDemoPhase] = useState<OnboardingMemoryDemoPhase | null>(null)
  const memoryReady = health?.ok === true && binding?.configured === true
  const shouldStartMemoryTour = !isCheckingHealth && !bindingError

  const advanceMemoryTour = useCallback((nextStep: number) => {
    if (memoryTourStep === 2 && nextStep === 3) {
      setIsSettingsOpen(false)
      window.setTimeout(() => setMemoryTourStep(3), 320)
      return
    }
    if (memoryTourStep === 3 && nextStep === 2) {
      setIsSettingsOpen(true)
      window.setTimeout(() => setMemoryTourStep(2), 320)
      return
    }
    if (memoryTourStep === 3 && nextStep === 4) {
      setOnboardingDemoPhase("input")
      setMemoryTourStep(4)
      return
    }
    if (memoryTourStep === 4 && nextStep === 5) {
      setOnboardingDemoPhase("generating")
      setMemoryTourStep(5)
      return
    }
    if (memoryTourStep === 6 && nextStep === 7) {
      setOnboardingDemoPhase("saving")
      return
    }
    if (memoryTourStep === 7 && nextStep === 8) {
      setOnboardingDemoPhase("disabled")
      setMemoryTourStep(8)
      return
    }
    if (memoryTourStep === 8 && nextStep === 9) {
      setOnboardingDemoPhase("enabled")
      setMemoryTourStep(9)
      return
    }
    if (memoryTourStep === 4 && nextStep === 3) {
      setOnboardingDemoPhase(null)
      setMemoryTourStep(3)
      return
    }
    if ((memoryTourStep === 5 || memoryTourStep === 6) && nextStep < memoryTourStep) {
      setOnboardingDemoPhase("input")
      setMemoryTourStep(4)
      return
    }
    if (memoryTourStep === 7 && nextStep === 6) {
      setOnboardingDemoPhase("preview")
      setMemoryTourStep(6)
      return
    }
    if (memoryTourStep === 8 && nextStep === 7) {
      setOnboardingDemoPhase("saved")
      setMemoryTourStep(7)
      return
    }
    if (memoryTourStep === 9 && nextStep === 8) {
      setOnboardingDemoPhase("disabled")
      setMemoryTourStep(8)
      return
    }
    setMemoryTourStep(nextStep)
  }, [memoryTourStep])

  const handleOnboardingDemoComplete = useCallback((phase: "preview" | "saved") => {
    if (phase === "preview") {
      setOnboardingDemoPhase("preview")
      setMemoryTourStep(6)
      return
    }
    setOnboardingDemoPhase("saved")
    setMemoryTourStep(7)
  }, [])

  const handleMemoryTourStepChange = useCallback((step: number | null) => {
    setIsMemoryTourActive(step !== null)
    if (step === null) {
      setIsSettingsOpen(false)
      setOnboardingDemoPhase(null)
      setMemoryTourStep(0)
    }
  }, [])

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
        if (!bindingResponse.ok) {
          setBinding(null)
          setBindingError(bindingPayload?.detail || "绑定状态加载失败")
        } else {
          setBinding(bindingPayload)
          setBindingError(null)
        }
      } else {
        setBinding(null)
        setBindingError(null)
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
      setBindingError(null)
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
      if (bindingError) {
        return (
          <Badge variant="destructive" className="gap-1.5">
            <AlertCircle className="size-3" />
            绑定状态异常
          </Badge>
        )
      }
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
    <div
      className="flex flex-col gap-4"
      data-testid="memory-page-content"
      inert={isMemoryTourActive}
    >
      <OnboardingTour
        tourId="memory-setup"
        enabled={shouldStartMemoryTour}
        stepIndex={memoryTourStep}
        onStepIndexChange={advanceMemoryTour}
        onStepChange={handleMemoryTourStepChange}
        steps={[
          {
            selector: '[data-tour="memory-overview"]',
            title: t("tour.memory.overviewTitle"),
            content: t("tour.memory.overviewContent"),
            placement: "bottom",
          },
          {
            selector: '[data-tour="memory-provider-binding"]',
            title: t("tour.memory.bindingTitle"),
            content: t("tour.memory.bindingContent"),
            placement: "left",
            onEnter: () => setIsSettingsOpen(true),
          },
          {
            selector: '[data-tour="memory-default-policy"]',
            title: "默认注入策略",
            content: "这里决定后续新项目和新任务默认从全局、项目和任务范围注入多少记忆。",
            placement: "left",
          },
          {
            selector: '[data-tour="memory-add-button"]',
            title: t("tour.memory.addTitle"),
            content: t("tour.memory.addContent"),
            placement: "bottom",
            onEnter: () => setIsSettingsOpen(false),
          },
          {
            selector: '[data-tour="memory-extraction-content"]',
            title: "新增记忆示例",
            content: "这是原有新增记忆窗口的只读模拟内容；继续后会自动生成预览。",
            placement: "top",
          },
          {
            selector: '[data-tour="memory-extraction-progress"]',
            title: "生成记忆预览",
            content: "正在模拟真实提取流程；不会调用长期记忆服务。",
            placement: "top",
          },
          {
            selector: '[data-tour="memory-extraction-preview"]',
            title: "保存预览",
            content: "预览确认后，引导会自动模拟“启用选中”，并使用真实卡片样式展示结果。",
            placement: "top",
          },
          {
            selector: '[data-tour="memory-onboarding-card"]',
            title: "已保存的示例记忆",
            content: "这张卡片使用真实列表组件渲染，但仅存在于本次引导中。",
            placement: "top",
          },
          {
            selector: '[data-tour="memory-onboarding-toggle"]',
            title: "禁用记忆",
            content: "禁用后，任务不会检索或注入这条记忆。",
            placement: "left",
          },
          {
            selector: '[data-tour="memory-onboarding-toggle"]',
            title: "重新启用记忆",
            content: "启用后，它会重新参与检索和注入。",
            placement: "left",
          },
        ]}
      />
      <div className="flex flex-wrap items-center gap-3 border-b pb-4">
        <div className="min-w-0 flex-1" data-tour="memory-overview">
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
          <Sheet
            open={isSettingsOpen}
            onOpenChange={(nextOpen) => {
              if (!nextOpen && isMemoryTourActive && (memoryTourStep === 1 || memoryTourStep === 2)) return
              setIsSettingsOpen(nextOpen)
            }}
          >
            <SheetTrigger asChild>
              <Button type="button" variant="outline" className="gap-1.5">
                <Settings2 className="size-4" />
                默认策略
              </Button>
            </SheetTrigger>
            <SheetContent
              inert={isMemoryTourActive}
              showCloseButton={!isMemoryTourActive || (memoryTourStep !== 1 && memoryTourStep !== 2)}
              onEscapeKeyDown={(event) => {
                if (isMemoryTourActive && (memoryTourStep === 1 || memoryTourStep === 2)) event.preventDefault()
              }}
              onPointerDownOutside={(event) => {
                if (isMemoryTourActive && (memoryTourStep === 1 || memoryTourStep === 2)) event.preventDefault()
              }}
              className="h-dvh max-h-dvh !w-[min(100vw,36rem)] min-w-0 !max-w-none transform-gpu gap-0 overflow-hidden p-0 will-change-transform data-[state=closed]:duration-200 data-[state=open]:duration-300 sm:!max-w-none"
            >
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
                    <MemoryProviderBindingEditor
                      binding={binding}
                      onSaved={refreshMemoryStatus}
                      readOnly={isMemoryTourActive && (memoryTourStep === 1 || memoryTourStep === 2)}
                    />
                    {bindingError && (
                      <Alert className="mt-4 border-destructive/40 bg-destructive/5 text-destructive">
                        <AlertCircle className="size-4" />
                        <AlertTitle>绑定状态加载失败</AlertTitle>
                        <AlertDescription>{bindingError}</AlertDescription>
                      </Alert>
                    )}
                    <div className="h-4" />
                    <MemoryConfigEditor
                      kind="user"
                      title="默认注入策略"
                      description="这些设置会影响后续创建的新项目和新任务。"
                      enabled={memoryReady}
                      readOnly={isMemoryTourActive && (memoryTourStep === 1 || memoryTourStep === 2)}
                      disabledReason={
                        bindingError
                          ? "记忆模型绑定状态加载失败，请重新检测服务状态后再配置默认注入策略。"
                          : health?.ok
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
          <AlertTitle>
            {bindingError ? "绑定状态加载失败" : health?.ok ? "记忆模型未绑定" : "MindMemOS 当前不可用"}
          </AlertTitle>
          <AlertDescription className="text-amber-900 dark:text-amber-100">
            {bindingError
              ? bindingError
              : health?.ok
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
        onboardingDemoActive={isMemoryTourActive}
        onboardingDemoPhase={isMemoryTourActive ? onboardingDemoPhase : null}
        onOnboardingDemoComplete={handleOnboardingDemoComplete}
        disabledReason={
          isCheckingHealth
            ? "正在检测 MindMemOS 服务状态。"
            : bindingError
              ? "记忆模型绑定状态加载失败，请重新检测服务状态。"
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
    <div data-testid="memory-settings-skeleton" className="space-y-4">
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
