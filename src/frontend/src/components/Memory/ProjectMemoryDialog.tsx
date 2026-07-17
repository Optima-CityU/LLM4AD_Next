import { Database, FolderKanban } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { authFetch } from "@/utils/auth"

import MemoryCardManager from "./MemoryCardManager"
import type { MemoryConfig } from "./types"
import OnboardingTour from "@/components/Onboarding/OnboardingTour"

export default function ProjectMemoryDialog({
  projectId,
  projectName,
}: {
  projectId: string
  projectName?: string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [config, setConfig] = useState<MemoryConfig | null>(null)
  const [isLoadingConfig, setIsLoadingConfig] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    setConfig(null)
    setLoadFailed(false)
  }, [projectId])

  const hasProject = Boolean(projectId)
  const isConfigLoading = open && hasProject && isLoadingConfig
  const systemReady = config?.system_runtime_available === true
  const bindingReady = Boolean(config?.mindmemos_binding_id)
  const memoryReady = !loadFailed && systemReady && bindingReady
  const memoryDisabled = !memoryReady
  const disabledReason = isConfigLoading
    ? t("memory.projectDialog.disabled.checking")
    : loadFailed
      ? t("memory.projectDialog.disabled.checkFailed")
      : !systemReady
        ? t("memory.projectDialog.disabled.serviceUnavailable")
        : !bindingReady
          ? t("memory.projectDialog.disabled.modelUnbound")
          : undefined

  useEffect(() => {
    if (!open || !hasProject) return

    const baseUrl = import.meta.env.VITE_API_URL || ""
    const controller = new AbortController()
    let cancelled = false

    setConfig(null)
    setLoadFailed(false)
    setIsLoadingConfig(true)

    async function loadAvailability() {
      try {
        const response = await authFetch(
          `${baseUrl}/api/v1/llm4ad/memory/projects/${projectId}/config`,
          { signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error("Failed to load project memory availability")
        }
        const payload = (await response.json()) as MemoryConfig
        if (!cancelled) setConfig(payload)
      } catch (error) {
        if (controller.signal.aborted) return
        console.error(error)
        if (!cancelled) setLoadFailed(true)
      } finally {
        if (!cancelled) setIsLoadingConfig(false)
      }
    }

    void loadAvailability()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [hasProject, open, projectId])

  const dialogDescription = projectName
    ? t("memory.projectDialog.descriptionWithProject", { projectName })
    : t("memory.projectDialog.description")
  const serviceLabel = systemReady
    ? t("memory.projectDialog.badges.serviceReady")
    : isConfigLoading
      ? t("memory.projectDialog.badges.checking")
      : t("memory.projectDialog.badges.serviceUnavailable")
  const bindingLabel = bindingReady
    ? t("memory.projectDialog.badges.modelBound")
    : t("memory.projectDialog.badges.modelUnbound")

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setIsLoadingConfig(true)
      setLoadFailed(false)
    }
    setOpen(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <OnboardingTour
        tourId="project-memory"
        enabled={open && !isConfigLoading && memoryReady}
        steps={[
          {
            selector: '[data-tour="project-memory-manager"]',
            title: t("tour.memory.projectTitle"),
            content: t("tour.memory.projectContent"),
            placement: "left",
          },
        ]}
      />
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-9 shrink-0 gap-1.5"
          title={t("memory.projectDialog.trigger")}
          disabled={!hasProject}
        >
          <Database className="size-4" />
          <span>{t("memory.projectDialog.trigger")}</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-hidden p-0 sm:max-w-6xl">
        <DialogHeader className="border-b px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3 pr-8">
            <div className="min-w-0">
              <DialogTitle className="flex items-center gap-2">
                <FolderKanban className="size-4 text-primary" />
                {t("memory.projectDialog.title")}
              </DialogTitle>
              <DialogDescription className="mt-1">{dialogDescription}</DialogDescription>
            </div>
            <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
              <Badge variant="outline">{t("memory.projectDialog.badges.scope")}</Badge>
              <Badge variant={systemReady ? "default" : "secondary"}>{serviceLabel}</Badge>
              <Badge variant={bindingReady ? "outline" : "secondary"}>{bindingLabel}</Badge>
            </div>
          </div>
        </DialogHeader>

        <div className="max-h-[calc(90vh-96px)] min-h-[560px] overflow-y-auto p-4">
          <div data-tour="project-memory-manager">
            <MemoryCardManager
              scope="project"
              projectId={projectId}
              title={t("memory.projectDialog.cardTitle")}
              description={t("memory.projectDialog.cardDescription")}
              disabled={memoryDisabled}
              disabledReason={disabledReason}
              loadEnabled={memoryReady}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
