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
        throw new Error(payload?.detail || t("memory.page.healthCheckFailed"))
      }
      setHealth(payload)
      if (payload?.ok) {
        const bindingResponse = await authFetch(`${baseUrl}/api/v1/llm4ad/memory/provider-binding`)
        const bindingPayload = await bindingResponse.json().catch(() => null)
        if (!bindingResponse.ok) {
          setBinding(null)
          setBindingError(bindingPayload?.detail || t("memory.page.bindingLoadFailed"))
        } else {
          setBinding(bindingPayload)
          setBindingError(null)
        }
      } else {
        setBinding(null)
        setBindingError(null)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : t("memory.page.healthCheckFailed")
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
  }, [t])

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
          {t("memory.page.checking")}
        </Badge>
      )
    }
    if (memoryReady) {
      return (
        <Badge className="gap-1.5">
          <CheckCircle2 className="size-3" />
          {t("memory.page.serviceHealthy")}
        </Badge>
      )
    }
    if (health?.ok && binding?.configured !== true) {
      if (bindingError) {
        return (
          <Badge variant="destructive" className="gap-1.5">
            <AlertCircle className="size-3" />
            {t("memory.page.bindingError")}
          </Badge>
        )
      }
      return (
        <Badge variant="secondary" className="gap-1.5">
          <AlertCircle className="size-3" />
          {t("memory.page.modelsUnbound")}
        </Badge>
      )
    }
    return (
      <Badge variant="destructive" className="gap-1.5">
        <AlertCircle className="size-3" />
        {t("memory.page.serviceUnhealthy")}
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
            title: t("tour.memory.defaultPolicyTitle"),
            content: t("tour.memory.defaultPolicyContent"),
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
            title: t("tour.memory.addExampleTitle"),
            content: t("tour.memory.addExampleContent"),
            placement: "top",
          },
          {
            selector: '[data-tour="memory-extraction-progress"]',
            title: t("tour.memory.previewGenerationTitle"),
            content: t("tour.memory.previewGenerationContent"),
            placement: "top",
          },
          {
            selector: '[data-tour="memory-extraction-preview"]',
            title: t("tour.memory.savePreviewTitle"),
            content: t("tour.memory.savePreviewContent"),
            placement: "top",
          },
          {
            selector: '[data-tour="memory-onboarding-card"]',
            title: t("tour.memory.savedExampleTitle"),
            content: t("tour.memory.savedExampleContent"),
            placement: "top",
          },
          {
            selector: '[data-tour="memory-onboarding-toggle"]',
            title: t("tour.memory.disableTitle"),
            content: t("tour.memory.disableContent"),
            placement: "left",
          },
          {
            selector: '[data-tour="memory-onboarding-toggle"]',
            title: t("tour.memory.reenableTitle"),
            content: t("tour.memory.reenableContent"),
            placement: "left",
          },
        ]}
      />
      <div className="flex flex-wrap items-center gap-3 border-b pb-4">
        <div className="min-w-0 flex-1" data-tour="memory-overview">
          <h1 className="text-2xl font-bold tracking-tight">{t("memory.page.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("memory.page.description")}
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
                aria-label={t("memory.page.recheck")}
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
            <TooltipContent>{t("memory.page.recheck")}</TooltipContent>
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
                {t("memory.page.defaultPolicy")}
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
                <SheetTitle>{t("memory.page.settingsTitle")}</SheetTitle>
                <SheetDescription>
                  {t("memory.page.settingsDescription")}
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
                        <AlertTitle>{t("memory.page.bindingLoadFailed")}</AlertTitle>
                        <AlertDescription>{bindingError}</AlertDescription>
                      </Alert>
                    )}
                    <div className="h-4" />
                    <MemoryConfigEditor
                      kind="user"
                      title={t("memory.page.defaultPolicy")}
                      description={t("memory.page.defaultPolicyDescription")}
                      enabled={memoryReady}
                      readOnly={isMemoryTourActive && (memoryTourStep === 1 || memoryTourStep === 2)}
                      disabledReason={
                        bindingError
                          ? t("memory.page.bindingLoadDisabledReason")
                          : health?.ok
                            ? t("memory.page.modelsUnboundDisabledReason")
                            : health?.message || t("memory.page.serviceUnavailableDisabledReason")
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
            {bindingError ? t("memory.page.bindingLoadFailed") : health?.ok ? t("memory.page.modelsUnbound") : t("memory.page.serviceUnavailable")}
          </AlertTitle>
          <AlertDescription className="text-amber-900 dark:text-amber-100">
            {bindingError
              ? bindingError
              : health?.ok
                ? t("memory.page.modelsUnboundAlertDescription")
                : health?.message || t("memory.page.serviceUnavailableAlertDescription")}
          </AlertDescription>
        </Alert>
      )}

      <MemoryCardManager
        scope="user"
        title={t("memory.page.managerTitle")}
        description={t("memory.page.managerDescription")}
        disabled={!memoryReady}
        loadEnabled={memoryReady}
        onboardingDemoActive={isMemoryTourActive}
        onboardingDemoPhase={isMemoryTourActive ? onboardingDemoPhase : null}
        onOnboardingDemoComplete={handleOnboardingDemoComplete}
        disabledReason={
          isCheckingHealth
            ? t("memory.page.checkingDisabledReason")
            : bindingError
              ? t("memory.page.bindingLoadManagerDisabledReason")
              : health?.ok
                ? t("memory.page.modelsUnboundManagerDisabledReason")
                : health?.message || t("memory.page.serviceUnavailableManagerDisabledReason")
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
